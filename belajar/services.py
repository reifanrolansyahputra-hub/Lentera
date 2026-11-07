
"""
LENTERA — layanan data belajar.

Mengelola:
- Pencatatan jawaban dari game.
- Pembaruan penguasaan dengan Bayesian Knowledge Tracing.
- Pembuatan misi harian.
- Asesmen diagnostik.
- Penugasan aktivitas.
- Ringkasan dashboard guru.
- Pemetaan semua kompetensi ke Game 1 dan Game 2.
- Jalan Lentera (bagian, level, lampu) ada di jalur.py; bank soal guru ada di bank_soal.py.
"""

import secrets
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from accounts.models import Profile

from . import engine, jalur
from .models import (
    Aktivitas,
    ArsipReset,
    HasilDiagnostik,
    Jawaban,
    Kelas,
    Kompetensi,
    Misi,
    Penguasaan,
    Penugasan,
    Siswa,
    SoalGame,
)


AVATAR = [
    "🦊", "🐼", "🐯", "🦄", "🐸", "🐰",
    "🦁", "🐙", "🐧", "🐨", "🦋", "🐢",
    "🐶", "🐱", "🐵", "🦉", "🐻", "🐳",
]


# ==========================================================
# 1. PEMETAAN GAME YANG SUDAH ADA
# ==========================================================

KODE_LITERASI = {
    "LT-KOSAKATA",
    "LT-LANCAR",
    "LT-KETEPATAN",
    "LT-KALIMAT",
}

KODE_NUMERASI = {
    "NM-TAMBAH",
    "NM-KURANG",
    "NM-KALI",
    "NM-BAGI",
}

KODE_GAME = KODE_LITERASI | KODE_NUMERASI

# Soal baca kalimat (mikrofon) hanya mengukur LT-KETEPATAN. Jawabannya tidak dicerminkan
# ke kompetensi lain, dan jawaban susun kalimat tidak dicerminkan ke LT-KETEPATAN.
KODE_BACA = "LT-KETEPATAN"
KODE_LITERASI_CERMIN = KODE_LITERASI - {KODE_BACA}

JUMLAH_SOAL_PER_LEVEL = 5


def jumlah_soal_game(kode, level):
    """
    Jumlah soal aktif di bank guru untuk kompetensi dan level tertentu
    (Game 1: seluruh soal literasi pada level itu; Game 2: soal kompetensi tersebut).
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        return 0

    if not 1 <= level <= 5 or kode not in KODE_GAME:
        return 0

    qs = SoalGame.objects.filter(level=level, aktif=True)

    if kode in KODE_LITERASI:
        qs = qs.filter(game="literasi")
    else:
        qs = qs.filter(game="numerasi", kompetensi__kode=kode)

    return qs.count()


def level_game_tersedia(
    kode,
    level,
    level_min=1,
    level_maks=5,
):
    """Memilih level terdekat yang punya soal di bank guru."""
    try:
        level = int(level)
        level_min = max(1, int(level_min))
        level_maks = min(5, int(level_maks))
    except (TypeError, ValueError):
        return None

    if level_min > level_maks:
        return None

    tersedia = [
        nomor
        for nomor in range(level_min, level_maks + 1)
        if jumlah_soal_game(kode, nomor) > 0
    ]

    if not tersedia:
        return None

    return min(
        tersedia,
        key=lambda nomor: (abs(nomor - level), nomor),
    )


def kode_game_tersedia(kode):
    """Memeriksa pemetaan kompetensi ke Game 1 atau Game 2."""
    return (
        kode in KODE_GAME
        and kode in getattr(settings, "LENTERA_GAMES", {})
    )


# ==========================================================
# 2. ADAPTIVE LEARNING ENGINE
# ==========================================================

def _langkah(p, benar):
    """Perbarui estimasi penguasaan setelah satu jawaban."""
    p.p_l = engine.update_bkt(p.p_l, benar)

    p.salah_beruntun = (
        0 if benar else p.salah_beruntun + 1
    )

    p.level, p.p_l, p.salah_beruntun = engine.atur_level(
        p.level,
        p.p_l,
        p.salah_beruntun,
    )


def hitung_ulang(siswa, kompetensi):
    """
    Hitung ulang BKT jika ada jawaban lama yang baru tersinkronisasi.
    Jawaban diagnostik tidak dicampur dengan latihan biasa.
    """
    penguasaan, _ = Penguasaan.objects.get_or_create(
        siswa=siswa,
        kompetensi=kompetensi,
        defaults={"p_l": engine.P_AWAL},
    )

    diagnostik = (
        HasilDiagnostik.objects.filter(
            siswa=siswa,
            kompetensi=kompetensi,
        )
        .order_by("dibuat", "id")
        .last()
    )

    if diagnostik:
        penguasaan.p_l = diagnostik.p_awal
        penguasaan.level = diagnostik.level_awal
        penguasaan.salah_beruntun = 0
    else:
        penguasaan.p_l = engine.P_AWAL
        penguasaan.level = 1
        penguasaan.salah_beruntun = 0

    jawaban = Jawaban.objects.filter(
        siswa=siswa,
        kompetensi=kompetensi,
    ).filter(
        Q(misi__isnull=True) | ~Q(misi__jenis="diagnostik")
    )

    if diagnostik:
        jawaban = jawaban.filter(
            dibuat__gte=diagnostik.dibuat
        )

    for item in jawaban.order_by("dibuat", "id"):
        _langkah(penguasaan, item.benar)

        Jawaban.objects.filter(id=item.id).update(
            p_setelah=penguasaan.p_l
        )

    penguasaan.save()

    return penguasaan


# ==========================================================
# 3. PENCATATAN DAN SINKRONISASI JAWABAN LITERASI
# ==========================================================

def cerminkan_jawaban_literasi(
    siswa,
    kompetensi_asal,
    benar,
    level,
    item_id,
    waktu_ms,
    petunjuk,
    waktu,
    event_id,
    misi=None,
):
    """
    Gunakan jawaban susun kalimat Game 1 untuk memperbarui kompetensi
    literasi lain yang terdaftar (kecuali LT-KETEPATAN, yang hanya
    diukur oleh soal baca kalimat lewat mikrofon).

    Ini memastikan indikator literasi menerima data ketika bank soal
    susun kalimat Game 1 yang digunakan.

    Catatan: hasil seluruh indikator berasal dari bukti jawaban
    yang sama. Ini adalah pencerminan data, bukan pengukuran
    kompetensi yang benar-benar terpisah.
    """
    if not misi or misi.kompetensi.ranah != "literasi":
        return

    # Bacaan lewat mikrofon hanya milik LT-KETEPATAN: tidak ada pencerminan.
    if kompetensi_asal.kode == KODE_BACA:
        return

    diagnostik = misi.jenis == "diagnostik"

    dasar_event = (
        event_id
        or (
            f"{siswa.pk}:{misi.pk}:{item_id}:"
            f"{waktu.isoformat()}"
        )
    )

    kompetensi_lain = (
        Kompetensi.objects
        .filter(kode__in=KODE_LITERASI_CERMIN)
        .exclude(pk=kompetensi_asal.pk)
    )

    for kompetensi in kompetensi_lain:
        # Hash unik per event dan kompetensi.
        mirror_event_id = hashlib.sha256(
            f"{dasar_event}|{kompetensi.kode}".encode("utf-8")
        ).hexdigest()

        # Cegah jawaban turunan tercatat dua kali.
        if Jawaban.objects.filter(
            event_id=mirror_event_id
        ).exists():
            continue

        penguasaan, _ = Penguasaan.objects.get_or_create(
            siswa=siswa,
            kompetensi=kompetensi,
            defaults={"p_l": engine.P_AWAL},
        )

        penguasaan = Penguasaan.objects.select_for_update().get(
            pk=penguasaan.pk
        )

        # Jangan mengaitkan latihan biasa ke misi dengan
        # kompetensi yang berbeda.
        jawaban_turunan = Jawaban.objects.create(
            siswa=siswa,
            kompetensi=kompetensi,
            misi=misi if diagnostik else None,
            level=level or penguasaan.level,
            item_id=str(item_id or "")[:40],
            benar=bool(benar),
            pola="",
            waktu_ms=max(0, int(waktu_ms or 0)),
            petunjuk=bool(petunjuk),
            event_id=mirror_event_id,
            dibuat=waktu,
        )

        # Jawaban diagnostik baru diterapkan saat diagnostik selesai.
        if diagnostik:
            continue

        ada_jawaban_lebih_baru = (
            Jawaban.objects
            .filter(
                siswa=siswa,
                kompetensi=kompetensi,
                dibuat__gt=waktu,
            )
            .filter(
                Q(misi__isnull=True)
                | ~Q(misi__jenis="diagnostik")
            )
            .exists()
        )

        if ada_jawaban_lebih_baru:
            penguasaan = hitung_ulang(
                siswa,
                kompetensi,
            )
        else:
            _langkah(penguasaan, bool(benar))

            jawaban_turunan.p_setelah = penguasaan.p_l
            jawaban_turunan.save(
                update_fields=["p_setelah"]
            )

            penguasaan.save()


@transaction.atomic
def catat_jawaban(
    siswa,
    kode,
    benar,
    level=0,
    item_id="",
    pola="",
    waktu_ms=0,
    petunjuk=False,
    waktu=None,
    event_id="",
    misi=None,
):
    """
    Satu-satunya pintu penyimpanan jawaban belajar.

    Dipanggil oleh API game dan perintah seed demo.
    event_id mencegah event yang sama dihitung dua kali.
    """
    if event_id and Jawaban.objects.filter(
        event_id=event_id
    ).exists():
        return None

    kompetensi = Kompetensi.objects.get(kode=kode)

    pola_valid = ""

    if not benar and pola:
        definisi = engine.POLA.get(pola)

        if definisi and definisi[1] == kode:
            pola_valid = pola

    penguasaan, _ = Penguasaan.objects.get_or_create(
        siswa=siswa,
        kompetensi=kompetensi,
        defaults={"p_l": engine.P_AWAL},
    )

    penguasaan = Penguasaan.objects.select_for_update().get(
        id=penguasaan.id
    )

    waktu = waktu or timezone.now()

    diagnostik = bool(
        misi and misi.jenis == "diagnostik"
    )

    terlambat = (
        not diagnostik
        and Jawaban.objects.filter(
            siswa=siswa,
            kompetensi=kompetensi,
            dibuat__gt=waktu,
        )
        .filter(
            Q(misi__isnull=True) | ~Q(misi__jenis="diagnostik")
        )
        .exists()
    )

    jawaban = Jawaban.objects.create(
        siswa=siswa,
        kompetensi=kompetensi,
        misi=misi,
        level=level or penguasaan.level,
        item_id=str(item_id or "")[:40],
        benar=bool(benar),
        pola=pola_valid,
        waktu_ms=max(0, int(waktu_ms or 0)),
        petunjuk=bool(petunjuk),
        event_id=(event_id or None),
        dibuat=waktu,
    )

    # ------------------------------------------------------
    # INI BAGIAN PENTING YANG SEBELUMNYA BELUM DIPANGGIL
    # ------------------------------------------------------
    # Satu hasil Game 1 ikut memperbarui seluruh kompetensi
    # literasi. Game 2 tidak terpengaruh.
    cerminkan_jawaban_literasi(
        siswa=siswa,
        kompetensi_asal=kompetensi,
        benar=bool(benar),
        level=level or penguasaan.level,
        item_id=item_id,
        waktu_ms=waktu_ms,
        petunjuk=petunjuk,
        waktu=waktu,
        event_id=event_id,
        misi=misi,
    )

    # Jawaban diagnostik diterapkan setelah sesi selesai.
    if diagnostik:
        return {
            "p_l": round(penguasaan.p_l, 3),
            "status": penguasaan.status,
            "level": penguasaan.level,
        }

    if terlambat:
        penguasaan = hitung_ulang(
            siswa,
            kompetensi,
        )
    else:
        _langkah(penguasaan, bool(benar))

        jawaban.p_setelah = penguasaan.p_l
        jawaban.save(update_fields=["p_setelah"])

        penguasaan.save()

    if benar:
        Siswa.objects.filter(id=siswa.id).update(
            poin=F("poin") + 1
        )

    return {
        "p_l": round(penguasaan.p_l, 3),
        "status": penguasaan.status,
        "level": penguasaan.level,
    }


# ==========================================================
# 4. PENGUASAAN SISWA
# ==========================================================

def penguasaan_lengkap(siswa):
    """Pastikan setiap kompetensi mempunyai state penguasaan."""
    for kompetensi in Kompetensi.objects.all():
        Penguasaan.objects.get_or_create(
            siswa=siswa,
            kompetensi=kompetensi,
            defaults={"p_l": engine.P_AWAL},
        )

    return list(
        siswa.penguasaan.select_related("kompetensi")
    )


# ==========================================================
# 5. MISI SISWA
# ==========================================================

def misi_aktif(siswa):
    """
    Misi yang ditampilkan di beranda murid: satu per kategori
    (Literasi = Game 1, Numerasi = Game 2), mengikuti Jalan Lentera.
    Lihat jalur.misi_aktif.
    """
    return jalur.misi_aktif(siswa)


# ==========================================================
# 6. ASESMEN DIAGNOSTIK
# ==========================================================

@transaction.atomic
def terapkan_diagnostik(
    siswa,
    kompetensi,
    benar,
    total,
    sumber="manual",
    level_tangga=None,
):
    """
    Terapkan hasil diagnostik menjadi state awal siswa.

    `level_tangga`: isi dengan nomor level kalau hasil ini datang dari diagnostik
    BERTANGGA (lihat selesaikan_diagnostik) -- `benar`/`total` lalu dibaca sebagai
    skor PADA LEVEL ITU dan dikonversi lewat engine.level_dari_tangga(), supaya murid
    yang sudah naik lewat beberapa level dulu mendapat P(L) yang mencerminkan itu,
    bukan cuma skor level tempat ia berhenti. Kosongkan (default) untuk input manual
    guru, yang tetap memakai engine.level_dari_skor() seperti sebelumnya.
    """
    benar = int(benar)
    total = int(total)

    if total <= 0:
        raise ValueError(
            "Jumlah soal diagnostik harus lebih dari nol."
        )

    if benar < 0 or benar > total:
        raise ValueError(
            "Jumlah benar harus berada antara nol dan total."
        )

    if benar:
        Siswa.objects.filter(id=siswa.id).update(
            poin=F("poin") + 1
        )

    if level_tangga is not None:
        level_awal, p_awal = engine.level_dari_tangga(
            level_tangga,
            benar,
            total,
        )
    else:
        level_awal, p_awal = engine.level_dari_skor(
            benar,
            total,
        )

    hasil = HasilDiagnostik.objects.create(
        siswa=siswa,
        kompetensi=kompetensi,
        benar=benar,
        total=total,
        level_awal=level_awal,
        p_awal=p_awal,
        sumber=sumber,
    )

    penguasaan, _ = Penguasaan.objects.get_or_create(
        siswa=siswa,
        kompetensi=kompetensi,
        defaults={"p_l": p_awal},
    )

    penguasaan.p_l = p_awal
    penguasaan.level = level_awal
    penguasaan.salah_beruntun = 0
    penguasaan.save()

    return hasil


@transaction.atomic
def aktifkan_diagnostik(kelas):
    """Buat misi diagnostik (level 1) untuk kompetensi yang soalnya sudah ada di bank."""
    jumlah = 0

    game_codes = {
        kode
        for kode in getattr(settings, "LENTERA_GAMES", {})
        if kode in KODE_GAME
    }

    kompetensi_tersedia = []

    for kompetensi in Kompetensi.objects.filter(kode__in=game_codes):
        game = jalur.game_dari_kompetensi(kompetensi)
        bagian = 1 if game == "literasi" else jalur.bagian_kompetensi(kompetensi.kode)
        ada_soal = SoalGame.objects.filter(
            game=game,
            bagian=bagian,
            level=1,
            aktif=True,
            **({"kompetensi": kompetensi} if game == "numerasi" else {}),
        ).exists()

        if ada_soal:
            kompetensi_tersedia.append((kompetensi, bagian))

    for siswa in kelas.siswa.all():
        for kompetensi, bagian in kompetensi_tersedia:
            sudah_selesai = HasilDiagnostik.objects.filter(
                siswa=siswa,
                kompetensi=kompetensi,
            ).exists()

            masih_aktif = Misi.objects.filter(
                siswa=siswa,
                kompetensi=kompetensi,
                jenis="diagnostik",
                status="baru",
            ).exists()

            if sudah_selesai or masih_aktif:
                continue

            Misi.objects.create(
                siswa=siswa,
                kompetensi=kompetensi,
                bagian=bagian,
                level=1,
                jenis="diagnostik",
                dari_guru=True,
            )

            jumlah += 1

    return jumlah


@transaction.atomic
def selesaikan_diagnostik(misi):
    """
    Hitung hasil diagnostik dari jawaban unik per soal (percobaan pertama tiap soal).

    Diagnostik naik tangga: kalau level ini TUNTAS SEMPURNA (seluruh percobaan
    pertama benar) dan belum level maksimal, langsung kirim misi diagnostik level
    berikutnya dan JANGAN tetapkan hasil akhir dulu -- murid lanjut naik. Hasil akhir
    baru ditetapkan (lewat terapkan_diagnostik) begitu ada jawaban pertama yang salah
    di suatu level, atau level maksimal sudah dituntaskan juga.
    """
    jawaban_pertama = {}

    for jawaban in (
        Jawaban.objects.filter(misi=misi)
        .select_related("kompetensi")
        .order_by("dibuat", "id")
    ):
        if not jawaban.item_id:
            continue

        key = (
            jawaban.kompetensi_id,
            jawaban.item_id,
        )

        jawaban_pertama.setdefault(key, jawaban)

    per_kompetensi = {}

    for (kompetensi_id, _item_id), jawaban in (
        jawaban_pertama.items()
    ):
        statistik = per_kompetensi.setdefault(
            kompetensi_id,
            {
                "kompetensi": jawaban.kompetensi,
                "benar": 0,
                "total": 0,
            },
        )

        statistik["total"] += 1

        if jawaban.benar:
            statistik["benar"] += 1

    hasil = []

    for statistik in per_kompetensi.values():
        kompetensi = statistik["kompetensi"]
        benar, total = statistik["benar"], statistik["total"]
        sempurna = total > 0 and benar == total

        # Satu misi literasi bisa memuat beberapa kompetensi sub-skill sekaligus
        # (kosakata, kelancaran, kalimat, dst). Kalau salah satunya sudah lebih dulu
        # punya hasil (lewat misi lain yang kebetulan menyentuh soal kompetensi yang
        # sama), jangan tetapkan ulang atau naikkan lagi -- cukup lewati.
        if HasilDiagnostik.objects.filter(siswa=misi.siswa, kompetensi=kompetensi).exists():
            continue

        if sempurna and misi.level < engine.LEVEL_MAKS:
            # Sempurna dan belum level maksimal -> lanjut, jangan tetapkan hasil akhir.
            # Kalau kompetensi ini kebetulan sudah punya misi diagnostik 'baru' lain
            # (dari aktifkan_diagnostik, atau dari kompetensi lain yang tumpang tindih),
            # tidak perlu bikin duplikat -- biarkan yang sudah ada dipakai.
            sudah_ada = Misi.objects.filter(
                siswa=misi.siswa, kompetensi=kompetensi, jenis="diagnostik", status="baru",
            ).exclude(id=misi.id).exists()
            if not sudah_ada:
                Misi.objects.create(
                    siswa=misi.siswa,
                    kompetensi=kompetensi,
                    bagian=misi.bagian,
                    level=misi.level + 1,
                    jenis="diagnostik",
                    dari_guru=True,
                )
            hasil.append({
                "kompetensi": kompetensi,
                "lanjut": True,
                "level_berikutnya": misi.level + 1,
            })
            continue

        hasil.append(
            terapkan_diagnostik(
                siswa=misi.siswa,
                kompetensi=kompetensi,
                benar=benar,
                total=total,
                sumber="game",
                level_tangga=misi.level,
            )
        )

        # Hasil sudah ditetapkan -- misi diagnostik 'baru' lain utk kompetensi yang
        # sama (kalau ada, dari tumpang tindih di atas) jadi tidak relevan lagi.
        Misi.objects.filter(
            siswa=misi.siswa, kompetensi=kompetensi, jenis="diagnostik", status="baru",
        ).exclude(id=misi.id).delete()

    return hasil


def status_diagnostik(kelas):
    """Siapkan status asesmen diagnostik setiap siswa."""
    kompetensi = list(Kompetensi.objects.all())
    siswa = list(kelas.siswa.select_related("user"))

    hasil_db = (
        HasilDiagnostik.objects.filter(siswa__in=siswa)
        .order_by("dibuat", "id")
    )

    hasil = {}

    for item in hasil_db:
        hasil[(item.siswa_id, item.kompetensi_id)] = item

    menunggu = set(
        Misi.objects.filter(
            siswa__in=siswa,
            jenis="diagnostik",
            status="baru",
        ).values_list(
            "siswa_id",
            "kompetensi_id",
        )
    )

    baris = []

    for murid in siswa:
        pilihan = []

        for kompetensi_item in kompetensi:
            key = (
                murid.id,
                kompetensi_item.id,
            )

            pilihan.append({
                "h": hasil.get(key),
                "tunggu": key in menunggu,
            })

        baris.append({
            "siswa": murid,
            "sel": pilihan,
        })

    jumlah_selesai = sum(
        1
        for baris_item in baris
        for pilihan in baris_item["sel"]
        if pilihan["h"]
    )

    return {
        "kompetensi": kompetensi,
        "baris": baris,
        "selesai": jumlah_selesai,
        "total": len(siswa) * len(kompetensi),
        "main": sorted(
            kode
            for kode in settings.LENTERA_GAMES
            if kode in KODE_GAME
        ),
    }


# ==========================================================
# 7. BANK AKTIVITAS DAN PENUGASAN
# ==========================================================

def _utamakan_misi(misi):
    """Jadikan `misi` satu-satunya tugas guru yang berlaku untuk kategorinya."""
    Misi.objects.filter(
        siswa=misi.siswa,
        jenis="misi",
        status="baru",
        dari_guru=True,
        kompetensi__ranah=misi.kompetensi.ranah,
    ).exclude(pk=misi.pk).update(dari_guru=False)


@transaction.atomic
def tugaskan_aktivitas(
    guru,
    kelas,
    aktivitas,
    target,
    ids=(),
):
    """Tugaskan aktivitas ke kelas, kelompok, atau siswa tertentu.

    Murid mendapat level berikutnya (di rentang level aktivitas) yang belum
    diselesaikan; bila semuanya sudah selesai, level pertama dalam rentang
    diulang sebagai latihan. Batas waktu mengikuti menit aktivitas ini.
    """
    siswa_kelas = list(kelas.siswa.all())

    kode = aktivitas.kompetensi.kode
    game = jalur.game_dari_kompetensi(aktivitas.kompetensi)
    level_min = max(1, int(aktivitas.level_min))
    level_maks = min(5, int(aktivitas.level_maks))

    dapat_dimainkan = (
        kode_game_tersedia(kode)
        and jalur.target_ulang(game, aktivitas.kompetensi, level_min, level_maks) is not None
    )

    penguasaan = {
        (p.siswa_id, p.kompetensi_id): p
        for p in Penguasaan.objects.filter(
            kompetensi=aktivitas.kompetensi,
            siswa__in=siswa_kelas,
        )
    }

    # Kelompok memakai persen Peta Kompetensi yang sama dengan yang dilihat guru.
    peta = jalur.hitung_peta(
        [s.id for s in siswa_kelas],
        [aktivitas.kompetensi],
        penguasaan,
        kelas.target_bagian,
    )

    def status_siswa(siswa):
        return peta[(siswa.id, aktivitas.kompetensi_id)]["st"]

    if target in ("Mulai", "Berkembang", "Cakap"):
        dipilih = [
            siswa
            for siswa in siswa_kelas
            if status_siswa(siswa) == target
        ]

        label = f"Kelompok {target}"

    elif target == "pilih":
        id_terpilih = {str(item) for item in ids}

        dipilih = [
            siswa
            for siswa in siswa_kelas
            if str(siswa.id) in id_terpilih
        ]

        label = "Siswa terpilih"

    else:
        dipilih = siswa_kelas
        label = "Seluruh kelas"

    jumlah_dibuat = 0

    if dapat_dimainkan:
        selesai = jalur.level_selesai([siswa.id for siswa in dipilih])

        for siswa in dipilih:
            tujuan = (
                jalur.target_siswa(
                    siswa,
                    game,
                    selesai.get((siswa.id, game), set()),
                    kompetensi=aktivitas.kompetensi,
                    level_min=level_min,
                    level_maks=level_maks,
                )
                or jalur.target_ulang(game, aktivitas.kompetensi, level_min, level_maks)
            )

            if tujuan is None:
                continue

            bagian, level, _ = tujuan

            misi = jalur._misi_baru(
                siswa,
                bagian,
                level,
                aktivitas.kompetensi,
                dari_guru=True,
                aktivitas=aktivitas,
            )
            _utamakan_misi(misi)

            jumlah_dibuat += 1

    Penugasan.objects.create(
        aktivitas=aktivitas,
        kelas=kelas,
        target=label,
        jumlah=jumlah_dibuat,
        dibuat_oleh=guru,
    )

    return jumlah_dibuat, dapat_dimainkan


@transaction.atomic
def terapkan_ke_kelompok(guru, kelas, kompetensi):
    """Kirim level berikutnya di Jalan Lentera untuk kompetensi ini ke seluruh murid kelas."""
    kode = kompetensi.kode

    if not kode_game_tersedia(kode):
        return 0

    game = jalur.game_dari_kompetensi(kompetensi)
    siswa_kelas = list(kelas.siswa.all())
    selesai = jalur.level_selesai([siswa.id for siswa in siswa_kelas])

    jumlah_misi = 0

    for siswa in siswa_kelas:
        tujuan = (
            jalur.target_siswa(
                siswa,
                game,
                selesai.get((siswa.id, game), set()),
                kompetensi=kompetensi,
            )
            or jalur.target_ulang(game, kompetensi)
        )

        if tujuan is None:
            continue

        misi = jalur._misi_baru(
            siswa,
            tujuan[0],
            tujuan[1],
            kompetensi,
            dari_guru=True,
        )
        _utamakan_misi(misi)

        jumlah_misi += 1

    return jumlah_misi


# ==========================================================
# 8. KELAS DAN SISWA
# ==========================================================

def kode_kelas_baru(nama):
    while True:
        kode = (
            f"{slugify(nama).upper()[:8] or 'KELAS'}-"
            f"{secrets.token_hex(2).upper()}"
        )

        if not Kelas.objects.filter(kode=kode).exists():
            return kode


def buat_kelas(guru, nama):
    return Kelas.objects.create(
        nama=nama,
        kode=kode_kelas_baru(nama),
        wali_kelas=guru,
    )


@transaction.atomic
def tambah_siswa(kelas, nama):
    """Buat akun murid tanpa kata sandi dan ID samaran unik."""
    dasar = slugify(nama)[:16] or "murid"
    nomor = 0

    while True:
        username = (
            f"{dasar}.{kelas.id}"
            + (str(nomor) if nomor else "")
        )

        if not User.objects.filter(username=username).exists():
            break

        nomor += 1

    user = User(
        username=username,
        first_name=nama[:30],
    )

    user.set_unusable_password()
    user.save()

    Profile.objects.create(
        user=user,
        role="murid",
    )

    while True:
        kode_samaran = f"S-{secrets.token_hex(3).upper()}"

        if not Siswa.objects.filter(
            kode_samaran=kode_samaran
        ).exists():
            break

    avatar_terpakai = set(
        kelas.siswa.values_list(
            "avatar",
            flat=True,
        )
    )

    avatar = next(
        (
            item
            for item in AVATAR
            if item not in avatar_terpakai
        ),
        AVATAR[kelas.siswa.count() % len(AVATAR)],
    )

    siswa = Siswa.objects.create(
        user=user,
        kelas=kelas,
        avatar=avatar,
        kode_samaran=kode_samaran,
    )

    penguasaan_lengkap(siswa)

    return siswa


def tetapkan_wali_kelas(guru, kelas):
    """Jadikan guru sebagai wali kelas; satu guru satu kelas."""
    Kelas.objects.filter(
        wali_kelas=guru
    ).exclude(
        id=kelas.id
    ).update(
        wali_kelas=None
    )

    kelas.wali_kelas = guru
    kelas.save(update_fields=["wali_kelas"])


def buat_guru(username, nama, sandi, kelas=None):
    """Buat akun guru baru dari menu Admin."""
    username = (username or "").strip()

    if not username:
        raise ValueError("Username wajib diisi.")

    if len(sandi or "") < 6:
        raise ValueError("Kata sandi minimal 6 karakter.")

    if User.objects.filter(username=username).exists():
        raise ValueError("Username itu sudah dipakai.")

    user = User.objects.create_user(
        username,
        password=sandi,
        first_name=(nama or "").strip()[:30],
    )

    Profile.objects.create(
        user=user,
        role="guru",
    )

    if kelas is not None:
        tetapkan_wali_kelas(user, kelas)

    return user


# ==========================================================
# 9. RINGKASAN TEACHER INSIGHT
# ==========================================================

def _rel(waktu):
    """Format waktu relatif untuk dashboard guru."""
    if not waktu:
        return ""

    menit = int(
        (timezone.now() - waktu).total_seconds() // 60
    )

    if menit < 5:
        return "baru saja"

    if menit < 60:
        return f"{menit} mnt lalu"

    if menit < 1440:
        return f"{menit // 60} jam lalu"

    return f"{menit // 1440} hari lalu"


def ringkasan_kelas(kelas):
    """
    Menyediakan data untuk Insight Guru, Peta Kompetensi,
    rekomendasi tindak lanjut, dan laporan kelas.
    """
    awal = timezone.now() - timedelta(days=7)

    kompetensi = list(Kompetensi.objects.all())
    siswa = list(kelas.siswa.select_related("user"))

    penguasaan = {
        (p.siswa_id, p.kompetensi_id): p
        for p in Penguasaan.objects.filter(siswa__in=siswa)
    }

    skor_sebelum = {}

    riwayat_sebelum = (
        Jawaban.objects
        .filter(
            siswa__in=siswa,
            dibuat__lt=awal,
            p_setelah__isnull=False,
        )
        .order_by("dibuat", "id")
        .values_list(
            "siswa_id",
            "kompetensi_id",
            "p_setelah",
        )
    )

    for siswa_id, kompetensi_id, nilai in riwayat_sebelum:
        skor_sebelum[(siswa_id, kompetensi_id)] = nilai

    # Persen Peta Kompetensi dicicil mengikuti kemajuan Jalan Lentera
    # (lihat jalur.persen_peta); 100% baru tercapai pada target bagian kelas.
    id_siswa = [murid.id for murid in siswa]

    peta = jalur.hitung_peta(
        id_siswa,
        kompetensi,
        penguasaan,
        kelas.target_bagian,
    )

    peta_lalu = jalur.hitung_peta(
        id_siswa,
        kompetensi,
        penguasaan,
        kelas.target_bagian,
        sebelum=awal,
        p_l_sebelum=skor_sebelum,
    )

    jawaban_7hari = Jawaban.objects.filter(
        siswa__in=siswa,
        dibuat__gte=awal,
    )

    statistik_siswa = {
        item["siswa_id"]: item
        for item in (
            jawaban_7hari
            .values("siswa_id")
            .annotate(
                jumlah=Count("id"),
                benar=Count(
                    "id",
                    filter=Q(benar=True),
                ),
            )
        )
    }

    aktivitas_terakhir = dict(
        Jawaban.objects
        .filter(siswa__in=siswa)
        .values_list("siswa_id")
        .annotate(waktu_terakhir=Max("dibuat"))
    )

    pola_siswa = {}

    pola_per_siswa = (
        jawaban_7hari
        .filter(benar=False)
        .exclude(pola="")
        .values("siswa_id", "pola")
        .annotate(jumlah=Count("id"))
        .order_by("siswa_id", "-jumlah", "pola")
    )

    for item in pola_per_siswa:
        pola_siswa.setdefault(
            item["siswa_id"],
            item["pola"],
        )

    baris = []

    for murid in siswa:
        sel = []

        for k in kompetensi:
            info = peta[(murid.id, k.id)]

            sel.append({
                "pct": info["pct"],
                "st": info["st"],
                "level": info["level"],
                "level_angka": info["level"] or 5,
                "bagian": info["bagian"],
                "penuh": info["penuh"],
                "selesai": info["selesai"],
            })

        statistik = statistik_siswa.get(
            murid.id,
            {"jumlah": 0, "benar": 0},
        )

        jumlah = statistik["jumlah"]
        benar = statistik["benar"]
        pola = pola_siswa.get(murid.id, "")

        baris.append({
            "siswa": murid,
            "sel": sel,
            "perhatian": (
                # "Mulai" cuma dipakai jalur.hitung_peta() untuk kompetensi yang SUDAH ada
                # buktinya (pernah dijawab atau didiagnostik) tapi masih di bawah 40%. Yang
                # belum ada bukti sama sekali berstatus "Belum mulai", jadi murid yang
                # memang belum mulai tidak ikut kehitung di sini.
                sum(
                    item["st"] == "Mulai"
                    for item in sel
                ) >= kelas.ambang_perhatian
            ),
            "n_jawab": jumlah,
            "akurasi": (
                round(100 * benar / jumlah)
                if jumlah else ""
            ),
            "terakhir": _rel(
                aktivitas_terakhir.get(murid.id)
            ),
            "pola": (
                engine.POLA[pola][0]
                if pola in engine.POLA
                else ""
            ),
            "pola_saran": (
                engine.POLA[pola][2]
                if pola in engine.POLA
                else ""
            ),
        })

    def rata_ranah(data, nama_ranah):
        nilai = [
            data[(murid.id, k.id)]["pct"]
            for murid in siswa
            for k in kompetensi
            if k.ranah == nama_ranah
        ]

        return sum(nilai) / len(nilai) if nilai else 0

    ranah = []

    for nama_ranah in ("literasi", "numerasi"):
        sekarang = rata_ranah(peta, nama_ranah)
        sebelumnya = rata_ranah(peta_lalu, nama_ranah)

        ranah.append({
            "kode": nama_ranah,
            "nama": nama_ranah.capitalize(),
            "nilai": round(sekarang),
            "delta": abs(round(sekarang - sebelumnya)),
            "naik": sekarang >= sebelumnya,
        })

    total_waktu = jawaban_7hari.aggregate(
        total=Sum("waktu_ms")
    )["total"] or 0

    waktu = round(
        total_waktu / 60000 / max(len(siswa), 1)
    )

    kesulitan = (
        jawaban_7hari
        .filter(benar=False)
        .exclude(pola="")
        .values("pola")
        .annotate(
            jumlah_siswa=Count("siswa", distinct=True)
        )
        .order_by("-jumlah_siswa", "pola")[:3]
    )

    daftar_kesulitan = [
        {
            "label": engine.POLA[item["pola"]][0],
            "n": item["jumlah_siswa"],
            "saran": engine.POLA[item["pola"]][2],
        }
        for item in kesulitan
        if item["pola"] in engine.POLA
    ]

    rekomendasi = None
    indeks_terendah = None

    if siswa and kompetensi:
        # Rata-rata cuma dihitung dari murid yang sudah ada buktinya (bukan "Belum mulai"),
        # supaya kompetensi yang memang belum disentuh siapa pun (baru ditambah, atau kelas
        # baru direset) tidak otomatis kelihatan "paling lemah" cuma karena belum dicoba.
        rata_kompetensi = []
        for i in range(len(kompetensi)):
            nilai = [
                data["sel"][i]["pct"]
                for data in baris
                if data["sel"][i]["st"] != "Belum mulai"
            ]
            rata_kompetensi.append(sum(nilai) / len(nilai) if nilai else None)

        kandidat = [i for i, r in enumerate(rata_kompetensi) if r is not None]

        indeks_terendah = (
            min(kandidat, key=lambda i: rata_kompetensi[i])
            if kandidat else None
        )

    if indeks_terendah is not None:
        k = kompetensi[indeks_terendah]
        kelompok = []

        for status in ("Mulai", "Berkembang", "Cakap"):
            anggota = [
                data
                for data in baris
                if data["sel"][indeks_terendah]["st"] == status
            ]

            level = (
                round(
                    sum(
                        data["sel"][indeks_terendah]["level_angka"]
                        for data in anggota
                    ) / len(anggota)
                )
                if anggota else 1
            )

            aktivitas = list(
                Aktivitas.objects.filter(
                    kompetensi=k,
                    tervalidasi=True,
                    level_min__lte=level,
                    level_maks__gte=level,
                )[:2]
            )

            kelompok.append({
                "status": status,
                "n": len(anggota),
                "teks": engine.TINDAK[k.ranah][status],
                "aktivitas": aktivitas,
            })

        rekomendasi = {
            "kompetensi": k,
            "kelompok": kelompok,
        }

    return {
        "kelas": kelas,
        "kompetensi": kompetensi,
        "baris": baris,
        "n": len(siswa),
        "target_bagian": kelas.target_bagian,
        "ranah": ranah,
        "waktu": waktu,
        "rek": rekomendasi,
        "perhatian": sum(
            item["perhatian"]
            for item in baris
        ),
        "kesulitan": daftar_kesulitan,
    }


# ==========================================================
# 10. RESET & ARSIP PROGRES SISWA
# ==========================================================
#
# Tombol "Reset" guru (menu Pengaturan) mengosongkan progres belajar satu siswa --
# Jalan Lentera, Peta Kompetensi, dan statistiknya kembali seperti murid baru --
# tanpa menyentuh akun/login/avatar/ID samaran siswa itu. Datanya tidak langsung
# hilang: dicadangkan dulu ke ArsipReset, lalu bisa dipulihkan dari Log Reset
# selama RESET_RETENSI_HARI hari dan selama siswa belum mulai progres baru.

RESET_RETENSI_HARI = 30


def _dt(nilai):
    """Parse balik string ISO (hasil DjangoJSONEncoder) jadi datetime; None bila kosong."""
    if not nilai:
        return None
    if isinstance(nilai, timezone.datetime):
        return nilai
    return parse_datetime(nilai)


def ada_progres(siswa):
    """True bila siswa punya sesuatu untuk direset (pernah main, atau punya poin)."""
    return (
        siswa.poin > 0
        or Misi.objects.filter(siswa=siswa).exists()
        or Jawaban.objects.filter(siswa=siswa).exists()
        or Penguasaan.objects.filter(siswa=siswa).exists()
        or HasilDiagnostik.objects.filter(siswa=siswa).exists()
    )


def _snapshot_progres(siswa):
    """Salin seluruh baris progres siswa (sebelum dihapus) jadi dict yang aman disimpan di JSONField."""
    return {
        "misi": list(Misi.objects.filter(siswa=siswa).values(
            "id", "kompetensi_id", "bagian", "level", "status", "jenis", "dari_guru",
            "aktivitas_id", "soal_ids", "skor", "durasi_detik", "mulai_pada", "selesai_pada", "dibuat",
        )),
        "jawaban": list(Jawaban.objects.filter(siswa=siswa).values(
            "kompetensi_id", "misi_id", "level", "item_id", "benar", "pola",
            "waktu_ms", "petunjuk", "p_setelah", "event_id", "dibuat",
        )),
        "penguasaan": list(Penguasaan.objects.filter(siswa=siswa).values(
            "kompetensi_id", "p_l", "level", "salah_beruntun",
        )),
        "diagnostik": list(HasilDiagnostik.objects.filter(siswa=siswa).values(
            "kompetensi_id", "benar", "total", "level_awal", "p_awal", "sumber", "dibuat",
        )),
    }


def _ringkasan_snapshot(snap, poin_lama):
    bagian = [f"{len(snap['misi'])} misi", f"{len(snap['jawaban'])} jawaban"]
    if snap["penguasaan"]:
        bagian.append(f"{len(snap['penguasaan'])} penguasaan")
    if snap["diagnostik"]:
        bagian.append(f"{len(snap['diagnostik'])} diagnostik")
    if poin_lama:
        bagian.append(f"{poin_lama} poin")
    return " · ".join(bagian)


@transaction.atomic
def reset_progres_siswa(siswa, oleh=None, retensi_hari=RESET_RETENSI_HARI):
    """
    Cadangkan lalu kosongkan seluruh progres belajar satu siswa (Jalan Lentera, Peta
    Kompetensi, dan statistiknya). Akun, login, avatar, dan ID samaran siswa TIDAK
    disentuh. Mengembalikan ArsipReset yang dibuat, atau None bila siswa memang
    belum punya progres untuk direset (supaya Log Reset tidak kebanjiran arsip kosong).
    """
    siswa = Siswa.objects.select_for_update().get(pk=siswa.pk)

    if not ada_progres(siswa):
        return None

    snap = _snapshot_progres(siswa)
    poin_lama = siswa.poin

    arsip = ArsipReset.objects.create(
        siswa=siswa,
        oleh=oleh,
        poin_lama=poin_lama,
        ringkasan=_ringkasan_snapshot(snap, poin_lama),
        data=snap,
        kedaluwarsa_pada=timezone.now() + timedelta(days=retensi_hari),
    )

    Jawaban.objects.filter(siswa=siswa).delete()
    Misi.objects.filter(siswa=siswa).delete()
    Penguasaan.objects.filter(siswa=siswa).delete()
    HasilDiagnostik.objects.filter(siswa=siswa).delete()
    Siswa.objects.filter(pk=siswa.pk).update(poin=0)

    return arsip


def reset_progres_kelas(kelas, oleh=None, retensi_hari=RESET_RETENSI_HARI):
    """Reset semua siswa di satu kelas sekaligus. Mengembalikan jumlah siswa yang benar-benar dicadangkan."""
    return sum(
        1
        for s in kelas.siswa.all()
        if reset_progres_siswa(s, oleh=oleh, retensi_hari=retensi_hari)
    )


def bersihkan_arsip_kedaluwarsa(kelas=None):
    """Hapus permanen arsip yang sudah lewat kedaluwarsa dan belum dipulihkan (dipanggil tiap Log Reset dibuka)."""
    qs = ArsipReset.objects.filter(dipulihkan_pada__isnull=True, kedaluwarsa_pada__lt=timezone.now())
    if kelas is not None:
        qs = qs.filter(siswa__kelas=kelas)
    n = qs.count()
    if n:
        qs.delete()
    return n


def arsip_bisa_dipulihkan(arsip):
    return (
        arsip.dipulihkan_pada is None
        and timezone.now() < arsip.kedaluwarsa_pada
        and not ada_progres(arsip.siswa)
    )


@transaction.atomic
def pulihkan_arsip(arsip):
    """
    Pulihkan satu ArsipReset: tulis ulang misi/jawaban/penguasaan/diagnostik dan poin
    persis seperti sebelum direset. Ditolak bila siswa sudah mulai progres baru sejak
    direset, supaya progres barunya tidak tertimpa data lama.
    """
    arsip = ArsipReset.objects.select_for_update().select_related("siswa").get(pk=arsip.pk)

    if arsip.dipulihkan_pada is not None:
        raise ValueError("Arsip ini sudah pernah dipulihkan sebelumnya.")
    if timezone.now() >= arsip.kedaluwarsa_pada:
        raise ValueError("Arsip ini sudah kedaluwarsa dan tidak bisa dipulihkan lagi.")

    siswa = Siswa.objects.select_for_update().get(pk=arsip.siswa_id)
    if ada_progres(siswa):
        raise ValueError(
            "Siswa ini sudah mulai main lagi sejak direset, jadi datanya tidak bisa "
            "dipulihkan otomatis (supaya progres barunya tidak tertimpa)."
        )

    snap = arsip.data
    kompetensi_valid = set(Kompetensi.objects.values_list("id", flat=True))

    id_misi_baru = {}
    for m in snap.get("misi", []):
        if m["kompetensi_id"] not in kompetensi_valid:
            continue
        aktivitas = Aktivitas.objects.filter(pk=m["aktivitas_id"]).first() if m.get("aktivitas_id") else None
        id_misi_baru[m["id"]] = Misi.objects.create(
            siswa=siswa, kompetensi_id=m["kompetensi_id"], bagian=m["bagian"], level=m["level"],
            status=m["status"], jenis=m["jenis"], dari_guru=m["dari_guru"], aktivitas=aktivitas,
            soal_ids=m.get("soal_ids") or [], skor=m.get("skor"), durasi_detik=m.get("durasi_detik"),
            mulai_pada=_dt(m.get("mulai_pada")), selesai_pada=_dt(m.get("selesai_pada")),
            dibuat=_dt(m.get("dibuat")) or timezone.now(),
        )

    for j in snap.get("jawaban", []):
        if j["kompetensi_id"] not in kompetensi_valid:
            continue
        Jawaban.objects.create(
            siswa=siswa, kompetensi_id=j["kompetensi_id"], misi=id_misi_baru.get(j.get("misi_id")),
            level=j["level"], item_id=j.get("item_id", ""), benar=j["benar"], pola=j.get("pola", ""),
            waktu_ms=j.get("waktu_ms", 0), petunjuk=j.get("petunjuk", False), p_setelah=j.get("p_setelah"),
            event_id=j.get("event_id"), dibuat=_dt(j.get("dibuat")) or timezone.now(),
        )

    for p in snap.get("penguasaan", []):
        if p["kompetensi_id"] not in kompetensi_valid:
            continue
        Penguasaan.objects.update_or_create(
            siswa=siswa, kompetensi_id=p["kompetensi_id"],
            defaults={"p_l": p["p_l"], "level": p["level"], "salah_beruntun": p["salah_beruntun"]},
        )

    for d in snap.get("diagnostik", []):
        if d["kompetensi_id"] not in kompetensi_valid:
            continue
        HasilDiagnostik.objects.create(
            siswa=siswa, kompetensi_id=d["kompetensi_id"], benar=d["benar"], total=d["total"],
            level_awal=d["level_awal"], p_awal=d["p_awal"], sumber=d.get("sumber", "manual"),
            dibuat=_dt(d.get("dibuat")) or timezone.now(),
        )

    Siswa.objects.filter(pk=siswa.pk).update(poin=arsip.poin_lama)
    arsip.dipulihkan_pada = timezone.now()
    arsip.save(update_fields=["dipulihkan_pada"])

    return arsip

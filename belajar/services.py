
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
"""

import secrets
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Profile

from . import engine
from .models import (
    Aktivitas,
    HasilDiagnostik,
    Jawaban,
    Kelas,
    Kompetensi,
    Misi,
    Penguasaan,
    Penugasan,
    Siswa,
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
    "LT-PAHAM",
    "LT-KALIMAT",
}

KODE_NUMERASI = {
    "NM-CERITA",
    "NM-NILAI-TEMPAT",
    "NM-REGROUPING",
    "NM-POLA",
}

KODE_GAME = KODE_LITERASI | KODE_NUMERASI

JUMLAH_SOAL_PER_LEVEL = 5


def jumlah_soal_game(kode, level):
    """
    Semua kompetensi literasi memakai Game 1.
    Semua kompetensi numerasi memakai Game 2.

    Angka lima adalah konfigurasi untuk penugasan,
    bukan jumlah soal aktual yang dibaca dari file game.
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        return 0

    if not 1 <= level <= 5:
        return 0

    if kode in KODE_GAME:
        return JUMLAH_SOAL_PER_LEVEL

    return 0


def level_game_tersedia(
    kode,
    level,
    level_min=1,
    level_maks=5,
):
    """Memilih level terdekat yang tersedia."""
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
    Gunakan jawaban Game 1 untuk memperbarui seluruh kompetensi
    literasi lain yang terdaftar.

    Ini memastikan semua indikator literasi menerima data
    ketika hanya satu bank soal Game 1 yang digunakan.

    Catatan: hasil seluruh indikator berasal dari bukti jawaban
    yang sama. Ini adalah pencerminan data, bukan pengukuran
    kompetensi yang benar-benar terpisah.
    """
    if not misi or misi.kompetensi.ranah != "literasi":
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
        .filter(kode__in=KODE_LITERASI)
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
    Memilih misi aktif per ranah.

    Semua kompetensi literasi dipetakan ke Game 1.
    Semua kompetensi numerasi dipetakan ke Game 2.
    """
    penguasaan_lengkap(siswa)

    hasil = []

    game_codes = {
        kode
        for kode in getattr(settings, "LENTERA_GAMES", {})
        if kode in KODE_GAME
    }

    for ranah in ("literasi", "numerasi"):
        misi_terpilih = None

        calon_misi = (
            siswa.misi.filter(
                status="baru",
                kompetensi__ranah=ranah,
                kompetensi__kode__in=game_codes,
            )
            .select_related("kompetensi", "aktivitas")
            .order_by("-dari_guru", "-id")
        )

        for calon in calon_misi:
            level_min = (
                calon.aktivitas.level_min
                if calon.aktivitas else 1
            )
            level_maks = (
                calon.aktivitas.level_maks
                if calon.aktivitas else 5
            )

            level_valid = level_game_tersedia(
                calon.kompetensi.kode,
                calon.level,
                level_min,
                level_maks,
            )

            if level_valid is None:
                continue

            if calon.level != level_valid:
                calon.level = level_valid
                calon.save(update_fields=["level"])

            misi_terpilih = calon
            break

        if misi_terpilih is None:
            calon_penguasaan = (
                siswa.penguasaan.filter(
                    kompetensi__ranah=ranah,
                    kompetensi__kode__in=game_codes,
                )
                .select_related("kompetensi")
                .order_by("p_l", "kompetensi__urutan")
            )

            for penguasaan in calon_penguasaan:
                level_valid = level_game_tersedia(
                    penguasaan.kompetensi.kode,
                    penguasaan.level,
                )

                if level_valid is None:
                    continue

                misi_terpilih = Misi.objects.create(
                    siswa=siswa,
                    kompetensi=penguasaan.kompetensi,
                    level=level_valid,
                    jenis="misi",
                )
                break

        if misi_terpilih is not None:
            hasil.append(misi_terpilih)

    return hasil


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
):
    """Terapkan hasil diagnostik menjadi state awal siswa."""
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
    """Buat misi diagnostik untuk kompetensi yang punya game."""
    jumlah = 0

    game_codes = {
        kode
        for kode in getattr(settings, "LENTERA_GAMES", {})
        if kode in KODE_GAME
    }

    kompetensi_tersedia = Kompetensi.objects.filter(
        kode__in=game_codes
    )

    for siswa in kelas.siswa.all():
        for kompetensi in kompetensi_tersedia:
            level_awal = level_game_tersedia(
                kompetensi.kode,
                1,
            )

            if level_awal is None:
                continue

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
                level=level_awal,
                jenis="diagnostik",
                dari_guru=True,
            )

            jumlah += 1

    return jumlah


@transaction.atomic
def selesaikan_diagnostik(misi):
    """Hitung hasil diagnostik dari jawaban unik per soal."""
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
        hasil.append(
            terapkan_diagnostik(
                siswa=misi.siswa,
                kompetensi=statistik["kompetensi"],
                benar=statistik["benar"],
                total=statistik["total"],
                sumber="game",
            )
        )

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

@transaction.atomic
def tugaskan_aktivitas(
    guru,
    kelas,
    aktivitas,
    target,
    ids=(),
):
    """Tugaskan aktivitas ke kelas, kelompok, atau siswa tertentu."""
    siswa_kelas = list(kelas.siswa.all())

    kode = aktivitas.kompetensi.kode
    level_min = max(1, int(aktivitas.level_min))
    level_maks = min(5, int(aktivitas.level_maks))

    dapat_dimainkan = (
        kode_game_tersedia(kode)
        and level_game_tersedia(
            kode,
            level_min,
            level_min,
            level_maks,
        ) is not None
    )

    penguasaan = {
        p.siswa_id: p
        for p in Penguasaan.objects.filter(
            kompetensi=aktivitas.kompetensi,
            siswa__in=siswa_kelas,
        )
    }

    def status_siswa(siswa):
        p = penguasaan.get(siswa.id)

        return engine.status(
            p.p_l if p else engine.P_AWAL
        )

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
        for siswa in dipilih:
            p = penguasaan.get(siswa.id)
            level_siswa = p.level if p else level_min

            level = level_game_tersedia(
                kode,
                level_siswa,
                level_min,
                level_maks,
            )

            if level is None:
                continue

            misi = (
                Misi.objects.filter(
                    siswa=siswa,
                    kompetensi=aktivitas.kompetensi,
                    status="baru",
                    jenis="misi",
                )
                .order_by("id")
                .first()
            )

            if misi:
                misi.level = level
                misi.dari_guru = True
                misi.aktivitas = aktivitas

                misi.save(
                    update_fields=[
                        "level",
                        "dari_guru",
                        "aktivitas",
                    ]
                )

            else:
                Misi.objects.create(
                    siswa=siswa,
                    kompetensi=aktivitas.kompetensi,
                    level=level,
                    jenis="misi",
                    dari_guru=True,
                    aktivitas=aktivitas,
                )

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
    """Terapkan rekomendasi ke siswa sesuai tingkat penguasaan."""
    kode = kompetensi.kode

    if not kode_game_tersedia(kode):
        return 0

    jumlah_misi = 0

    for siswa in kelas.siswa.all():
        penguasaan, _ = Penguasaan.objects.get_or_create(
            siswa=siswa,
            kompetensi=kompetensi,
            defaults={"p_l": engine.P_AWAL},
        )

        level = penguasaan.level

        if penguasaan.status == "Cakap":
            level = min(5, level + 1)

        level_valid = level_game_tersedia(
            kode,
            level,
        )

        if level_valid is None:
            continue

        misi = (
            Misi.objects.filter(
                siswa=siswa,
                kompetensi=kompetensi,
                status="baru",
                jenis="misi",
            )
            .order_by("id")
            .first()
        )

        if misi:
            misi.level = level_valid
            misi.dari_guru = True

            misi.save(
                update_fields=[
                    "level",
                    "dari_guru",
                ]
            )

        else:
            Misi.objects.create(
                siswa=siswa,
                kompetensi=kompetensi,
                level=level_valid,
                jenis="misi",
                dari_guru=True,
            )

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

    skor_sekarang = {
        kunci: p.p_l
        for kunci, p in penguasaan.items()
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
            p = penguasaan.get((murid.id, k.id))
            nilai = p.p_l if p else engine.P_AWAL

            sel.append({
                "pct": round(nilai * 100),
                "st": engine.status(nilai),
                "level": p.level if p else 1,
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

    def rata_ranah(data, ranah):
        nilai = [
            (
                data.get((murid.id, k.id), engine.P_AWAL)
                * 100
            )
            for murid in siswa
            for k in kompetensi
            if k.ranah == ranah
        ]

        return sum(nilai) / len(nilai) if nilai else 0

    ranah = []

    for nama_ranah in ("literasi", "numerasi"):
        sekarang = rata_ranah(
            skor_sekarang,
            nama_ranah,
        )
        sebelumnya = rata_ranah(
            skor_sebelum,
            nama_ranah,
        )

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

    if siswa and kompetensi:
        rata_kompetensi = [
            (
                sum(
                    data["sel"][i]["pct"]
                    for data in baris
                ) / len(baris)
            )
            for i in range(len(kompetensi))
        ]

        indeks_terendah = min(
            range(len(kompetensi)),
            key=lambda i: rata_kompetensi[i],
        )

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
                        data["sel"][indeks_terendah]["level"]
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
        "ranah": ranah,
        "waktu": waktu,
        "rek": rekomendasi,
        "perhatian": sum(
            item["perhatian"]
            for item in baris
        ),
        "kesulitan": daftar_kesulitan,
    }

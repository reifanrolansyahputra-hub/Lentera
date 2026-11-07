"""
Jalan Lentera: jalur belajar murid per kategori (Literasi dan Numerasi).

Aturan inti
-----------
- Satu BAGIAN = 5 LEVEL. Satu LEVEL = 5 soal = 5 lampu (satu lampu menyala + centang tiap jawaban benar).
- Di beranda murid, satu BAGIAN digambar sebagai satu jalur zig-zag: 5 titik, satu titik per level.
  Level selesai = titik menyala emas. Bagian berikutnya terkunci sampai kelima titik bagian sebelumnya emas.
- Jalur dibangun dari bank soal (SoalGame). Bagian baru tampil bila kelima levelnya sudah punya soal.
- Murid memainkan level secara berurutan: level 1 -> 5 bagian 1, lalu bagian 2, dst.
- Literasi: susun kalimat dan baca kalimat (mikrofon) satu kesatuan; satu level memuat keduanya.
- Numerasi: satu bagian = satu operasi hitung (penjumlahan, pengurangan, perkalian, pembagian).
- Batas waktu level = menit pada Bank Aktivitas (lihat `batas_waktu`).
- Peta Kompetensi dicicil: persen = dasar + (100 - dasar) x kemajuan jalur x penguasaan,
  100% baru tercapai setelah murid menyelesaikan `Kelas.target_bagian` bagian.
"""

from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from . import engine
from .bank_soal import (
    BAGIAN_NUMERASI_BAWAAN,
    KOMPETENSI_UTAMA_LITERASI,
    pilih_soal,
)
from .models import Aktivitas, HasilDiagnostik, Jawaban, Kelas, Kompetensi, Misi, SoalGame

GAMES = ("literasi", "numerasi")
NAMA_GAME = {"literasi": "Literasi", "numerasi": "Numerasi"}
GAME_KE = {"literasi": "Game 1", "numerasi": "Game 2"}


def level_per_bagian():
    return int(getattr(settings, "LENTERA_LEVEL_PER_BAGIAN", 5))


def soal_per_level():
    return int(getattr(settings, "LENTERA_SOAL_PER_LEVEL", 5))


# ==========================================================
# 1. STRUKTUR JALUR (dari bank soal)
# ==========================================================

def struktur(game):
    """Daftar bagian sebuah game, urut nomor.

    Tiap bagian: {no, kompetensi, judul, level: [{level, jumlah, tersedia}], lengkap}
    `tersedia` = jumlah soal aktif; `jumlah` = soal yang dimainkan (maks LENTERA_SOAL_PER_LEVEL).
    """
    maks = soal_per_level()
    per_bagian = {}
    baris = SoalGame.objects.filter(game=game, aktif=True).values("bagian", "level", "kompetensi_id").annotate(n=Count("id"))
    for r in baris:
        b = per_bagian.setdefault(r["bagian"], {"level": Counter(), "kompetensi": Counter()})
        b["level"][r["level"]] += r["n"]
        b["kompetensi"][r["kompetensi_id"]] += r["n"]

    kompetensi = {k.id: k for k in Kompetensi.objects.all()}
    utama = next((k for k in kompetensi.values() if k.kode == KOMPETENSI_UTAMA_LITERASI), None)
    muncul = Counter()
    hasil = []
    for no in sorted(per_bagian):
        b = per_bagian[no]
        if game == "numerasi":
            komp = kompetensi[b["kompetensi"].most_common(1)[0][0]]
            muncul[komp.id] += 1
            judul = komp.judul or komp.nama
            if muncul[komp.id] > 1:
                judul = f"{judul} {muncul[komp.id]}"
        else:
            komp = utama
            judul = "Susun & Baca Kalimat" if no == 1 else f"Susun & Baca Kalimat {no}"
        level = [
            {"level": n, "jumlah": min(maks, b["level"].get(n, 0)), "tersedia": b["level"].get(n, 0)}
            for n in range(1, level_per_bagian() + 1)
        ]
        hasil.append({
            "no": no, "kompetensi": komp, "judul": judul, "level": level,
            "lengkap": all(x["tersedia"] > 0 for x in level),
        })
    return hasil


def bagian_bisa_dimainkan(game, bagian=None):
    """Bagian yang tampil di jalur murid: berurutan dari bagian 1 selama kelima levelnya punya soal."""
    hasil = []
    for b in (bagian if bagian is not None else struktur(game)):
        if not b["lengkap"]:
            break
        hasil.append(b)
    return hasil


def bagian_kompetensi(kode):
    """Nomor bagian Numerasi pertama yang dipakai sebuah kompetensi (bawaan bila belum ada soal)."""
    for b in struktur("numerasi"):
        if b["kompetensi"] and b["kompetensi"].kode == kode:
            return b["no"]
    return BAGIAN_NUMERASI_BAWAAN.get(kode, 1)


def game_dari_kompetensi(kompetensi):
    return "literasi" if kompetensi.ranah == "literasi" else "numerasi"


# ==========================================================
# 2. KEMAJUAN MURID
# ==========================================================

def level_selesai(siswa_ids, sebelum=None):
    """{(siswa_id, game): {(bagian, level), ...}} dari misi biasa yang selesai.

    `sebelum`: hanya hitung yang selesai sebelum waktu itu (misi lama tanpa waktu dianggap sudah lama).
    """
    from django.db.models import Q

    qs = Misi.objects.filter(siswa_id__in=siswa_ids, jenis="misi", status="selesai")
    if sebelum is not None:
        qs = qs.filter(Q(selesai_pada__lt=sebelum) | Q(selesai_pada__isnull=True))
    hasil = {}
    for sid, ranah, bagian, level in qs.values_list("siswa_id", "kompetensi__ranah", "bagian", "level"):
        hasil.setdefault((sid, ranah), set()).add((bagian, level))
    return hasil


def target_siswa(siswa, game, selesai=None, kompetensi=None, level_min=1, level_maks=5):
    """(bagian, level, kompetensi) berikutnya di jalur, atau None bila semua level yang tersedia sudah selesai.

    kompetensi / level_min / level_maks dipakai saat guru menugaskan aktivitas tertentu.
    """
    if selesai is None:
        selesai = level_selesai([siswa.id]).get((siswa.id, game), set())
    for b in bagian_bisa_dimainkan(game):
        if game == "numerasi" and kompetensi is not None and b["kompetensi"] != kompetensi:
            continue
        for x in b["level"]:
            if not level_min <= x["level"] <= level_maks:
                continue
            if (b["no"], x["level"]) not in selesai:
                return b["no"], x["level"], b["kompetensi"]
    return None


def target_ulang(game, kompetensi=None, level_min=1, level_maks=5):
    """Level pertama dalam rentang (untuk mengulang latihan bila semuanya sudah selesai)."""
    for b in bagian_bisa_dimainkan(game):
        if game == "numerasi" and kompetensi is not None and b["kompetensi"] != kompetensi:
            continue
        for x in b["level"]:
            if level_min <= x["level"] <= level_maks:
                return b["no"], x["level"], b["kompetensi"]
    return None


# ==========================================================
# 3. MISI AKTIF
# ==========================================================

def _misi_baru(siswa, bagian, level, kompetensi, dari_guru=False, aktivitas=None):
    """Pakai ulang misi 'baru' untuk level yang sama, atau buat yang baru."""
    ada = (
        Misi.objects.filter(siswa=siswa, jenis="misi", status="baru", kompetensi=kompetensi, bagian=bagian, level=level)
        .order_by("-id").first()
    )
    if ada:
        ubah = []
        if dari_guru and not ada.dari_guru:
            ada.dari_guru = True
            ubah.append("dari_guru")
        if aktivitas is not None and ada.aktivitas_id != aktivitas.id:
            ada.aktivitas = aktivitas
            ubah.append("aktivitas")
        if ubah:
            ada.save(update_fields=ubah)
        return ada
    return Misi.objects.create(
        siswa=siswa, kompetensi=kompetensi, bagian=bagian, level=level, jenis="misi",
        dari_guru=dari_guru, aktivitas=aktivitas,
    )


def misi_aktif(siswa):
    """Misi yang ditampilkan di beranda murid: satu per kategori (Literasi, Numerasi).

    Prioritas: tugas guru yang masih berlaku, lalu level berikutnya di jalur.
    Kategori yang semua levelnya sudah selesai tidak menampilkan misi.
    """
    from .services import penguasaan_lengkap

    penguasaan_lengkap(siswa)
    tandai_kedaluwarsa(siswa)
    selesai = level_selesai([siswa.id])
    hasil = []
    for game in GAMES:
        sudah = selesai.get((siswa.id, game), set())
        bagian = {b["no"]: b for b in bagian_bisa_dimainkan(game)}
        pilihan = None

        # 1. Tugas dari guru yang masih berlaku (levelnya masih punya soal).
        for m in (
            siswa.misi.filter(jenis="misi", status="baru", dari_guru=True, kompetensi__ranah=game)
            .select_related("kompetensi", "aktivitas").order_by("-id")
        ):
            b = bagian.get(m.bagian)
            if b and any(x["level"] == m.level and x["tersedia"] > 0 for x in b["level"]):
                pilihan = m
                break

        # 2. Level berikutnya di jalur.
        if pilihan is None:
            t = target_siswa(siswa, game, sudah)
            if t:
                pilihan = _misi_baru(siswa, t[0], t[1], t[2])
        if pilihan is not None:
            hasil.append(pilihan)
    return hasil


# ==========================================================
# 4. BATAS WAKTU
# ==========================================================

def aktivitas_misi(misi):
    """Aktivitas yang menentukan batas waktu: yang ditugaskan guru, atau yang cocok dengan kompetensi dan level."""
    if misi.aktivitas_id:
        return misi.aktivitas
    return (
        Aktivitas.objects.filter(kompetensi=misi.kompetensi, level_min__lte=misi.level, level_maks__gte=misi.level)
        .order_by("-tervalidasi", "id").first()
    )


def batas_detik(misi):
    """Batas waktu level dalam detik (0 = tanpa batas). Asesmen diagnostik tidak dibatasi waktu."""
    if misi.jenis == "diagnostik":
        return 0
    a = aktivitas_misi(misi)
    return int(a.menit) * 60 if a and a.menit else 0


def aturan_waktu():
    w = getattr(settings, "LENTERA_WAKTU", {})
    return {
        "tick_detik": int(w.get("tick", 10)),
        "toleransi_detik": int(w.get("maks_lewat", 30)),
        "penalti_poin": int(w.get("penalti", 5)),
    }


def denda_waktu(durasi, batas, tick=None, toleransi=None):
    """Jumlah kali denda yang berlaku pada durasi tertentu, dan apakah level sudah gagal.

    Setelah `batas` habis, denda terkena tiap `tick` detik; lewat `toleransi` detik = gagal.
    tick / toleransi bawaan dari LENTERA_WAKTU; token game boleh membawa nilai sendiri.
    """
    if not batas or durasi <= batas:
        return 0, False
    a = aturan_waktu()
    tick = int(tick or a["tick_detik"])
    toleransi = a["toleransi_detik"] if toleransi is None else int(toleransi)
    lewat = durasi - batas
    return min(lewat // tick, toleransi // tick), lewat > toleransi


def tandai_kedaluwarsa(siswa):
    """Misi yang jamnya sudah berjalan lalu ditinggalkan melewati batas + toleransi dinyatakan gagal.

    Tanpa ini murid yang kembali setelah lama langsung "gagal" saat game dibuka. Dengan ini beranda
    menawarkan percobaan baru (soal baru, jam baru). Mengembalikan jumlah misi yang ditandai.
    """
    sekarang = timezone.now()
    jumlah = 0
    for m in siswa.misi.filter(jenis="misi", status="baru", mulai_pada__isnull=False).select_related("kompetensi", "aktivitas"):
        berjalan = int((sekarang - m.mulai_pada).total_seconds())
        _, gagal = denda_waktu(berjalan, batas_detik(m))
        if gagal:
            m.status, m.durasi_detik, m.selesai_pada = "gagal", berjalan, sekarang
            m.save(update_fields=["status", "durasi_detik", "selesai_pada"])
            jumlah += 1
    return jumlah


# ==========================================================
# 5. SOAL DAN LAMPU SEBUAH MISI
# ==========================================================

def kompetensi_soal(misi):
    """Filter kompetensi bank soal: Game 2 per kompetensi bagian, Game 1 semua kompetensi literasi."""
    return misi.kompetensi if misi.kompetensi.ranah == "numerasi" else None


def bekukan_soal(misi):
    """Tetapkan soal yang dipakai percobaan ini (sekali saja). Mengembalikan daftar SoalGame sesuai urutan."""
    game = game_dari_kompetensi(misi.kompetensi)
    if not misi.soal_ids:
        dipilih = pilih_soal(game, misi.bagian, misi.level, kompetensi_soal(misi), benih=misi.id)
        if dipilih:
            misi.soal_ids = [s.pk for s in dipilih]
            misi.save(update_fields=["soal_ids"])
        return dipilih
    ada = {s.pk: s for s in SoalGame.objects.filter(pk__in=misi.soal_ids).select_related("kompetensi")}
    return [ada[pk] for pk in misi.soal_ids if pk in ada]


def lampu_misi(misi):
    """(daftar item_id yang sudah benar, jumlah soal) untuk misi ini."""
    soal = bekukan_soal(misi) if misi.soal_ids else None
    if soal is None:
        game = game_dari_kompetensi(misi.kompetensi)
        soal = pilih_soal(game, misi.bagian, misi.level, kompetensi_soal(misi), benih=misi.id)
    ids = {s.item_id for s in soal}
    benar = set(
        Jawaban.objects.filter(misi=misi, benar=True).exclude(item_id="").values_list("item_id", flat=True)
    ) & ids
    return sorted(benar), len(soal)


# ==========================================================
# 6. TAMPILAN JALUR MURID
# ==========================================================

# Jalur zig-zag satu bagian digambar pada kanvas SVG 320 x 156: tangga naik ke kanan
# (datar, naik, datar, naik, ...) seperti peta perjalanan. Satu titik = satu level.
ZIGZAG_LEBAR, ZIGZAG_TINGGI = 320, 156


def titik_zigzag(n):
    """[(x, y)] untuk n titik jalur zig-zag (bilangan bulat supaya aman di template berlokal Indonesia)."""
    n = max(1, int(n))
    kiri, kanan, dasar = 34, ZIGZAG_LEBAR - 38, 116
    langkah = (kanan - kiri) / (n - 1) if n > 1 else 0
    naik = (n - 1) // 2
    dy = min(36, 72 / naik) if naik else 0
    return [(round(kiri + i * langkah), round(dasar - dy * (i // 2))) for i in range(n)]


def jalur_siswa(siswa, aktif=None):
    """Data jalur Literasi dan Numerasi untuk beranda murid (tanpa menulis ke database).

    `aktif` = hasil misi_aktif(siswa) supaya lampu level yang sedang dimainkan ikut tampil.

    Per bagian: bisa_main = kelima levelnya sudah punya soal; terbuka = bisa dimainkan sekarang
    (semua bagian sebelumnya sudah penuh); penuh = kelima level selesai; berikut = nomor bagian
    berikutnya yang bisa dimainkan (untuk tombol "Lanjut ke bagian berikutnya").
    Per level (= satu titik zig-zag): status selesai / kini / belum / terkunci / kosong, daftar lampu
    (True = menyala), serta nyala/total lampu untuk titik yang sedang dimainkan.
    """
    aktif = aktif if aktif is not None else misi_aktif(siswa)
    selesai = level_selesai([siswa.id])
    misi_game = {game_dari_kompetensi(m.kompetensi): m for m in aktif}
    maks = soal_per_level()

    hasil = {}
    for game in GAMES:
        sudah = selesai.get((siswa.id, game), set())
        semua = struktur(game)
        nomor_main = {b["no"] for b in bagian_bisa_dimainkan(game, semua)}
        misi = misi_game.get(game)
        lampu_aktif = lampu_misi(misi) if misi else ([], 0)

        daftar, kini_bagian, rantai_penuh = [], None, True
        for b in semua:
            main = b["no"] in nomor_main
            nyala, level = 0, []
            for x in b["level"]:
                kunci = (b["no"], x["level"])
                if not main:
                    status = "kosong" if x["tersedia"] == 0 else "terkunci"
                    lamp = [False] * x["jumlah"]
                elif kunci in sudah:
                    status, lamp = "selesai", [True] * (x["jumlah"] or maks)
                    nyala += 1
                elif misi and (misi.bagian, misi.level) == kunci:
                    ids, total = lampu_aktif
                    status, lamp = "kini", [i < len(ids) for i in range(total or x["jumlah"])]
                    kini_bagian = b["no"]
                else:
                    status, lamp = "belum", [False] * x["jumlah"]
                level.append({
                    **x, "status": status, "lampu": lamp, "nomor": x["level"],
                    "nyala": sum(1 for l in lamp if l), "total": len(lamp) or x["jumlah"] or maks,
                })
            # titik jalur zig-zag + ruas menuju titik berikutnya (menyala emas bila level ini selesai)
            titik = titik_zigzag(len(level))
            for i, x in enumerate(level):
                x["gx"], x["gy"] = titik[i]
                x["seg_x"], x["seg_y"] = titik[i + 1] if i + 1 < len(level) else (None, None)
            penuh = main and nyala == len(b["level"])
            daftar.append({
                "no": b["no"], "judul": b["judul"], "lengkap": b["lengkap"], "bisa_main": main,
                "terbuka": main and rantai_penuh, "level": level, "selesai": nyala, "penuh": penuh,
                "kompetensi": b["kompetensi"],
                "lampu_nyala": sum(sum(1 for l in x["lampu"] if l) for x in level),
                "lampu_total": sum(len(x["lampu"]) for x in level),
            })
            rantai_penuh = rantai_penuh and penuh
        for i, b in enumerate(daftar):
            b["berikut"] = next((c["no"] for c in daftar[i + 1:] if c["bisa_main"]), None)

        main_daftar = [b for b in daftar if b["bisa_main"]]
        # bagian yang dibuka bawaan: yang sedang dimainkan; bila semua selesai, bagian terakhir
        buka = kini_bagian or next((b["no"] for b in main_daftar if not b["penuh"]), None)
        if buka is None and main_daftar:
            buka = main_daftar[-1]["no"]
        hasil[game] = {
            "game": game, "nama": NAMA_GAME[game], "bagian": daftar, "buka": buka, "misi": misi,
            "selesai_semua": bool(main_daftar) and all(b["penuh"] for b in main_daftar) and misi is None,
            "ada_bagian": bool(daftar), "ada_main": bool(main_daftar), "level_selesai": len(sudah),
            "jumlah_main": len(main_daftar), "jumlah_penuh": sum(1 for b in main_daftar if b["penuh"]),
            "lampu_nyala": sum(b["lampu_nyala"] for b in main_daftar),
            "disiapkan": len(daftar) - len(main_daftar),
        }
    return hasil


# ==========================================================
# 7. LENCANA
# ==========================================================

_IKON_LENCANA = {
    "literasi": [("📖", "Pembaca Tekun"), ("🎤", "Pembaca Lantang"), ("📚", "Kutu Buku"), ("🦉", "Bijak Bahasa")],
    "numerasi": [("➕", "Raja Tambah"), ("➖", "Jagoan Kurang"), ("✖️", "Bintang Kali"), ("➗", "Ahli Bagi")],
}


def lencana_siswa(jalur, target_bagian):
    """Daftar lencana: menyala saat sebuah bagian selesai. [(ikon, nama, keterangan, menyala)]"""
    hasil = []
    for game in GAMES:
        for i, b in enumerate(jalur[game]["bagian"]):
            if not b["bisa_main"]:
                continue
            ikon = _IKON_LENCANA[game][i % len(_IKON_LENCANA[game])]
            nama = ikon[1] if i < len(_IKON_LENCANA[game]) else f"{ikon[1]} {i // len(_IKON_LENCANA[game]) + 1}"
            hasil.append((ikon[0], nama, f"Selesaikan Bagian {b['no']} {NAMA_GAME[game]}", b["penuh"]))
    total = sum(j["level_selesai"] for j in jalur.values())
    hasil.append(("🌟", "Penyala Pertama", "Selesaikan 1 level mana saja", total >= 1))
    penuh = sum(1 for j in jalur.values() for b in j["bagian"] if b["bisa_main"] and b["penuh"])
    hasil.append(("🏮", "Lentera Emas", f"Selesaikan {target_bagian} bagian (Literasi + Numerasi)", penuh >= target_bagian))
    return hasil


# ==========================================================
# 8. PETA KOMPETENSI (DICICIL)
# ==========================================================

def kemajuan(jumlah_level, target_bagian):
    """Kemajuan jalur 0..1 = level selesai / (target bagian x level per bagian)."""
    total = max(1, int(target_bagian)) * level_per_bagian()
    return max(0.0, min(1.0, jumlah_level / total))


def persen_peta(p_l, kemajuan_jalur, dasar=None):
    """Persen kompetensi yang ditampilkan (0-100), dicicil mengikuti kemajuan jalur.

    dasar  = P(L) awal (hasil diagnostik, atau engine.P_AWAL).
    Naik dari `dasar` menuju 100% mengikuti kemajuan jalur x penguasaan BKT (P(L) >= 0,80 dianggap penuh).
    """
    d = engine.batasi_peluang(engine.P_AWAL if dasar is None else dasar)
    batas = engine.AMBANG_CAKAP
    if d >= batas:
        m = 1.0
    else:
        m = max(0.0, min(1.0, (engine.batasi_peluang(p_l) - d) / (batas - d)))
    return round(100 * (d + (1 - d) * kemajuan_jalur * m))


def status_persen(pct):
    return engine.status(pct / 100)


def dasar_diagnostik(siswa_ids):
    """{(siswa_id, kompetensi_id): p_awal diagnostik terbaru}."""
    hasil = {}
    for sid, kid, p in HasilDiagnostik.objects.filter(siswa_id__in=siswa_ids).order_by("dibuat", "id").values_list("siswa_id", "kompetensi_id", "p_awal"):
        hasil[(sid, kid)] = p
    return hasil


def pernah_jawab(siswa_ids):
    """{(siswa_id, kompetensi_id)} yang pernah punya minimal satu Jawaban (sepanjang waktu, bukan 7 hari)."""
    return set(Jawaban.objects.filter(siswa_id__in=siswa_ids).values_list("siswa_id", "kompetensi_id").distinct())


def _levels_kompetensi(done, game, kompetensi, bagian_numerasi):
    """Level yang relevan untuk satu kompetensi: Literasi = semua level literasi, Numerasi = level bagian kompetensi itu."""
    if game == "literasi":
        return done
    return {(b, l) for (b, l) in done if bagian_numerasi.get(b) == kompetensi.id}


def hitung_peta(siswa_ids, kompetensi, penguasaan, target_bagian, sebelum=None, p_l_sebelum=None):
    """{(siswa_id, kompetensi_id): {pct, st, p_l, bagian, level, selesai, penuh}} untuk banyak siswa sekaligus.

    penguasaan   : {(siswa_id, kompetensi_id): Penguasaan}
    sebelum      : bila diisi, hitung kondisi pada waktu itu (untuk perbandingan 7 hari).
    p_l_sebelum  : {(siswa_id, kompetensi_id): P(L)} pada waktu itu.
    """
    selesai = level_selesai(siswa_ids, sebelum)
    dasar = dasar_diagnostik(siswa_ids)
    jawab = pernah_jawab(siswa_ids)
    strukt = {g: struktur(g) for g in GAMES}
    bagian_numerasi = {b["no"]: b["kompetensi"].id for b in strukt["numerasi"] if b["kompetensi"]}
    hasil = {}
    for sid in siswa_ids:
        done_game = {g: selesai.get((sid, g), set()) for g in GAMES}
        # kemajuan per kategori: level selesai di seluruh jalur kategori itu
        k_game = {g: kemajuan(len(done_game[g]), target_bagian) for g in GAMES}
        for k in kompetensi:
            game = k.ranah
            p = penguasaan.get((sid, k.id))
            if p_l_sebelum is not None:
                p_l = p_l_sebelum.get((sid, k.id), engine.P_AWAL)
            else:
                p_l = p.p_l if p else engine.P_AWAL
            # Belum ada bukti sama sekali (belum pernah menjawab, belum diagnostik) -> tampilkan
            # 0% polos, bukan dasar P_AWAL. "Mulai" dipakai hanya setelah murid benar-benar mencoba,
            # supaya warna "perlu diperhatikan" tidak salah kena murid yang memang belum mulai.
            ada_bukti = (sid, k.id) in dasar or (sid, k.id) in jawab
            if ada_bukti:
                pct = persen_peta(p_l, k_game[game], dasar.get((sid, k.id)))
                st = status_persen(pct)
            else:
                pct = 0
                st = "Belum mulai"
            # posisi di jalur: level berikutnya untuk kompetensi ini
            relevan = _levels_kompetensi(done_game[game], game, k, bagian_numerasi)
            pos = None
            for b in bagian_bisa_dimainkan(game, strukt[game]):
                if game == "numerasi" and b["kompetensi"] and b["kompetensi"].id != k.id:
                    continue
                for x in b["level"]:
                    if (b["no"], x["level"]) not in relevan:
                        pos = (b["no"], x["level"])
                        break
                if pos:
                    break
            hasil[(sid, k.id)] = {
                "pct": pct, "st": st, "p_l": p_l, "selesai": len(relevan),
                "bagian": pos[0] if pos else None, "level": pos[1] if pos else None, "penuh": pos is None and bool(relevan),
            }
    return hasil


def peta_siswa(siswa, penguasaan_list=None, target_bagian=None):
    """Peta satu murid: [(Penguasaan, info)] untuk dashboard murid ('Perkembanganku')."""
    from .services import penguasaan_lengkap

    pen = penguasaan_list if penguasaan_list is not None else penguasaan_lengkap(siswa)
    if target_bagian is None:
        target_bagian = siswa.kelas.target_bagian
    info = hitung_peta([siswa.id], [p.kompetensi for p in pen], {(siswa.id, p.kompetensi_id): p for p in pen}, target_bagian)
    return [(p, info[(siswa.id, p.kompetensi_id)]) for p in pen]


def waktu_banding():
    return timezone.now() - timedelta(days=7)

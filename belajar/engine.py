
"""
LENTERA Adaptive Learning Engine.

Fungsi utama:
- Bayesian Knowledge Tracing (BKT).
- Status penguasaan siswa.
- Penyesuaian level berdasarkan hasil belajar.
- Pemilihan butir soal.
- Taksonomi kompetensi Literasi dan Numerasi.
- Pemetaan pola kesalahan dan tindak lanjut.

Modul ini tidak bergantung pada Django.
"""

from math import isfinite


# ==========================================================
# 1. PARAMETER ADAPTIF
# ==========================================================

G = 0.20
S = 0.10
T = 0.10

AMBANG_MULAI = 0.40
AMBANG_CAKAP = 0.80
AMBANG_NAIK_LEVEL = 0.95

AMBANG_PERHATIAN = 3
P_AWAL = 0.30

LEVEL_MIN = 1
LEVEL_MAKS = 5


# ==========================================================
# 2. TAKSONOMI KOMPETENSI
# ==========================================================

# Format:
# (kode, ranah, nama, judul, cakupan, contoh)

TAKSONOMI = [
    (
        "LT-KOSAKATA",
        "literasi",
        "Kosakata",
        "Petualangan Kata",
        "Mengenal dan memahami makna kata sehari-hari",
        ["kucing", "buku", "apel"],
    ),
    (
        "LT-LANCAR",
        "literasi",
        "Membaca lancar",
        "Membaca Bersama Lumi",
        "Mengeja dan membaca kata serta kalimat pendek",
        ["nyala", "nyanyi", "ngantuk"],
    ),
    (
        "LT-KETEPATAN",
        "literasi",
        "Ketepatan membaca",
        "Baca Kalimat",
        "Membaca kalimat dengan suara lantang secara tepat (diperiksa lewat mikrofon)",
        ["Ibu", "memasak", "nasi"],
    ),
    (
        "LT-KALIMAT",
        "literasi",
        "Susun kalimat",
        "Susun & Baca Kalimat",
        "Menyusun kata menjadi kalimat berpola S-P-O, lalu membacanya dengan tepat",
        ["Ibu", "membeli", "sayur"],
    ),
    (
        "NM-TAMBAH",
        "numerasi",
        "Penjumlahan",
        "Penjumlahan Seru",
        "Menjumlahkan bilangan, dengan dan tanpa menyimpan",
        ["12", "+", "7", "=", "?"],
    ),
    (
        "NM-KURANG",
        "numerasi",
        "Pengurangan",
        "Pengurangan Seru",
        "Mengurangkan bilangan, dengan dan tanpa meminjam",
        ["15", "-", "8", "=", "?"],
    ),
    (
        "NM-KALI",
        "numerasi",
        "Perkalian",
        "Perkalian Seru",
        "Perkalian sebagai penjumlahan berulang dan kelompok sama banyak",
        ["4", "\u00d7", "6", "=", "?"],
    ),
    (
        "NM-BAGI",
        "numerasi",
        "Pembagian",
        "Pembagian Seru",
        "Membagi rata dan mengelompokkan benda secara sama banyak",
        ["24", "\u00f7", "4", "=", "?"],
    ),
]

CONTOH = {
    item[0]: item[5]
    for item in TAKSONOMI
}


# ==========================================================
# 3. KOMPATIBILITAS KODE DARI GAME
# ==========================================================

# Kode lama diarahkan ke kode kompetensi resmi.
# Penambahan alias tidak mengubah soal secara otomatis.
# Game 1 tetap perlu mengirim kompetensi yang sesuai
# dengan butir soal yang sedang dikerjakan.

ALIAS_GAME = {
    # Alias Game 2: numerasi (empat operasi hitung)
    "PENJUMLAHAN": "NM-TAMBAH",
    "penjumlahan": "NM-TAMBAH",
    "PERTAMBAHAN": "NM-TAMBAH",
    "pertambahan": "NM-TAMBAH",

    "PENGURANGAN": "NM-KURANG",
    "pengurangan": "NM-KURANG",

    "PERKALIAN": "NM-KALI",
    "perkalian": "NM-KALI",

    "PEMBAGIAN": "NM-BAGI",
    "pembagian": "NM-BAGI",

    # Alias Game 1: literasi (susun kalimat dan baca kalimat satu kesatuan)
    "MENYUSUN_KALIMAT": "LT-KALIMAT",
    "menyusun_kalimat": "LT-KALIMAT",

    "KOSAKATA": "LT-KOSAKATA",
    "kosakata": "LT-KOSAKATA",

    "MEMBACA_LANCAR": "LT-LANCAR",
    "membaca_lancar": "LT-LANCAR",

    "KETEPATAN": "LT-KETEPATAN",
    "ketepatan": "LT-KETEPATAN",
    "BACA_LANTANG": "LT-KETEPATAN",
    "baca_lantang": "LT-KETEPATAN",
    "BACA_KALIMAT": "LT-KETEPATAN",
    "baca_kalimat": "LT-KETEPATAN",
}


def normalisasi_kode(kode):
    """Mengubah alias game menjadi kode resmi."""
    kode = str(kode or "").strip()
    return ALIAS_GAME.get(kode, kode)


# ==========================================================
# 4. POLA KESALAHAN DAN TINDAK LANJUT
# ==========================================================

# Format:
# kode_pola: (label, kode kompetensi, tindak lanjut)

POLA = {
    # ------------------------------------------------------
    # Literasi: membaca
    # ------------------------------------------------------

    "ny_ng": (
        "Membaca kata berawalan ny-/ng-",
        "LT-LANCAR",
        "Latihan suku kata ny- dan ng- bergambar",
    ),

    # Dikirim game saat bacaan lewat mikrofon belum tepat.

    "baca_kata_terlewat": (
        "Ada kata yang tidak terbaca saat membaca lantang",
        "LT-KETEPATAN",
        "Baca kalimat bersama per kata, jari menunjuk tiap kata",
    ),

    "baca_kata_berbeda": (
        "Kata yang dibaca berbeda dari tulisan",
        "LT-KETEPATAN",
        "Eja kata yang salah per suku kata, lalu baca ulang kalimatnya",
    ),

    # ------------------------------------------------------
    # Literasi: menyusun kalimat
    # Pola lama Game 1 dipertahankan.
    # ------------------------------------------------------

    "urutan_spo": (
        "Urutan subjek-predikat",
        "LT-KALIMAT",
        'Petunjuk bertahap: "Siapa? Melakukan apa?"',
    ),

    "SPO_TERBALIK": (
        "Urutan kata dalam kalimat terbalik",
        "LT-KALIMAT",
        "Bantu siswa mengenali subjek, predikat, dan objek",
    ),

    "PREDIKAT_AWAL": (
        "Predikat ditempatkan di awal kalimat",
        "LT-KALIMAT",
        "Arahkan siswa menentukan siapa yang melakukan tindakan",
    ),

    # ------------------------------------------------------
    # Numerasi: penjumlahan
    # ------------------------------------------------------

    "tambah_simpan": (
        "Lupa menyimpan 1 puluhan saat menjumlahkan",
        "NM-TAMBAH",
        "Latihan menukar 10 satuan menjadi 1 puluhan dengan blok",
    ),

    "tambah_salah_operasi": (
        "Mengurangkan padahal soal meminta menjumlahkan",
        "NM-TAMBAH",
        'Garis bawahi kata kunci "bertambah", "semua", dan "seluruhnya"',
    ),

    # ------------------------------------------------------
    # Numerasi: pengurangan
    # ------------------------------------------------------

    "kurang_pinjam": (
        "Salah meminjam puluhan saat mengurangkan",
        "NM-KURANG",
        "Latihan meminjam 1 puluhan menjadi 10 satuan dengan blok",
    ),

    "kurang_terbalik": (
        "Mengurangkan angka kecil dengan angka besar pada tiap tempat",
        "NM-KURANG",
        "Kurangi dari satuan dulu, pinjam puluhan bila satuan tidak cukup",
    ),

    "kurang_salah_operasi": (
        "Menjumlahkan padahal soal meminta mengurangkan",
        "NM-KURANG",
        'Garis bawahi kata kunci "sisa", "diambil", dan "hilang"',
    ),

    # ------------------------------------------------------
    # Numerasi: perkalian
    # ------------------------------------------------------

    "kali_salah_operasi": (
        "Menjumlahkan kedua bilangan padahal soal meminta mengalikan",
        "NM-KALI",
        "Tunjukkan perkalian sebagai kelompok sama banyak dengan benda",
    ),

    "kali_fakta": (
        "Hasil perkalian meleset satu kelompok",
        "NM-KALI",
        "Latihan penjumlahan berulang dan hafalan tabel bertahap",
    ),

    # ------------------------------------------------------
    # Numerasi: pembagian
    # ------------------------------------------------------

    "bagi_salah_operasi": (
        "Memilih operasi lain padahal soal meminta membagi rata",
        "NM-BAGI",
        "Peragakan membagi rata benda ke beberapa piring",
    ),

    "bagi_terbalik": (
        "Menukar bilangan yang dibagi dengan pembaginya",
        "NM-BAGI",
        "Tegaskan: yang dibagi adalah jumlah semua benda",
    ),
}


# ==========================================================
# 5. REKOMENDASI KELOMPOK
# ==========================================================

TINDAK = {
    "literasi": {
        "Mulai": (
            "Latihan suku kata dan kartu kata bergambar, "
            "lalu baca kalimat bersama per kata."
        ),
        "Berkembang": (
            "Menyusun kalimat dengan kartu kata berpasangan, "
            "lalu membacakannya bergantian."
        ),
        "Cakap": (
            "Tantangan membaca lantang kalimat panjang "
            "dengan jeda dan lafal yang tepat."
        ),
    },
    "numerasi": {
        "Mulai": (
            "Gunakan benda konkret seperti sapu lidi untuk menambah, "
            "mengurangi, mengelompokkan, dan membagi rata."
        ),
        "Berkembang": (
            "Latihan berpasangan dengan kartu operasi dan garis bilangan."
        ),
        "Cakap": (
            "Tantangan soal cerita yang memadukan beberapa operasi hitung."
        ),
    },
}


# ==========================================================
# 6. STATUS PENGUASAAN
# ==========================================================

def _angka_valid(nilai):
    """Memastikan nilai numerik dan bukan NaN/infinity."""
    try:
        angka = float(nilai)
    except (TypeError, ValueError, OverflowError):
        return None

    if not isfinite(angka):
        return None

    return angka


def batasi_peluang(p):
    """Memastikan peluang selalu berada pada interval 0 sampai 1."""
    angka = _angka_valid(p)

    if angka is None:
        return P_AWAL

    return max(0.0, min(1.0, angka))


def fase(level):
    """
    Level 1-2 = Fase A.
    Level 3-5 = Fase B.
    """
    level = int(level)

    if not LEVEL_MIN <= level <= LEVEL_MAKS:
        raise ValueError("Level harus berada pada 1 sampai 5.")

    return "A" if level <= 2 else "B"


def status(p):
    """
    Kategori penguasaan:
    - Mulai: P(L) < 0.40
    - Berkembang: 0.40 <= P(L) < 0.80
    - Cakap: P(L) >= 0.80
    """
    p = batasi_peluang(p)

    if p >= AMBANG_CAKAP:
        return "Cakap"

    if p >= AMBANG_MULAI:
        return "Berkembang"

    return "Mulai"


# ==========================================================
# 7. BAYESIAN KNOWLEDGE TRACING
# ==========================================================

def update_bkt(p, benar):
    """
    Perbarui estimasi penguasaan setelah satu jawaban.

    p = peluang siswa menguasai kompetensi sebelum jawaban.
    benar = True jika jawaban benar, False jika salah.
    """
    p = batasi_peluang(p)

    if not isinstance(benar, bool):
        benar = bool(benar)

    if benar:
        pembilang = p * (1 - S)
        penyebut = (
            p * (1 - S)
            + (1 - p) * G
        )
    else:
        pembilang = p * S
        penyebut = (
            p * S
            + (1 - p) * (1 - G)
        )

    if penyebut <= 0:
        peluang_observasi = p
    else:
        peluang_observasi = pembilang / penyebut

    peluang_belajar = (
        peluang_observasi
        + (1 - peluang_observasi) * T
    )

    return round(
        batasi_peluang(peluang_belajar),
        6,
    )


# ==========================================================
# 8. PENYESUAIAN LEVEL
# ==========================================================

def atur_level(level, p, salah_beruntun):
    """
    Menyesuaikan level berdasarkan penguasaan.

    Aturan:
    - Naik satu level jika P(L) >= 0.95.
    - Turun satu level jika salah tiga kali berturut-turut.
    - Level tetap berada pada rentang 1 hingga 5.

    Return:
        (level_baru, p_l_baru, salah_beruntun_baru)
    """
    level = max(
        LEVEL_MIN,
        min(LEVEL_MAKS, int(level)),
    )

    p = batasi_peluang(p)

    salah_beruntun = max(
        0,
        int(salah_beruntun),
    )

    if salah_beruntun >= 3:
        level_baru = max(
            LEVEL_MIN,
            level - 1,
        )
        return level_baru, p, 0

    if p >= AMBANG_NAIK_LEVEL and level < LEVEL_MAKS:
        return (
            level + 1,
            AMBANG_CAKAP,
            0,
        )

    return (
        level,
        p,
        salah_beruntun,
    )


# ==========================================================
# 9. ESTIMASI PELUANG BENAR
# ==========================================================

def peluang_benar(p, level_siswa, level_item):
    """
    Estimasi peluang menjawab benar berdasarkan penguasaan
    dan perbedaan level soal.

    Nilai minimum 0.03 dan maksimum 0.97 menghindari
    peluang ekstrem.
    """
    p = batasi_peluang(p)

    level_siswa = max(
        LEVEL_MIN,
        min(LEVEL_MAKS, int(level_siswa)),
    )

    level_item = max(
        LEVEL_MIN,
        min(LEVEL_MAKS, int(level_item)),
    )

    hasil = (
        p * (1 - S)
        + (1 - p) * G
        - 0.15 * (level_item - level_siswa)
    )

    return max(
        0.03,
        min(0.97, hasil),
    )


# ==========================================================
# 10. PEMILIHAN BUTIR SOAL
# ==========================================================

def pilih_item(kandidat, p_l, level, riwayat=()):
    """
    Memilih soal dengan estimasi peluang benar mendekati 70%.

    Format kandidat:
        [
            {"id": "q1", "level": 1},
            {"id": "q2", "level": 2},
        ]

    Soal di lima posisi riwayat terakhir dihindari jika
    masih ada kandidat lain.

    Return:
        Satu kandidat, atau None bila tidak ada kandidat valid.
    """
    if not kandidat:
        return None

    kandidat_valid = []

    for item in kandidat:
        if not isinstance(item, dict):
            continue

        if not item.get("id"):
            continue

        try:
            level_item = int(item["level"])
        except (KeyError, TypeError, ValueError):
            continue

        if not LEVEL_MIN <= level_item <= LEVEL_MAKS:
            continue

        kandidat_valid.append(item)

    if not kandidat_valid:
        return None

    riwayat_terakhir = set(list(riwayat)[-5:])

    pool = [
        item
        for item in kandidat_valid
        if item["id"] not in riwayat_terakhir
    ]

    if not pool:
        pool = kandidat_valid

    return min(
        pool,
        key=lambda item: abs(
            peluang_benar(
                p_l,
                level,
                item["level"],
            ) - 0.70
        ),
    )


# ==========================================================
# 11. LEVEL AWAL DARI ASESMEN DIAGNOSTIK
# ==========================================================

def level_dari_skor(benar, total):
    """
    Konversi skor asesmen awal ke:
        (level_awal, P(L)_awal)

    Aturan:
        rasio = benar / total
        level = min(5, 1 + floor(rasio * 5))
        P(L)  = 0.10 + 0.75 * rasio
    """
    try:
        benar = int(benar)
        total = int(total)
    except (TypeError, ValueError):
        raise ValueError(
            "Jumlah benar dan total harus berupa bilangan bulat."
        )

    if total <= 0:
        raise ValueError(
            "Jumlah soal harus lebih besar dari nol."
        )

    if benar < 0 or benar > total:
        raise ValueError(
            "Jumlah jawaban benar harus berada antara 0 dan total."
        )

    rasio = benar / total

    level_awal = min(
        LEVEL_MAKS,
        1 + int(rasio * 5),
    )

    p_awal = round(
        0.10 + 0.75 * rasio,
        2,
    )

    return level_awal, p_awal


def level_dari_tangga(level, benar, total):
    """
    Konversi hasil asesmen BERTANGGA (diagnostik yang naik level selama benar terus,
    berhenti begitu ada yang salah atau sudah tuntas level 5) ke (level_awal, P(L)_awal).

    `level`  = level tempat murid berhenti (level yang baru saja ia kerjakan).
    `benar`, `total` = skor percobaan-pertama murid PADA LEVEL ITU SAJA.

    Setiap level yang sudah ia lewati dengan sempurna (sebelum level ini) dihitung
    sebagai satu anak tangga penuh; level tempat ia berhenti memberi anak tangga
    SEBAGIAN sebesar rasio-nya di situ -- supaya nilai makin tinggi hanya kalau ia
    sungguh-sungguh naik lewat beberapa level, bukan cuma tuntas level 1 saja:

        sempurna     = (benar == total)
        anak_tangga  = level jika sempurna dan level == LEVEL_MAKS (tuntas semua level)
                       else (level - 1) + benar/total
        level_awal   = LEVEL_MAKS jika sempurna dan level == LEVEL_MAKS, else level
        P(L)         = 0.10 + 0.75 * (anak_tangga / LEVEL_MAKS)
    """
    try:
        level = int(level)
        benar = int(benar)
        total = int(total)
    except (TypeError, ValueError):
        raise ValueError(
            "Level, jumlah benar, dan total harus berupa bilangan bulat."
        )

    if total <= 0:
        raise ValueError(
            "Jumlah soal harus lebih besar dari nol."
        )

    if benar < 0 or benar > total:
        raise ValueError(
            "Jumlah jawaban benar harus berada antara 0 dan total."
        )

    if level < 1 or level > LEVEL_MAKS:
        raise ValueError(
            f"Level harus antara 1 dan {LEVEL_MAKS}."
        )

    sempurna = benar == total

    if sempurna and level == LEVEL_MAKS:
        anak_tangga = LEVEL_MAKS
        level_awal = LEVEL_MAKS
    else:
        anak_tangga = (level - 1) + benar / total
        level_awal = level

    p_awal = round(
        0.10 + 0.75 * (anak_tangga / LEVEL_MAKS),
        2,
    )

    return level_awal, p_awal

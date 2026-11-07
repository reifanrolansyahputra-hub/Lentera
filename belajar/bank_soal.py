"""
Bank soal Game 1 (Literasi) dan Game 2 (Numerasi) yang dikelola guru.

Isi modul:
- validasi isian form guru (Game 1: soal susun kalimat dengan gambar + kolom kata + jawaban,
  atau soal baca kalimat dengan mikrofon; Game 2: dua bilangan untuk empat operasi hitung),
- perhitungan jawaban otomatis keempat operasi hitung,
- serialisasi soal ke format asli tiap game (dipakai API /api/game/soal/),
- ringkasan jumlah soal per bagian dan level.

Tidak ada HTML mentah dari guru yang diteruskan ke game: teks di-escape dahulu,
hanya **tebal** dan baris baru yang diubah menjadi <b> dan <br>.
"""

import html
import random
import re
from collections import Counter

from django.conf import settings

from .models import SoalGame


# ==========================================================
# 1. KONSTANTA
# ==========================================================

KODE_LITERASI = ("LT-KALIMAT", "LT-KOSAKATA", "LT-KETEPATAN", "LT-LANCAR")
KODE_NUMERASI = ("NM-TAMBAH", "NM-KURANG", "NM-KALI", "NM-BAGI")

# Kompetensi utama misi Game 1 (satu level Game 1 memuat campuran kompetensi literasi).
KOMPETENSI_UTAMA_LITERASI = "LT-KALIMAT"

# Soal baca kalimat (dibaca lantang, diperiksa mikrofon). Susun kalimat dan baca kalimat
# satu kesatuan dalam satu level Game 1: jenis soal dibedakan lewat kompetensinya.
KOMPETENSI_BACA = "LT-KETEPATAN"
KODE_LITERASI_SUSUN = tuple(k for k in KODE_LITERASI if k != KOMPETENSI_BACA)

# Urutan bagian bawaan Numerasi (1 operasi hitung = 1 bagian).
BAGIAN_NUMERASI_BAWAAN = {"NM-TAMBAH": 1, "NM-KURANG": 2, "NM-KALI": 3, "NM-BAGI": 4}

JENIS_KOMPETENSI_GAME1 = {
    "LT-KALIMAT": "susun_kalimat",
    "LT-KOSAKATA": "kosakata_deskriptif_bergambar",
    "LT-KETEPATAN": "baca_lantang",
    "LT-LANCAR": "membaca_kata_kalimat",
}

# Data tiap operasi hitung Game 2: (tipe game, kata operasi, simbol, label 1, label 2).
OPERASI = {
    "NM-TAMBAH": ("penjumlahan", "Penjumlahan", "+", "Kelompok pertama", "Kelompok kedua"),
    "NM-KURANG": ("pengurangan", "Pengurangan", "-", "Mula-mula", "Diambil"),
    "NM-KALI": ("perkalian", "Perkalian", "\u00d7", "Banyak kelompok", "Isi tiap kelompok"),
    "NM-BAGI": ("pembagian", "Pembagian", "\u00f7", "Semua benda", "Banyak kelompok"),
}

# Kata maksimal pada kalimat baca, dan emoji bawaan bila guru tidak mengisi gambar.
KATA_BACA_MAKS = 20
EMOJI_BACA_BAWAAN = "\U0001F4D6"

BAGIAN_MAKS = 20
KATA_MAKS = 30
PANJANG_KATA_MAKS = 40

# Ikon ilustrasi untuk soal cerita: kunci -> (label, kelas Font Awesome, warna, kode twemoji)
IKON_CERITA = {
    "bintang": ("Bintang", "fa-star", "text-amber-400", "2b50"),
    "apel": ("Apel", "fa-apple-whole", "text-red-500", "1f34e"),
    "buku": ("Buku", "fa-book", "text-blue-500", "1f4da"),
    "kue": ("Kue", "fa-cookie", "text-orange-500", "1f9c1"),
    "ikan": ("Ikan", "fa-fish", "text-teal-500", "1f41f"),
    "pensil": ("Pensil", "fa-pencil", "text-purple-500", "270f"),
    "daun": ("Daun", "fa-leaf", "text-green-500", "1f343"),
    "kelereng": ("Kelereng", "fa-circle", "text-sky-500", "1f535"),
    "permen": ("Permen", "fa-candy-cane", "text-violet-500", "1f36c"),
    "jeruk": ("Jeruk", "fa-lemon", "text-yellow-500", "1f34a"),
}

EKSTENSI_GAMBAR = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}

TWEMOJI = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/{}.svg"


class SoalTidakValid(ValueError):
    """Isian guru tidak valid; pesannya langsung ditampilkan ke guru."""


# ==========================================================
# 2. TEKS AMAN
# ==========================================================

_TEBAL = re.compile(r"\*\*(.+?)\*\*", re.S)


def markup_ke_html(teks):
    """Escape semua HTML, lalu **tebal** -> <b>tebal</b> dan baris baru -> <br>."""
    aman = html.escape(str(teks or "").strip(), quote=False)
    aman = _TEBAL.sub(r"<b>\1</b>", aman)
    return aman.replace("\r\n", "\n").replace("\n", "<br>")


def _teks(post, nama, wajib=False, maks=300, label=None):
    nilai = " ".join(str(post.get(nama, "")).split()) if maks <= 300 else str(post.get(nama, "")).strip()
    if wajib and not nilai:
        raise SoalTidakValid(f"{label or nama.capitalize()} wajib diisi.")
    if len(nilai) > maks:
        raise SoalTidakValid(f"{label or nama.capitalize()} terlalu panjang (maksimal {maks} karakter).")
    return nilai


def _angka(post, nama, label, minimum=0, maksimum=10000, wajib=True):
    mentah = str(post.get(nama, "")).strip()
    if mentah == "":
        if wajib:
            raise SoalTidakValid(f"{label} wajib diisi.")
        return None
    try:
        nilai = int(mentah)
    except ValueError:
        raise SoalTidakValid(f"{label} harus berupa bilangan bulat.")
    if not minimum <= nilai <= maksimum:
        raise SoalTidakValid(f"{label} harus antara {minimum} dan {maksimum}.")
    return nilai


def _daftar_kata(mentah, label):
    """Teks satu kata/frasa per baris -> daftar kata (spasi di dalam frasa dipertahankan)."""
    kata = [" ".join(baris.split()) for baris in str(mentah or "").replace(",", "\n").splitlines()]
    kata = [k for k in kata if k]
    if len(kata) > KATA_MAKS:
        raise SoalTidakValid(f"{label}: maksimal {KATA_MAKS} kata.")
    if any(len(k) > PANJANG_KATA_MAKS for k in kata):
        raise SoalTidakValid(f"{label}: setiap kata maksimal {PANJANG_KATA_MAKS} karakter.")
    return kata


def _daftar_baris(mentah, label):
    """Satu kata/frasa per BARIS saja (koma tidak dianggap pemisah) - dipakai untuk jawaban benar."""
    kata = [" ".join(baris.split()) for baris in str(mentah or "").splitlines()]
    kata = [k for k in kata if k]
    if len(kata) > KATA_MAKS:
        raise SoalTidakValid(f"{label}: maksimal {KATA_MAKS} kata.")
    if any(len(k) > PANJANG_KATA_MAKS for k in kata):
        raise SoalTidakValid(f"{label}: setiap kata maksimal {PANJANG_KATA_MAKS} karakter.")
    return kata


def _termuat_dalam(kata, bank):
    """True bila setiap kata pada `kata` ada di `bank` (memperhitungkan jumlah kemunculan)."""
    kurang = Counter(kata) - Counter(bank)
    return not kurang, sorted(kurang)


# ==========================================================
# 3. VALIDASI GAMBAR GAME 1
# ==========================================================

def periksa_gambar(berkas):
    """Pastikan berkas benar-benar gambar (jpg/png/gif/webp) dan tidak terlalu besar."""
    nama = (berkas.name or "").lower()
    ekstensi = "." + nama.rsplit(".", 1)[-1] if "." in nama else ""
    if ekstensi not in EKSTENSI_GAMBAR:
        raise SoalTidakValid("Gambar harus berformat JPG, PNG, GIF, atau WEBP.")
    maks_mb = getattr(settings, "LENTERA_GAMBAR_MAKS_MB", 5)
    if berkas.size > maks_mb * 1024 * 1024:
        raise SoalTidakValid(f"Ukuran gambar maksimal {maks_mb} MB.")
    kepala = berkas.read(16)
    berkas.seek(0)
    if not any(kepala.startswith(tanda) for tanda in EKSTENSI_GAMBAR[ekstensi]):
        raise SoalTidakValid("Isi berkas bukan gambar yang valid.")
    if ekstensi == ".webp" and kepala[8:12] != b"WEBP":
        raise SoalTidakValid("Isi berkas bukan gambar WEBP yang valid.")


# ==========================================================
# 4. VALIDASI FORM GURU
# ==========================================================

def _umum(post, kompetensi_valid):
    kode = str(post.get("kompetensi", "")).strip()
    if kode not in kompetensi_valid:
        raise SoalTidakValid("Pilih kompetensi soal.")
    return {
        "kode": kode,
        "bagian": _angka(post, "bagian", "Bagian", 1, BAGIAN_MAKS),
        "level": _angka(post, "level", "Level", 1, 5),
        "aktif": post.get("aktif", "1") not in ("0", "false", "off"),
    }


def bersihkan_game1(post, files=None, soal=None):
    """Isian Game 1: gambar soal, pertanyaan, kolom kata, dan jawaban benar. Mengembalikan dict field SoalGame."""
    umum = _umum(post, KODE_LITERASI)
    if umum["kode"] == KOMPETENSI_BACA:
        return _bersihkan_baca(post, files, soal, umum)
    instruksi = _teks(post, "instruksi", wajib=True, maks=300, label="Pertanyaan soal")
    kolom = _daftar_kata(post.get("kolom_kata", ""), "Kolom kata")
    jawaban = _daftar_baris(post.get("jawaban", ""), "Jawaban benar")

    if len(kolom) < 2:
        raise SoalTidakValid("Kolom kata minimal berisi 2 kata.")
    if not jawaban:
        raise SoalTidakValid("Jawaban benar wajib diisi (susun kata dari kolom kata).")
    ada, hilang = _termuat_dalam(jawaban, kolom)
    if not ada:
        raise SoalTidakValid("Kata pada jawaban belum ada di kolom kata: " + ", ".join(hilang) + ".")

    jawaban_lain = []
    for baris in str(post.get("jawaban_lain", "")).splitlines():
        alternatif = [" ".join(k.split()) for k in baris.split(",")]
        alternatif = [k for k in alternatif if k]
        if not alternatif:
            continue
        ada, hilang = _termuat_dalam(alternatif, kolom)
        if not ada:
            raise SoalTidakValid("Jawaban lain memakai kata yang tidak ada di kolom kata: " + ", ".join(hilang) + ".")
        if alternatif != jawaban and alternatif not in jawaban_lain:
            jawaban_lain.append(alternatif)

    gambar = (files or {}).get("gambar")
    if gambar:
        periksa_gambar(gambar)
    elif soal is None or not soal.gambar:
        raise SoalTidakValid("Gambar soal wajib diunggah.")

    return {
        "game": "literasi",
        "kompetensi_kode": umum["kode"], "bagian": umum["bagian"], "level": umum["level"], "aktif": umum["aktif"],
        "instruksi": instruksi, "petunjuk": "", "kolom_kata": kolom, "jawaban": jawaban, "jawaban_lain": jawaban_lain,
        "urutan_bebas": post.get("urutan_bebas") in ("1", "on", "true"),
        "kunci": None, "data": {}, "gambar_baru": gambar,
    }


def _bersihkan_baca(post, files, soal, umum):
    """Soal baca kalimat: kalimat yang dibaca lantang + gambar/emoji pendamping (opsional)."""
    kalimat = _teks(post, "kalimat", wajib=True, maks=160, label="Kalimat yang dibaca")
    jumlah_kata = len(kalimat.split())
    if jumlah_kata < 2:
        raise SoalTidakValid("Kalimat yang dibaca minimal terdiri dari 2 kata.")
    if jumlah_kata > KATA_BACA_MAKS:
        raise SoalTidakValid(f"Kalimat yang dibaca maksimal {KATA_BACA_MAKS} kata.")
    emoji = _teks(post, "emoji", maks=8, label="Emoji") or EMOJI_BACA_BAWAAN

    gambar = (files or {}).get("gambar")
    if gambar:
        periksa_gambar(gambar)

    return {
        "game": "literasi",
        "kompetensi_kode": KOMPETENSI_BACA, "bagian": umum["bagian"], "level": umum["level"], "aktif": umum["aktif"],
        "instruksi": kalimat, "petunjuk": "", "kolom_kata": [], "jawaban": [], "jawaban_lain": [],
        "urutan_bebas": False, "kunci": None, "data": {"emoji": emoji}, "gambar_baru": gambar,
    }


def hitung_kunci(kode, data):
    """Jawaban otomatis keempat operasi hitung Game 2."""
    a, b = data["a"], data["b"]
    if kode == "NM-TAMBAH":
        return a + b
    if kode == "NM-KURANG":
        return a - b
    if kode == "NM-KALI":
        return a * b
    if kode == "NM-BAGI":
        return a // b
    return None


def bersihkan_game2(post):
    """Isian Game 2: dua bilangan untuk salah satu dari empat operasi hitung."""
    umum = _umum(post, KODE_NUMERASI)
    kode = umum["kode"]
    nama_operasi = OPERASI[kode][1].lower()
    petunjuk = _teks(post, "petunjuk", maks=240, label="Petunjuk")
    # Cerita dan nama kelompok diatur guru (seperti template soal cerita). Kosong = kalimat dan nama bawaan game.
    instruksi = _teks(post, "instruksi", maks=400, label="Teks soal cerita")
    label1 = _teks(post, "label1", maks=30, label="Nama kelompok 1")
    label2 = _teks(post, "label2", maks=30, label="Nama kelompok 2")

    if kode == "NM-BAGI":
        a = _angka(post, "a", "Bilangan yang dibagi", 0, 999)
        b = _angka(post, "b", "Pembagi (banyak kelompok)", 1, 99)
        if a % b != 0:
            raise SoalTidakValid(
                f"{a} tidak habis dibagi {b}. Pilih bilangan agar hasil bagi bulat tanpa sisa."
            )
    elif kode == "NM-KALI":
        a = _angka(post, "a", "Banyak kelompok", 0, 99)
        b = _angka(post, "b", "Isi tiap kelompok", 0, 99)
    else:
        a = _angka(post, "a", "Bilangan pertama", 0, 999)
        b = _angka(post, "b", "Bilangan kedua", 0, 999)
        if kode == "NM-KURANG" and a < b:
            raise SoalTidakValid("Pada pengurangan, bilangan pertama tidak boleh lebih kecil dari bilangan kedua.")
        if kode == "NM-TAMBAH" and a + b > 999:
            raise SoalTidakValid("Hasil penjumlahan maksimal 999.")

    preset = IKON_CERITA.get(str(post.get("ikon", "bintang")), IKON_CERITA["bintang"])
    data = {"a": a, "b": b, "label1": label1, "label2": label2, "icon": preset[1], "color": preset[2], "emoji": preset[3]}

    kunci_hitung = hitung_kunci(kode, data)
    kunci_isian = _angka(post, "kunci", "Jawaban benar", 0, 99999, wajib=False)
    if kunci_isian is not None and kunci_isian != kunci_hitung:
        raise SoalTidakValid(
            f"Jawaban benar tidak cocok dengan hitungan {nama_operasi}: seharusnya {kunci_hitung}."
        )

    return {
        "game": "numerasi",
        "kompetensi_kode": kode, "bagian": umum["bagian"], "level": umum["level"], "aktif": umum["aktif"],
        "instruksi": instruksi, "petunjuk": petunjuk, "kolom_kata": [], "jawaban": [], "jawaban_lain": [],
        "urutan_bebas": False, "kunci": kunci_hitung, "data": data, "gambar_baru": None,
    }


# ==========================================================
# 5. SERIALISASI KE FORMAT ASLI GAME
# ==========================================================

def _pola_salah(urutan):
    return "_".join(reversed(urutan))


def untuk_game1(soal, request=None):
    """Soal Game 1 dalam format array `questions` milik game."""
    kode = soal.kompetensi.kode
    url = soal.gambar.url if soal.gambar else ""
    if request is not None and url:
        url = request.build_absolute_uri(url)

    if kode == KOMPETENSI_BACA:
        d = soal.data or {}
        return {
            "id": soal.item_id,
            "tipe": "baca",
            "kompetensi": "baca_lantang",
            "kompetensiDashboard": kode,
            "jenisKompetensi": JENIS_KOMPETENSI_GAME1[kode],
            "level": soal.level,
            "gambar": url,
            "emoji": d.get("emoji") or EMOJI_BACA_BAWAAN,
            "kalimat": soal.instruksi,
        }

    pola = {}
    if not soal.urutan_bebas:
        for urutan in [soal.jawaban] + list(soal.jawaban_lain or []):
            if len(urutan) > 1:
                pola[_pola_salah(urutan)] = "SPO_TERBALIK"

    q = {
        "id": soal.item_id,
        "tipe": "susun",
        "kompetensi": "menyusun_kalimat",
        "kompetensiDashboard": kode,
        "jenisKompetensi": (
            "identifikasi_kategori_nama_bergambar" if soal.urutan_bebas else JENIS_KOMPETENSI_GAME1.get(kode, "susun_kalimat")
        ),
        "level": soal.level,
        "gambar": url,
        "instruksi": soal.instruksi,
        "correctOrder": list(soal.jawaban),
        "words": list(soal.kolom_kata),
        "errorPatterns": pola,
    }
    if soal.jawaban_lain:
        q["jawabanLain"] = [list(x) for x in soal.jawaban_lain]
    if soal.urutan_bebas:
        q["urutanBebas"] = True
    return q


def _html_deret(deret):
    return ", ".join(str(x) for x in deret)


def _hint_operasi(kode, a, b):
    """Petunjuk bawaan (tanpa membocorkan jawaban) bila guru tidak mengisi petunjuk."""
    if kode == "NM-TAMBAH":
        if a % 10 + b % 10 >= 10 and a >= 10:
            return f"Jumlahkan satuannya dulu. Jika lebih dari 9, simpan 1 puluhan lalu tambahkan {a // 10 * 10} dan {b // 10 * 10}."
        return f"Gabungkan {a} dan {b}. Mulai dari bilangan yang lebih besar, lalu tambahkan sisanya."
    if kode == "NM-KURANG":
        if a % 10 < b % 10:
            return f"Satuan {a % 10} kurang dari {b % 10}. Pinjam 1 puluhan menjadi 10 satuan, lalu kurangkan."
        return f"Kurangi {a} dengan {b}. Hitung mundur dari {a} sebanyak {b} langkah."
    if kode == "NM-KALI":
        return f"{a} \u00d7 {b} artinya {a} kelompok yang masing-masing berisi {b}. Jumlahkan {b} sebanyak {a} kali."
    return f"Bagi {a} rata ke {b} kelompok. Cari bilangan yang dikalikan {b} hasilnya {a}."


def untuk_game2(soal):
    """Soal Game 2 dalam format objek soal milik game (soalBank*)."""
    kode, d = soal.kompetensi.kode, soal.data or {}
    tipe, _kata, simbol, label1, label2 = OPERASI[kode]
    a, b = int(d["a"]), int(d["b"])
    tampil = "\u2212" if simbol == "-" else simbol  # tanda kurang yang rapi di teks
    q = {
        "id": soal.item_id, "kunciJawaban": soal.kunci,
        "tipe": tipe, "operasi": tipe, "operator": simbol,
        "teksSoal": markup_ke_html(soal.instruksi) if soal.instruksi
        else f"Hitung <b>{a} {tampil} {b}</b>. Berapa hasilnya?",
        "label1": d.get("label1") or label1, "label2": d.get("label2") or label2, "val1": a, "val2": b,
        "icon": d.get("icon") or "fa-star", "color": d.get("color") or "text-teal-500",
        "gambar": TWEMOJI.format(d["emoji"]) if d.get("emoji") else None,
        "hint": soal.petunjuk or _hint_operasi(kode, a, b),
    }
    return q


def serialkan(soal, request=None):
    return untuk_game1(soal, request) if soal.game == "literasi" else untuk_game2(soal)


# ==========================================================
# 6. PEMILIHAN SOAL UNTUK SATU PERCOBAAN
# ==========================================================

def soal_level(game, bagian, level, kompetensi=None):
    """Soal aktif sebuah level (game 1: semua kompetensi literasi; game 2: sesuai kompetensi bagian)."""
    qs = SoalGame.objects.filter(game=game, bagian=bagian, level=level, aktif=True).select_related("kompetensi")
    if game == "numerasi" and kompetensi is not None:
        qs = qs.filter(kompetensi=kompetensi)
    return list(qs.order_by("urutan", "id"))


def pilih_soal(game, bagian, level, kompetensi=None, benih=0):
    """Maksimal LENTERA_SOAL_PER_LEVEL soal; bila bank lebih banyak, dipilih acak yang tetap untuk `benih` yang sama.

    Level Literasi memadukan soal susun kalimat dan soal baca kalimat: bila bank level memuat
    keduanya, LENTERA_SOAL_BACA_PER_LEVEL soal berupa baca kalimat dan sisanya susun kalimat.
    """
    daftar = soal_level(game, bagian, level, kompetensi)
    maks = getattr(settings, "LENTERA_SOAL_PER_LEVEL", 5)
    if len(daftar) <= maks:
        return daftar

    acak = random.Random(benih)
    if game == "literasi":
        baca = [x for x in daftar if x.kompetensi.kode == KOMPETENSI_BACA]
        susun = [x for x in daftar if x.kompetensi.kode != KOMPETENSI_BACA]
        if baca and susun:
            n_baca = min(len(baca), getattr(settings, "LENTERA_SOAL_BACA_PER_LEVEL", 2), maks - 1)
            n_susun = min(len(susun), maks - n_baca)
            n_baca = min(len(baca), maks - n_susun)  # bank susun sedikit -> baca mengisi sisanya
            dipilih = acak.sample(baca, n_baca) + acak.sample(susun, n_susun)
            dipilih.sort(key=lambda x: (x.urutan, x.id))
            return dipilih

    dipilih = acak.sample(daftar, maks)
    dipilih.sort(key=lambda x: (x.urutan, x.id))
    return dipilih


# ==========================================================
# 7. RINGKASAN BANK UNTUK HALAMAN GURU
# ==========================================================

def ringkasan_bank():
    """{game: {bagian: {level: jumlah_aktif}}} ditambah jumlah nonaktif."""
    hasil = {"literasi": {}, "numerasi": {}}
    for game, bagian, level, aktif in SoalGame.objects.values_list("game", "bagian", "level", "aktif"):
        sel = hasil[game].setdefault(bagian, {}).setdefault(level, [0, 0])
        sel[0 if aktif else 1] += 1
    return hasil


def kompetensi_bagian_numerasi(bagian, kecuali_pk=None):
    """Kode kompetensi yang sudah memakai bagian Numerasi tertentu (satu bagian = satu kompetensi), atau None."""
    qs = SoalGame.objects.filter(game="numerasi", bagian=bagian)
    if kecuali_pk:
        qs = qs.exclude(pk=kecuali_pk)
    return qs.values_list("kompetensi__kode", flat=True).first()


# ==========================================================
# 8. TAMPILAN DAFTAR DAN FORM GURU
# ==========================================================

def ringkas(soal):
    """Ringkasan satu soal untuk daftar Bank Soal: (judul, detail, jawaban)."""
    kode, d = soal.kompetensi.kode, soal.data or {}
    if soal.game == "literasi":
        if kode == KOMPETENSI_BACA:
            return (soal.instruksi, f"Baca kalimat (mikrofon) {d.get('emoji') or EMOJI_BACA_BAWAAN}", "dibaca lantang")
        return (
            soal.instruksi,
            "Kolom kata: " + ", ".join(soal.kolom_kata),
            " ".join(soal.jawaban) + (" (urutan bebas)" if soal.urutan_bebas else ""),
        )
    tipe, kata, simbol, _l1, _l2 = OPERASI[kode]
    ujaran = f"{d.get('a')} {simbol} {d.get('b')}"
    if soal.instruksi:
        ujaran += f"  ({soal.instruksi.replace('**', '')[:60]})"
    return (ujaran, kata, str(soal.kunci))


def jenis_dan_thumb(soal):
    """(jenis soal, teks pengganti gambar kecil) untuk daftar Bank Soal."""
    kode, d = soal.kompetensi.kode, soal.data or {}
    if soal.game == "literasi":
        if kode == KOMPETENSI_BACA:
            return "Baca kalimat", d.get("emoji") or EMOJI_BACA_BAWAAN
        return "Susun kalimat", "\U0001F9E9"
    simbol = OPERASI[kode][2]
    return OPERASI[kode][1], ("\u2212" if simbol == "-" else simbol)


def nilai_form(soal):
    """Nilai isian form untuk soal yang sudah ada (mode ubah)."""
    d = soal.data or {}
    nilai = {
        "kompetensi": soal.kompetensi.kode, "bagian": soal.bagian, "level": soal.level, "aktif": "1" if soal.aktif else "0",
        "instruksi": soal.instruksi, "petunjuk": soal.petunjuk, "kunci": "" if soal.kunci is None else soal.kunci,
        "kalimat": soal.instruksi if soal.kompetensi.kode == KOMPETENSI_BACA else "",
        "kolom_kata": "\n".join(soal.kolom_kata), "jawaban": "\n".join(soal.jawaban),
        "jawaban_lain": "\n".join(", ".join(x) for x in soal.jawaban_lain or []),
        "urutan_bebas": "1" if soal.urutan_bebas else "",
        "a": d.get("a", ""), "b": d.get("b", ""),
        "label1": d.get("label1", ""), "label2": d.get("label2", ""),
        "emoji": d.get("emoji", "") if soal.game == "literasi" else "",
        "ikon": next((k for k, v in IKON_CERITA.items() if v[3] == d.get("emoji")), "bintang"),
    }
    return nilai

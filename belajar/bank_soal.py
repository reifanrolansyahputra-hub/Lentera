"""
Bank soal Game 1 (Literasi) dan Game 2 (Numerasi) yang dikelola guru.

Isi modul:
- validasi isian form guru (Game 1: gambar + kolom kata + jawaban; Game 2: angka + jawaban),
- perhitungan jawaban otomatis untuk jenis soal yang bisa dihitung,
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

KODE_LITERASI = ("LT-KALIMAT", "LT-KOSAKATA", "LT-PAHAM", "LT-LANCAR")
KODE_NUMERASI = ("NM-NILAI-TEMPAT", "NM-REGROUPING", "NM-POLA", "NM-CERITA")

# Kompetensi utama misi Game 1 (satu level Game 1 memuat campuran kompetensi literasi).
KOMPETENSI_UTAMA_LITERASI = "LT-KALIMAT"

# Urutan bagian bawaan Numerasi (1 kompetensi = 1 bagian).
BAGIAN_NUMERASI_BAWAAN = {"NM-NILAI-TEMPAT": 1, "NM-REGROUPING": 2, "NM-POLA": 3, "NM-CERITA": 4}

JENIS_KOMPETENSI_GAME1 = {
    "LT-KALIMAT": "susun_kalimat",
    "LT-KOSAKATA": "kosakata_deskriptif_bergambar",
    "LT-PAHAM": "pemahaman_konteks_visual_bukan_bacaan_teks",
    "LT-LANCAR": "membaca_kata_kalimat",
}

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


def hitung_kunci(kode, data):
    """Jawaban otomatis untuk jenis soal yang bisa dihitung; None untuk soal cerita/pola."""
    if kode == "NM-NILAI-TEMPAT":
        return data["puluhan"] * 10 + data["satuan"]
    if kode == "NM-REGROUPING":
        return data["a"] + data["b"] if data["op"] == "+" else data["a"] - data["b"]
    return None


def bersihkan_game2(post):
    """Isian Game 2 sesuai jenis soal (kompetensi). Mengembalikan dict field SoalGame."""
    umum = _umum(post, KODE_NUMERASI)
    kode = umum["kode"]
    petunjuk = _teks(post, "petunjuk", maks=240, label="Petunjuk")
    instruksi, data = "", {}

    if kode == "NM-NILAI-TEMPAT":
        data = {"puluhan": _angka(post, "puluhan", "Ikat puluhan", 1, 20), "satuan": _angka(post, "satuan", "Satuan", 0, 9)}
    elif kode == "NM-REGROUPING":
        op = str(post.get("op", "+")).strip()
        if op not in ("+", "-"):
            raise SoalTidakValid("Operasi harus + atau -.")
        a, b = _angka(post, "a", "Bilangan pertama", 0, 999), _angka(post, "b", "Bilangan kedua", 0, 999)
        if op == "-" and a < b:
            raise SoalTidakValid("Pada pengurangan, bilangan pertama tidak boleh lebih kecil dari bilangan kedua.")
        data = {"a": a, "op": op, "b": b}
    elif kode == "NM-POLA":
        mentah = re.split(r"[,\s]+", str(post.get("deret", "")).strip())
        try:
            deret = [int(x) for x in mentah if x != ""]
        except ValueError:
            raise SoalTidakValid("Deret bilangan hanya boleh berisi angka, dipisah koma atau spasi.")
        if not 3 <= len(deret) <= 8:
            raise SoalTidakValid("Deret bilangan harus berisi 3 sampai 8 angka.")
        if any(abs(x) > 9999 for x in deret):
            raise SoalTidakValid("Angka pada deret maksimal 9999.")
        data = {"deret": deret}
    else:  # NM-CERITA
        instruksi = _teks(post, "instruksi", wajib=True, maks=400, label="Teks soal cerita")
        preset = IKON_CERITA.get(str(post.get("ikon", "bintang")), IKON_CERITA["bintang"])
        data = {
            "label1": _teks(post, "label1", maks=30, label="Label kelompok 1") or "Kelompok A",
            "val1": _angka(post, "val1", "Jumlah kelompok 1", 0, 500, wajib=False),
            "label2": _teks(post, "label2", maks=30, label="Label kelompok 2") or "Kelompok B",
            "val2": _angka(post, "val2", "Jumlah kelompok 2", 0, 500, wajib=False),
            "icon": preset[1], "color": preset[2], "emoji": preset[3],
            "langkah": _angka(post, "langkah", "Jumlah langkah", 1, 3, wajib=False) or 1,
        }

    kunci_hitung = hitung_kunci(kode, data)
    kunci_isian = _angka(post, "kunci", "Jawaban benar", -9999, 99999, wajib=kunci_hitung is None)
    if kunci_hitung is not None and kunci_isian is not None and kunci_isian != kunci_hitung:
        raise SoalTidakValid(f"Jawaban benar tidak cocok dengan hitungan soal: seharusnya {kunci_hitung}.")
    kunci = kunci_hitung if kunci_hitung is not None else kunci_isian
    if kunci < 0:
        raise SoalTidakValid("Jawaban benar tidak boleh negatif (papan angka game hanya mendukung bilangan bulat tak-negatif).")

    return {
        "game": "numerasi",
        "kompetensi_kode": kode, "bagian": umum["bagian"], "level": umum["level"], "aktif": umum["aktif"],
        "instruksi": instruksi, "petunjuk": petunjuk, "kolom_kata": [], "jawaban": [], "jawaban_lain": [],
        "urutan_bebas": False, "kunci": kunci, "data": data, "gambar_baru": None,
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

    pola = {}
    if not soal.urutan_bebas:
        for urutan in [soal.jawaban] + list(soal.jawaban_lain or []):
            if len(urutan) > 1:
                pola[_pola_salah(urutan)] = "SPO_TERBALIK"

    q = {
        "id": soal.item_id,
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


def untuk_game2(soal):
    """Soal Game 2 dalam format objek soal milik game (soalBank*)."""
    kode, d = soal.kompetensi.kode, soal.data or {}
    q = {"id": soal.item_id, "kunciJawaban": soal.kunci, "gambar": None}

    if kode == "NM-NILAI-TEMPAT":
        p, s = d["puluhan"], d["satuan"]
        q.update(
            tipe="nilai_tempat",
            teksSoal=f"Ada <b>{p} ikat puluhan</b> dan <b>{s} satuan</b>. Jika satu ikat berisi 10 benda, berapa jumlah semuanya?",
            label1="Ikat puluhan", label2="Satuan", val1=p, val2=s,
            icon="fa-cubes", color="text-amber-500",
            hint=soal.petunjuk or f"{p} puluhan bernilai {p * 10}. Tambahkan {s} satuan.",
        )
    elif kode == "NM-REGROUPING":
        a, op, b = d["a"], d["op"], d["b"]
        q.update(
            tipe="regrouping", operasi="penjumlahan" if op == "+" else "pengurangan", operator=op,
            teksSoal=f"Hitung <b>{a} {op} {b}</b>. Berapa hasilnya?",
            label1="Bilangan pertama", label2="Bilangan kedua", val1=a, val2=b,
            icon="fa-calculator", color="text-teal-500",
            hint=soal.petunjuk or (
                f"Jumlahkan {a} dengan {b}. Periksa apakah satuan mencapai 10 sehingga perlu menyimpan 1 puluhan."
                if op == "+" else f"Kurangi {a} dengan {b}. Jika satuan tidak cukup, pinjam 1 puluhan."
            ),
        )
    elif kode == "NM-POLA":
        deret = d["deret"]
        q.update(
            tipe="pola", deret=list(deret),
            teksSoal=f"Perhatikan pola bilangan berikut: <b>{_html_deret(deret)}, ...</b><br>Angka berikutnya adalah?",
            label1="Deret bilangan", label2="Angka berikutnya", val1=len(deret), val2=2,
            icon="fa-arrow-trend-up", color="text-violet-500",
            hint=soal.petunjuk or "Perhatikan selisih atau perkalian antar angka yang berurutan.",
        )
    else:  # NM-CERITA
        q.update(
            teksSoal=markup_ke_html(soal.instruksi),
            label1=d.get("label1") or "Kelompok A", label2=d.get("label2") or "Kelompok B",
            val1=d.get("val1"), val2=d.get("val2"),
            icon=d.get("icon") or "fa-star", color=d.get("color") or "text-teal-500",
            gambar=TWEMOJI.format(d["emoji"]) if d.get("emoji") else None,
            hint=soal.petunjuk or "Baca soal pelan-pelan, lalu pilih operasi hitung yang tepat.",
        )
        langkah = int(d.get("langkah") or 1)
        if langkah >= 2:
            q["tipe"] = "cerita_dua_langkah"
            q["jumlahLangkah"] = langkah
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
    """Maksimal LENTERA_SOAL_PER_LEVEL soal; bila bank lebih banyak, dipilih acak yang tetap untuk `benih` yang sama."""
    daftar = soal_level(game, bagian, level, kompetensi)
    maks = getattr(settings, "LENTERA_SOAL_PER_LEVEL", 5)
    if len(daftar) > maks:
        dipilih = random.Random(benih).sample(daftar, maks)
        dipilih.sort(key=lambda s: (s.urutan, s.id))
        return dipilih
    return daftar


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
        return (
            soal.instruksi,
            "Kolom kata: " + ", ".join(soal.kolom_kata),
            " ".join(soal.jawaban) + (" (urutan bebas)" if soal.urutan_bebas else ""),
        )
    if kode == "NM-NILAI-TEMPAT":
        return (f"{d.get('puluhan')} ikat puluhan dan {d.get('satuan')} satuan", "Nilai tempat", str(soal.kunci))
    if kode == "NM-REGROUPING":
        return (f"{d.get('a')} {d.get('op')} {d.get('b')}", "Simpan dan pinjam", str(soal.kunci))
    if kode == "NM-POLA":
        return (", ".join(str(x) for x in d.get("deret", [])) + ", ...", "Pola bilangan", str(soal.kunci))
    return (soal.instruksi.replace("**", ""), "Soal cerita", str(soal.kunci))


def nilai_form(soal):
    """Nilai isian form untuk soal yang sudah ada (mode ubah)."""
    d = soal.data or {}
    nilai = {
        "kompetensi": soal.kompetensi.kode, "bagian": soal.bagian, "level": soal.level, "aktif": "1" if soal.aktif else "0",
        "instruksi": soal.instruksi, "petunjuk": soal.petunjuk, "kunci": "" if soal.kunci is None else soal.kunci,
        "kolom_kata": "\n".join(soal.kolom_kata), "jawaban": "\n".join(soal.jawaban),
        "jawaban_lain": "\n".join(", ".join(x) for x in soal.jawaban_lain or []),
        "urutan_bebas": "1" if soal.urutan_bebas else "",
        "puluhan": d.get("puluhan", ""), "satuan": d.get("satuan", ""),
        "a": d.get("a", ""), "op": d.get("op", "+"), "b": d.get("b", ""),
        "deret": ", ".join(str(x) for x in d.get("deret", [])),
        "label1": d.get("label1", ""), "val1": "" if d.get("val1") is None else d.get("val1"),
        "label2": d.get("label2", ""), "val2": "" if d.get("val2") is None else d.get("val2"),
        "langkah": d.get("langkah", 1),
        "ikon": next((k for k, v in IKON_CERITA.items() if v[3] == d.get("emoji")), "bintang"),
    }
    return nilai


import os
from pathlib import Path


# ==========================================================
# KONFIGURASI DASAR DJANGO
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "dev-ganti-di-produksi",
)

DEBUG = os.getenv("DEBUG", "1") == "1"

ALLOWED_HOSTS = os.getenv(
    "ALLOWED_HOSTS",
    "*",
).split(",")


# ==========================================================
# APLIKASI
# ==========================================================

INSTALLED_APPS = [
    "accounts",
    "belajar",

    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]


# ==========================================================
# MIDDLEWARE
# ==========================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "belajar.cors.GameCorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ==========================================================
# URL DAN TEMPLATE
# ==========================================================

ROOT_URLCONF = "lentera.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "lentera.wsgi.application"


# ==========================================================
# DATABASE
# ==========================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ==========================================================
# BAHASA DAN WAKTU
# ==========================================================

LANGUAGE_CODE = "id"
TIME_ZONE = "Asia/Jakarta"

USE_I18N = True
USE_TZ = True


# ==========================================================
# FILE STATIS DAN MODEL
# ==========================================================

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Gambar soal Game 1 yang diunggah guru (dan gambar soal bawaan) disimpan di sini.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Batas ukuran gambar soal yang diunggah guru.
LENTERA_GAMBAR_MAKS_MB = 5

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"

LENTERA_SEKOLAH = "SD Muhammadiyah Bekasi"


# ==========================================================
# INTEGRASI GAME LENTERA
# ==========================================================

# Secret untuk menandatangani dan memverifikasi token JWT.
# Pada deployment, atur melalui environment variable.
LENTERA_GAME_SECRET = os.getenv(
    "LENTERA_GAME_SECRET",
    "dev-only-lentera-game-secret-change-me-2026",
)

# Masa berlaku token game: 30 menit.
LENTERA_GAME_TOKEN_TTL = 1800

# Masa toleransi token untuk menerima event setelah kedaluwarsa.
LENTERA_GAME_EVENT_GRACE = 7 * 86400


# ==========================================================
# JALAN LENTERA, BATAS WAKTU, DAN PETA KOMPETENSI
# ==========================================================

# Jumlah soal (= jumlah lampu) pada setiap level, dan jumlah level per bagian.
LENTERA_SOAL_PER_LEVEL = 5
LENTERA_LEVEL_PER_BAGIAN = 5

# Level Literasi memadukan soal susun kalimat dan soal baca kalimat (mikrofon).
# Bila bank level memuat keduanya, sebanyak ini dari soal per level berupa baca kalimat.
LENTERA_SOAL_BACA_PER_LEVEL = 2

# Aturan batas waktu game. Waktu dasar = menit pada Bank Aktivitas.
# Setelah waktu dasar habis, poin dikurangi setiap `tick` detik.
# Bila keterlambatan melewati `maks_lewat` detik, level dinyatakan GAGAL.
#   contoh (waktu 8 menit): 8:10 -5 poin, 8:20 -5 poin, 8:30 -5 poin, lewat 8:30 = gagal.
LENTERA_WAKTU = {
    "tick": 10,
    "maks_lewat": 30,
    "penalti": 5,
}

# Peta Kompetensi baru mencapai 100% bila murid menyelesaikan sejumlah bagian.
# Jumlahnya diatur guru per kelas di menu Pengaturan (Kelas.target_bagian, bawaan 3).


# ==========================================================
# ORIGIN YANG BOLEH MENGAKSES API GAME
# ==========================================================

# Game 1 menggunakan Flask.
# Game 2 menggunakan Live Server.
# Pisahkan beberapa origin menggunakan koma.
LENTERA_GAME_ORIGIN = os.getenv(
    "LENTERA_GAME_ORIGIN",
    "http://127.0.0.1:5000,http://127.0.0.1:5500,http://127.0.0.1:5501",
)


# ==========================================================
# GAME 1 — LITERASI
# ==========================================================

URL_GAME_MISI1 = os.getenv(
    "GAME_MISI1_URL",
    "http://127.0.0.1:5000/level.html",
)


# ==========================================================
# GAME 2 — NUMERASI
# ==========================================================

URL_GAME_MISI2 = os.getenv(
    "GAME_MISI2_URL",
    "http://127.0.0.1:5501/Misi-2-lentera/index.html",
)


# ==========================================================
# PEMETAAN KOMPETENSI KE GAME
# ==========================================================

LENTERA_GAMES = {
    # ------------------------------------------------------
    # GAME 1 — LITERASI
    # Satu aplikasi Game 1 yang sama. Susun kalimat dan baca
    # kalimat (mikrofon) adalah satu kesatuan: satu level memuat
    # kedua jenis soal, tanpa pilihan game terpisah.
    # ------------------------------------------------------

    "LT-KOSAKATA": URL_GAME_MISI1,
    "LT-LANCAR": URL_GAME_MISI1,
    "LT-KETEPATAN": URL_GAME_MISI1,
    "LT-KALIMAT": URL_GAME_MISI1,

    # ------------------------------------------------------
    # GAME 2 — NUMERASI
    # Empat operasi hitung: satu kompetensi = satu bagian.
    # ------------------------------------------------------

    "NM-TAMBAH": URL_GAME_MISI2,
    "NM-KURANG": URL_GAME_MISI2,
    "NM-KALI": URL_GAME_MISI2,
    "NM-BAGI": URL_GAME_MISI2,
}

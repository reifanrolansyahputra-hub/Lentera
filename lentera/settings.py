
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
# ORIGIN YANG BOLEH MENGAKSES API GAME
# ==========================================================

# Game 1 menggunakan Flask.
# Game 2 menggunakan Live Server.
# Pisahkan beberapa origin menggunakan koma.
LENTERA_GAME_ORIGIN = os.getenv(
    "LENTERA_GAME_ORIGIN",
    "http://127.0.0.1:5000,http://127.0.0.1:5500",
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
    "http://127.0.0.1:5500/Misi-2-lentera/index.html",
)


# ==========================================================
# PEMETAAN KOMPETENSI KE GAME
# ==========================================================

LENTERA_GAMES = {
    # ------------------------------------------------------
    # GAME 1 — LITERASI
    # Menggunakan aplikasi Game 1 yang sama.
    # Pemilihan soal per kompetensi diatur pada kode Game 1.
    # ------------------------------------------------------

    "LT-KOSAKATA": URL_GAME_MISI1,
    "LT-LANCAR": URL_GAME_MISI1,
    "LT-PAHAM": URL_GAME_MISI1,
    "LT-KALIMAT": URL_GAME_MISI1,

    # ------------------------------------------------------
    # GAME 2 — NUMERASI
    # Seluruh pemetaan numerasi lama dipertahankan.
    # ------------------------------------------------------

    "NM-CERITA": URL_GAME_MISI2,
    "NM-NILAI-TEMPAT": URL_GAME_MISI2,
    "NM-REGROUPING": URL_GAME_MISI2,
    "NM-POLA": URL_GAME_MISI2,
}

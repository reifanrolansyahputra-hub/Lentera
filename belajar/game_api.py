
"""
API integrasi Game LENTERA.

Game yang didukung:
- Game 1: Literasi
- Game 2: Numerasi

Fitur:
- Token sesi siswa
- Validasi kompetensi dan level
- Pencatatan jawaban ke dashboard
- Pembaruan penguasaan BKT
- Pencegahan event duplikat
- Penyelesaian misi berdasarkan jumlah soal yang tersedia
"""

import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jwt

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.utils import role_required
from . import engine
from .models import Kompetensi, Jawaban, Misi, Penguasaan, Siswa
from .services import catat_jawaban, selesaikan_diagnostik


# ==========================================================
# 1. PEMETAAN KODE KOMPETENSI
# ==========================================================

ALIAS_KOMPETENSI = {
    **getattr(engine, "ALIAS_GAME", {}),
    "menyusun_kalimat": "LT-KALIMAT",
}


def normalisasi_kompetensi(kode):
    """Normalisasi kode kompetensi lama dari game."""
    kode = str(kode or "").strip()
    return ALIAS_KOMPETENSI.get(kode, kode)


# ==========================================================
# 2. JUMLAH SOAL TERSEDIA PER KOMPETENSI DAN LEVEL
# ==========================================================

JUMLAH_SOAL_LITERASI = {
    "LT-KALIMAT": {
        1: 5,
        2: 2,
        3: 3,
        4: 3,
        5: 4,
    },
    "LT-KOSAKATA": {
        1: 0,
        2: 1,
        3: 2,
        4: 2,
        5: 1,
    },
    "LT-PAHAM": {
        1: 0,
        2: 2,
        3: 0,
        4: 0,
        5: 0,
    },
    # Bank soal lama belum mengukur kelancaran membaca
    # lisan secara langsung.
    "LT-LANCAR": {},
}

KOMPETENSI_NUMERASI = {
    "NM-CERITA",
    "NM-NILAI-TEMPAT",
    "NM-REGROUPING",
    "NM-POLA",
}



def _jumlah_soal_wajib(misi):
    """
    Semua kompetensi literasi menggunakan Game 1.
    Semua kompetensi numerasi menggunakan Game 2.

    Setiap level menggunakan 5 soal asli dari game
    yang sesuai. Tidak ada penambahan bank soal baru.

    Jumlah soal dapat diatur melalui
    LENTERA_GAME_REQUIRED_ITEMS di settings.py.
    """
    kode = misi.kompetensi.kode
    level = int(misi.level)

    # Level permainan yang didukung adalah 1 sampai 5.
    if level < 1 or level > 5:
        return 0

    # Kompetensi harus dipetakan ke salah satu dari dua game.
    game_map = getattr(
        settings,
        "LENTERA_GAMES",
        {},
    )

    if kode not in game_map:
        return 0

    # Hanya menerima ranah yang digunakan dashboard.
    if misi.kompetensi.ranah not in ("literasi", "numerasi"):
        return 0

    # Konfigurasi opsional untuk jumlah soal berbeda.
    konfigurasi = getattr(
        settings,
        "LENTERA_GAME_REQUIRED_ITEMS",
        {},
    )

    override = (
        konfigurasi.get(kode)
        if isinstance(konfigurasi, dict)
        else None
    )

    if isinstance(override, dict):
        try:
            jumlah = int(
                override.get(
                    level,
                    override.get(str(level), 5),
                )
            )
        except (ValueError, TypeError):
            raise ValueError(
                f"Jumlah soal {kode} level {level} tidak valid."
            )

        return max(0, jumlah)

    if override is not None:
        try:
            jumlah = int(override)
        except (ValueError, TypeError):
            raise ValueError(
                f"Jumlah soal {kode} tidak valid."
            )

        return max(0, jumlah)

    # Default: 5 soal untuk setiap level kompetensi.
    return 5



# ==========================================================
# 3. TOKEN DAN PEMBUKAAN GAME
# ==========================================================

@role_required("murid")
def mulai_game(request, misi_id):
    misi = get_object_or_404(
        Misi.objects.select_related("siswa", "kompetensi"),
        id=misi_id,
        siswa=request.user.siswa,
        status="baru",
    )

    # Jangan membuka kompetensi-level yang belum punya soal.
    jumlah_wajib = _jumlah_soal_wajib(misi)

    if jumlah_wajib <= 0:
        return JsonResponse(
            {
                "error": (
                    f"Belum ada soal untuk {misi.kompetensi.kode} "
                    f"pada Level {misi.level}. "
                    "Pilih aktivitas lain yang tersedia."
                )
            },
            status=409,
        )

    # Hanya Game 1 atau Game 2 yang boleh dibuka.
    url_game = settings.LENTERA_GAMES.get(
        misi.kompetensi.kode
    )

    if not url_game:
        return JsonResponse(
            {
                "error": (
                    f"Kompetensi {misi.kompetensi.kode} belum "
                    "dipetakan ke Game 1 atau Game 2."
                )
            },
            status=400,
        )

    payload = {
        "sub": misi.siswa.kode_samaran,
        "nama": misi.siswa.nama,
        "misi": misi.id,
        "kompetensi": misi.kompetensi.kode,
        "ranah": misi.kompetensi.ranah,
        "level": misi.level,
        "mode": misi.jenis,
        "home": request.build_absolute_uri("/murid/"),
        "logout": request.build_absolute_uri("/logout/"),
        "api": request.build_absolute_uri("/api/game/event/"),
        "exp": int(time.time()) + settings.LENTERA_GAME_TOKEN_TTL,
    }

    token = jwt.encode(
        payload,
        settings.LENTERA_GAME_SECRET,
        algorithm="HS256",
    )

    # Pertahankan query parameter URL yang sudah ada.
    parsed = urlsplit(url_game)

    query = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    query["ssoToken"] = token

    tujuan = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )

    return redirect(tujuan)


# ==========================================================
# 4. NORMALISASI WAKTU, BOOLEAN, DAN PENGUASAAN
# ==========================================================

def _waktu(value):
    if not value or not isinstance(value, str):
        return None

    try:
        waktu = parse_datetime(value)
    except (ValueError, TypeError):
        return None

    if waktu is None:
        return None

    if timezone.is_naive(waktu):
        return timezone.make_aware(waktu)

    return waktu


def _boolean(value, default=None):
    """Parsing boolean secara ketat."""
    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in (0, 1):
        return bool(value)

    if isinstance(value, str):
        nilai = value.strip().lower()

        if nilai == "true":
            return True

        if nilai == "false":
            return False

    if value is None:
        return default

    raise ValueError("Nilai boolean tidak valid.")


def _state_siswa(siswa, kompetensi):
    """Mengambil kondisi penguasaan terbaru."""
    penguasaan = Penguasaan.objects.filter(
        siswa=siswa,
        kompetensi=kompetensi,
    ).first()

    if penguasaan is None:
        return {
            "p_l": round(engine.P_AWAL, 3),
            "status": engine.status(engine.P_AWAL),
            "level": 1,
        }

    return {
        "p_l": round(penguasaan.p_l, 3),
        "status": penguasaan.status,
        "level": penguasaan.level,
    }


# ==========================================================
# 5. NORMALISASI EVENT DARI GAME
# ==========================================================

def _norm(event, misi):
    """
    Menerima event Game 1 dan Game 2.

    Field yang didukung:
    - kompetensi / competency
    - benar / is_correct
    - item_id / question_id
    - pola / error_code / error_pattern
    - waktu_ms / response_time_ms / response_time_seconds
    - petunjuk / used_hint
    """
    if not isinstance(event, dict):
        raise ValueError(
            "Setiap event harus berbentuk objek JSON."
        )

    kode = (
        event.get("kompetensi")
        or event.get("competency")
        or misi.kompetensi.kode
    )

    kode = normalisasi_kompetensi(kode)

    if kode != misi.kompetensi.kode:
        raise ValueError(
            "Kompetensi event tidak cocok dengan kompetensi misi."
        )

    try:
        level = int(event.get("level", misi.level))
    except (TypeError, ValueError):
        raise ValueError("Level soal tidak valid.")

    if level != misi.level or not 1 <= level <= 5:
        raise ValueError(
            "Level event tidak cocok dengan level misi."
        )

    benar = _boolean(
        event.get("benar", event.get("is_correct")),
        default=None,
    )

    if benar is None:
        raise ValueError("Field benar tidak tersedia.")

    item_id = str(
        event.get("item_id")
        or event.get("question_id")
        or ""
    ).strip()

    if len(item_id) > 40:
        raise ValueError("ID soal terlalu panjang.")

    raw_ms = event.get("waktu_ms")

    if raw_ms is None:
        raw_ms = event.get("response_time_ms")

    if raw_ms is None:
        try:
            raw_ms = round(
                float(event.get("response_time_seconds", 0)) * 1000
            )
        except (TypeError, ValueError, OverflowError):
            raw_ms = 0

    try:
        waktu_ms = int(raw_ms)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Waktu pengerjaan tidak valid.")

    if waktu_ms < 0:
        raise ValueError(
            "Waktu pengerjaan tidak boleh negatif."
        )

    waktu_ms = min(
        waktu_ms,
        24 * 60 * 60 * 1000,
    )

    event_id = str(event.get("event_id") or "").strip()

    if len(event_id) > 64:
        raise ValueError("ID event terlalu panjang.")

    pola = str(
        event.get("pola")
        or event.get("error_code")
        or event.get("error_pattern")
        or ""
    ).strip()

    if len(pola) > 30:
        pola = ""

    definisi_pola = getattr(engine, "POLA", {})

    if pola not in definisi_pola:
        pola = ""
    elif definisi_pola[pola][1] != kode:
        pola = ""

    petunjuk = _boolean(
        event.get("petunjuk", event.get("used_hint")),
        default=False,
    )

    return {
        "kode": kode,
        "benar": benar,
        "level": level,
        "item_id": item_id,
        "pola": pola,
        "waktu_ms": waktu_ms,
        "petunjuk": petunjuk,
        "event_id": event_id,
        "waktu": _waktu(event.get("waktu")),
    }


# ==========================================================
# 6. VALIDASI PENYELESAIAN MISI
# ==========================================================

def _boleh_selesai(misi, events=None):
    """
    Misi selesai ketika jumlah ID soal unik dengan jawaban
    benar yang sudah tersimpan mencapai jumlah wajib.
    """
    jumlah_wajib = _jumlah_soal_wajib(misi)

    if jumlah_wajib <= 0:
        return False

    jumlah_benar = (
        Jawaban.objects.filter(
            misi=misi,
            kompetensi=misi.kompetensi,
            benar=True,
        )
        .exclude(item_id="")
        .values("item_id")
        .distinct()
        .count()
    )

    return jumlah_benar >= jumlah_wajib


# ==========================================================
# 7. API PENERIMA EVENT GAME
# ==========================================================

@csrf_exempt
@require_POST
def api_event(request):
    if len(request.body) > 256 * 1024:
        return JsonResponse(
            {"error": "Ukuran permintaan terlalu besar."},
            status=413,
        )

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "JSON permintaan tidak valid."},
            status=400,
        )

    if not isinstance(data, dict):
        return JsonResponse(
            {"error": "Body harus berupa objek JSON."},
            status=400,
        )

    try:
        selesai_diminta = _boolean(
            data.get("selesai"),
            default=False,
        )
    except ValueError:
        return JsonResponse(
            {"error": "Field selesai harus berupa boolean."},
            status=400,
        )

    # Token utama menggunakan Authorization Bearer.
    # Dukungan ssoToken di body dipertahankan untuk kompatibilitas.
    authorization = request.headers.get(
        "Authorization",
        "",
    ).strip()

    if authorization:
        if not authorization.startswith("Bearer "):
            return JsonResponse(
                {"error": "Format Authorization tidak valid."},
                status=401,
            )

        token = authorization[7:].strip()
    else:
        token = str(data.get("ssoToken") or "").strip()

    if not token:
        return JsonResponse(
            {"error": "Token misi tidak tersedia."},
            status=401,
        )

    try:
        claims = jwt.decode(
            token,
            settings.LENTERA_GAME_SECRET,
            algorithms=["HS256"],
            leeway=getattr(
                settings,
                "LENTERA_GAME_EVENT_GRACE",
                0,
            ),
        )

        misi_id = int(claims["misi"])
        kode_token = normalisasi_kompetensi(
            claims["kompetensi"]
        )
        level_token = int(claims["level"])
        sub = str(claims["sub"])

    except (
        jwt.PyJWTError,
        KeyError,
        ValueError,
        TypeError,
    ):
        return JsonResponse(
            {"error": "Token misi tidak valid atau kedaluwarsa."},
            status=401,
        )

    events_raw = data.get("events")

    # Mendukung event tunggal dari Game 1 versi lama.
    if events_raw is None:
        if "item_id" in data or "question_id" in data:
            events_raw = [data]
        else:
            events_raw = []

    if not isinstance(events_raw, list):
        return JsonResponse(
            {"error": "Field events harus berupa array."},
            status=400,
        )

    if len(events_raw) > 20:
        return JsonResponse(
            {"error": "Maksimal 20 event per permintaan."},
            status=400,
        )

    try:
        with transaction.atomic():
            misi = (
                Misi.objects.select_for_update()
                .select_related("siswa", "kompetensi")
                .get(
                    id=misi_id,
                    siswa__kode_samaran=sub,
                )
            )

            if (
                misi.kompetensi.kode != kode_token
                or misi.level != level_token
            ):
                return JsonResponse(
                    {
                        "error": (
                            "Token tidak cocok dengan "
                            "kompetensi atau level misi."
                        )
                    },
                    status=403,
                )

            if _jumlah_soal_wajib(misi) <= 0:
                return JsonResponse(
                    {
                        "error": (
                            "Kompetensi dan level ini belum "
                            "memiliki soal yang tersedia."
                        )
                    },
                    status=409,
                )

            # Semua event divalidasi terlebih dahulu.
            events = [
                _norm(event, misi)
                for event in events_raw
            ]

            if misi.status == "selesai":
                if selesai_diminta and not events:
                    return JsonResponse({
                        "state": _state_siswa(
                            misi.siswa,
                            misi.kompetensi,
                        ),
                        "selesai": True,
                    })

                semua_event_sudah_ada = bool(events) and all(
                    event["event_id"]
                    and Jawaban.objects.filter(
                        event_id=event["event_id"],
                    ).exists()
                    for event in events
                )

                if semua_event_sudah_ada:
                    return JsonResponse({
                        "state": _state_siswa(
                            misi.siswa,
                            misi.kompetensi,
                        ),
                        "selesai": True,
                    })

                return JsonResponse(
                    {"error": "Misi ini sudah selesai."},
                    status=409,
                )

            # Simpan jawaban.
            for event in events:
                try:
                    with transaction.atomic():
                        catat_jawaban(
                            misi.siswa,
                            event["kode"],
                            event["benar"],
                            event["level"],
                            event["item_id"],
                            event["pola"],
                            event["waktu_ms"],
                            event["petunjuk"],
                            event["waktu"],
                            event["event_id"],
                            misi=misi,
                        )
                except IntegrityError:
                    # Event yang sama sudah tersimpan.
                    continue

            state = _state_siswa(
                misi.siswa,
                misi.kompetensi,
            )

            if selesai_diminta:
                if not _boleh_selesai(misi, events):
                    return JsonResponse({
                        "state": state,
                        "selesai": False,
                        "error": (
                            "Belum seluruh soal wajib dijawab "
                            "benar. Misi belum ditandai selesai."
                        ),
                    })

                misi.status = "selesai"
                misi.save(update_fields=["status"])

                if misi.jenis == "diagnostik":
                    selesaikan_diagnostik(misi)
                else:
                    Siswa.objects.filter(
                        id=misi.siswa_id,
                    ).update(
                        poin=F("poin") + 10
                    )

            return JsonResponse({
                "state": _state_siswa(
                    misi.siswa,
                    misi.kompetensi,
                ),
                "selesai": misi.status == "selesai",
            })

    except Misi.DoesNotExist:
        return JsonResponse(
            {"error": "Misi tidak ditemukan."},
            status=404,
        )

    except Kompetensi.DoesNotExist:
        return JsonResponse(
            {"error": "Kode kompetensi tidak dikenal."},
            status=400,
        )

    except (ValueError, TypeError, OverflowError) as error:
        return JsonResponse(
            {"error": str(error)},
            status=400,
        )

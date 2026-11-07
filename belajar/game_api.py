"""
API integrasi Game LENTERA (Game 1: Literasi, Game 2: Numerasi).

Alur sebuah level:
  1. Murid menekan "Mulai misi" di dashboard      -> GET /game/mulai/<misi>/  (membuat token JWT, lalu mengalihkan ke game)
  2. Game mengambil soal dari bank guru            -> GET  /api/game/soal/     (Authorization: Bearer <token>)
  3. Soal pertama tampil, jam batas waktu jalan    -> POST /api/game/mulai/
  4. Tiap jawaban dikirim sebagai event            -> POST /api/game/event/    {"events": [...]}
  5. Level selesai atau gagal (waktu habis)        -> POST /api/game/event/    {"events": [...], "selesai": true, "skor": 85, "durasi_detik": 240}
                                                                              {"events": [], "gagal": "waktu", "skor": 55, "durasi_detik": 540}

Aturan waktu (lihat jalur.denda_waktu): setelah batas waktu habis, poin berkurang tiap 10 detik;
lewat 30 detik = level gagal dan murid mengulang dengan soal baru.
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
from django.views.decorators.http import require_GET, require_POST

from accounts.utils import role_required
from . import engine, jalur
from .bank_soal import KOMPETENSI_BACA, KOMPETENSI_UTAMA_LITERASI, serialkan
from .models import Jawaban, Kompetensi, Misi, Penguasaan, Siswa
from .services import KODE_LITERASI, catat_jawaban, selesaikan_diagnostik

# Toleransi jam server vs jam game saat memeriksa klaim "gagal" (detik).
LONGGAR_DETIK = 5


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
# 2. TOKEN DAN PEMBUKAAN GAME
# ==========================================================

@role_required("murid")
def mulai_game(request, misi_id):
    misi = get_object_or_404(
        Misi.objects.select_related("siswa", "kompetensi"),
        id=misi_id,
        siswa=request.user.siswa,
        status="baru",
    )

    url_game = settings.LENTERA_GAMES.get(misi.kompetensi.kode)
    if not url_game:
        return JsonResponse(
            {"error": f"Kompetensi {misi.kompetensi.kode} belum dipetakan ke Game 1 atau Game 2."},
            status=400,
        )

    # Soal level ini dibekukan (dipilih dari bank guru) saat game dibuka.
    soal = jalur.bekukan_soal(misi)
    if not soal:
        return JsonResponse(
            {
                "error": (
                    f"Belum ada soal untuk Bagian {misi.bagian} Level {misi.level}. "
                    "Minta gurumu menambahkan soal di Bank Soal."
                )
            },
            status=409,
        )

    sekarang = int(time.time())
    payload = {
        "sub": misi.siswa.kode_samaran,
        "nama": misi.siswa.nama,
        "misi": misi.id,
        "kompetensi": misi.kompetensi.kode,
        "ranah": misi.kompetensi.ranah,
        "game": jalur.game_dari_kompetensi(misi.kompetensi),
        "bagian": misi.bagian,
        "level": misi.level,
        "mode": misi.jenis,
        "total_soal": len(soal),
        # Batas waktu dari menit pada Bank Aktivitas (0 = tanpa batas).
        "batas_detik": jalur.batas_detik(misi),
        **jalur.aturan_waktu(),
        "home": request.build_absolute_uri("/murid/"),
        "logout": request.build_absolute_uri("/logout/"),
        "api": request.build_absolute_uri("/api/game/event/"),
        "soal_api": request.build_absolute_uri("/api/game/soal/"),
        "mulai_api": request.build_absolute_uri("/api/game/mulai/"),
        "iat": sekarang,
        "exp": sekarang + settings.LENTERA_GAME_TOKEN_TTL,
    }

    token = jwt.encode(payload, settings.LENTERA_GAME_SECRET, algorithm="HS256")

    # Pertahankan query parameter URL yang sudah ada.
    parsed = urlsplit(url_game)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["ssoToken"] = token

    return redirect(
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    )


# ==========================================================
# 3. NORMALISASI WAKTU, BOOLEAN, DAN PENGUASAAN
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


def _bilangan(value, nama, minimum, maksimum):
    """Bilangan bulat opsional dalam rentang; None bila tidak diisi."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Field {nama} tidak valid.")
    try:
        angka = round(float(value))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"Field {nama} tidak valid.")
    if not minimum <= angka <= maksimum:
        raise ValueError(f"Field {nama} harus antara {minimum} dan {maksimum}.")
    return angka


def _state_siswa(siswa, kompetensi):
    """Mengambil kondisi penguasaan terbaru."""
    penguasaan = Penguasaan.objects.filter(siswa=siswa, kompetensi=kompetensi).first()

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


def _respons(misi, **tambahan):
    """Respons standar: state BKT, status misi, dan lampu level (jumlah jawaban benar / jumlah soal)."""
    benar, total = jalur.lampu_misi(misi)
    return JsonResponse({
        "state": _state_siswa(misi.siswa, misi.kompetensi),
        "selesai": misi.status == "selesai",
        "gagal": misi.status == "gagal",
        "lampu": {"nyala": len(benar), "total": total, "benar_ids": benar},
        **tambahan,
    })


# ==========================================================
# 4. TOKEN PADA PERMINTAAN API
# ==========================================================

def _galat(pesan, status):
    return JsonResponse({"error": pesan}, status=status)


def _klaim(request, data=None):
    """Baca dan verifikasi token. Mengembalikan (klaim, None) atau (None, respons galat)."""
    # Token utama menggunakan Authorization Bearer.
    # Dukungan ssoToken di body dipertahankan untuk kompatibilitas.
    authorization = request.headers.get("Authorization", "").strip()

    if authorization:
        if not authorization.startswith("Bearer "):
            return None, _galat("Format Authorization tidak valid.", 401)
        token = authorization[7:].strip()
    else:
        token = str((data or {}).get("ssoToken") or "").strip()

    if not token:
        return None, _galat("Token misi tidak tersedia.", 401)

    try:
        c = jwt.decode(
            token,
            settings.LENTERA_GAME_SECRET,
            algorithms=["HS256"],
            leeway=getattr(settings, "LENTERA_GAME_EVENT_GRACE", 0),
        )
        return {
            "misi": int(c["misi"]),
            "kode": normalisasi_kompetensi(c["kompetensi"]),
            "level": int(c["level"]),
            "sub": str(c["sub"]),
            "batas": max(0, int(c.get("batas_detik") or 0)),
            "toleransi": int(c.get("toleransi_detik") or jalur.aturan_waktu()["toleransi_detik"]),
            "tick": int(c.get("tick_detik") or jalur.aturan_waktu()["tick_detik"]),
        }, None
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None, _galat("Token misi tidak valid atau kedaluwarsa.", 401)


def _misi_token(klaim, kunci=False):
    """Muat misi milik token; (misi, None) atau (None, respons galat)."""
    qs = Misi.objects.select_related("siswa", "kompetensi")
    if kunci:
        qs = qs.select_for_update()
    try:
        misi = qs.get(id=klaim["misi"], siswa__kode_samaran=klaim["sub"])
    except Misi.DoesNotExist:
        return None, _galat("Misi tidak ditemukan.", 404)
    if misi.kompetensi.kode != klaim["kode"] or misi.level != klaim["level"]:
        return None, _galat("Token tidak cocok dengan kompetensi atau level misi.", 403)
    return misi, None


# ==========================================================
# 5. NORMALISASI EVENT DARI GAME
# ==========================================================

def _norm(event, misi, id_soal=(), kode_item=None):
    """
    Menerima event Game 1 dan Game 2.

    Field yang didukung:
    - kompetensi / competency
    - benar / is_correct
    - item_id / question_id (harus salah satu soal pada misi)
    - pola / error_code / error_pattern
    - waktu_ms / response_time_ms / response_time_seconds
    - petunjuk / used_hint

    `kode_item` (item_id -> kode kompetensi soal) membuat server yang menentukan kompetensi pada level Literasi:
    bacaan mikrofon selalu milik LT-KETEPATAN, dan soal susun kalimat tidak pernah dihitung sebagai LT-KETEPATAN.
    """
    if not isinstance(event, dict):
        raise ValueError("Setiap event harus berbentuk objek JSON.")

    kode = normalisasi_kompetensi(
        event.get("kompetensi") or event.get("competency") or misi.kompetensi.kode
    )

    # Satu level Game 1 memuat campuran kompetensi literasi; Game 2 satu level = satu kompetensi.
    if misi.kompetensi.ranah == "literasi":
        if kode not in KODE_LITERASI:
            raise ValueError("Kompetensi event bukan kompetensi literasi.")
    elif kode != misi.kompetensi.kode:
        raise ValueError("Kompetensi event tidak cocok dengan kompetensi misi.")

    try:
        level = int(event.get("level", misi.level))
    except (TypeError, ValueError):
        raise ValueError("Level soal tidak valid.")

    if level != misi.level or not 1 <= level <= 5:
        raise ValueError("Level event tidak cocok dengan level misi.")

    benar = _boolean(event.get("benar", event.get("is_correct")), default=None)

    if benar is None:
        raise ValueError("Field benar tidak tersedia.")

    item_id = str(event.get("item_id") or event.get("question_id") or "").strip()

    if len(item_id) > 40:
        raise ValueError("ID soal terlalu panjang.")

    if id_soal and item_id not in id_soal:
        raise ValueError("Soal tidak termasuk dalam misi ini.")

    if misi.kompetensi.ranah == "literasi" and kode_item:
        asal = kode_item.get(item_id)
        if asal == KOMPETENSI_BACA:
            kode = KOMPETENSI_BACA
        elif kode == KOMPETENSI_BACA:
            kode = asal or KOMPETENSI_UTAMA_LITERASI

    raw_ms = event.get("waktu_ms")

    if raw_ms is None:
        raw_ms = event.get("response_time_ms")

    if raw_ms is None:
        try:
            raw_ms = round(float(event.get("response_time_seconds", 0)) * 1000)
        except (TypeError, ValueError, OverflowError):
            raw_ms = 0

    try:
        waktu_ms = int(raw_ms)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Waktu pengerjaan tidak valid.")

    if waktu_ms < 0:
        raise ValueError("Waktu pengerjaan tidak boleh negatif.")

    waktu_ms = min(waktu_ms, 24 * 60 * 60 * 1000)

    event_id = str(event.get("event_id") or "").strip()

    if len(event_id) > 64:
        raise ValueError("ID event terlalu panjang.")

    pola = str(
        event.get("pola") or event.get("error_code") or event.get("error_pattern") or ""
    ).strip()

    if len(pola) > 30:
        pola = ""

    definisi_pola = getattr(engine, "POLA", {})

    if pola not in definisi_pola:
        pola = ""
    elif definisi_pola[pola][1] != kode:
        pola = ""

    petunjuk = _boolean(event.get("petunjuk", event.get("used_hint")), default=False)

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

def _boleh_selesai(misi):
    """Misi selesai ketika SEMUA soal pada misi sudah pernah dijawab benar."""
    id_soal = {s.item_id for s in jalur.bekukan_soal(misi)}

    if not id_soal:
        return False

    benar = set(
        Jawaban.objects.filter(misi=misi, benar=True).values_list("item_id", flat=True)
    )

    return id_soal <= benar


# ==========================================================
# 7. API SOAL
# ==========================================================

@csrf_exempt
@require_GET
def api_soal(request):
    """Soal level ini (format asli game) beserta aturan waktu dan lampu yang sudah menyala."""
    klaim, galat = _klaim(request)
    if galat:
        return galat

    misi, galat = _misi_token(klaim)
    if galat:
        return galat

    if misi.status == "selesai":
        return JsonResponse({"error": "Misi ini sudah selesai.", "selesai": True}, status=409)
    if misi.status == "gagal":
        return JsonResponse({"error": "Waktu misi ini sudah habis. Mulai lagi dari dashboard.", "gagal": True}, status=409)

    soal = jalur.bekukan_soal(misi)
    if not soal:
        return _galat(
            f"Belum ada soal untuk Bagian {misi.bagian} Level {misi.level}. Minta gurumu menambahkan soal di Bank Soal.",
            409,
        )

    benar, total = jalur.lampu_misi(misi)
    sekarang = timezone.now()

    return JsonResponse({
        "misi": misi.id,
        "game": jalur.game_dari_kompetensi(misi.kompetensi),
        "mode": misi.jenis,
        "kompetensi": misi.kompetensi.kode,
        "bagian": misi.bagian,
        "level": misi.level,
        "total": total,
        "benar_ids": benar,
        "batas_detik": klaim["batas"],
        **jalur.aturan_waktu(),
        "dimulai": misi.mulai_pada is not None,
        "berjalan_detik": int((sekarang - misi.mulai_pada).total_seconds()) if misi.mulai_pada else 0,
        "soal": [serialkan(s, request) for s in soal],
    })


@csrf_exempt
@require_POST
def api_mulai(request):
    """Dipanggil game saat soal pertama tampil: jam batas waktu berjalan sejak saat ini.

    Aman dipanggil berulang (mis. halaman dimuat ulang): jam tidak diulang dari nol.
    Bila waktu sudah lewat dari batas + toleransi, misi dinyatakan gagal.
    """
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = {}
    klaim, galat = _klaim(request, data if isinstance(data, dict) else {})
    if galat:
        return galat

    with transaction.atomic():
        misi, galat = _misi_token(klaim, kunci=True)
        if galat:
            return galat

        if misi.status == "selesai":
            return JsonResponse({"error": "Misi ini sudah selesai.", "selesai": True}, status=409)
        if misi.status == "gagal":
            return JsonResponse({"gagal": True, "berjalan_detik": misi.durasi_detik or 0, "batas_detik": klaim["batas"]})

        sekarang = timezone.now()
        if misi.mulai_pada is None:
            misi.mulai_pada = sekarang
            misi.save(update_fields=["mulai_pada"])

        berjalan = int((sekarang - misi.mulai_pada).total_seconds())
        _, lewat_batas = jalur.denda_waktu(berjalan, klaim["batas"], klaim["tick"], klaim["toleransi"])

        if lewat_batas:
            misi.status = "gagal"
            misi.durasi_detik = berjalan
            misi.selesai_pada = sekarang
            misi.save(update_fields=["status", "durasi_detik", "selesai_pada"])
            return JsonResponse({"gagal": True, "berjalan_detik": berjalan, "batas_detik": klaim["batas"]})

        return JsonResponse({"gagal": False, "berjalan_detik": berjalan, "batas_detik": klaim["batas"]})


# ==========================================================
# 8. API PENERIMA EVENT GAME
# ==========================================================

@csrf_exempt
@require_POST
def api_event(request):
    if len(request.body) > 256 * 1024:
        return _galat("Ukuran permintaan terlalu besar.", 413)

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _galat("JSON permintaan tidak valid.", 400)

    if not isinstance(data, dict):
        return _galat("Body harus berupa objek JSON.", 400)

    try:
        selesai_diminta = _boolean(data.get("selesai"), default=False)
    except ValueError:
        return _galat("Field selesai harus berupa boolean.", 400)

    gagal_diminta = data.get("gagal")
    if gagal_diminta not in (None, False, "", "waktu"):
        return _galat('Field gagal hanya boleh bernilai "waktu".', 400)
    gagal_diminta = gagal_diminta == "waktu"

    if gagal_diminta and selesai_diminta:
        return _galat("Misi tidak bisa selesai dan gagal sekaligus.", 400)

    try:
        skor = _bilangan(data.get("skor"), "skor", 0, 100)
        durasi = _bilangan(data.get("durasi_detik"), "durasi_detik", 0, 24 * 3600)
    except ValueError as error:
        return _galat(str(error), 400)

    klaim, galat = _klaim(request, data)
    if galat:
        return galat

    events_raw = data.get("events")

    # Mendukung event tunggal dari Game 1 versi lama.
    if events_raw is None:
        events_raw = [data] if ("item_id" in data or "question_id" in data) else []

    if not isinstance(events_raw, list):
        return _galat("Field events harus berupa array.", 400)

    if len(events_raw) > 20:
        return _galat("Maksimal 20 event per permintaan.", 400)

    try:
        with transaction.atomic():
            misi, galat = _misi_token(klaim, kunci=True)
            if galat:
                return galat

            soal = jalur.bekukan_soal(misi)
            if not soal:
                return _galat("Level ini belum memiliki soal yang tersedia.", 409)
            id_soal = {s.item_id for s in soal}

            # Semua event divalidasi terlebih dahulu.
            kode_item = {s.item_id: s.kompetensi.kode for s in soal}
            events = [_norm(event, misi, id_soal, kode_item) for event in events_raw]

            if misi.status == "gagal":
                if gagal_diminta and not events:
                    return _respons(misi)
                return JsonResponse(
                    {"error": "Waktu misi ini sudah habis. Mulai lagi dari dashboard.", "gagal": True},
                    status=409,
                )

            if misi.status == "selesai":
                if selesai_diminta and not events:
                    return _respons(misi)

                semua_event_sudah_ada = bool(events) and all(
                    event["event_id"] and Jawaban.objects.filter(event_id=event["event_id"]).exists()
                    for event in events
                )

                if semua_event_sudah_ada:
                    return _respons(misi)

                return _galat("Misi ini sudah selesai.", 409)

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

            sekarang = timezone.now()

            # ---- Gagal: waktu habis melewati toleransi ----
            if gagal_diminta:
                if klaim["batas"] <= 0:
                    return _galat("Level ini tidak memiliki batas waktu.", 400)
                if misi.mulai_pada is None:
                    return _galat("Misi belum dimulai.", 409)

                berjalan = (sekarang - misi.mulai_pada).total_seconds()
                if berjalan < klaim["batas"] + klaim["toleransi"] - LONGGAR_DETIK:
                    return JsonResponse(
                        {"error": "Batas waktu belum terlampaui.", "state": _state_siswa(misi.siswa, misi.kompetensi)},
                        status=409,
                    )

                misi.status = "gagal"
                misi.skor = skor
                misi.durasi_detik = durasi if durasi is not None else int(berjalan)
                misi.selesai_pada = sekarang
                misi.save(update_fields=["status", "skor", "durasi_detik", "selesai_pada"])
                return _respons(misi)

            # ---- Selesai: seluruh soal sudah benar ----
            if selesai_diminta:
                if not _boleh_selesai(misi):
                    benar, total = jalur.lampu_misi(misi)
                    return JsonResponse({
                        "state": _state_siswa(misi.siswa, misi.kompetensi),
                        "selesai": False,
                        "gagal": False,
                        "lampu": {"nyala": len(benar), "total": total, "benar_ids": benar},
                        "error": "Belum seluruh soal wajib dijawab benar. Misi belum ditandai selesai.",
                    })

                misi.status = "selesai"
                misi.skor = skor
                if durasi is not None:
                    misi.durasi_detik = durasi
                elif misi.mulai_pada:
                    misi.durasi_detik = int((sekarang - misi.mulai_pada).total_seconds())
                misi.selesai_pada = sekarang
                misi.save(update_fields=["status", "skor", "durasi_detik", "selesai_pada"])

                if misi.jenis == "diagnostik":
                    selesaikan_diagnostik(misi)
                else:
                    Siswa.objects.filter(id=misi.siswa_id).update(poin=F("poin") + 10)

            return _respons(misi)

    except Kompetensi.DoesNotExist:
        return _galat("Kode kompetensi tidak dikenal.", 400)

    except (ValueError, TypeError, OverflowError) as error:
        return _galat(str(error), 400)

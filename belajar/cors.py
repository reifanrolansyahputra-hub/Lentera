from django.conf import settings
from django.http import HttpResponse


class GameCorsMiddleware:
    """Izinkan game/quiz di origin lain memanggil /api/game/. Auth pakai Bearer token (bukan cookie).
    LENTERA_GAME_ORIGIN = "*" (dev) atau daftar origin dipisah koma (produksi)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        api = request.path.startswith("/api/game/")
        resp = HttpResponse(status=204) if api and request.method == "OPTIONS" else self.get_response(request)
        if api:
            izin = [o.strip() for o in settings.LENTERA_GAME_ORIGIN.split(",")]
            asal = request.headers.get("Origin", "")
            if "*" in izin:
                resp["Access-Control-Allow-Origin"] = "*"
            elif asal in izin:
                resp["Access-Control-Allow-Origin"] = asal
                resp["Vary"] = "Origin"
            resp["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from accounts import views as akun

urlpatterns = [
    path("login/", akun.login_view, name="login"),
    path("logout/", akun.logout_view, name="logout"),
    path("", include("belajar.urls")),
]

# Gambar soal (media) dilayani Django hanya saat DEBUG (pengembangan lokal).
# Pada produksi, layani MEDIA_ROOT lewat web server (nginx dsb.).
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

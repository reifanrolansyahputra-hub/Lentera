from django.urls import include, path

from accounts import views as akun

urlpatterns = [
    path("login/", akun.login_view, name="login"),
    path("logout/", akun.logout_view, name="logout"),
    path("", include("belajar.urls")),
]

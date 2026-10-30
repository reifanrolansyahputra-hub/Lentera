from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render

from belajar.models import Kelas
from .utils import HOME, get_role


def login_view(request):
    role = get_role(request.user)
    if role:
        return redirect(HOME[role])
    ctx = {}
    kode = (request.POST.get("kode") or request.GET.get("kode") or "").strip().upper()
    if kode:  # alur murid (F-01): kode kelas -> pilih avatar, tanpa kata sandi
        kelas = Kelas.objects.filter(kode=kode).first()
        ctx["kode"] = kode
        if not kelas:
            ctx["error"] = "Kode kelas tidak ditemukan."
        elif request.POST.get("siswa"):
            s = kelas.siswa.select_related("user").filter(id=request.POST["siswa"]).first()
            if s:
                login(request, s.user, backend="django.contrib.auth.backends.ModelBackend")
                return redirect(HOME["murid"])
        else:
            ctx["kelas"], ctx["daftar"] = kelas, kelas.siswa.select_related("user")
    elif request.method == "POST":  # alur guru/admin: username + kata sandi
        u = authenticate(request, username=request.POST.get("username"), password=request.POST.get("password"))
        if u and get_role(u):
            login(request, u)
            return redirect(HOME[get_role(u)])
        ctx["error"] = "Username atau kata sandi salah."
    return render(request, "accounts/login.html", ctx)


def logout_view(request):
    logout(request)
    return redirect("/login/")

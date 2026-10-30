"""
Views khusus role Admin.

Admin bukan wali kelas siapa pun - tanggung jawabnya lintas kelas:
- Membuat kelas + kode kelas, dan mengisi akun siswa (bulk, lewat kode kelas).
- Membuat akun guru (dengan kata sandi) dan menentukan kelas yang diampunya.
- Mengekspor laporan kelas mana saja.

Semua fungsi di sini memakai ulang services.py yang sama dengan jalur
guru (buat_kelas, tambah_siswa, kode_kelas_baru, ringkasan_kelas, dst.),
supaya hubungan ke game (kode_samaran, Penguasaan, Misi) tidak berubah.
"""

from django.contrib import messages
from django.db import IntegrityError
from django.contrib.auth.models import User
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.utils import role_required
from .models import Kelas
from .services import buat_guru, buat_kelas, kode_kelas_baru, ringkasan_kelas, tambah_siswa, tetapkan_wali_kelas
from .views import JENIS_EKSPOR_VALID, ekspor_csv


def _ctx(request, aktif, **kw):
    return {"admin": request.user, "aktif": aktif, **kw}


def _semua_kelas():
    """Semua kelas dengan jumlah siswa dan daftar siswanya (untuk ditampilkan/diciutkan per kelas)."""
    return (
        Kelas.objects.select_related("wali_kelas")
        .annotate(jumlah_siswa=Count("siswa"))
        .prefetch_related("siswa__user")
        .order_by("nama")
    )


def _semua_guru():
    """Semua akun guru, masing-masing disisipi .kelas (kelas yang diampu, atau None)."""
    guru = list(User.objects.filter(profile__role="guru").order_by("username"))
    kelas_by_guru = {k.wali_kelas_id: k for k in Kelas.objects.exclude(wali_kelas__isnull=True)}

    for g in guru:
        g.kelas = kelas_by_guru.get(g.id)

    return guru


@role_required("admin")
def admin_kelas(request):
    if request.method == "POST":
        aksi, p = request.POST.get("aksi"), request.POST
        try:
            if aksi == "buat_kelas":
                nama = p["nama"].strip()[:20]
                if not nama:
                    raise ValueError
                buat_kelas(None, nama)
                messages.success(request, f"Kelas “{nama}” dibuat. Tambahkan siswa dan bagikan kode kelasnya ke guru.")
            elif aksi == "tambah_siswa":
                kelas = get_object_or_404(Kelas, id=p["kelas"])
                nama = [x.strip()[:40] for x in p["nama"].splitlines() if x.strip()]
                if not nama:
                    raise ValueError
                for x in nama:
                    tambah_siswa(kelas, x)
                messages.success(request, f"{len(nama)} siswa ditambahkan ke kelas {kelas.nama}. Murid tinggal masuk pakai kode kelas, lalu pilih namanya sendiri.")
            elif aksi == "hapus_siswa":
                kelas = get_object_or_404(Kelas, id=p["kelas"])
                get_object_or_404(kelas.siswa.all(), id=p["id"]).user.delete()
                messages.success(request, "Siswa dihapus beserta datanya.")
            elif aksi == "ganti_kode":
                kelas = get_object_or_404(Kelas, id=p["kelas"])
                kelas.kode = kode_kelas_baru(kelas.nama)
                kelas.save()
                messages.success(request, f"Kode kelas {kelas.nama} diperbarui: {kelas.kode}. Kartu masuk lama perlu dicetak ulang.")
        except IntegrityError:
            messages.error(request, "Nama kelas itu sudah dipakai.")
        except (ValueError, KeyError):
            messages.error(request, "Isian tidak valid.")
        return redirect("/admin/")

    return render(request, "belajar/admin_kelas.html", _ctx(request, "kelas", daftar_kelas=_semua_kelas()))


@role_required("admin")
def admin_guru(request):
    if request.method == "POST":
        aksi, p = request.POST.get("aksi"), request.POST
        try:
            if aksi == "buat_guru":
                kelas = get_object_or_404(Kelas, id=p["kelas"]) if p.get("kelas") else None
                guru = buat_guru(p["username"], p["nama"], p["sandi"], kelas)
                messages.success(request, f"Akun guru “{guru.username}” dibuat" + (f" dan dijadikan wali kelas {kelas.nama}." if kelas else ", belum ada kelas yang diampu."))
            elif aksi == "pindah_kelas":
                guru = get_object_or_404(User, id=p["id"], profile__role="guru")
                kelas = get_object_or_404(Kelas, id=p["kelas"]) if p.get("kelas") else None
                if kelas:
                    tetapkan_wali_kelas(guru, kelas)
                    messages.success(request, f"{guru.first_name or guru.username} sekarang wali kelas {kelas.nama}.")
                else:
                    Kelas.objects.filter(wali_kelas=guru).update(wali_kelas=None)
                    messages.success(request, f"{guru.first_name or guru.username} dilepas dari kelas yang diampu.")
            elif aksi == "reset_sandi":
                guru = get_object_or_404(User, id=p["id"], profile__role="guru")
                if len(p["sandi"]) < 6:
                    raise ValueError
                guru.set_password(p["sandi"])
                guru.save()
                messages.success(request, f"Kata sandi {guru.first_name or guru.username} direset.")
            elif aksi == "hapus_guru":
                guru = get_object_or_404(User, id=p["id"], profile__role="guru")
                nama = guru.first_name or guru.username
                guru.delete()
                messages.success(request, f"Akun guru {nama} dihapus. Kelas yang diampunya kini belum punya wali kelas.")
        except IntegrityError:
            messages.error(request, "Username itu sudah dipakai.")
        except (ValueError, KeyError):
            messages.error(request, "Isian tidak valid (kata sandi minimal 6 karakter).")
        return redirect("/admin/guru/")

    return render(request, "belajar/admin_guru.html", _ctx(request, "guru", daftar_guru=_semua_guru(), daftar_kelas=Kelas.objects.order_by("nama")))


@role_required("admin")
def admin_laporan(request):
    kelas_id = request.GET.get("kelas", "").strip()
    jenis = request.GET.get("jenis", "").strip().lower()

    if kelas_id and jenis:
        if jenis not in JENIS_EKSPOR_VALID:
            return HttpResponse("Jenis ekspor tidak dikenal.", status=400, content_type="text/plain; charset=utf-8")
        return ekspor_csv(get_object_or_404(Kelas, id=kelas_id), jenis)

    kelas_terpilih = get_object_or_404(Kelas, id=kelas_id) if kelas_id else None
    d = ringkasan_kelas(kelas_terpilih) if kelas_terpilih else None
    return render(request, "belajar/admin_laporan.html", _ctx(
        request, "laporan", daftar_kelas=Kelas.objects.order_by("nama"), kelas_terpilih=kelas_terpilih, d=d, hari_ini=timezone.localdate(),
    ))


@role_required("admin")
def admin_kartu(request, kelas_id):
    """Pakai ulang template kartu masuk guru - isinya sama, hanya kelasnya bebas dipilih admin."""
    kelas = get_object_or_404(Kelas, id=kelas_id)
    return render(request, "belajar/guru_kartu.html", {"kelas": kelas, "siswa": kelas.siswa.select_related("user")})

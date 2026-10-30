import csv
from functools import wraps
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db import IntegrityError
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.utils import HOME, get_role, role_required
from . import engine
from .models import Aktivitas, HasilDiagnostik, Jawaban, Kelas, Kompetensi, Penugasan
from django.utils import timezone
from .services import (aktifkan_diagnostik, buat_kelas, kode_kelas_baru, misi_aktif, penguasaan_lengkap, ringkasan_kelas, status_diagnostik,
                       tambah_siswa, terapkan_diagnostik, terapkan_ke_kelompok, tugaskan_aktivitas)


def root(request):
    return redirect(HOME.get(get_role(request.user), "/login/"))


def _kartu(m):
    """Data pratinjau kartu misi (Gambar 4)."""
    c = engine.CONTOH.get(m.kompetensi.kode)
    k = {"m": m, "fase": engine.fase(m.level), "menit": 8 if m.kompetensi.ranah == "literasi" else 10, "r3": m.kompetensi.ranah[:3], "diag": m.jenis == "diagnostik"}
    if isinstance(c, int):
        k.update(angka=c, puluhan=range(c // 10), satuan=range(c % 10))
    else:
        k["kata"] = c or []
    return k


def _jalur(pen):
    """Jalur Lenteraku: kurva mulus melewati 8 lentera; Cakap = menyala, kompetensi pertama yang belum Cakap = 'kini'."""
    n = len(pen)
    pts = [(22 + i * 256 / max(n - 1, 1), 160 - i * 124 / max(n - 1, 1) + (8 if i % 2 else -8)) for i in range(n)]
    d = f"M{pts[0][0]:.0f} {pts[0][1]:.0f}"
    for i in range(n - 1):
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, n - 1)]
        d += (f" C{p1[0] + (p2[0] - p0[0]) / 6:.0f} {p1[1] + (p2[1] - p0[1]) / 6:.0f} "
              f"{p2[0] - (p3[0] - p1[0]) / 6:.0f} {p2[1] - (p3[1] - p1[1]) / 6:.0f} {p2[0]:.0f} {p2[1]:.0f}")
    kini = next((i for i, p in enumerate(pen) if p.status != "Cakap"), -1)
    nodes = [{"x": round(x), "y": round(y), "nama": p.kompetensi.nama, "st": "done" if p.status == "Cakap" else "kini" if i == kini else "next"}
             for i, ((x, y), p) in enumerate(zip(pts, pen))]
    return {"d": d, "nodes": nodes}


@role_required("murid")
def murid_beranda(request):
    siswa = request.user.siswa
    pen = penguasaan_lengkap(siswa)
    st = {p.kompetensi.kode: p.status for p in pen}
    jml = Jawaban.objects.filter(siswa=siswa, kompetensi__ranah="literasi", dibuat__gte=timezone.now() - timedelta(days=7)).count()
    lencana = [("📖", "Pembaca Tekun", jml >= 15), ("🔢", "Ahli Puluhan", st.get("NM-NILAI-TEMPAT") == "Cakap"), ("🏆", "Juara Pola", st.get("NM-POLA") == "Cakap")]
    return render(request, "belajar/murid_beranda.html", {
        "siswa": siswa, "kartu": [_kartu(m) for m in misi_aktif(siswa)], "pen": pen, "jalur": _jalur(pen), "lencana": lencana,
        "menyala": sum(p.status == "Cakap" for p in pen), "sekolah": settings.LENTERA_SEKOLAH})


def guru_view(fn):
    """Khusus guru. Menyuntikkan kelas wali; guru yang belum punya kelas diarahkan ke Pengaturan."""
    @wraps(fn)
    @role_required("guru")
    def wrapped(request, *a, **kw):
        kelas = Kelas.objects.filter(wali_kelas=request.user).first()
        if not kelas:
            messages.error(request, "Buat kelas dulu di menu Pengaturan.")
            return redirect("/guru/pengaturan/")
        return fn(request, kelas, *a, **kw)
    return wrapped


def _ctx(request, kelas, aktif, **kw):
    return {"guru": request.user, "kelas": kelas, "aktif": aktif, **kw}


@guru_view
def guru_insight(request, kelas):
    return render(request, "belajar/guru_insight.html", _ctx(request, kelas, "insight", d=ringkasan_kelas(kelas)))


@guru_view
def guru_peta(request, kelas):
    return render(request, "belajar/guru_peta.html", _ctx(request, kelas, "peta", d=ringkasan_kelas(kelas)))


@guru_view
@require_POST
def guru_terapkan(request, kelas):
    """Tombol 'Terapkan ke kelompok': misi sesuai level tiap kelompok (F-10)."""
    k = get_object_or_404(Kompetensi, kode=request.POST["kompetensi"])
    n = terapkan_ke_kelompok(request.user, kelas, k)
    messages.success(request, f"Misi “{k.nama}” dikirim ke {n} siswa sesuai level kelompoknya.")
    return redirect("/guru/")


def _kompetensi_game(kode):
    """Aktivitas cuma boleh dibuat untuk kompetensi yang sudah punya game asli tersambung."""
    if kode not in settings.LENTERA_GAMES:
        raise ValueError
    return get_object_or_404(Kompetensi, kode=kode)


@guru_view
def guru_aktivitas(request, kelas):
    if request.method == "POST":
        aksi, p = request.POST.get("aksi"), request.POST
        try:
            if aksi == "tambah":
                a, b = sorted((int(p["level_min"]), int(p["level_maks"])))
                if not p["judul"].strip():
                    raise ValueError
                Aktivitas.objects.create(judul=p["judul"].strip()[:80], kompetensi=_kompetensi_game(p["kompetensi"]),
                                         level_min=max(1, a), level_maks=min(5, b), menit=int(p.get("menit") or 10), deskripsi=p.get("deskripsi", ""), dibuat_oleh=request.user)
                messages.success(request, "Aktivitas ditambahkan. Validasi dulu sebelum bisa ditugaskan.")
            elif aksi == "edit":
                obj = get_object_or_404(Aktivitas, id=p["id"])
                a, b = sorted((int(p["level_min"]), int(p["level_maks"])))
                if not p["judul"].strip():
                    raise ValueError
                obj.judul = p["judul"].strip()[:80]
                obj.kompetensi = _kompetensi_game(p["kompetensi"])
                obj.level_min, obj.level_maks = max(1, a), min(5, b)
                obj.menit = int(p.get("menit") or 10)
                obj.deskripsi = p.get("deskripsi", "")
                obj.save()
                messages.success(request, f"“{obj.judul}” diperbarui.")
            elif aksi == "validasi":
                a = get_object_or_404(Aktivitas, id=p["id"])
                a.tervalidasi = not a.tervalidasi
                a.save()
                messages.success(request, f"“{a.judul}” {'divalidasi' if a.tervalidasi else 'dicabut validasinya'}.")
            elif aksi == "hapus":
                a = get_object_or_404(Aktivitas, id=p["id"])
                judul = a.judul
                a.delete()
                messages.success(request, f"“{judul}” dihapus dari bank aktivitas.")
            elif aksi == "tugaskan":
                a = get_object_or_404(Aktivitas, id=p["id"])
                if not a.tervalidasi:
                    messages.error(request, "Aktivitas belum divalidasi, jadi belum bisa ditugaskan.")
                else:
                    n, main = tugaskan_aktivitas(request.user, kelas, a, p.get("target", "kelas"), request.POST.getlist("siswa"))
                    messages.success(request, (f"Misi “{a.judul}” dikirim ke {n} siswa." if main else f"Penugasan “{a.judul}” untuk {n} siswa dicatat.") if n else "Tidak ada siswa pada target itu.")
            elif aksi == "selesai_penugasan":
                t = get_object_or_404(Penugasan, id=p["id"], kelas=kelas)
                t.selesai = not t.selesai
                t.save()
        except (ValueError, KeyError):
            messages.error(request, "Isian tidak valid.")
        return redirect(request.get_full_path())
    f, daftar = {k: request.GET.get(k, "") for k in ("kompetensi", "level")}, Aktivitas.objects.select_related("kompetensi")
    if f["kompetensi"]:
        daftar = daftar.filter(kompetensi__kode=f["kompetensi"])
    if f["level"].isdigit():
        daftar = daftar.filter(level_min__lte=int(f["level"]), level_maks__gte=int(f["level"]))
    daftar = list(daftar)
    for a in daftar:
        a.main = a.kompetensi.kode in settings.LENTERA_GAMES
    return render(request, "belajar/guru_aktivitas.html", _ctx(request, kelas, "aktivitas", daftar=daftar, f=f, kompetensi=Kompetensi.objects.filter(kode__in=settings.LENTERA_GAMES),
                  siswa=kelas.siswa.select_related("user"), penugasan=Penugasan.objects.filter(kelas=kelas).select_related("aktivitas")[:10]))


@guru_view
def guru_diagnostik(request, kelas):
    if request.method == "POST":
        if request.POST.get("aksi") == "aktifkan":
            n = aktifkan_diagnostik(kelas)
            messages.success(request, f"{n} misi asesmen dikirim ke murid." if n else "Semua murid sudah punya hasil atau misi asesmen.")
        elif request.POST.get("aksi") == "manual":
            s, n = get_object_or_404(kelas.siswa.all(), id=request.POST["siswa"]), 0
            for k in Kompetensi.objects.all():
                if not request.POST.get(f"b_{k.kode}", "").strip():
                    continue  # kolom "benar" kosong = kompetensi ini tidak diasesmen
                try:
                    b, t = int(request.POST[f"b_{k.kode}"]), int(request.POST.get(f"t_{k.kode}") or 0)
                except ValueError:
                    continue
                if t > 0 and 0 <= b <= t:
                    terapkan_diagnostik(s, k, b, t, "manual")
                    n += 1
            messages.success(request, f"Hasil asesmen {s.nama}: {n} kompetensi disimpan, level awal diperbarui.")
        return redirect("/guru/diagnostik/")
    return render(request, "belajar/guru_diagnostik.html", _ctx(request, kelas, "diagnostik", d=status_diagnostik(kelas), siswa=kelas.siswa.select_related("user")))


@guru_view
def guru_laporan(request, kelas):
    jenis = request.GET.get("jenis", "").strip().lower()

    # Jika tidak ada jenis ekspor, tampilkan halaman laporan.
    if not jenis:
        return render(
            request,
            "belajar/guru_laporan.html",
            _ctx(
                request,
                kelas,
                "laporan",
                d=ringkasan_kelas(kelas),
                hari_ini=timezone.localdate(),
            ),
        )

    # Hanya tiga jenis ekspor yang disediakan halaman laporan.
    if jenis not in JENIS_EKSPOR_VALID:
        return HttpResponse(
            "Jenis ekspor tidak dikenal.",
            status=400,
            content_type="text/plain; charset=utf-8",
        )

    return ekspor_csv(kelas, jenis)


# Dipakai guru (kelasnya sendiri) maupun admin (kelas mana saja) -
# lihat belajar/admin_views.py.
JENIS_EKSPOR_VALID = {"peta", "jawaban", "diagnostik"}


def ekspor_csv(kelas, jenis):
    """Unduhan CSV: peta kompetensi, log jawaban, atau hasil diagnostik satu kelas."""
    nama_file = {
        "peta": "lentera_peta_kompetensi.csv",
        "jawaban": "lentera_log_jawaban_30_hari.csv",
        "diagnostik": "lentera_hasil_diagnostik.csv",
    }

    # BOM membantu Excel mengenali karakter UTF-8, termasuk nama Indonesia.
    response = HttpResponse(
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{nama_file[jenis]}"'
    )
    response.write("\ufeff")

    writer = csv.writer(response, lineterminator="\n")

    # -----------------------------------------
    # 1. EKSPOR PETA KOMPETENSI PER SISWA
    # -----------------------------------------
    if jenis == "peta":
        data = ringkasan_kelas(kelas)

        writer.writerow([
            "Nama siswa",
            *[
                f"{kompetensi.nama} (%)"
                for kompetensi in data["kompetensi"]
            ],
            "Jawaban 7 hari",
            "Akurasi 7 hari (%)",
            "Aktivitas terakhir",
            "Pola kesalahan 7 hari",
        ])

        for baris in data["baris"]:
            writer.writerow([
                baris["siswa"].nama,
                *[
                    sel["pct"]
                    for sel in baris["sel"]
                ],
                baris["n_jawab"],
                baris["akurasi"],
                baris["terakhir"],
                baris["pola"],
            ])

    # -----------------------------------------
    # 2. EKSPOR LOG JAWABAN 30 HARI
    # -----------------------------------------
    elif jenis == "jawaban":
        batas_waktu = timezone.now() - timedelta(days=30)

        writer.writerow([
            "Waktu",
            "Siswa",
            "Kompetensi",
            "Level",
            "ID soal",
            "Jawaban benar",
            "Pola kesalahan",
            "Saran tindak lanjut",
            "Waktu respons (detik)",
            "Menggunakan petunjuk",
            "Jenis misi",
        ])

        jawaban = (
            Jawaban.objects
            .filter(
                siswa__kelas=kelas,
                dibuat__gte=batas_waktu,
            )
            .select_related(
                "siswa__user",
                "kompetensi",
                "misi",
            )
            .order_by("dibuat", "id")[:20000]
        )

        for item in jawaban:
            waktu_lokal = timezone.localtime(item.dibuat)

            pola = (
                engine.POLA[item.pola][0]
                if item.pola in engine.POLA
                else ""
            )

            saran = (
                engine.POLA[item.pola][2]
                if item.pola in engine.POLA
                else ""
            )

            writer.writerow([
                waktu_lokal.strftime("%Y-%m-%d %H:%M:%S"),
                item.siswa.nama,
                item.kompetensi.nama,
                item.level,
                item.item_id,
                "Ya" if item.benar else "Tidak",
                pola,
                saran,
                round(item.waktu_ms / 1000, 1),
                "Ya" if item.petunjuk else "Tidak",
                item.misi.jenis if item.misi else "misi",
            ])

    # -----------------------------------------
    # 3. EKSPOR HASIL ASESMEN DIAGNOSTIK
    # -----------------------------------------
    elif jenis == "diagnostik":
        writer.writerow([
            "Nama siswa",
            "Kompetensi",
            "Jawaban benar",
            "Total soal",
            "Level awal",
            "P(L) awal",
            "Sumber asesmen",
            "Tanggal",
        ])

        hasil = (
            HasilDiagnostik.objects
            .filter(siswa__kelas=kelas)
            .select_related(
                "siswa__user",
                "kompetensi",
            )
            .order_by(
                "siswa__user__first_name",
                "kompetensi__urutan",
                "-dibuat",
            )
        )

        for item in hasil:
            tanggal = timezone.localtime(item.dibuat)

            writer.writerow([
                item.siswa.nama,
                item.kompetensi.nama,
                item.benar,
                item.total,
                item.level_awal,
                item.p_awal,
                item.get_sumber_display(),
                tanggal.strftime("%Y-%m-%d"),
            ])

    return response
@guru_view
def guru_kartu(request, kelas):
    return render(request, "belajar/guru_kartu.html", {"kelas": kelas, "siswa": kelas.siswa.select_related("user")})


@role_required("guru")
def guru_pengaturan(request):
    kelas = Kelas.objects.filter(wali_kelas=request.user).first()
    if request.method == "POST":
        aksi, p = request.POST.get("aksi"), request.POST
        try:
            if aksi == "buat_kelas" and not kelas:
                buat_kelas(request.user, p["nama"].strip()[:20])
                messages.success(request, "Kelas dibuat. Sekarang tambahkan siswa.")
            elif aksi == "tambah_siswa" and kelas:
                nama = [x.strip()[:40] for x in p["nama"].splitlines() if x.strip()]
                for x in nama:
                    tambah_siswa(kelas, x)
                messages.success(request, f"{len(nama)} siswa ditambahkan. Cetak kartu masuknya di bawah.")
            elif aksi == "hapus_siswa" and kelas:
                get_object_or_404(kelas.siswa.all(), id=p["id"]).user.delete()
                messages.success(request, "Siswa dihapus beserta datanya.")
            elif aksi == "ganti_kode" and kelas:
                kelas.kode = kode_kelas_baru(kelas.nama)
                kelas.save()
                messages.success(request, f"Kode kelas baru: {kelas.kode}. Kartu masuk lama perlu dicetak ulang.")
            elif aksi == "ambang" and kelas:
                kelas.ambang_perhatian = max(1, min(8, int(p["ambang"])))
                kelas.save()
                messages.success(request, "Ambang “Perlu perhatian” disimpan.")
            elif aksi == "sandi":
                if len(p["sandi"]) < 6:
                    raise ValueError
                request.user.set_password(p["sandi"])
                request.user.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, "Kata sandi diganti.")
        except IntegrityError:
            messages.error(request, "Nama kelas itu sudah dipakai.")
        except (ValueError, KeyError):
            messages.error(request, "Isian tidak valid (kata sandi minimal 6 karakter).")
        return redirect("/guru/pengaturan/")
    return render(request, "belajar/guru_pengaturan.html", _ctx(request, kelas, "pengaturan", siswa=kelas.siswa.select_related("user") if kelas else []))

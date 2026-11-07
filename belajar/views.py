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
from . import engine, jalur
from .models import Aktivitas, ArsipReset, HasilDiagnostik, Jawaban, Kelas, Kompetensi, Penugasan
from django.utils import timezone
from .bank_soal import KOMPETENSI_UTAMA_LITERASI
from .services import (RESET_RETENSI_HARI, aktifkan_diagnostik, arsip_bisa_dipulihkan, bersihkan_arsip_kedaluwarsa, buat_kelas,
                       kode_kelas_baru, misi_aktif, penguasaan_lengkap, pulihkan_arsip, reset_progres_kelas, reset_progres_siswa,
                       ringkasan_kelas, status_diagnostik, tambah_siswa, terapkan_diagnostik, terapkan_ke_kelompok, tugaskan_aktivitas)


def root(request):
    return redirect(HOME.get(get_role(request.user), "/login/"))


def _kartu(m, jalan=None):
    """Data kartu misi di beranda murid: bagian, level, lampu, dan batas waktu dari Bank Aktivitas."""
    c = engine.CONTOH.get(m.kompetensi.kode)
    batas = jalur.batas_detik(m)
    benar, total = jalur.lampu_misi(m)
    k = {
        "m": m, "fase": engine.fase(m.level), "r3": m.kompetensi.ranah[:3], "diag": m.jenis == "diagnostik",
        "batas_detik": batas, "menit": batas // 60, "lampu": [i < len(benar) for i in range(total)], "nyala": len(benar), "total": total,
        "judul_bagian": next((b["judul"] for b in (jalan or {}).get(jalur.game_dari_kompetensi(m.kompetensi), {}).get("bagian", []) if b["no"] == m.bagian), ""),
    }
    if isinstance(c, int):
        k.update(angka=c, puluhan=range(c // 10), satuan=range(c % 10))
    else:
        k["kata"] = c or []
    return k


@role_required("murid")
def murid_beranda(request):
    siswa = request.user.siswa
    pen = penguasaan_lengkap(siswa)
    aktif = misi_aktif(siswa)
    jalan = jalur.jalur_siswa(siswa, aktif)
    target = siswa.kelas.target_bagian
    peta = jalur.peta_siswa(siswa, pen, target)
    diagnostik = list(siswa.misi.filter(jenis="diagnostik", status="baru").select_related("kompetensi"))
    return render(request, "belajar/murid_beranda.html", {
        "siswa": siswa, "kartu": [_kartu(m, jalan) for m in aktif], "peta": [{"p": p, "i": i} for p, i in peta],
        "jalan": [jalan["literasi"], jalan["numerasi"]], "lencana": jalur.lencana_siswa(jalan, target),
        "zig_lebar": jalur.ZIGZAG_LEBAR, "zig_tinggi": jalur.ZIGZAG_TINGGI,
        "lencana_menyala": sum(1 for x in jalur.lencana_siswa(jalan, target) if x[3]),
        "diagnostik": diagnostik, "target_bagian": target, "aturan": jalur.aturan_waktu(), "sekolah": settings.LENTERA_SEKOLAH})


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
    # Peta tiap kategori baru bisa mencapai 100% bila kategori itu punya bagian sebanyak target kelas.
    catatan = [(jalur.NAMA_GAME[g], len(jalur.bagian_bisa_dimainkan(g))) for g in jalur.GAMES
               if len(jalur.bagian_bisa_dimainkan(g)) < kelas.target_bagian]
    return render(request, "belajar/guru_peta.html", _ctx(request, kelas, "peta", d=ringkasan_kelas(kelas), catatan_bagian=catatan, target=kelas.target_bagian))


@guru_view
@require_POST
def guru_terapkan(request, kelas):
    """Tombol 'Terapkan ke kelompok': misi sesuai level tiap kelompok (F-10)."""
    k = get_object_or_404(Kompetensi, kode=request.POST["kompetensi"])
    n = terapkan_ke_kelompok(request.user, kelas, k)
    messages.success(request, f"Misi “{k.nama}” dikirim ke {n} siswa sesuai level kelompoknya.")
    return redirect("/guru/")


def _menit(p):
    """Menit aktivitas = batas waktu game. 0 berarti tanpa batas waktu; maksimal 90 menit."""
    mentah = str(p.get("menit", "")).strip()
    menit = 10 if mentah == "" else int(mentah)
    if not 0 <= menit <= 90:
        raise ValueError
    return menit


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
                                         level_min=max(1, a), level_maks=min(5, b), menit=_menit(p), deskripsi=p.get("deskripsi", ""), dibuat_oleh=request.user)
                messages.success(request, "Aktivitas ditambahkan. Validasi dulu sebelum bisa ditugaskan.")
            elif aksi == "edit":
                obj = get_object_or_404(Aktivitas, id=p["id"])
                a, b = sorted((int(p["level_min"]), int(p["level_maks"])))
                if not p["judul"].strip():
                    raise ValueError
                obj.judul = p["judul"].strip()[:80]
                obj.kompetensi = _kompetensi_game(p["kompetensi"])
                obj.level_min, obj.level_maks = max(1, a), min(5, b)
                obj.menit = _menit(p)
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
        a.game = (jalur.GAME_KE[jalur.game_dari_kompetensi(a.kompetensi)] if a.main else "")
        # Game 1 memakai waktu aktivitas dengan kompetensi utamanya (Menyusun Kalimat); Game 2 per kompetensi bagian.
        a.pengatur_waktu = a.main and (a.kompetensi.ranah == "numerasi" or a.kompetensi.kode == KOMPETENSI_UTAMA_LITERASI)
    return render(request, "belajar/guru_aktivitas.html", _ctx(request, kelas, "aktivitas", daftar=daftar, f=f, kompetensi=Kompetensi.objects.filter(kode__in=settings.LENTERA_GAMES),
                  siswa=kelas.siswa.select_related("user"), aturan=jalur.aturan_waktu(), penugasan=Penugasan.objects.filter(kelas=kelas).select_related("aktivitas")[:10]))


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
            elif aksi == "reset_siswa" and kelas:
                s = get_object_or_404(kelas.siswa.all(), id=p["id"])
                nama = s.nama
                arsip = reset_progres_siswa(s, oleh=request.user)
                if arsip:
                    messages.success(
                        request,
                        f"Progres {nama} direset ({arsip.ringkasan}). Jalan Lentera dan Peta Kompetensinya "
                        f"kembali seperti murid baru. Bisa dipulihkan dari Log Reset di bawah sampai "
                        f"{timezone.localtime(arsip.kedaluwarsa_pada).strftime('%d %b %Y')}.",
                    )
                else:
                    messages.success(request, f"{nama} memang belum punya progres untuk direset.")
            elif aksi == "reset_kelas" and kelas:
                n = reset_progres_kelas(kelas, oleh=request.user)
                messages.success(
                    request,
                    f"Progres {n} dari {kelas.siswa.count()} siswa direset." if n else
                    "Belum ada siswa yang punya progres untuk direset.",
                )
            elif aksi == "pulihkan_reset" and kelas:
                arsip = get_object_or_404(ArsipReset, id=p["id"], siswa__kelas=kelas)
                try:
                    pulihkan_arsip(arsip)
                    messages.success(request, f"Progres {arsip.siswa.nama} berhasil dipulihkan seperti sebelum direset.")
                except ValueError as e:
                    messages.error(request, str(e))
            elif aksi == "hapus_reset" and kelas:
                arsip = get_object_or_404(ArsipReset, id=p["id"], siswa__kelas=kelas)
                arsip.delete()
                messages.success(request, "Arsip reset dihapus permanen.")
            elif aksi == "ganti_kode" and kelas:
                kelas.kode = kode_kelas_baru(kelas.nama)
                kelas.save()
                messages.success(request, f"Kode kelas baru: {kelas.kode}. Kartu masuk lama perlu dicetak ulang.")
            elif aksi == "ambang" and kelas:
                kelas.ambang_perhatian = max(1, min(8, int(p["ambang"])))
                kelas.save()
                messages.success(request, "Ambang “Perlu perhatian” disimpan.")
            elif aksi == "target_bagian" and kelas:
                kelas.target_bagian = max(1, min(10, int(p["target_bagian"])))
                kelas.save()
                messages.success(request, f"Peta Kompetensi mencapai 100% setelah murid menyelesaikan {kelas.target_bagian} bagian.")
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
    arsip = []
    if kelas:
        bersihkan_arsip_kedaluwarsa(kelas)
        arsip = list(ArsipReset.objects.filter(siswa__kelas=kelas).select_related("siswa__user", "oleh")[:30])
        for a in arsip:
            a.bisa_pulih = arsip_bisa_dipulihkan(a)
    return render(request, "belajar/guru_pengaturan.html", _ctx(
        request, kelas, "pengaturan",
        siswa=kelas.siswa.select_related("user") if kelas else [],
        arsip=arsip,
        retensi_hari=RESET_RETENSI_HARI,
    ))

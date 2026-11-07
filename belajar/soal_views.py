"""
Bank Soal guru: membuat, mengubah, menonaktifkan, dan menghapus soal Game 1 (Literasi: susun kalimat + baca kalimat)
dan Game 2 (Numerasi: empat operasi hitung).

Soal yang dibuat di sini langsung dipakai game melalui GET /api/game/soal/ (lihat game_api.py).
Satu BAGIAN = 5 LEVEL; tiap level dimainkan dengan 5 soal (5 lampu).
"""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from . import bank_soal, jalur
from .bank_soal import IKON_CERITA, SoalTidakValid
from .models import Kompetensi, SoalGame
from .views import _ctx, guru_view

GAME_SAH = ("literasi", "numerasi")


def _game(nilai):
    return nilai if nilai in GAME_SAH else "literasi"


def _hapus_berkas(soal):
    """Hapus berkas gambar yang diunggah guru bila tidak dipakai soal lain (gambar bawaan tidak disentuh)."""
    nama = soal.gambar.name if soal.gambar else ""
    if nama and not nama.startswith("soal/bawaan/") and not SoalGame.objects.filter(gambar=nama).exclude(pk=soal.pk).exists():
        soal.gambar.delete(save=False)


def _matriks(game):
    """Ringkasan bank per bagian dan level untuk panel atas halaman daftar."""
    data = bank_soal.ringkasan_bank()[game]
    struktur = {b["no"]: b for b in jalur.struktur(game)}
    kompetensi = {}
    if game == "numerasi":
        for no, kode in SoalGame.objects.filter(game="numerasi").values_list("bagian", "kompetensi__nama").distinct():
            kompetensi[no] = kode
    baris = []
    for no in sorted(data):
        level = []
        for n in range(1, jalur.level_per_bagian() + 1):
            aktif, nonaktif = data[no].get(n, [0, 0])
            level.append({"level": n, "aktif": aktif, "nonaktif": nonaktif, "cukup": aktif >= jalur.soal_per_level(), "ada": aktif > 0})
        kurang = [x["level"] for x in level if not x["ada"]]
        baris.append({
            "no": no, "level": level, "lengkap": not kurang, "kurang": kurang,
            "judul": struktur[no]["judul"] if no in struktur else f"Bagian {no}",
            "kompetensi": kompetensi.get(no, ""),
        })
    return baris


@guru_view
def guru_soal(request, kelas):
    game = _game(request.GET.get("game"))

    if request.method == "POST":
        game = _game(request.POST.get("game"))
        aksi = request.POST.get("aksi")
        soal = get_object_or_404(SoalGame, pk=request.POST.get("id"))
        if aksi == "aktif":
            soal.aktif = not soal.aktif
            soal.save(update_fields=["aktif", "diubah"])
            messages.success(request, f"Soal {soal.item_id} {'diaktifkan' if soal.aktif else 'dinonaktifkan (tidak dipakai di game)'}.")
        elif aksi == "hapus":
            item = soal.item_id
            _hapus_berkas(soal)
            soal.delete()
            messages.success(request, f"Soal {item} dihapus dari bank soal.")
        tujuan = request.POST.get("kembali", "")
        if not tujuan or not url_has_allowed_host_and_scheme(tujuan, allowed_hosts={request.get_host()}):
            tujuan = reverse("soal") + f"?game={game}"
        return redirect(tujuan)

    filter_ = {k: request.GET.get(k, "") for k in ("bagian", "level")}
    daftar = SoalGame.objects.filter(game=game).select_related("kompetensi")
    if filter_["bagian"].isdigit():
        daftar = daftar.filter(bagian=int(filter_["bagian"]))
    if filter_["level"].isdigit():
        daftar = daftar.filter(level=int(filter_["level"]))
    daftar = list(daftar)
    for s in daftar:
        s.judul, s.detail, s.jawaban_teks = bank_soal.ringkas(s)
        s.jenis, s.thumb = bank_soal.jenis_dan_thumb(s)

    matriks = _matriks(game)
    return render(request, "belajar/guru_soal.html", _ctx(
        request, kelas, "soal", game=game, daftar=daftar, f=filter_, matriks=matriks,
        bagian_ada=[m["no"] for m in matriks], per_level=jalur.soal_per_level(),
        per_level_baca=getattr(settings, "LENTERA_SOAL_BACA_PER_LEVEL", 2),
        jumlah={g: SoalGame.objects.filter(game=g).count() for g in GAME_SAH},
        kembali=request.get_full_path(),
    ))


def _bagian_awal(game):
    """Saran nomor bagian untuk soal baru: bagian pertama yang belum lengkap, atau bagian berikutnya."""
    matriks = _matriks(game)
    for m in matriks:
        if not m["lengkap"]:
            return m["no"]
    return (matriks[-1]["no"] + 1) if matriks else 1


@guru_view
def guru_soal_form(request, kelas, soal_id=None):
    soal = get_object_or_404(SoalGame.objects.select_related("kompetensi"), pk=soal_id) if soal_id else None
    game = soal.game if soal else _game(request.GET.get("game") or request.POST.get("game"))
    nilai = bank_soal.nilai_form(soal) if soal else {
        "kompetensi": request.GET.get("kompetensi") or ("LT-KALIMAT" if game == "literasi" else "NM-TAMBAH"),
        "bagian": request.GET.get("bagian") or _bagian_awal(game), "level": request.GET.get("level") or 1, "aktif": "1",
        "ikon": "bintang",
    }
    kode_pilihan = bank_soal.KODE_LITERASI if game == "literasi" else bank_soal.KODE_NUMERASI

    if request.method == "POST":
        nilai = request.POST.dict()
        try:
            data = bank_soal.bersihkan_game1(request.POST, request.FILES, soal) if game == "literasi" else bank_soal.bersihkan_game2(request.POST)
            kode = data.pop("kompetensi_kode")
            if game == "numerasi":
                dipakai = bank_soal.kompetensi_bagian_numerasi(data["bagian"], soal.pk if soal else None)
                if dipakai and dipakai != kode:
                    nama = Kompetensi.objects.filter(kode=dipakai).values_list("nama", flat=True).first() or dipakai
                    raise SoalTidakValid(
                        f"Bagian {data['bagian']} Numerasi sudah dipakai untuk kompetensi “{nama}”. "
                        "Pilih nomor bagian lain, atau samakan kompetensinya."
                    )
            gambar = data.pop("gambar_baru")
            if soal is None:
                soal = SoalGame(game=game, dibuat_oleh=request.user)
                akhir = SoalGame.objects.filter(game=game, bagian=data["bagian"], level=data["level"]).order_by("-urutan").values_list("urutan", flat=True).first()
                soal.urutan = (akhir or 0) + 1
            soal.kompetensi = get_object_or_404(Kompetensi, kode=kode)
            for kolom, isi in data.items():
                if kolom != "game":
                    setattr(soal, kolom, isi)
            if gambar:
                lama = soal.gambar.name if soal.pk and soal.gambar else ""
                if lama:
                    _hapus_berkas(soal)
                soal.gambar = gambar
            soal.save()
            messages.success(request, f"Soal {soal.item_id} disimpan (Bagian {soal.bagian}, Level {soal.level}).")
            if request.POST.get("lagi"):
                return redirect(f"{reverse('soal_baru')}?game={game}&bagian={soal.bagian}&level={soal.level}&kompetensi={soal.kompetensi.kode}")
            return redirect(f"{reverse('soal')}?game={game}&bagian={soal.bagian}")
        except SoalTidakValid as galat:
            messages.error(request, str(galat))

    peta_bagian = {}
    if game == "numerasi":
        for no, nama in SoalGame.objects.filter(game="numerasi").values_list("bagian", "kompetensi__kode").distinct():
            peta_bagian[no] = nama
    return render(request, "belajar/guru_soal_form.html", _ctx(
        request, kelas, "soal", game=game, soal=soal, v=nilai,
        kompetensi=list(Kompetensi.objects.filter(kode__in=kode_pilihan)),
        ikon=[(k, v[0]) for k, v in IKON_CERITA.items()], peta_bagian=peta_bagian,
        per_level_baca=getattr(settings, "LENTERA_SOAL_BACA_PER_LEVEL", 2),
        kode_baca=bank_soal.KOMPETENSI_BACA,
        ringkasan=bank_soal.ringkasan_bank()[game], per_level=jalur.soal_per_level(),
        gambar_url=soal.gambar.url if soal and soal.gambar else "",
        gambar_maks_mb=getattr(settings, "LENTERA_GAMBAR_MAKS_MB", 5),
    ))

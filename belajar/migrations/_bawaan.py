"""Bantuan impor soal bawaan untuk migrasi 0005 dan 0006.

Nama berkas berawalan "_" sehingga Django tidak memuatnya sebagai migrasi.
Sumber data: belajar/data/soal_bawaan.json (Game 1: susun kalimat + baca kalimat; Game 2: empat operasi hitung).
"""

import json
from pathlib import Path

from belajar import engine


def kompetensi(Kompetensi, kode):
    """Ambil kompetensi; bila database baru (belum disinkronkan), buat dari taksonomi engine."""
    obj = Kompetensi.objects.filter(kode=kode).first()
    if obj:
        return obj
    for urutan, (k, ranah, nama, judul, cakupan, _contoh) in enumerate(engine.TAKSONOMI):
        if k == kode:
            return Kompetensi.objects.create(kode=k, ranah=ranah, nama=nama, judul=judul, cakupan=cakupan, urutan=urutan)
    raise LookupError(kode)


def impor_soal_bawaan(apps):
    """Isi bank soal dari soal bawaan. Soal yang kode_bawaan-nya sudah ada tidak dibuat ulang."""
    SoalGame = apps.get_model("belajar", "SoalGame")
    Kompetensi = apps.get_model("belajar", "Kompetensi")

    berkas = Path(__file__).resolve().parent.parent / "data" / "soal_bawaan.json"
    data = json.loads(berkas.read_text(encoding="utf-8"))

    for q in data["game1"]:
        if q.get("tipe") == "baca":
            isi = dict(
                instruksi=q["instruksi"], gambar="", kolom_kata=[], jawaban=[], jawaban_lain=[], urutan_bebas=False,
                data={"emoji": q["emoji"]},
            )
        else:
            isi = dict(
                instruksi=q["instruksi"], gambar=q["gambar"], kolom_kata=q["kolom_kata"], jawaban=q["jawaban"],
                jawaban_lain=q["jawaban_lain"], urutan_bebas=q["urutan_bebas"],
            )
        SoalGame.objects.get_or_create(
            kode_bawaan=q["kode"],
            defaults=dict(
                game="literasi", kompetensi=kompetensi(Kompetensi, q["kompetensi"]), bagian=q.get("bagian", 1),
                level=q["level"], urutan=q["urutan"], **isi,
            ),
        )

    for q in data["game2"]:
        SoalGame.objects.get_or_create(
            kode_bawaan=q["kode"],
            defaults=dict(
                game="numerasi", kompetensi=kompetensi(Kompetensi, q["kompetensi"]), bagian=q["bagian"], level=q["level"],
                instruksi=q["instruksi"], petunjuk=q["petunjuk"], kunci=q["kunci"], data=q["data"], urutan=q["urutan"],
            ),
        )

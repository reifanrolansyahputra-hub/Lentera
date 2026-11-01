"""Mengisi bank soal dari soal bawaan Game 1 (25 soal) dan Game 2 (100 soal).

Hanya berjalan sekali per database. Soal yang nanti dihapus guru tidak akan
dibuat ulang saat `migrate` dijalankan lagi.
"""

import json
from pathlib import Path

from django.db import migrations

from belajar import engine

# Urutan bagian Numerasi: satu kompetensi = satu bagian (5 level).
BAGIAN_NUMERASI = {"NM-NILAI-TEMPAT": 1, "NM-REGROUPING": 2, "NM-POLA": 3, "NM-CERITA": 4}


def _kompetensi(Kompetensi, kode):
    """Ambil kompetensi; bila database baru (belum disinkronkan), buat dari taksonomi engine."""
    obj = Kompetensi.objects.filter(kode=kode).first()
    if obj:
        return obj
    for urutan, (k, ranah, nama, judul, cakupan, _contoh) in enumerate(engine.TAKSONOMI):
        if k == kode:
            return Kompetensi.objects.create(kode=k, ranah=ranah, nama=nama, judul=judul, cakupan=cakupan, urutan=urutan)
    raise LookupError(kode)


def impor(apps, schema_editor):
    SoalGame = apps.get_model("belajar", "SoalGame")
    Kompetensi = apps.get_model("belajar", "Kompetensi")
    Misi = apps.get_model("belajar", "Misi")

    berkas = Path(__file__).resolve().parent.parent / "data" / "soal_bawaan.json"
    data = json.loads(berkas.read_text(encoding="utf-8"))

    for q in data["game1"]:
        SoalGame.objects.get_or_create(
            kode_bawaan=q["kode"],
            defaults=dict(
                game="literasi", kompetensi=_kompetensi(Kompetensi, q["kompetensi"]), bagian=1, level=q["level"],
                instruksi=q["instruksi"], gambar=q["gambar"], kolom_kata=q["kolom_kata"], jawaban=q["jawaban"],
                jawaban_lain=q["jawaban_lain"], urutan_bebas=q["urutan_bebas"], urutan=q["urutan"],
            ),
        )

    for q in data["game2"]:
        SoalGame.objects.get_or_create(
            kode_bawaan=q["kode"],
            defaults=dict(
                game="numerasi", kompetensi=_kompetensi(Kompetensi, q["kompetensi"]), bagian=q["bagian"], level=q["level"],
                instruksi=q["instruksi"], petunjuk=q["petunjuk"], kunci=q["kunci"], data=q["data"], urutan=q["urutan"],
            ),
        )

    # Misi lama: tempatkan di bagian yang sesuai dengan kompetensinya supaya kemajuan lama ikut terhitung.
    for kode, bagian in BAGIAN_NUMERASI.items():
        Misi.objects.filter(kompetensi__kode=kode).update(bagian=bagian)


def hapus(apps, schema_editor):
    SoalGame = apps.get_model("belajar", "SoalGame")
    SoalGame.objects.exclude(kode_bawaan="").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("belajar", "0004_jalan_lentera_bank_soal"),
    ]

    operations = [
        migrations.RunPython(impor, hapus),
    ]

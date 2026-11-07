"""Mengisi bank soal dari soal bawaan (Game 1: susun + baca kalimat, Game 2: empat operasi hitung).

Hanya berjalan sekali per database. Soal yang nanti dihapus guru tidak akan
dibuat ulang saat `migrate` dijalankan lagi.

Database lama (yang sudah menjalankan versi awal berkas ini dengan kompetensi Numerasi lama) dirapikan
oleh migrasi 0006.
"""

from django.db import migrations

from ._bawaan import impor_soal_bawaan

# Versi awal berkas ini menempatkan misi Numerasi lama di bagian sesuai kompetensinya.
# Kompetensi lama itu kini dihapus oleh migrasi 0006, jadi pemetaan ini tidak lagi diperlukan.


def impor(apps, schema_editor):
    impor_soal_bawaan(apps)


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

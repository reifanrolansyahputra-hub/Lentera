"""Mengganti kompetensi lama dengan taksonomi baru.

- Literasi : LT-PAHAM (Pemahaman bacaan) diganti LT-KETEPATAN (Ketepatan membaca, soal baca kalimat lewat mikrofon).
             Susun kalimat dan baca kalimat satu kesatuan dalam satu level Game 1.
- Numerasi : Nilai tempat, Regrouping, Pola bilangan, dan Soal cerita diganti empat operasi hitung:
             NM-TAMBAH, NM-KURANG, NM-KALI, NM-BAGI (satu operasi = satu bagian).

Kompetensi lama dihapus beserta data turunannya (aktivitas, penguasaan, misi, jawaban, hasil diagnostik, dan
soal numerasi lama) karena level yang sudah selesai pada kompetensi lama tidak boleh terhitung sebagai level
selesai pada bagian baru. Data murid, kelas, guru, dan poin tidak berubah.

Soal Game 1 bergambar yang dulu bertanda LT-PAHAM tetap dipakai sebagai soal susun kalimat (kini LT-KOSAKATA).
Soal bawaan baru (empat operasi hitung dan 25 kalimat baca) diimpor; soal buatan guru tidak disentuh.
"""

from django.db import migrations

from ._bawaan import impor_soal_bawaan, kompetensi as ambil_kompetensi

LAMA = ["LT-PAHAM", "NM-NILAI-TEMPAT", "NM-REGROUPING", "NM-POLA", "NM-CERITA"]


def ganti(apps, schema_editor):
    Kompetensi = apps.get_model("belajar", "Kompetensi")
    SoalGame = apps.get_model("belajar", "SoalGame")
    Aktivitas = apps.get_model("belajar", "Aktivitas")

    # 1. Soal susun kalimat bertanda LT-PAHAM tetap dipakai: pindahkan ke LT-KOSAKATA sebelum LT-PAHAM dihapus.
    paham = Kompetensi.objects.filter(kode="LT-PAHAM").first()
    if paham:
        SoalGame.objects.filter(kompetensi=paham).update(kompetensi=ambil_kompetensi(Kompetensi, "LT-KOSAKATA"))

    # 2. Hapus kompetensi lama; data turunannya ikut terhapus (CASCADE).
    Kompetensi.objects.filter(kode__in=LAMA).delete()

    # 3. Aktivitas susun kalimat kini memuat baca kalimat juga.
    Aktivitas.objects.filter(kompetensi__kode="LT-KALIMAT", judul="Menyusun Kalimat").update(judul="Susun & Baca Kalimat")

    # 4. Soal bawaan baru.
    impor_soal_bawaan(apps)


class Migration(migrations.Migration):

    dependencies = [
        ("belajar", "0005_impor_soal_bawaan"),
    ]

    operations = [
        # Tidak dapat dibalik: data kompetensi lama sudah dihapus.
        migrations.RunPython(ganti, migrations.RunPython.noop),
    ]

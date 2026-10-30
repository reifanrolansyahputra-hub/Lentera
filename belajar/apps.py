
"""
Konfigurasi aplikasi belajar LENTERA.

Tanggung jawab:
- Menyinkronkan taksonomi kompetensi dari engine.py.
- Memastikan aktivitas resmi tersedia.
- Mempertahankan aktivitas lama dan riwayat belajar.
- Tidak menambahkan soal baru.
- Tidak menghapus aktivitas lama secara otomatis.
"""

from django.apps import AppConfig
from django.db.models.signals import post_migrate


# Format:
# (
#     judul,
#     kode kompetensi,
#     level minimum,
#     level maksimum,
#     durasi menit,
#     deskripsi,
#     validasi awal jika aktivitas baru
# )

BANK_AWAL = [
    # ==================================================
    # LITERASI — GAME 1
    # ==================================================

    (
        "Petualangan Kata",
        "LT-KOSAKATA",
        1,
        5,
        8,
        (
            "Aktivitas literasi yang direncanakan menggunakan "
            "soal asli Game 1 untuk mengenali nama dan makna kata. "
            "Tidak menambahkan bank soal baru. Aktivitas baru "
            "belum divalidasi sampai pemetaan soal di Game 1 "
            "selesai diperbaiki."
        ),
        False,
    ),

    (
        "Membaca Kata dan Kalimat",
        "LT-LANCAR",
        1,
        5,
        8,
        (
            "Aktivitas menggunakan kembali materi kata dan "
            "kalimat dari Game 1. Pengenalan kata tidak sama "
            "dengan pengukuran kelancaran membaca lisan atau "
            "kecepatan membaca. Aktivitas harus divalidasi "
            "setelah pemetaan soal diperbaiki."
        ),
        False,
    ),

    (
        "Pemahaman Konteks Bergambar",
        "LT-PAHAM",
        1,
        5,
        8,
        (
            "Aktivitas yang direncanakan menggunakan soal lama "
            "berbasis gambar untuk memahami informasi dan "
            "konteks. Soal berbasis gambar tidak otomatis "
            "merupakan tes pemahaman bacaan teks. Aktivitas "
            "belum divalidasi sampai pemetaan soal diperbaiki."
        ),
        False,
    ),

    (
        "Menyusun Kalimat",
        "LT-KALIMAT",
        1,
        5,
        8,
        (
            "Menyusun kata menjadi kalimat sesuai gambar, "
            "menggunakan materi asli Game 1. Pemetaan butir "
            "soal ke kompetensi akan disesuaikan di kode game."
        ),
        True,
    ),

    # ==================================================
    # NUMERASI — GAME 2
    # Aktivitas lama tetap dipertahankan.
    # ==================================================

    (
        "Nilai Tempat: Puluhan & Satuan",
        "NM-NILAI-TEMPAT",
        1,
        5,
        10,
        (
            "Menghitung total benda dari sejumlah ikat puluhan "
            "dan satuan untuk memahami konsep nilai tempat. "
            "Bilangan yang dipakai makin besar di level lanjut."
        ),
        True,
    ),

    (
        "Berhitung Simpan & Pinjam",
        "NM-REGROUPING",
        1,
        5,
        10,
        (
            "Latihan penjumlahan dengan teknik menyimpan dan "
            "pengurangan dengan teknik meminjam. Tingkat "
            "kesulitan bertambah pada level selanjutnya."
        ),
        True,
    ),

    (
        "Pola Bilangan",
        "NM-POLA",
        1,
        5,
        8,
        (
            "Menentukan angka berikutnya dari deret bilangan "
            "dengan pola naik, turun, kelipatan, atau aturan "
            "gabungan."
        ),
        True,
    ),

    (
        "Soal Cerita Berhitung",
        "NM-CERITA",
        1,
        5,
        10,
        (
            "Soal cerita berhitung sehari-hari. Tingkat "
            "kesulitan dan jumlah langkah bertambah pada "
            "level yang lebih tinggi."
        ),
        True,
    ),
]


def sinkron_taksonomi(sender=None, **kwargs):
    """
    Sinkronkan kompetensi dan aktivitas ke database.

    Kompetensi dan aktivitas lama tidak dihapus otomatis.
    Riwayat jawaban, penguasaan, serta misi siswa harus
    tetap terjaga.
    """

    from . import engine
    from .models import Aktivitas, Kompetensi

    # --------------------------------------------------
    # 1. Sinkronkan seluruh kompetensi
    # --------------------------------------------------

    for urutan, item in enumerate(engine.TAKSONOMI):
        (
            kode,
            ranah,
            nama,
            judul,
            cakupan,
            _contoh,
        ) = item

        Kompetensi.objects.update_or_create(
            kode=kode,
            defaults={
                "ranah": ranah,
                "nama": nama,
                "judul": judul,
                "cakupan": cakupan,
                "urutan": urutan,
            },
        )

    # --------------------------------------------------
    # 2. Ambil kompetensi yang sudah tersinkron
    # --------------------------------------------------

    kompetensi_map = {
        kompetensi.kode: kompetensi
        for kompetensi in Kompetensi.objects.all()
    }

    # --------------------------------------------------
    # 3. Pastikan aktivitas resmi tersedia
    #
    # Aktivitas yang sudah ada akan diperbarui deskripsi,
    # level, dan durasinya tanpa mengubah status validasi.
    #
    # Aktivitas baru mengikuti status validasi awal pada
    # BANK_AWAL.
    # --------------------------------------------------

    for (
        judul,
        kode,
        level_min,
        level_maks,
        menit,
        deskripsi,
        validasi_awal,
    ) in BANK_AWAL:

        kompetensi = kompetensi_map.get(kode)

        # Jangan membuat aktivitas jika kompetensinya
        # tidak ditemukan di engine.TAKSONOMI.
        if kompetensi is None:
            continue

        aktivitas = (
            Aktivitas.objects.filter(
                kompetensi=kompetensi,
                judul=judul,
            )
            .order_by("id")
            .first()
        )

        defaults = {
            "level_min": level_min,
            "level_maks": level_maks,
            "menit": menit,
            "deskripsi": deskripsi,
        }

        if aktivitas is None:
            # Aktivitas baru dibuat sesuai status validasi
            # awal yang ditetapkan dalam BANK_AWAL.
            Aktivitas.objects.create(
                kompetensi=kompetensi,
                judul=judul,
                tervalidasi=validasi_awal,
                **defaults,
            )

        else:
            # Perbarui informasi aktivitas tanpa mengubah
            # status validasi guru.
            for field, value in defaults.items():
                setattr(aktivitas, field, value)

            aktivitas.save(
                update_fields=list(defaults.keys())
            )

    # Sengaja tidak menghapus aktivitas di luar BANK_AWAL.
    # Aktivitas buatan guru, aktivitas terdahulu, dan
    # catatan terkait tidak boleh terhapus otomatis.


class BelajarConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "belajar"

    def ready(self):
        post_migrate.connect(
            sinkron_taksonomi,
            sender=self,
            dispatch_uid="belajar.sinkron_taksonomi",
            weak=False,
        )

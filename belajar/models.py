from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from . import engine


class Kelas(models.Model):
    nama = models.CharField(max_length=20, unique=True)
    kode = models.CharField(max_length=20, unique=True, help_text="Kode kelas untuk login murid")
    wali_kelas = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="kelas_wali")
    ambang_perhatian = models.PositiveSmallIntegerField(default=3, help_text="Siswa perlu perhatian bila sebanyak ini kompetensinya berstatus Mulai")
    target_bagian = models.PositiveSmallIntegerField(
        default=3,
        help_text="Peta Kompetensi baru mencapai 100% bila murid sudah menyelesaikan sebanyak ini bagian (1 bagian = 5 level)",
    )

    class Meta:
        verbose_name_plural = "Kelas"

    def __str__(self):
        return self.nama


class Siswa(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="siswa")
    kelas = models.ForeignKey(Kelas, on_delete=models.CASCADE, related_name="siswa")
    avatar = models.CharField(max_length=8, default="🙂")
    kode_samaran = models.CharField(max_length=20, unique=True, help_text="ID samaran: satu-satunya ID yang dikenal game")
    poin = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "Siswa"

    @property
    def nama(self):
        return self.user.first_name or self.user.username

    def __str__(self):
        return self.nama


class Kompetensi(models.Model):
    """Diisi otomatis dari engine.TAKSONOMI (lihat apps.sinkron_taksonomi); jangan diubah manual."""
    RANAH = [("literasi", "Literasi"), ("numerasi", "Numerasi")]
    kode = models.CharField(max_length=30, unique=True)
    nama = models.CharField(max_length=60)
    judul = models.CharField(max_length=60, blank=True)
    cakupan = models.CharField(max_length=160, blank=True)
    ranah = models.CharField(max_length=10, choices=RANAH)
    urutan = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ranah", "urutan"]
        verbose_name_plural = "Kompetensi"

    def __str__(self):
        return self.nama


class Penguasaan(models.Model):
    """Status BKT satu siswa pada satu kompetensi."""
    siswa = models.ForeignKey(Siswa, on_delete=models.CASCADE, related_name="penguasaan")
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE, related_name="penguasaan")
    p_l = models.FloatField(default=engine.P_AWAL)
    level = models.PositiveSmallIntegerField(default=1)
    salah_beruntun = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ("siswa", "kompetensi")
        ordering = ["kompetensi__ranah", "kompetensi__urutan"]
        verbose_name_plural = "Penguasaan"

    @property
    def status(self):
        return engine.status(self.p_l)

    @property
    def persen(self):
        return round(self.p_l * 100)


class Aktivitas(models.Model):
    """Bank aktivitas: game pembelajaran yang tersambung ke kompetensi. Hanya yang tervalidasi guru yang boleh ditugaskan/disarankan."""
    judul = models.CharField(max_length=80)
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE)
    level_min = models.PositiveSmallIntegerField(default=1)
    level_maks = models.PositiveSmallIntegerField(default=5)
    menit = models.PositiveSmallIntegerField(default=10)
    deskripsi = models.TextField(blank=True)
    tervalidasi = models.BooleanField(default=False)
    dibuat_oleh = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["kompetensi__ranah", "kompetensi__urutan", "judul"]
        verbose_name_plural = "Aktivitas"


class Misi(models.Model):
    """Satu percobaan memainkan satu level di Jalan Lentera (atau satu misi asesmen diagnostik)."""
    STATUS = [("baru", "Baru"), ("selesai", "Selesai"), ("gagal", "Gagal (waktu habis)")]
    JENIS = [("misi", "Misi"), ("diagnostik", "Asesmen diagnostik")]
    siswa = models.ForeignKey(Siswa, on_delete=models.CASCADE, related_name="misi")
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE)
    bagian = models.PositiveSmallIntegerField(default=1, help_text="Bagian di Jalan Lentera (1 bagian = 5 level)")
    level = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS, default="baru")
    jenis = models.CharField(max_length=12, choices=JENIS, default="misi")
    dari_guru = models.BooleanField(default=False)
    aktivitas = models.ForeignKey(Aktivitas, null=True, blank=True, on_delete=models.SET_NULL)
    soal_ids = models.JSONField(default=list, blank=True, help_text="Daftar SoalGame yang dipakai percobaan ini; dibekukan saat game dibuka")
    skor = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Skor akhir yang dikirim game (sudah dipotong denda waktu)")
    durasi_detik = models.PositiveIntegerField(null=True, blank=True)
    mulai_pada = models.DateTimeField(null=True, blank=True, help_text="Saat soal pertama tampil di game; jam batas waktu berjalan sejak saat ini")
    selesai_pada = models.DateTimeField(null=True, blank=True)
    dibuat = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "Misi"


class SoalGame(models.Model):
    """Bank soal Game 1 (Literasi) dan Game 2 (Numerasi) yang dikelola guru dari dashboard.

    Game 1  : gambar soal + kolom kata + jawaban benar (urutan kata).
    Game 2  : teks/angka soal + jawaban benar (angka), tergantung kompetensi.
    """
    GAME = [("literasi", "Game 1 · Literasi"), ("numerasi", "Game 2 · Numerasi")]
    game = models.CharField(max_length=10, choices=GAME)
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE, related_name="soal_game")
    bagian = models.PositiveSmallIntegerField(default=1)
    level = models.PositiveSmallIntegerField(default=1)
    aktif = models.BooleanField(default=True)
    instruksi = models.TextField(blank=True, help_text="Game 1: pertanyaan. Game 2: teks soal cerita (opsional untuk jenis lain).")
    petunjuk = models.CharField(max_length=240, blank=True)

    # Game 1
    gambar = models.FileField(upload_to="soal/", blank=True)
    kolom_kata = models.JSONField(default=list, blank=True)
    jawaban = models.JSONField(default=list, blank=True, help_text="Game 1: urutan kata yang benar")
    jawaban_lain = models.JSONField(default=list, blank=True, help_text="Game 1: daftar urutan kata lain yang juga benar")
    urutan_bebas = models.BooleanField(default=False, help_text="Game 1: urutan kata tidak dinilai (menyebutkan semua)")

    # Game 2
    kunci = models.IntegerField(null=True, blank=True, help_text="Game 2: jawaban benar (angka)")
    data = models.JSONField(default=dict, blank=True, help_text="Game 2: data tambahan sesuai jenis soal")

    kode_bawaan = models.CharField(max_length=40, blank=True, db_index=True, help_text="ID soal bawaan game (mis. LIT-SPO-01)")
    urutan = models.PositiveIntegerField(default=0)
    dibuat_oleh = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    dibuat = models.DateTimeField(default=timezone.now)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["game", "bagian", "level", "urutan", "id"]
        verbose_name_plural = "Soal game"

    @property
    def item_id(self):
        """ID soal yang dikirim game pada setiap event jawaban (maks. 40 karakter)."""
        return self.kode_bawaan or f"SG-{self.pk}"

    def __str__(self):
        return f"{self.item_id} · B{self.bagian} L{self.level}"


class Jawaban(models.Model):
    """Log event jawaban (append-only; proposal 7.3). event_id = UUID dari perangkat agar kiriman ulang dari antrean offline tidak ganda."""
    siswa = models.ForeignKey(Siswa, on_delete=models.CASCADE, related_name="jawaban")
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE)
    misi = models.ForeignKey(Misi, null=True, blank=True, on_delete=models.SET_NULL)
    level = models.PositiveSmallIntegerField(default=1)
    item_id = models.CharField(max_length=40, blank=True)
    benar = models.BooleanField()
    pola = models.CharField(max_length=30, blank=True, help_text="Kode pola kesalahan (engine.POLA)")
    waktu_ms = models.PositiveIntegerField(default=0)
    petunjuk = models.BooleanField(default=False)
    p_setelah = models.FloatField(null=True, blank=True, help_text="P(L) sesudah jawaban ini; dasar perubahan penguasaan mingguan")
    event_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    dibuat = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name_plural = "Jawaban"


class HasilDiagnostik(models.Model):
    """Hasil asesmen diagnostik: sumber P(L) awal dan level awal tiap kompetensi (proposal 6.6b)."""
    SUMBER = [("game", "Game"), ("manual", "Input guru")]
    siswa = models.ForeignKey(Siswa, on_delete=models.CASCADE, related_name="diagnostik")
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE)
    benar = models.PositiveSmallIntegerField()
    total = models.PositiveSmallIntegerField()
    level_awal = models.PositiveSmallIntegerField()
    p_awal = models.FloatField()
    sumber = models.CharField(max_length=8, choices=SUMBER, default="manual")
    dibuat = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "Hasil diagnostik"


class Penugasan(models.Model):
    """Riwayat penugasan dari Bank Aktivitas (termasuk rencana kegiatan kelas yang tidak punya game)."""
    aktivitas = models.ForeignKey(Aktivitas, on_delete=models.CASCADE)
    kelas = models.ForeignKey(Kelas, on_delete=models.CASCADE)
    target = models.CharField(max_length=60)
    jumlah = models.PositiveSmallIntegerField(default=0)
    selesai = models.BooleanField(default=False)
    dibuat_oleh = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    dibuat = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-dibuat"]
        verbose_name_plural = "Penugasan"

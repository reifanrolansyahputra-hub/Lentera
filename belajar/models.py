from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from . import engine


class Kelas(models.Model):
    nama = models.CharField(max_length=20, unique=True)
    kode = models.CharField(max_length=20, unique=True, help_text="Kode kelas untuk login murid")
    wali_kelas = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="kelas_wali")
    ambang_perhatian = models.PositiveSmallIntegerField(default=3, help_text="Siswa perlu perhatian bila sebanyak ini kompetensinya berstatus Mulai")

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
    STATUS = [("baru", "Baru"), ("selesai", "Selesai")]
    JENIS = [("misi", "Misi"), ("diagnostik", "Asesmen diagnostik")]
    siswa = models.ForeignKey(Siswa, on_delete=models.CASCADE, related_name="misi")
    kompetensi = models.ForeignKey(Kompetensi, on_delete=models.CASCADE)
    level = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS, default="baru")
    jenis = models.CharField(max_length=12, choices=JENIS, default="misi")
    dari_guru = models.BooleanField(default=False)
    aktivitas = models.ForeignKey(Aktivitas, null=True, blank=True, on_delete=models.SET_NULL)
    dibuat = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "Misi"


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

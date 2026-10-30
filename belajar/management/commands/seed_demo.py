import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Profile
from belajar.apps import sinkron_taksonomi
from belajar.models import Kelas, Kompetensi, Siswa
from belajar.services import catat_jawaban

MURID = [("Raka", "🦊"), ("Alya", "🐼"), ("Bima", "🐯"), ("Citra", "🦄"), ("Dimas", "🐸"), ("Fitri", "🐰"),
         ("Gilang", "🦁"), ("Hana", "🐙"), ("Indra", "🐧"), ("Jihan", "🐨"), ("Kirana", "🦋"), ("Laras", "🐢")]
POLA_DEMO = {"LT-LANCAR": "ny_ng", "LT-KALIMAT": "urutan_spo", "NM-NILAI-TEMPAT": "nilai_tempat", "NM-REGROUPING": "regrouping"}


def buat_user(username, nama, role, sandi=None):
    u, baru = User.objects.get_or_create(username=username, defaults={"first_name": nama})
    if baru:
        u.set_password(sandi) if sandi else u.set_unusable_password()
        u.save()
    Profile.objects.get_or_create(user=u, defaults={"role": role})
    return u


class Command(BaseCommand):
    help = "Data demo: kelas 3B, guru, 12 murid, dan ±8 minggu riwayat jawaban simulasi lewat services.catat_jawaban (jalur sama dengan API game)."

    def handle(self, *args, **opts):
        random.seed(7)
        sinkron_taksonomi()
        guru = buat_user("guru_sari", "Bu Sari", "guru", "guru12345")
        kelas, _ = Kelas.objects.get_or_create(kode="3B-LENTERA", defaults={"nama": "3B", "wali_kelas": guru})
        kom = list(Kompetensi.objects.all())
        now = timezone.now()
        with transaction.atomic():
            for i, (nama, avatar) in enumerate(MURID):
                s, baru = Siswa.objects.get_or_create(user=buat_user(f"murid_{nama.lower()}", nama, "murid"),
                                                      defaults={"kelas": kelas, "avatar": avatar, "kode_samaran": f"S-{1000 + i}"})
                if not baru:
                    continue
                bakat, off = random.uniform(0.2, 0.75), {k.kode: random.uniform(-0.25, 0.2) for k in kom}
                for hari in range(56, -1, -1):  # kronologis
                    if hari > 0 and random.random() > 0.35:
                        continue
                    mulai = now - timedelta(days=hari, minutes=16 if hari == 0 else random.randint(0, 240))
                    for j in range(random.randint(8, 14)):
                        k = random.choice(kom)
                        t = mulai + timedelta(seconds=60 * j)
                        benar = random.random() < min(0.97, max(0.1, bakat + off[k.kode] + (56 - hari) * 0.002))
                        pola = POLA_DEMO.get(k.kode, "") if (not benar and random.random() < 0.7) else ""
                        catat_jawaban(s, k.kode, benar, 0, "demo", pola, random.randint(30000, 90000), False, t)
        self.stdout.write("Seed selesai. Murid: kode kelas 3B-LENTERA | Guru: guru_sari / guru12345")

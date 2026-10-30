from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from accounts.models import Profile


class Command(BaseCommand):
    help = 'Buat akun guru: python manage.py buat_guru sari "Bu Sari" kataSandi'

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("nama")
        parser.add_argument("sandi")

    def handle(self, *args, **o):
        u = User.objects.create_user(o["username"], password=o["sandi"], first_name=o["nama"])
        Profile.objects.create(user=u, role="guru")
        self.stdout.write(f"Guru {u.username} dibuat. Masuk lalu buat kelas di menu Pengaturan.")

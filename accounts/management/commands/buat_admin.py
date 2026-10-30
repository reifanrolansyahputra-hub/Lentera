from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Profile


class Command(BaseCommand):
    help = 'Buat akun admin: python manage.py buat_admin budi "Pak Budi" kataSandi'

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("nama")
        parser.add_argument("sandi")

    def handle(self, *args, **o):
        if len(o["sandi"]) < 6:
            raise CommandError("Kata sandi minimal 6 karakter.")
        if User.objects.filter(username=o["username"]).exists():
            raise CommandError(f"Username {o['username']} sudah dipakai.")
        u = User.objects.create_user(o["username"], password=o["sandi"], first_name=o["nama"])
        Profile.objects.create(user=u, role="admin")
        self.stdout.write(f"Admin {u.username} dibuat. Masuk di /login/ untuk membuat kelas dan akun guru.")

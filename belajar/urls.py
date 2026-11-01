
from django.urls import path

from . import admin_views, game_api, soal_views, views


urlpatterns = [
    # Beranda dan dashboard murid
    path("", views.root),
    path("murid/", views.murid_beranda, name="murid"),

    # Dashboard guru
    path("guru/", views.guru_insight, name="guru"),
    path("guru/peta/", views.guru_peta, name="peta"),
    path("guru/aktivitas/", views.guru_aktivitas, name="aktivitas"),
    path("guru/soal/", soal_views.guru_soal, name="soal"),
    path("guru/soal/baru/", soal_views.guru_soal_form, name="soal_baru"),
    path("guru/soal/<int:soal_id>/", soal_views.guru_soal_form, name="soal_ubah"),
    path("guru/diagnostik/", views.guru_diagnostik, name="diagnostik"),
    path("guru/laporan/", views.guru_laporan, name="laporan"),
    path("guru/pengaturan/", views.guru_pengaturan, name="pengaturan"),
    path("guru/kartu/", views.guru_kartu, name="kartu"),
    path("guru/terapkan/", views.guru_terapkan, name="terapkan"),

    # Dashboard admin
    path("admin/", admin_views.admin_kelas, name="admin_kelas"),
    path("admin/guru/", admin_views.admin_guru, name="admin_guru"),
    path("admin/laporan/", admin_views.admin_laporan, name="admin_laporan"),
    path(
        "admin/kartu/<int:kelas_id>/",
        admin_views.admin_kartu,
        name="admin_kartu",
    ),

    # Membuka Game 1 atau Game 2 berdasarkan kompetensi misi
    path(
        "game/mulai/<int:misi_id>/",
        game_api.mulai_game,
        name="mulai_game",
    ),

    # API game: soal level, mulai jam batas waktu, dan pencatatan jawaban / selesai / gagal
    path("api/game/soal/", game_api.api_soal, name="api_soal"),
    path("api/game/mulai/", game_api.api_mulai, name="api_mulai"),
    path(
        "api/game/event/",
        game_api.api_event,
        name="api_event",
    ),
]

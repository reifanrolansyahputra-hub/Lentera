"""
Uji otomatis: Jalan Lentera, bank soal guru, API game (soal, mulai, event), batas waktu, dan Peta Kompetensi.

Jalankan:  python manage.py test belajar
"""

import io
import json
import shutil
import tempfile
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import bank_soal, jalur
from .models import Aktivitas, HasilDiagnostik, Jawaban, Kelas, Kompetensi, Misi, Penguasaan, Siswa, SoalGame
from .services import (aktifkan_diagnostik, buat_guru, buat_kelas, ringkasan_kelas, tambah_siswa, terapkan_ke_kelompok,
                       tugaskan_aktivitas)

MEDIA_UJI = tempfile.mkdtemp(prefix="lentera-media-")

# PNG 1x1 yang valid, untuk uji unggah gambar.
PNG_KECIL = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\x9d\x1e\x00\x00\x00\x00IEND\xaeB`\x82"
)


def jawab(client, token, soal, benar=True, **ekstra):
    """Kirim satu event jawaban untuk `soal` (dict dari API soal)."""
    badan = {"events": [{"item_id": soal["id"], "kompetensi": soal.get("kompetensiDashboard", ""), "benar": benar}], **ekstra}
    if not badan["events"][0]["kompetensi"]:
        del badan["events"][0]["kompetensi"]
    return client.post("/api/game/event/", json.dumps(badan), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")


class DasarUji(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_UJI, ignore_errors=True)

    def setUp(self):
        self.guru = buat_guru("guru_uji", "Bu Uji", "rahasia123")
        self.kelas = buat_kelas(self.guru, "4A")
        self.siswa = tambah_siswa(self.kelas, "Budi")
        self.klien = Client()
        self.klien.force_login(self.siswa.user)

    # --- pembantu ---
    def token_untuk(self, misi):
        r = self.klien.get(f"/game/mulai/{misi.id}/")
        self.assertEqual(r.status_code, 302, r.content[:200])
        return parse_qs(urlsplit(r["Location"]).query)["ssoToken"][0]

    def api(self, token, path="soal"):
        return Client().get(f"/api/game/{path}/", HTTP_AUTHORIZATION=f"Bearer {token}")

    def mulai(self, token):
        return Client().post("/api/game/mulai/", "{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")

    def kirim(self, token, badan):
        return Client().post("/api/game/event/", json.dumps(badan), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")

    def selesaikan_level(self, misi, skor=90):
        """Mainkan satu level sampai selesai lewat API (semua jawaban benar)."""
        token = self.token_untuk(misi)
        soal = self.api(token).json()["soal"]
        for q in soal:
            r = jawab(Client(), token, q)
            self.assertEqual(r.status_code, 200, r.content)
        r = self.kirim(token, {"events": [], "selesai": True, "skor": skor, "durasi_detik": 100})
        self.assertTrue(r.json()["selesai"], r.content)
        return r.json()


class JalurTest(DasarUji):
    def test_struktur_bawaan(self):
        lit = jalur.struktur("literasi")
        num = jalur.struktur("numerasi")
        self.assertEqual([b["no"] for b in lit], [1])
        self.assertEqual([b["no"] for b in num], [1, 2, 3, 4])
        self.assertEqual([b["kompetensi"].kode for b in num], ["NM-TAMBAH", "NM-KURANG", "NM-KALI", "NM-BAGI"])
        for b in lit + num:
            self.assertTrue(b["lengkap"])
            self.assertEqual([x["jumlah"] for x in b["level"]], [5] * 5)

    def test_misi_aktif_satu_per_kategori_mulai_level_1(self):
        misi = jalur.misi_aktif(self.siswa)
        self.assertEqual([m.kompetensi.ranah for m in misi], ["literasi", "numerasi"])
        self.assertTrue(all((m.bagian, m.level) == (1, 1) for m in misi))
        self.assertEqual(misi[1].kompetensi.kode, "NM-TAMBAH")
        # Memanggil ulang tidak membuat misi ganda.
        self.assertEqual([m.id for m in jalur.misi_aktif(self.siswa)], [m.id for m in misi])

    def test_selesai_lima_level_membuka_bagian_berikutnya(self):
        for harapan in range(1, 6):
            lit = jalur.misi_aktif(self.siswa)[0]
            self.assertEqual((lit.bagian, lit.level), (1, harapan))
            self.selesaikan_level(lit)
        # Literasi hanya punya bagian 1 -> tidak ada misi lagi untuk Literasi.
        aktif = jalur.misi_aktif(self.siswa)
        self.assertEqual([m.kompetensi.ranah for m in aktif], ["numerasi"])
        for harapan in range(1, 6):
            num = jalur.misi_aktif(self.siswa)[0]
            self.assertEqual((num.bagian, num.level), (1, harapan))
            self.selesaikan_level(num)
        num = jalur.misi_aktif(self.siswa)[0]
        self.assertEqual((num.bagian, num.level, num.kompetensi.kode), (2, 1, "NM-KURANG"))
        j = jalur.jalur_siswa(self.siswa)
        self.assertTrue(j["numerasi"]["bagian"][0]["penuh"])
        self.assertTrue(j["literasi"]["selesai_semua"])
        lencana = {n: nyala for _, n, _, nyala in jalur.lencana_siswa(j, 3)}
        self.assertTrue(lencana["Pembaca Tekun"] and lencana["Raja Tambah"])
        self.assertFalse(lencana["Jagoan Kurang"])

    def test_bagian_tidak_lengkap_tidak_tampil_di_jalur_murid(self):
        guru_soal = SoalGame.objects.filter(game="literasi")
        baru = SoalGame.objects.create(game="literasi", kompetensi=Kompetensi.objects.get(kode="LT-KALIMAT"), bagian=2, level=1,
                                       instruksi="x", kolom_kata=["a", "b"], jawaban=["a", "b"], gambar="soal/bawaan/buah.jpg")
        self.assertEqual([b["no"] for b in jalur.struktur("literasi")], [1, 2])
        self.assertEqual([b["no"] for b in jalur.bagian_bisa_dimainkan("literasi")], [1])
        self.assertFalse(jalur.struktur("literasi")[1]["lengkap"])
        # lengkapi bagian 2 -> muncul
        for level in range(1, 6):
            SoalGame.objects.get_or_create(game="literasi", bagian=2, level=level, kode_bawaan=f"UJI-{level}",
                                           defaults=dict(kompetensi=baru.kompetensi, instruksi="x", kolom_kata=["a", "b"], jawaban=["a", "b"], gambar="soal/bawaan/buah.jpg"))
        self.assertEqual([b["no"] for b in jalur.bagian_bisa_dimainkan("literasi")], [1, 2])
        self.assertTrue(guru_soal.exists())

    def test_lampu_menyala_per_jawaban_benar(self):
        misi = jalur.misi_aktif(self.siswa)[0]
        token = self.token_untuk(misi)
        soal = self.api(token).json()["soal"]
        self.assertEqual(len(soal), 5)
        self.assertEqual(jalur.lampu_misi(Misi.objects.get(pk=misi.pk))[1], 5)
        self.assertEqual(jawab(Client(), token, soal[0], benar=False).json()["lampu"]["nyala"], 0)
        r = jawab(Client(), token, soal[0], benar=True).json()
        self.assertEqual((r["lampu"]["nyala"], r["lampu"]["total"]), (1, 5))
        r = jawab(Client(), token, soal[1], benar=True).json()
        self.assertEqual(r["lampu"]["benar_ids"], sorted([soal[0]["id"], soal[1]["id"]]))
        j = jalur.jalur_siswa(self.siswa)["literasi"]
        self.assertEqual(j["bagian"][0]["level"][0]["status"], "kini")
        self.assertEqual(j["bagian"][0]["level"][0]["lampu"], [True, True, False, False, False])


class ApiGameTest(DasarUji):
    def test_soal_format_asli_game1_dan_game2(self):
        lit, num = jalur.misi_aktif(self.siswa)
        q1 = self.api(self.token_untuk(lit)).json()
        self.assertEqual((q1["game"], q1["bagian"], q1["level"], q1["total"]), ("literasi", 1, 1, 5))
        s = q1["soal"][0]
        for kunci in ("id", "tipe", "gambar", "instruksi", "correctOrder", "words", "kompetensiDashboard", "errorPatterns"):
            self.assertIn(kunci, s)
        self.assertTrue(s["gambar"].startswith("http://testserver/media/soal/bawaan/"))
        self.assertEqual(s["id"], "LIT-SPO-01")
        self.assertEqual(s["correctOrder"], ["Ibu", "membeli", "sayur"])
        self.assertEqual(s["errorPatterns"], {"sayur_membeli_Ibu": "SPO_TERBALIK"})
        self.assertEqual(s["tipe"], "susun")

        q2 = self.api(self.token_untuk(num)).json()
        self.assertEqual((q2["game"], q2["kompetensi"], q2["total"]), ("numerasi", "NM-TAMBAH", 5))
        s = q2["soal"][0]
        self.assertEqual(s["id"], "M2-NM-TAMBAH-L1-Q1")
        self.assertEqual((s["tipe"], s["operator"], s["kunciJawaban"], s["val1"], s["val2"]), ("penjumlahan", "+", 5, 2, 3))
        self.assertIn("<b>2 apel</b>", s["teksSoal"])
        self.assertEqual((s["label1"], s["label2"]), ("Punya", "Dapat lagi"))

    def test_token_wajib_dan_cors_get(self):
        self.assertEqual(Client().get("/api/game/soal/").status_code, 401)
        self.assertEqual(Client().get("/api/game/soal/", HTTP_AUTHORIZATION="Bearer ngawur").status_code, 401)
        token = self.token_untuk(jalur.misi_aktif(self.siswa)[0])
        r = Client().options("/api/game/soal/", HTTP_ORIGIN="http://127.0.0.1:5000")
        self.assertIn("GET", r["Access-Control-Allow-Methods"])
        self.assertEqual(r["Access-Control-Allow-Origin"], "http://127.0.0.1:5000")
        r = Client().get("/api/game/soal/", HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_ORIGIN="http://127.0.0.1:5500")
        self.assertEqual(r["Access-Control-Allow-Origin"], "http://127.0.0.1:5500")
        r = Client().get("/api/game/soal/", HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_ORIGIN="http://jahat.example")
        self.assertNotIn("Access-Control-Allow-Origin", r)

    def test_item_id_asing_ditolak_dan_selesai_butuh_semua_lampu(self):
        misi = jalur.misi_aktif(self.siswa)[1]
        token = self.token_untuk(misi)
        soal = self.api(token).json()["soal"]
        r = self.kirim(token, {"events": [{"item_id": "M2-NM-TAMBAH-L2-Q1", "benar": True}]})
        self.assertEqual(r.status_code, 400)
        # selesai sebelum semua benar -> tidak ditandai selesai
        jawab(Client(), token, soal[0])
        r = self.kirim(token, {"events": [], "selesai": True}).json()
        self.assertFalse(r["selesai"])
        for q in soal[1:]:
            jawab(Client(), token, q)
        r = self.kirim(token, {"events": [], "selesai": True, "skor": 85, "durasi_detik": 77}).json()
        self.assertTrue(r["selesai"])
        misi.refresh_from_db()
        self.assertEqual((misi.status, misi.skor, misi.durasi_detik), ("selesai", 85, 77))
        self.assertIsNotNone(misi.selesai_pada)
        # idempoten
        self.assertTrue(self.kirim(token, {"events": [], "selesai": True}).json()["selesai"])
        # soal level yang sudah selesai tidak bisa diambil lagi
        self.assertEqual(self.api(token).status_code, 409)

    def test_game1_menerima_kompetensi_literasi_lain_dalam_satu_level(self):
        # level 2 Game 1 memuat soal susun (LT-KALIMAT / LT-KOSAKATA) dan soal baca (LT-KETEPATAN)
        self.selesaikan_level(jalur.misi_aktif(self.siswa)[0])
        misi = jalur.misi_aktif(self.siswa)[0]
        token = self.token_untuk(misi)
        soal = self.api(token).json()["soal"]
        kode = {q["kompetensiDashboard"] for q in soal}
        self.assertIn("LT-KETEPATAN", kode)
        self.assertTrue(kode <= {"LT-KALIMAT", "LT-KOSAKATA", "LT-KETEPATAN"}, kode)
        for q in soal:
            self.assertEqual(jawab(Client(), token, q).status_code, 200)
        # kompetensi numerasi pada misi literasi ditolak
        r = self.kirim(token, {"events": [{"item_id": soal[0]["id"], "kompetensi": "NM-KALI", "benar": True}]})
        self.assertEqual(r.status_code, 400)
        # jawaban tercatat pada kompetensinya masing-masing
        self.assertTrue(Jawaban.objects.filter(misi=misi, kompetensi__kode="LT-KETEPATAN").exists())

    def test_level_literasi_memadukan_susun_dan_baca_satu_kesatuan(self):
        lit = jalur.misi_aktif(self.siswa)[0]
        token = self.token_untuk(lit)
        data = self.api(token).json()
        soal = data["soal"]
        # tiga soal susun lalu dua soal baca, dalam satu level dan satu jam yang sama
        self.assertEqual([q["tipe"] for q in soal], ["susun", "susun", "susun", "baca", "baca"])
        self.assertEqual(data["total"], 5)
        for q in soal[3:]:
            self.assertEqual(q["kompetensiDashboard"], "LT-KETEPATAN")
            self.assertEqual(q["jenisKompetensi"], "baca_lantang")
            for kunci in ("kalimat", "emoji"):
                self.assertTrue(q[kunci])
            self.assertTrue(q["id"].startswith("LIT-BACA-"))
        for q in soal[:3]:
            self.assertNotEqual(q["kompetensiDashboard"], "LT-KETEPATAN")
        # selesai hanya bila kelima lampu (susun + baca) menyala
        for q in soal[:4]:
            jawab(Client(), token, q)
        self.assertFalse(self.kirim(token, {"events": [], "selesai": True}).json()["selesai"])
        jawab(Client(), token, soal[4])
        self.assertTrue(self.kirim(token, {"events": [], "selesai": True, "skor": 90, "durasi_detik": 120}).json()["selesai"])
        self.assertEqual(jalur.level_selesai([self.siswa.id])[(self.siswa.id, "literasi")], {(1, 1)})

    def test_server_menentukan_kompetensi_jawaban_baca_dan_susun(self):
        lit = jalur.misi_aktif(self.siswa)[0]
        token = self.token_untuk(lit)
        soal = self.api(token).json()["soal"]
        susun, baca = soal[0], soal[4]
        # game keliru mengirim bacaan sebagai LT-KALIMAT: server tetap mencatatnya sebagai LT-KETEPATAN
        r = self.kirim(token, {"events": [{"event_id": "e-baca", "item_id": baca["id"], "kompetensi": "LT-KALIMAT", "level": 1, "benar": True}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Jawaban.objects.get(event_id="e-baca").kompetensi.kode, "LT-KETEPATAN")
        # soal susun yang ditandai LT-KETEPATAN dicatat pada kompetensi soalnya, bukan ketepatan membaca
        r = self.kirim(token, {"events": [{"event_id": "e-susun", "item_id": susun["id"], "kompetensi": "LT-KETEPATAN", "level": 1, "benar": True}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Jawaban.objects.get(event_id="e-susun", kompetensi__kode=susun["kompetensiDashboard"]).item_id, susun["id"])
        self.assertFalse(Jawaban.objects.filter(item_id=susun["id"], kompetensi__kode="LT-KETEPATAN").exists())

    def test_jawaban_baca_hanya_milik_ketepatan_dan_tidak_dicerminkan(self):
        lit = jalur.misi_aktif(self.siswa)[0]
        token = self.token_untuk(lit)
        soal = self.api(token).json()["soal"]
        susun, baca = soal[0], soal[4]
        # bacaan lewat mikrofon: hanya LT-KETEPATAN yang menerima
        jawab(Client(), token, baca)
        self.assertEqual(Jawaban.objects.filter(siswa=self.siswa).count(), 1)
        self.assertEqual(Jawaban.objects.get(siswa=self.siswa).kompetensi.kode, "LT-KETEPATAN")
        # jawaban susun kalimat dicerminkan ke kompetensi susun lain, tetapi tidak ke LT-KETEPATAN
        jawab(Client(), token, susun)
        asal = susun["kompetensiDashboard"]
        diterima = set(Jawaban.objects.filter(siswa=self.siswa, item_id=susun["id"]).values_list("kompetensi__kode", flat=True))
        self.assertEqual(diterima, {"LT-KALIMAT", "LT-KOSAKATA", "LT-LANCAR"} | {asal})
        self.assertNotIn("LT-KETEPATAN", diterima)
        self.assertEqual(Jawaban.objects.filter(siswa=self.siswa, kompetensi__kode="LT-KETEPATAN").count(), 1)

    def test_diagnostik_tidak_dibatasi_waktu_dan_tidak_menghalangi_jalur(self):
        n = aktifkan_diagnostik(self.kelas)
        self.assertEqual(n, 8)
        diag = Misi.objects.filter(siswa=self.siswa, jenis="diagnostik")
        self.assertEqual(diag.count(), 8)
        # misi jalur tetap tampil walau diagnostik belum dikerjakan
        self.assertEqual([m.jenis for m in jalur.misi_aktif(self.siswa)], ["misi", "misi"])
        d = diag.get(kompetensi__kode="NM-KALI")
        self.assertEqual((d.bagian, d.level), (3, 1))
        token = self.token_untuk(d)
        r = self.api(token).json()
        self.assertEqual((r["batas_detik"], r["mode"]), (0, "diagnostik"))
        self.assertEqual(r["soal"][0]["tipe"], "perkalian")
        for q in r["soal"]:
            jawab(Client(), token, q, benar=True)
        self.assertTrue(self.kirim(token, {"events": [], "selesai": True}).json()["selesai"])
        self.assertTrue(HasilDiagnostik.objects.filter(siswa=self.siswa, kompetensi__kode="NM-KALI", benar=5, total=5).exists())
        # diagnostik tidak menghitung sebagai level jalur
        self.assertEqual(jalur.level_selesai([self.siswa.id]), {})


class BatasWaktuTest(DasarUji):
    def test_batas_dari_menit_bank_aktivitas(self):
        lit, num = jalur.misi_aktif(self.siswa)
        self.assertEqual((jalur.batas_detik(lit), jalur.batas_detik(num)), (8 * 60, 10 * 60))
        a = Aktivitas.objects.get(judul="Penjumlahan Seru")
        a.menit = 3
        a.save()
        self.assertEqual(jalur.batas_detik(num), 180)
        self.assertEqual(self.api(self.token_untuk(num)).json()["batas_detik"], 180)
        a.menit = 0  # 0 = tanpa batas waktu
        a.save()
        self.assertEqual(jalur.batas_detik(num), 0)

    def test_aktivitas_yang_ditugaskan_menentukan_waktu(self):
        khusus = Aktivitas.objects.create(judul="Kilat", kompetensi=Kompetensi.objects.get(kode="NM-TAMBAH"), level_min=1,
                                          level_maks=2, menit=2, tervalidasi=True)
        n, main = tugaskan_aktivitas(self.guru, self.kelas, khusus, "kelas")
        self.assertTrue(main)
        self.assertEqual(n, 1)
        num = [m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0]
        self.assertEqual(jalur.batas_detik(num), 120)
        self.assertTrue(num.dari_guru)

    def test_denda_waktu_tiap_10_detik_maks_30_detik_lalu_gagal(self):
        d = jalur.denda_waktu
        self.assertEqual(d(100, 0), (0, False))        # tanpa batas
        self.assertEqual(d(60, 60), (0, False))        # tepat di batas
        self.assertEqual(d(69, 60), (0, False))
        self.assertEqual(d(70, 60), (1, False))        # 10 detik lewat: -5
        self.assertEqual(d(80, 60), (2, False))
        self.assertEqual(d(90, 60), (3, False))        # 30 detik lewat: -15 total, masih boleh
        self.assertEqual(d(91, 60), (3, True))         # lewat 30 detik: gagal
        self.assertEqual(d(500, 60), (3, True))

    def test_jam_mulai_dan_gagal_karena_waktu(self):
        misi = jalur.misi_aktif(self.siswa)[1]
        token = self.token_untuk(misi)
        soal = self.api(token).json()["soal"]
        self.assertFalse(self.api(token).json()["dimulai"])
        r = self.mulai(token).json()
        self.assertEqual((r["gagal"], r["berjalan_detik"], r["batas_detik"]), (False, 0, 600))
        self.assertTrue(self.api(token).json()["dimulai"])
        jawab(Client(), token, soal[0])

        # klaim gagal terlalu cepat ditolak
        r = self.kirim(token, {"events": [], "gagal": "waktu", "skor": 40, "durasi_detik": 700})
        self.assertEqual(r.status_code, 409)
        misi.refresh_from_db()
        self.assertEqual(misi.status, "baru")

        # waktu berjalan melewati batas + toleransi
        Misi.objects.filter(pk=misi.pk).update(mulai_pada=timezone.now() - timedelta(seconds=600 + 31))
        r = self.kirim(token, {"events": [], "gagal": "waktu", "skor": 40, "durasi_detik": 631})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["gagal"])
        misi.refresh_from_db()
        self.assertEqual((misi.status, misi.skor, misi.durasi_detik), ("gagal", 40, 631))
        # tidak bisa melanjutkan misi yang gagal
        self.assertEqual(jawab(Client(), token, soal[1]).status_code, 409)
        self.assertEqual(self.api(token).status_code, 409)
        # percobaan baru: level yang sama, soal baru (lampu kembali kosong), level belum dianggap selesai
        baru = [m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0]
        self.assertNotEqual(baru.id, misi.id)
        self.assertEqual((baru.bagian, baru.level), (1, 1))
        self.assertEqual(jalur.lampu_misi(baru)[0], [])
        self.assertEqual(jalur.level_selesai([self.siswa.id]), {})

    def test_gagal_ditolak_bila_tanpa_batas_waktu(self):
        a = Aktivitas.objects.get(judul="Penjumlahan Seru")
        a.menit = 0
        a.save()
        misi = jalur.misi_aktif(self.siswa)[1]
        token = self.token_untuk(misi)
        self.mulai(token)
        Misi.objects.filter(pk=misi.pk).update(mulai_pada=timezone.now() - timedelta(hours=2))
        self.assertEqual(self.kirim(token, {"events": [], "gagal": "waktu"}).status_code, 400)
        self.assertFalse(self.mulai(token).json()["gagal"])

    def test_buka_ulang_setelah_kedaluwarsa_dinyatakan_gagal(self):
        misi = jalur.misi_aktif(self.siswa)[1]
        token = self.token_untuk(misi)
        self.mulai(token)
        Misi.objects.filter(pk=misi.pk).update(mulai_pada=timezone.now() - timedelta(seconds=600 + 45))
        r = self.mulai(token).json()
        self.assertTrue(r["gagal"])
        misi.refresh_from_db()
        self.assertEqual(misi.status, "gagal")

    def test_selesai_dalam_masa_denda_tetap_diterima(self):
        misi = jalur.misi_aktif(self.siswa)[1]
        token = self.token_untuk(misi)
        self.mulai(token)
        Misi.objects.filter(pk=misi.pk).update(mulai_pada=timezone.now() - timedelta(seconds=600 + 20))
        soal = self.api(token).json()["soal"]
        for q in soal:
            jawab(Client(), token, q)
        r = self.kirim(token, {"events": [], "selesai": True, "skor": 85, "durasi_detik": 620}).json()
        self.assertTrue(r["selesai"])


class PetaKompetensiTest(DasarUji):
    def test_rumus_dicicil_tidak_langsung_100(self):
        p = jalur.persen_peta
        self.assertEqual(p(0.3, 0.0), 30)            # belum main
        self.assertEqual(p(0.99, 0.0), 30)           # jawaban bagus tapi belum ada kemajuan jalur
        self.assertEqual(p(0.9, jalur.kemajuan(5, 3)), 53)   # selesai bagian 1 dari 3
        self.assertEqual(p(0.9, jalur.kemajuan(10, 3)), 77)  # bagian 2
        self.assertEqual(p(0.9, jalur.kemajuan(15, 3)), 100)  # bagian 3 = target
        self.assertEqual(p(0.9, jalur.kemajuan(40, 3)), 100)
        self.assertEqual(p(0.3, jalur.kemajuan(15, 3)), 30)  # target tercapai tetapi penguasaan rendah
        self.assertEqual(p(0.6, 0.0, dasar=0.85), 85)        # hasil diagnostik tinggi tetap terbaca

    def test_peta_kelas_naik_bertahap_setelah_bagian_1(self):
        for _ in range(5):
            self.selesaikan_level(jalur.misi_aktif(self.siswa)[1])
        d = ringkasan_kelas(self.kelas)
        baris = d["baris"][0]
        nt = [k.kode for k in d["kompetensi"]].index("NM-TAMBAH")
        self.assertLess(baris["sel"][nt]["pct"], 100)
        self.assertEqual(baris["sel"][nt]["pct"], 53)
        self.assertEqual(d["target_bagian"], 3)
        # target bagian diubah guru -> persen ikut
        self.kelas.target_bagian = 1
        self.kelas.save()
        d = ringkasan_kelas(self.kelas)
        self.assertEqual(d["baris"][0]["sel"][nt]["pct"], 100)

    def test_tiga_bagian_numerasi_mencapai_100(self):
        for _ in range(15):
            misi = [m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0]
            self.selesaikan_level(misi)
        d = ringkasan_kelas(self.kelas)
        pct = {k.kode: d["baris"][0]["sel"][i]["pct"] for i, k in enumerate(d["kompetensi"])}
        self.assertEqual(pct["NM-TAMBAH"], 100)
        self.assertEqual(pct["NM-KURANG"], 100)
        self.assertEqual(pct["NM-KALI"], 100)
        self.assertLess(pct["NM-BAGI"], 100)  # bagian 4 belum dimainkan


class PenugasanTest(DasarUji):
    def test_terapkan_ke_kelompok_mengarahkan_ke_level_berikutnya_kompetensi(self):
        kompetensi = Kompetensi.objects.get(kode="NM-KALI")
        n = terapkan_ke_kelompok(self.guru, self.kelas, kompetensi)
        self.assertEqual(n, 1)
        num = [m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0]
        self.assertEqual((num.kompetensi.kode, num.bagian, num.level, num.dari_guru), ("NM-KALI", 3, 1, True))
        self.selesaikan_level(num)
        # setelah selesai, kembali mengikuti jalur alami (bagian 1) tetapi level perkalian 1 tetap tercatat selesai
        num2 = [m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0]
        self.assertEqual((num2.bagian, num2.level), (1, 1))
        self.assertIn((3, 1), jalur.level_selesai([self.siswa.id])[(self.siswa.id, "numerasi")])


@override_settings(MEDIA_ROOT=MEDIA_UJI)
class BankSoalGuruTest(DasarUji):
    def setUp(self):
        super().setUp()
        self.guru_klien = Client()
        self.guru_klien.login(username="guru_uji", password="rahasia123")

    def test_halaman_bank_soal_guru(self):
        r = self.guru_klien.get("/guru/soal/")
        self.assertContains(r, "LIT-SPO-01")
        r = self.guru_klien.get("/guru/soal/?game=numerasi&bagian=3&level=2")
        self.assertContains(r, "Perkalian")
        r = self.guru_klien.get("/guru/soal/?game=literasi&level=1")
        self.assertContains(r, "LIT-BACA-01")
        self.assertContains(r, "Baca kalimat")
        self.assertContains(r, "Susun kalimat")
        self.assertEqual(self.guru_klien.get("/guru/soal/baru/?game=literasi").status_code, 200)
        self.assertEqual(self.guru_klien.get("/guru/soal/baru/?game=numerasi").status_code, 200)
        self.assertEqual(self.klien.get("/guru/soal/").status_code, 302)  # murid dialihkan

    def test_buat_soal_game1_dengan_gambar_kolom_kata_dan_jawaban(self):
        data = {
            "kompetensi": "LT-KALIMAT", "bagian": "2", "level": "1", "instruksi": "Apa yang dilakukan Ani?",
            "kolom_kata": "Ani\nmenyapu\nlantai\nmenari", "jawaban": "Ani\nmenyapu\nlantai", "jawaban_lain": "menyapu, lantai",
            "gambar": SimpleUploadedFile("ani.png", PNG_KECIL, content_type="image/png"),
        }
        r = self.guru_klien.post("/guru/soal/baru/?game=literasi", data)
        self.assertEqual(r.status_code, 302, r.content.decode()[:600])
        s = SoalGame.objects.get(bagian=2, game="literasi")
        self.assertEqual((s.jawaban, s.kolom_kata, s.jawaban_lain), (["Ani", "menyapu", "lantai"], ["Ani", "menyapu", "lantai", "menari"], [["menyapu", "lantai"]]))
        self.assertTrue(s.gambar.name.startswith("soal/"))
        self.assertEqual(s.dibuat_oleh, self.guru)
        out = bank_soal.untuk_game1(s)
        self.assertEqual(out["correctOrder"], ["Ani", "menyapu", "lantai"])
        self.assertEqual(out["jawabanLain"], [["menyapu", "lantai"]])
        self.assertIn("lantai_menyapu_Ani", out["errorPatterns"])
        self.assertEqual(s.item_id, f"SG-{s.pk}")

    def test_validasi_game1(self):
        dasar = {"kompetensi": "LT-KALIMAT", "bagian": "2", "level": "1", "instruksi": "x?", "kolom_kata": "a\nb", "jawaban": "a\nb",
                 "gambar": SimpleUploadedFile("a.png", PNG_KECIL, content_type="image/png")}

        def coba(**ubah):
            d = {**dasar, **ubah}
            if not isinstance(d["gambar"], SimpleUploadedFile) or ubah.get("gambar") is None:
                d["gambar"] = SimpleUploadedFile("a.png", PNG_KECIL, content_type="image/png")
            self.guru_klien.post("/guru/soal/baru/?game=literasi", d)
            return SoalGame.objects.filter(bagian=2, game="literasi").count()

        self.assertEqual(coba(jawaban="a\nc"), 0)                       # kata jawaban tidak ada di kolom kata
        self.assertEqual(coba(kolom_kata="a"), 0)                       # kolom kata terlalu sedikit
        self.assertEqual(coba(jawaban=""), 0)
        self.assertEqual(coba(instruksi=""), 0)
        self.assertEqual(coba(level="9"), 0)
        self.assertEqual(coba(jawaban="a\na"), 0)                       # 'a' dipakai dua kali padahal hanya ada satu
        self.assertEqual(coba(gambar=SimpleUploadedFile("a.svg", b"<svg onload=alert(1)>", content_type="image/svg+xml")), 0)
        self.assertEqual(coba(gambar=SimpleUploadedFile("a.png", b"bukan gambar", content_type="image/png")), 0)
        d = {**dasar}
        d.pop("gambar")
        self.guru_klien.post("/guru/soal/baru/?game=literasi", d)       # tanpa gambar
        self.assertEqual(SoalGame.objects.filter(bagian=2, game="literasi").count(), 0)
        self.assertEqual(coba(), 1)                                      # isian benar tersimpan

    def test_buat_soal_game2_empat_operasi_dengan_cerita_yang_diatur_guru(self):
        def kirim(bagian="7", **d):
            return self.guru_klien.post("/guru/soal/baru/?game=numerasi", {"bagian": bagian, "level": "1", **d})

        # penjumlahan di bagian 7: jawaban dihitung otomatis, cerita dan nama kelompok diatur guru
        kirim(kompetensi="NM-TAMBAH", a="27", b="15", instruksi="Ani punya **27 apel** <script>x</script>\nlalu beli 15.",
              label1="Apel Ani", label2="Beli", ikon="apel")
        s = SoalGame.objects.get(bagian=7)
        self.assertEqual(s.kunci, 42)
        q = bank_soal.untuk_game2(s)
        self.assertEqual((q["tipe"], q["operator"], q["kunciJawaban"], q["val1"], q["val2"]), ("penjumlahan", "+", 42, 27, 15))
        self.assertEqual((q["label1"], q["label2"], q["icon"]), ("Apel Ani", "Beli", "fa-apple-whole"))
        self.assertIn("<b>27 apel</b>", q["teksSoal"])
        self.assertNotIn("<script>", q["teksSoal"])
        self.assertIn("&lt;script&gt;", q["teksSoal"])
        self.assertIn("simpan", q["hint"])  # 27 + 15: satuan 7 + 5 melewati 10
        # tanpa cerita dan nama kelompok -> kalimat dan nama bawaan operasi
        kirim(kompetensi="NM-TAMBAH", a="8", b="5")
        bawaan = bank_soal.untuk_game2(SoalGame.objects.get(bagian=7, kunci=13))
        self.assertEqual((bawaan["label1"], bawaan["label2"]), ("Kelompok pertama", "Kelompok kedua"))
        self.assertIn("Hitung <b>8 + 5</b>", bawaan["teksSoal"])
        # jawaban salah dari guru ditolak
        kirim(kompetensi="NM-TAMBAH", a="3", b="4", kunci="99")
        self.assertEqual(SoalGame.objects.filter(bagian=7).count(), 2)
        # satu bagian = satu operasi
        kirim(kompetensi="NM-KALI", a="3", b="4")
        self.assertEqual(SoalGame.objects.filter(bagian=7).count(), 2)

        # pengurangan: a < b ditolak
        kirim(bagian="8", kompetensi="NM-KURANG", a="52", b="27")
        s = SoalGame.objects.get(bagian=8)
        self.assertEqual((s.kunci, bank_soal.untuk_game2(s)["operasi"]), (25, "pengurangan"))
        kirim(bagian="8", kompetensi="NM-KURANG", a="5", b="27")
        self.assertEqual(SoalGame.objects.filter(bagian=8).count(), 1)
        self.assertIn("Pinjam", bank_soal.untuk_game2(s)["hint"])
        # perkalian: a kelompok isi b
        kirim(bagian="9", kompetensi="NM-KALI", a="4", b="6", label1="Kotak", label2="Isi kotak")
        q = bank_soal.untuk_game2(SoalGame.objects.get(bagian=9))
        self.assertEqual((q["tipe"], q["operator"], q["kunciJawaban"], q["label1"]), ("perkalian", "\u00d7", 24, "Kotak"))
        # pembagian: harus habis dibagi dan pembagi bukan nol
        kirim(bagian="10", kompetensi="NM-BAGI", a="25", b="4")
        kirim(bagian="10", kompetensi="NM-BAGI", a="8", b="0")
        self.assertEqual(SoalGame.objects.filter(bagian=10).count(), 0)
        kirim(bagian="10", kompetensi="NM-BAGI", a="24", b="4")
        q = bank_soal.untuk_game2(SoalGame.objects.get(bagian=10))
        self.assertEqual((q["tipe"], q["operator"], q["kunciJawaban"]), ("pembagian", "\u00f7", 6))
        # kompetensi numerasi lama tidak dikenal lagi
        kirim(bagian="11", kompetensi="NM-POLA", a="1", b="1")
        self.assertEqual(SoalGame.objects.filter(bagian=11).count(), 0)

    def test_buat_soal_baca_kalimat_untuk_level_literasi(self):
        def kirim(**d):
            return self.guru_klien.post("/guru/soal/baru/?game=literasi", {"kompetensi": "LT-KETEPATAN", "bagian": "2", "level": "1", **d})

        kirim(kalimat="Ani menyapu lantai.", emoji="\U0001F9F9")
        s = SoalGame.objects.get(bagian=2, game="literasi")
        self.assertEqual((s.kompetensi.kode, s.instruksi, s.data["emoji"], s.jawaban, s.kolom_kata), ("LT-KETEPATAN", "Ani menyapu lantai.", "\U0001F9F9", [], []))
        q = bank_soal.untuk_game1(s)
        self.assertEqual((q["tipe"], q["kalimat"], q["emoji"], q["kompetensiDashboard"]), ("baca", "Ani menyapu lantai.", "\U0001F9F9", "LT-KETEPATAN"))
        self.assertEqual(bank_soal.ringkas(s)[2], "dibaca lantang")
        self.assertEqual(bank_soal.jenis_dan_thumb(s), ("Baca kalimat", "\U0001F9F9"))
        # emoji bawaan bila kosong; satu kata, kalimat kosong, dan kalimat terlalu panjang ditolak
        kirim(kalimat="Budi membaca buku.", bagian="3")
        self.assertEqual(SoalGame.objects.get(game="literasi", bagian=3).data["emoji"], bank_soal.EMOJI_BACA_BAWAAN)
        kirim(kalimat="Halo", bagian="4")
        kirim(kalimat="", bagian="4")
        kirim(kalimat=" ".join(["kata"] * 21), bagian="4")
        self.assertEqual(SoalGame.objects.filter(game="literasi", bagian=4).count(), 0)
        # formulir ubah soal baca menampilkan kalimatnya
        self.assertContains(self.guru_klien.get(f"/guru/soal/{s.pk}/"), "Ani menyapu lantai.")

    def test_komposisi_level_literasi_tiga_susun_dua_baca(self):
        pilih = bank_soal.pilih_soal("literasi", 1, 3, benih=1)
        self.assertEqual([s.kompetensi.kode == "LT-KETEPATAN" for s in pilih], [False, False, False, True, True])
        # benih berbeda memberi kombinasi berbeda, tetapi komposisi tetap
        beda = {tuple(s.kode_bawaan for s in bank_soal.pilih_soal("literasi", 1, 3, benih=n)) for n in range(12)}
        self.assertGreater(len(beda), 1)
        for kombinasi in beda:
            self.assertEqual(sum(k.startswith("LIT-BACA-") for k in kombinasi), 2)

    def test_soal_buatan_guru_dimainkan_murid_setelah_bagian_lengkap(self):
        # selesaikan semua level Literasi bagian 1
        for _ in range(5):
            self.selesaikan_level(jalur.misi_aktif(self.siswa)[0])
        self.assertEqual([m.kompetensi.ranah for m in jalur.misi_aktif(self.siswa)], ["numerasi"])
        # guru membuat bagian 2 Literasi lengkap (5 level x 5 soal)
        for level in range(1, 6):
            for i in range(5):
                r = self.guru_klien.post("/guru/soal/baru/?game=literasi", {
                    "kompetensi": "LT-KALIMAT", "bagian": "2", "level": str(level), "instruksi": f"Soal {level}-{i}", "kolom_kata": "Ani\nmenari\nbola",
                    "jawaban": "Ani\nmenari", "gambar": SimpleUploadedFile("x.png", PNG_KECIL, content_type="image/png")})
                self.assertEqual(r.status_code, 302)
        misi = jalur.misi_aktif(self.siswa)
        lit = [m for m in misi if m.kompetensi.ranah == "literasi"][0]
        self.assertEqual((lit.bagian, lit.level), (2, 1))
        soal = self.api(self.token_untuk(lit)).json()["soal"]
        self.assertEqual(len(soal), 5)
        self.assertTrue(all(q["id"].startswith("SG-") for q in soal))
        self.assertEqual(soal[0]["correctOrder"], ["Ani", "menari"])

    def test_lebih_dari_5_soal_dipilih_5_dan_tetap_untuk_misi_yang_sama(self):
        for i in range(4):
            self.guru_klien.post("/guru/soal/baru/?game=numerasi", {"kompetensi": "NM-TAMBAH", "bagian": "1", "level": "1", "a": str(i + 2), "b": "1"})
        self.assertEqual(SoalGame.objects.filter(game="numerasi", bagian=1, level=1).count(), 9)
        misi = jalur.misi_aktif(self.siswa)[1]
        a = [q["id"] for q in self.api(self.token_untuk(misi)).json()["soal"]]
        b = [q["id"] for q in self.api(self.token_untuk(misi)).json()["soal"]]
        self.assertEqual(len(a), 5)
        self.assertEqual(a, b)

    def test_nonaktif_hapus_dan_ubah(self):
        s = SoalGame.objects.get(kode_bawaan="LIT-SPO-01")
        self.guru_klien.post("/guru/soal/", {"aksi": "aktif", "id": s.pk, "game": "literasi"})
        s.refresh_from_db()
        self.assertFalse(s.aktif)
        # soal nonaktif tidak dipakai di game; level tetap 5 soal (susun yang tersisa + soal baca)
        misi = jalur.misi_aktif(self.siswa)[0]
        ids = [q["id"] for q in self.api(self.token_untuk(misi)).json()["soal"]]
        self.assertEqual(len(ids), 5)
        self.assertNotIn("LIT-SPO-01", ids)
        self.guru_klien.post("/guru/soal/", {"aksi": "hapus", "id": s.pk, "game": "literasi"})
        self.assertFalse(SoalGame.objects.filter(kode_bawaan="LIT-SPO-01").exists())
        self.assertTrue(SoalGame.objects.filter(kode_bawaan="LIT-SPO-02").exists())  # soal lain dan gambar bawaan tidak terhapus
        # ubah soal numerasi
        n = SoalGame.objects.get(kode_bawaan="M2-NM-KALI-L1-Q1")
        r = self.guru_klien.post(f"/guru/soal/{n.pk}/", {"kompetensi": "NM-KALI", "bagian": "3", "level": "1", "a": "7", "b": "6",
                                                       "instruksi": "Ada **7 kotak** berisi **6 kue**.", "label1": "Kotak", "petunjuk": "Jumlahkan 6 tujuh kali"})
        self.assertEqual(r.status_code, 302)
        n.refresh_from_db()
        self.assertEqual((n.data["a"], n.data["b"], n.kunci, n.data["label1"]), (7, 6, 42, "Kotak"))

    def test_level_tanpa_soal_tidak_bisa_dibuka(self):
        SoalGame.objects.filter(game="numerasi", bagian=1, level=1).update(aktif=False)
        # bagian 1 numerasi menjadi tidak lengkap -> jalur numerasi tidak punya misi
        self.assertEqual([m.kompetensi.ranah for m in jalur.misi_aktif(self.siswa)], ["literasi"])


class MigrasiDanBawaanTest(TestCase):
    def test_bank_bawaan_terisi(self):
        self.assertEqual(SoalGame.objects.filter(game="literasi").count(), 50)
        self.assertEqual(SoalGame.objects.filter(game="numerasi").count(), 100)
        self.assertEqual(
            set(Kompetensi.objects.values_list("kode", flat=True)),
            {"LT-KOSAKATA", "LT-LANCAR", "LT-KETEPATAN", "LT-KALIMAT", "NM-TAMBAH", "NM-KURANG", "NM-KALI", "NM-BAGI"},
        )
        for kode in ("LT-KALIMAT", "LT-KOSAKATA", "LT-KETEPATAN"):
            self.assertTrue(SoalGame.objects.filter(game="literasi", kompetensi__kode=kode).exists())
        # 25 soal susun bergambar bawaan + 25 kalimat baca (5 per level)
        susun = SoalGame.objects.filter(game="literasi").exclude(kompetensi__kode="LT-KETEPATAN")
        baca = SoalGame.objects.filter(game="literasi", kompetensi__kode="LT-KETEPATAN")
        self.assertEqual((susun.count(), baca.count()), (25, 25))
        for s in susun:
            self.assertTrue(s.gambar.name.startswith("soal/bawaan/"))
        for level in range(1, 6):
            self.assertEqual(baca.filter(level=level).count(), 5)
            self.assertEqual(susun.filter(level=level).count(), 5)
        for s in baca:
            self.assertGreaterEqual(len(s.instruksi.split()), 3)
            self.assertTrue(s.data["emoji"])
        # empat operasi: satu operasi satu bagian, 5 level x 5 soal
        for kode, bagian in bank_soal.BAGIAN_NUMERASI_BAWAAN.items():
            qs = SoalGame.objects.filter(game="numerasi", kompetensi__kode=kode)
            self.assertEqual((qs.count(), set(qs.values_list("bagian", flat=True))), (25, {bagian}))
            for level in range(1, 6):
                self.assertEqual(qs.filter(level=level).count(), 5)
        # jawaban bawaan konsisten dengan hitungan, cerita memuat kedua angka, nama kelompok muat di game
        for s in SoalGame.objects.filter(game="numerasi"):
            a, b = s.data["a"], s.data["b"]
            self.assertEqual(s.kunci, bank_soal.hitung_kunci(s.kompetensi.kode, s.data), s.item_id)
            self.assertGreaterEqual(s.kunci, 0)
            self.assertIn(str(a), s.instruksi, s.item_id)
            self.assertIn(str(b), s.instruksi, s.item_id)
            self.assertLessEqual(len(s.data["label1"]), 30)
            self.assertLessEqual(len(s.data["label2"]), 30)
            if s.kompetensi.kode == "NM-BAGI":
                self.assertEqual(a % b, 0, s.item_id)
        # tingkat kesulitan naik: bilangan level 5 lebih besar dari level 1 pada tiap operasi
        for kode in ("NM-TAMBAH", "NM-KURANG", "NM-KALI", "NM-BAGI"):
            besar = lambda level: max(s.data["a"] for s in SoalGame.objects.filter(kompetensi__kode=kode, level=level))
            self.assertGreater(besar(5), besar(1))


class TampilanTest(DasarUji):
    """Halaman yang dilihat murid dan guru: Jalan Lenteraku, Lencana, batas waktu, target Peta, form Bank Soal."""

    MINUS = "−"

    def setUp(self):
        super().setUp()
        self.guru_klien = Client()
        self.guru_klien.login(username="guru_uji", password="rahasia123")

    def test_beranda_murid_dua_kategori_lampu_dan_lencana(self):
        r = self.klien.get("/murid/")
        self.assertEqual(r.status_code, 200)
        for teks in ("Jalan Lenteraku", 'id="tab-literasi"', 'id="tab-numerasi"', "Kelima titik emas = 1 bagian", "Lencana", "Mulai misi"):
            self.assertContains(r, teks)
        self.assertNotContains(r, "Lencana minggu ini")
        self.assertEqual([k["total"] for k in r.context["kartu"]], [5, 5])
        self.assertEqual([g["nama"] for g in r.context["jalan"]], ["Literasi", "Numerasi"])

    def test_jalur_zigzag_satu_titik_per_level_emas_bila_selesai(self):
        def titik(html, bagian_id):
            potong = html.split(f'id="bagian-{bagian_id}"')[1].split("</svg>")[0]
            return {
                "node": potong.count('class="zig-node'),
                "selesai": potong.count('class="zig-node selesai'),
                "kini": potong.count('class="zig-node kini'),
                "ruas_emas": potong.count('class="seg on"'),
            }

        html = self.klien.get("/murid/").content.decode()
        self.assertEqual(titik(html, "numerasi-1"), {"node": 5, "selesai": 0, "kini": 1, "ruas_emas": 0})
        # level 1 dan 2 berhasil -> dua titik menyala emas, titik 3 giliran sekarang
        for _ in range(2):
            self.selesaikan_level([m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0])
        html = self.klien.get("/murid/").content.decode()
        self.assertEqual(titik(html, "numerasi-1"), {"node": 5, "selesai": 2, "kini": 1, "ruas_emas": 2})
        # bagian 2 masih terkunci sampai kelima titik bagian 1 emas
        self.assertIn('class="zig kunci"', html.split('id="bagian-numerasi-2"')[1].split("</svg>")[0])
        self.assertIn("Selesaikan bagian sebelumnya dulu", html)
        for _ in range(3):
            self.selesaikan_level([m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0])
        html = self.klien.get("/murid/").content.decode()
        self.assertEqual(titik(html, "numerasi-1"), {"node": 5, "selesai": 5, "kini": 0, "ruas_emas": 4})
        self.assertNotIn('class="zig kunci"', html.split('id="bagian-numerasi-2"')[1].split("</svg>")[0])
        self.assertIn("Kelima titiknya menyala emas", html)
        # literasi: satu jalur untuk susun + baca (tidak ada jalur terpisah per jenis soal)
        self.assertEqual(html.count('id="bagian-literasi-'), 1)

    def test_bagian_1_penuh_tapi_murid_bisa_lanjut_ke_bagian_2(self):
        for _ in range(5):
            num = [m for m in jalur.misi_aktif(self.siswa) if m.kompetensi.ranah == "numerasi"][0]
            self.selesaikan_level(num)
        r = self.klien.get("/murid/")
        num = [g for g in r.context["jalan"] if g["game"] == "numerasi"][0]
        self.assertTrue(num["bagian"][0]["penuh"])
        self.assertEqual(num["bagian"][0]["lampu_nyala"], num["bagian"][0]["lampu_total"])
        self.assertTrue(num["bagian"][1]["terbuka"])
        self.assertFalse(num["bagian"][2]["terbuka"])
        self.assertEqual(num["bagian"][0]["berikut"], 2)
        self.assertContains(r, "Bagian 1 penuh!")
        self.assertContains(r, "Lanjut ke Bagian 2")
        kartu = [k for k in r.context["kartu"] if k["r3"] == "num"][0]
        self.assertEqual((kartu["m"].bagian, kartu["m"].level), (2, 1))
        # lencana bagian 1 Numerasi menyala, Literasi belum
        self.assertTrue(any(x[3] and "Bagian 1 Numerasi" in x[2] for x in r.context["lencana"]))
        self.assertFalse(any(x[3] and "Bagian 1 Literasi" in x[2] for x in r.context["lencana"]))

    def test_batas_waktu_dan_aturan_denda_tampil_di_kartu_misi(self):
        r = self.klien.get("/murid/")
        self.assertContains(r, "Batas waktu <b>8 menit</b>")
        self.assertContains(r, "Batas waktu <b>10 menit</b>")
        self.assertContains(r, f"{self.MINUS}5 poin tiap 10 detik, paling lama 30 detik")
        Aktivitas.objects.filter(kompetensi__ranah="numerasi").update(menit=0)
        self.assertContains(self.klien.get("/murid/"), "Level ini tanpa batas waktu")

    def test_misi_ditinggalkan_lewat_batas_dan_toleransi_diganti_percobaan_baru(self):
        lama = jalur.misi_aktif(self.siswa)[1]  # Numerasi, batas 600 detik
        Misi.objects.filter(pk=lama.pk).update(mulai_pada=timezone.now() - timedelta(seconds=600 + 25))
        self.assertEqual(jalur.misi_aktif(self.siswa)[1].pk, lama.pk)  # masih dalam masa denda: tetap dipakai
        Misi.objects.filter(pk=lama.pk).update(mulai_pada=timezone.now() - timedelta(seconds=600 + 45))
        baru = jalur.misi_aktif(self.siswa)[1]
        lama.refresh_from_db()
        self.assertEqual(lama.status, "gagal")
        self.assertNotEqual(baru.pk, lama.pk)
        self.assertEqual((baru.bagian, baru.level), (lama.bagian, lama.level))
        self.assertIsNone(baru.mulai_pada)

    def test_guru_aktivitas_menit_nol_pengaturan_target_dan_peta(self):
        r = self.guru_klien.get("/guru/aktivitas/")
        self.assertContains(r, "Batas waktu (menit)")
        self.assertContains(r, "0 = tanpa batas")
        r = self.guru_klien.post("/guru/aktivitas/", {
            "aksi": "tambah", "judul": "Tanpa batas", "kompetensi": "NM-TAMBAH", "level_min": "1", "level_maks": "5", "menit": "0", "deskripsi": "",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Aktivitas.objects.get(judul="Tanpa batas").menit, 0)
        self.assertContains(self.guru_klien.get("/guru/aktivitas/"), "tanpa batas waktu")

        self.assertContains(self.guru_klien.get("/guru/pengaturan/"), "Target Peta Kompetensi")
        self.guru_klien.post("/guru/pengaturan/", {"aksi": "target_bagian", "target_bagian": "5"})
        self.kelas.refresh_from_db()
        self.assertEqual(self.kelas.target_bagian, 5)
        r = self.guru_klien.get("/guru/peta/")
        self.assertContains(r, "B1·L1")
        self.assertContains(r, "5 bagian")
        self.assertContains(r, "Literasi</b> baru punya 1 bagian")
        self.assertContains(r, "Numerasi</b> baru punya 4 bagian")
        self.guru_klien.post("/guru/pengaturan/", {"aksi": "target_bagian", "target_bagian": "1"})
        self.assertNotContains(self.guru_klien.get("/guru/peta/"), "baru punya")

    def test_form_soal_sesuai_game_dan_pengalihan_aman(self):
        r = self.guru_klien.get("/guru/soal/baru/?game=literasi")
        for teks in ('name="gambar"', 'name="kolom_kata"', 'name="jawaban"', 'name="jawaban_lain"', 'name="urutan_bebas"', 'enctype="multipart/form-data"',
                     'data-jenis="susun"', 'data-jenis="baca"', 'name="kalimat"', 'name="emoji"'):
            self.assertContains(r, teks)
        # template soal numerasi seperti soal cerita: cerita, nama kelompok, angka, dan gambar ilustrasi bisa diatur
        r = self.guru_klien.get("/guru/soal/baru/?game=numerasi")
        for teks in ('name="instruksi"', 'name="label1"', 'name="label2"', 'name="a"', 'name="b"', 'name="ikon"', 'id="btn-cerita"', 'name="kunci"', 'name="petunjuk"'):
            self.assertContains(r, teks)
        for lama in ('name="puluhan"', 'name="deret"', 'data-komp='):
            self.assertNotContains(r, lama)
        s = SoalGame.objects.get(kode_bawaan="LIT-SPO-01")
        self.assertContains(self.guru_klien.get(f"/guru/soal/{s.pk}/"), f"Ubah soal {s.item_id}")

        lain = SoalGame.objects.get(kode_bawaan="LIT-SPO-02")
        r = self.guru_klien.post("/guru/soal/", {"aksi": "aktif", "id": lain.pk, "game": "literasi", "kembali": "https://jahat.example/"})
        self.assertEqual(r["Location"], "/guru/soal/?game=literasi")
        r = self.guru_klien.post("/guru/soal/", {"aksi": "aktif", "id": lain.pk, "game": "literasi", "kembali": "/guru/soal/?game=literasi&bagian=1"})
        self.assertEqual(r["Location"], "/guru/soal/?game=literasi&bagian=1")

# LENTERA Dashboard (Django): repo dashboard + backend

Repo ini hanya berisi dashboard murid dan guru beserta backend-nya. Game dan quiz ada di repo masing-masing
(contoh: `lentera-game-misi2`) dan terhubung lewat HTTP, tidak digabung ke folder ini.

## 1. Menjalankan (Windows PowerShell)
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:GAME_MISI2_URL = "http://127.0.0.1:5500/index.html"       # alamat game Misi 2 (repo game)
$env:LENTERA_GAME_ORIGIN = "http://127.0.0.1:5500"             # origin game yang boleh mengirim jawaban (CORS)
python manage.py migrate                                        # db.sqlite3 sudah berisi data demo; aman dijalankan lagi
python manage.py runserver 8000
```
Buka `http://127.0.0.1:8000/login/` (pakai `127.0.0.1` di semua tempat, jangan dicampur dengan `localhost`).
- Murid: kode kelas `3B-LENTERA` → pilih avatar (tanpa kata sandi)
- Guru: `guru_sari` / `guru12345`
- Admin: belum ada akun bawaan. Buat yang pertama lewat terminal: `python manage.py buat_admin budi "Pak Budi" kataSandi`, lalu masuk di `/login/` (form yang sama dengan guru).
  Dari dashboard Admin (`/admin/`), admin bisa membuat kelas + kode kelas, mengisi belasan akun siswa sekaligus (murid tinggal masuk pakai kode kelas), membuat akun guru dengan kata sandi dan langsung menentukan kelas yang diampunya (`/admin/guru/`), serta mengekspor laporan kelas mana saja (`/admin/laporan/`).
  Guru tetap bisa membuat kelasnya sendiri lewat Pengaturan seperti biasa kalau tidak dibuatkan admin - keduanya memakai kode yang sama di `belajar/services.py`, jadi hasilnya identik.
  Perintah CLI `buat_guru` lama tetap ada sebagai cara alternatif membuat akun guru tanpa dashboard Admin.
- Data demo diisi ulang dengan `python manage.py seed_demo` (pada DB kosong).

## 2. Menyambungkan game / quiz (repo terpisah)
1. Jalankan repo game (lihat README-nya) di `http://127.0.0.1:5500`.
2. Daftarkan alamatnya di `lentera/settings.py` → `LENTERA_GAMES` (kode kompetensi → URL). Kompetensi yang belum terdaftar membuka halaman "game belum tersedia".
3. Murid menekan **Mulai misi** → dashboard membuat token JWT (30 menit) → browser pindah ke `URL_GAME?ssoToken=...`.
4. Game mengirim tiap jawaban ke `POST /api/game/event/` dengan header `Authorization: Bearer <token>`. Alamat API ada di dalam token.
5. Produksi: isi `LENTERA_GAME_ORIGIN` dengan origin game (boleh beberapa, pisah koma), ganti `SECRET_KEY` dan `LENTERA_GAME_SECRET` lewat environment variable, dan pakai HTTPS.

Isi token: `sub` (ID samaran), `nama`, `misi`, `kompetensi`, `ranah`, `level`, `mode` (`misi` atau `diagnostik`), `home`, `logout`, `api`, `exp`.
Body event: `{"events":[{"event_id","item_id","kompetensi","level","benar","pola","waktu_ms","petunjuk","waktu"}],"selesai":false}`.
Kode kompetensi: `LT-KOSAKATA LT-LANCAR LT-PAHAM LT-KALIMAT NM-NILAI-TEMPAT NM-REGROUPING NM-POLA NM-CERITA`
(nama lama game dipetakan otomatis: `NILAI_TEMPAT`, `PENGURANGAN`, `KOMBINASI`).

## 3. Fitur dan data di belakangnya
| Menu | Yang dikerjakan | Tersimpan / dihitung di |
|---|---|---|
| Murid: beranda | 2 misi harian (kompetensi terlemah atau kiriman guru), jalur lentera, lencana, progres | `Misi`, `Penguasaan` |
| Guru: Insight Kelas | rata-rata penguasaan, waktu belajar, perlu perhatian, peta, kesulitan umum, rekomendasi, **Terapkan ke kelompok** | `ringkasan_kelas()`, `Misi`, `Penugasan` |
| Guru: Peta Kompetensi | level dan P(L) tiap siswa per kompetensi, aktivitas 7 hari, pola kesalahan terbanyak | `Penguasaan`, `Jawaban` |
| Guru: Bank Aktivitas | cari, tambah, **edit**, validasi, hapus, dan tugaskan aktivitas ke kelas / kelompok / siswa terpilih. Hanya kompetensi yang sudah punya game asli tersambung (`LENTERA_GAMES`) yang bisa dipilih - tidak ada lagi jenis kuis/kegiatan kelas, semua aktivitas adalah game | `Aktivitas`, `Misi`, `Penugasan` |
| Guru: Asesmen Diagnostik | kirim asesmen lewat game atau input manual; hasilnya menetapkan level awal dan P(L) awal | `HasilDiagnostik`, `Penguasaan` |
| Guru: Laporan & Ekspor | ringkasan yang bisa dicetak jadi PDF + 3 CSV (peta, log jawaban, diagnostik, dengan kolom saran tindak lanjut) | `ringkasan_kelas()`, `Jawaban` |
| Guru: Pengaturan | tambah/hapus siswa, kode kelas, kartu masuk, ambang perlu perhatian, ganti sandi | `Siswa`, `Kelas` |
| Guru: Insight &amp; Peta | "Kesulitan yang sering muncul" dan kolom "Pola terbanyak" kini menampilkan **rekomendasi remedial** konkret, bukan cuma nama pola kesalahannya | `engine.POLA`, `ringkasan_kelas()` |
| Admin: Kelola Kelas | buat kelas + kode kelas (format bebas, mis. `1A`..`6C`), tambah/hapus siswa per kelas, ganti kode, cetak kartu masuk - untuk kelas mana pun | `Kelas`, `Siswa` |
| Admin: Kelola Guru | buat akun guru (username + kata sandi) sekaligus menentukan kelas yang diampu, pindahkan guru ke kelas lain, reset sandi, hapus akun | `User`, `Profile`, `Kelas` |
| Admin: Laporan | pilih kelas mana pun lalu lihat ringkasan + unduh 3 CSV yang sama seperti punya guru | `ringkasan_kelas()` |

## 4. Memperbarui dari versi sebelumnya
Ganti file proyek, lalu `python manage.py migrate` (ada migrasi baru `accounts.0002` dan `belajar.0003`). Bank aktivitas otomatis dirapikan saat migrate: aktivitas demo lama (buatan sistem, bukan buatan guru) dihapus dan diganti 5 aktivitas yang sesuai game asli; aktivitas yang pernah ditambah guru sendiri tidak disentuh.

## 5. Struktur
- `accounts/` login dan role (murid | guru | admin), perintah `buat_guru` dan `buat_admin`
- `belajar/engine.py` BKT, level, taksonomi, pola kesalahan (+ saran remedialnya), Item Selector, skor asesmen (tanpa Django)
- `belajar/services.py` `catat_jawaban()` (satu-satunya pintu tulis data belajar), asesmen, kelas/siswa/guru, bank aktivitas, Teacher Insight
- `belajar/views.py` dashboard murid dan guru; `belajar/admin_views.py` dashboard admin (pakai ulang fungsi yang sama di services.py)
- `belajar/game_api.py` token dan API untuk game/quiz; `belajar/cors.py` izin lintas origin
- `static/js/item-selector.js` port JS Item Selector untuk game

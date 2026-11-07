# LENTERA Dashboard (Django): dashboard murid, guru, admin + backend + Bank Soal

Folder ini berisi dashboard murid, guru, dan admin beserta backend-nya. Game 1 (Literasi) dan Game 2 (Numerasi)
ada di zip terpisah (`misi1lentera.zip` dan `Misi2.zip`) dan terhubung lewat HTTP, tidak digabung ke folder ini.

## Yang baru di versi gabungan ini
1. **Kompetensi baru.** Literasi: `LT-PAHAM` diganti **`LT-KETEPATAN`** (Ketepatan membaca, diukur lewat mikrofon).
   Numerasi: Nilai Tempat, Regrouping, Pola, dan Soal Cerita diganti **empat operasi hitung**: `NM-TAMBAH` (Penjumlahan),
   `NM-KURANG` (Pengurangan), `NM-KALI` (Perkalian), `NM-BAGI` (Pembagian). Satu operasi = satu bagian.
2. **Susun kalimat dan baca kalimat satu kesatuan.** Game 1 bukan dua game: satu level Literasi (5 soal) memuat
   **3 soal susun kalimat lalu 2 soal baca kalimat (mikrofon)** dalam satu halaman, satu jam, dan satu set lampu.
   Soal baca mengukur `LT-KETEPATAN`; jawabannya tidak dicerminkan ke kompetensi lain.
3. **Jalan Lenteraku berbentuk jalur zig-zag.** Satu bagian = satu jalur dengan 5 titik, satu titik per level.
   Level berhasil = titik menyala emas. Bagian berikutnya terkunci sampai kelima titik emas.
4. **Form soal Numerasi seperti template soal cerita**: guru mengatur teks cerita, nama kelompok, dua bilangan, dan gambar
   ilustrasi. Jawaban dihitung otomatis; ada tombol contoh cerita dan peringatan bila cerita tidak memuat kedua angka.
5. **Bank Soal** (menu guru): soal Game 1 (susun kalimat dan baca kalimat) dan Game 2 dibuat langsung dari dashboard.
6. **Lencana** menyala setiap murid menyelesaikan satu bagian. Setelah level selesai, game hanya menawarkan **Kembali ke Beranda**.
7. **Peta Kompetensi dicicil** (baru 100% setelah beberapa bagian selesai, bawaan 3) dan **batas waktu** game mengikuti
   kolom menit di Bank Aktivitas (lewat waktu: -5 poin tiap 10 detik, maksimal 30 detik, lebih dari itu level gagal).
8. **Beranda murid dirapikan**: chip "Mode Touchless aktif" (teks statis yang tidak terhubung ke apa pun di dashboard; sensor tangan hanya ada di dalam game)
   dan chip "★ poin" dihapus dari header. Counter `Siswa.poin` tetap tersimpan di database (bertambah tiap level selesai) tetapi tidak ditampilkan.

## 1. Menjalankan
**Cara cepat (Windows):** klik dua kali **`jalankan_dashboard.bat`**. File ini memasang paket bila belum ada, membuat database + data demo bila `db.sqlite3` belum ada
(selain itu cukup `migrate`), menyalakan server di port 8000, lalu membuka halaman login. Untuk menyalakan Dashboard + Game 1 + Game 2 sekaligus, pakai `jalankan_semua.bat`
(file terpisah di luar ketiga zip; petunjuk ada di dalamnya).

**Cara manual (Windows PowerShell):**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # hanya bila database masih kosong: kelas 3B, guru_sari, 12 murid demo
$env:LENTERA_GAME_ORIGIN = "http://127.0.0.1:5000,http://127.0.0.1:5500"   # origin game yang boleh memanggil API (CORS)
python manage.py runserver 8000
```
Buka `http://127.0.0.1:8000/login/` (pakai `127.0.0.1` di semua tempat, jangan dicampur dengan `localhost`).
- Murid: kode kelas `3B-LENTERA` → pilih avatar (tanpa kata sandi)
- Guru: `guru_sari` / `guru12345`
- Admin: belum ada akun bawaan. Buat yang pertama lewat terminal: `python manage.py buat_admin budi "Pak Budi" kataSandi`, lalu masuk di `/login/` (form yang sama dengan guru).
  Dari dashboard Admin (`/admin/`), admin bisa membuat kelas + kode kelas, mengisi belasan akun siswa sekaligus, membuat akun guru dan menentukan kelas yang diampunya (`/admin/guru/`), serta mengekspor laporan kelas mana saja (`/admin/laporan/`).
  Guru tetap bisa membuat kelasnya sendiri lewat Pengaturan. Perintah CLI `buat_guru` tetap ada sebagai cara alternatif.

`db.sqlite3` sengaja tidak disertakan di zip supaya data lama tidak tertimpa. Bila belum punya database, `migrate` lalu `seed_demo` seperti di atas. Soal bawaan otomatis masuk ke Bank Soal saat `migrate`: Literasi 50 soal (25 susun kalimat + 25 kalimat baca) dan Numerasi 100 soal (4 operasi × 5 level × 5 soal, semuanya berbentuk cerita).

Game dijalankan dari repo masing-masing (lihat bagian 6):
```
# Game 1 (Flask)  : pip install flask ; python app.py   -> http://127.0.0.1:5000   (di folder misi1lentera/lentera dari misi1lentera.zip)
# Game 2 (statis) : python -m http.server 5500          -> http://127.0.0.1:5500   (di folder Misi2 dari Misi2.zip, yaitu folder yang berisi Misi-2-lentera)
```
Alamat game bisa diganti lewat environment variable `GAME_MISI1_URL` dan `GAME_MISI2_URL` (lihat `lentera/settings.py`).

## 2. Jalan Lentera (murid)
- **Bagian → level → lampu.** 1 bagian = 5 level, 1 level = 5 soal = 5 lampu (`LENTERA_LEVEL_PER_BAGIAN`, `LENTERA_SOAL_PER_LEVEL`).
- Level dimainkan berurutan. Murid tidak bisa melompat ke level yang belum waktunya.
- **Jalan Lenteraku** (beranda murid): tombol **Literasi / Numerasi**, tombol **Bagian 1, 2, …**, dan **satu jalur zig-zag per bagian**
  (tangga naik ke kanan, 5 titik). Titik **emas** = level selesai, titik **besar bercincin** = giliranmu (lampu level itu tampil di bawah jalur),
  titik **abu** = belum. Ruas jalur ikut emas sampai titik terakhir yang selesai. Bagian yang belum terbuka tampil pudar dengan pesan terkunci.
  Bagian yang penuh menampilkan tombol **Lanjut ke Bagian berikutnya**.
- **Lencana**: satu lencana per bagian (menyala saat bagian itu penuh), ditambah **Penyala Pertama** (1 level selesai) dan **Lentera Emas** (sebanyak target bagian selesai).
- Bawaan: Literasi 1 bagian ("Susun & Baca Kalimat", 5 level), Numerasi 4 bagian: Penjumlahan, Pengurangan, Perkalian, Pembagian. Guru menambah bagian dan soal lewat Bank Soal.
- **Komposisi level Literasi**: bila bank level memuat soal susun **dan** soal baca, murid mendapat `LENTERA_SOAL_BACA_PER_LEVEL` (bawaan 2) soal baca
  dan sisanya soal susun, dipilih acak tetap untuk satu percobaan; urutannya susun dulu, baru baca.
- **Penilaian soal**: susun kalimat 100/75/50/25 menurut percobaan; baca kalimat 100 dikurangi 5 poin tiap bacaan salah. Nilai level = rata-rata kelima soal.
- **Kompetensi ditentukan server**: bacaan mikrofon selalu dicatat sebagai `LT-KETEPATAN`; soal susun tidak pernah dicatat sebagai `LT-KETEPATAN`,
  apa pun yang dikirim game (`game_api._norm`).

## 3. Bank Soal (guru) - `/guru/soal/`
- Dua tab: **Game 1 · Literasi** dan **Game 2 · Numerasi**. Ada peta bank per bagian dan level (hijau = cukup, kuning = kurang dari 5, abu = kosong), filter, **Ubah**, **Nonaktifkan**, **Hapus**, dan **＋ Tambah soal**.
- **Game 1** (Literasi) memuat dua jenis soal dalam satu level. Jenis dipilih lewat **Jenis soal (kemampuan)**:
  - **Susun kalimat** (Susun kalimat / Kosakata / Membaca lancar): gambar soal (JPG/PNG/GIF/WEBP, maksimal 5 MB), pertanyaan, **kolom kata**
    (boleh ada kata pengecoh), **jawaban benar** (urutan kata; klik kata untuk menyusunnya), jawaban benar lainnya (opsional), dan opsi urutan bebas.
  - **Baca kalimat** (Ketepatan membaca): **kalimat yang dibaca** (2 sampai 20 kata), emoji pendamping, dan gambar (opsional). Murid membaca kalimat dengan suara
    dan mikrofon memeriksa ketepatannya.
- **Game 2** (Numerasi): pilih **operasi hitung** (satu bagian = satu operasi), lalu atur seperti template soal cerita:
  - **Teks soal cerita** (`**tebal**` didukung; kosong = kalimat bawaan "Hitung 12 + 7"), tombol **Isi contoh cerita dari angka**, dan pemeriksa angka di cerita.
  - **Nama kelompok 1 dan 2** serta **dua bilangan**. Arti kelompok mengikuti operasi: penjumlahan (kelompok pertama + kedua), pengurangan (mula-mula − diambil),
    perkalian (banyak kelompok × isi tiap kelompok), pembagian (semua benda ÷ banyak kelompok, harus habis dibagi).
  - **Gambar ilustrasi**, petunjuk (opsional; kosong = petunjuk otomatis), dan **jawaban benar dihitung otomatis**.
- Bagian baru tampil di Jalan Lentera murid setelah **kelima levelnya** punya soal aktif (ideal minimal 5 per level). Bila ada lebih dari 5 soal aktif, murid mendapat 5 soal acak yang dikunci selama percobaan itu.
- Soal dikunci saat game dibuka. Soal baru atau perubahan guru berlaku pada misi berikutnya, bukan pada misi yang sedang berjalan.
- Numerasi: 1 bagian = 1 operasi hitung (form menolak mencampur operasi dalam satu bagian).
- Gambar tersimpan di `media/soal/` (soal bawaan di `media/soal/bawaan/`). Django melayani `media/` hanya saat `DEBUG`; pada produksi layani `MEDIA_ROOT` lewat web server. Teks dari guru di-escape, hanya `**tebal**` dan baris baru yang diubah menjadi HTML.

## 4. Batas waktu
- Kolom **Menit** di **Bank Aktivitas** = batas waktu **satu level** (isi **0** untuk tanpa batas). Asesmen diagnostik tidak dibatasi waktu.
- Game 1 memakai aktivitas "Susun & Baca Kalimat" (kompetensi utama Literasi), Game 2 memakai aktivitas kompetensi bagian itu. Aktivitas yang ditugaskan guru ke murid menentukan waktu level yang ditugaskan.
- **Setelah waktu habis**: poin berkurang **5 tiap 10 detik, maksimal 30 detik** (paling banyak -15). Lewat dari 30 detik = **gagal**, level diulang dengan soal baru. Atur di `LENTERA_WAKTU` (`tick`, `maks_lewat`, `penalti`).
- Contoh batas 8 menit: 8:10 → -5, 8:20 → -5, 8:30 → -5, lewat 8:30 → gagal.
- Server yang menilai: jam mulai disimpan di `Misi.mulai_pada` (diisi saat soal pertama tampil). Memuat ulang halaman game tidak mengulang jam, dan klaim "gagal" dari game hanya diterima bila waktunya benar-benar lewat.
- Misi yang sudah dimulai lalu ditinggalkan melewati batas + toleransi otomatis ditandai gagal saat murid membuka beranda, jadi murid mendapat percobaan baru.

## 5. Peta Kompetensi dicicil
`persen = dasar + (100 − dasar) × kemajuan × penguasaan`
- `dasar` = P(L) awal (hasil diagnostik, atau nilai awal engine).
- `kemajuan` = level selesai di kategori itu ÷ (`target_bagian` × 5), maksimal 1.
- `penguasaan` = seberapa jauh P(L) hasil BKT menuju ambang Cakap (0-1).
- `target_bagian` bawaan **3**, diatur guru per kelas di **Pengaturan** (1-10). Setelah bagian 1 selesai peta **tidak** langsung 100% (contoh Numerasi: 5 dari 15 level → sekitar 53%).
- Literasi bawaan baru punya 1 bagian, jadi Peta Literasi tertahan di sekitar 53% sampai guru menambah bagian 2 dan 3 di Bank Soal (halaman Peta guru memberi peringatan) atau menurunkan target.
- Peta guru dan murid menampilkan persen beserta posisi berikutnya (`B` = bagian, `L` = level, ✓ = semua level tersedia tuntas).

## 6. Menyambungkan game / quiz (repo terpisah)
1. Jalankan repo game (lihat README-nya).
2. Alamat game dipetakan di `lentera/settings.py` → `LENTERA_GAMES` (kode kompetensi → URL). Kompetensi yang belum terdaftar membuka halaman "game belum tersedia".
3. Murid menekan **Mulai misi** → dashboard membuat token JWT (30 menit) → browser pindah ke `URL_GAME?ssoToken=...`.
4. Game memuat soal level dari dashboard, memulai jam batas waktu, dan mengirim jawaban. Semua alamat API ada di dalam token, jadi game tidak perlu mengetahui alamat dashboard.
5. Produksi: isi `LENTERA_GAME_ORIGIN` dengan origin game (boleh beberapa, pisah koma), ganti `SECRET_KEY` dan `LENTERA_GAME_SECRET` lewat environment variable, dan pakai HTTPS.

Endpoint (header `Authorization: Bearer <token>`):

| Endpoint | Fungsi |
|---|---|
| `GET /api/game/soal/` | soal level ini dalam format asli tiap game, `benar_ids` (lampu yang sudah menyala), `batas_detik`, `tick_detik`, `toleransi_detik`, `penalti_poin`, `dimulai`, `berjalan_detik` |
| `POST /api/game/mulai/` | menjalankan jam batas waktu (aman dipanggil berulang; mengembalikan `gagal` bila sudah lewat) |
| `POST /api/game/event/` | mencatat jawaban. `{"events":[...]}`; level selesai: `{"events":[],"selesai":true,"skor":85,"durasi_detik":240}`; waktu habis: `{"events":[],"gagal":"waktu","skor":55,"durasi_detik":540}` |

- Server hanya menerima `selesai` bila **semua** soal level itu sudah dijawab benar, dan skor sudah dipotong denda waktu.
- Isi token: `sub` (ID samaran), `nama`, `misi`, `kompetensi`, `ranah`, `game`, `bagian`, `level`, `mode` (`misi` atau `diagnostik`), `total_soal`, `batas_detik`, `tick_detik`, `toleransi_detik`, `penalti_poin`, `home`, `logout`, `api`, `soal_api`, `mulai_api`, `iat`, `exp`.
- Body event: `{"events":[{"event_id","item_id","kompetensi","level","benar","pola","waktu_ms","petunjuk","waktu"}],"selesai":false}`. `item_id` adalah `id` soal dari `GET /api/game/soal/`.
- Kode kompetensi: `LT-KOSAKATA LT-LANCAR LT-KETEPATAN LT-KALIMAT NM-TAMBAH NM-KURANG NM-KALI NM-BAGI` (nama game dipetakan otomatis, mis. `PENJUMLAHAN`, `PENGURANGAN`, `PERKALIAN`, `PEMBAGIAN`, `BACA_LANTANG`).
- Soal Game 1 dikirim dengan `tipe`: `susun` (kartu kata) atau `baca` (`kalimat`, `emoji`/`gambar`, kompetensi `LT-KETEPATAN`). Soal Game 2 dikirim dengan `tipe`: `penjumlahan`, `pengurangan`, `perkalian`, atau `pembagian`.
- Di sisi game, satu modul bersama `lentera-misi.js` (ada di repo Game 1 dan Game 2) menangani pemuatan soal, lampu, jam batas waktu, denda, dan tombol kembali ke beranda. Tanpa `ssoToken` game tetap berjalan mandiri seperti sebelumnya.

## 7. Fitur dan data di belakangnya
| Menu | Yang dikerjakan | Tersimpan / dihitung di |
|---|---|---|
| Murid: beranda | misi Literasi dan Numerasi (level berikutnya di jalur atau tugas guru), **Jalan Lenteraku** (tab kategori, bagian, jalur zig-zag 5 titik), **Lencana**, Perkembanganku, asesmen diagnostik | `Misi`, `Penguasaan`, `jalur.py` |
| Guru: Insight Kelas | rata-rata penguasaan, waktu belajar, perlu perhatian, peta, kesulitan umum, rekomendasi, **Terapkan ke kelompok** | `ringkasan_kelas()`, `Misi`, `Penugasan` |
| Guru: Peta Kompetensi | persen dicicil dan posisi bagian/level tiap siswa per kompetensi, aktivitas 7 hari, pola kesalahan terbanyak | `Penguasaan`, `Jawaban`, `jalur.hitung_peta()` |
| Guru: Bank Aktivitas | cari, tambah, edit, validasi, hapus, dan tugaskan aktivitas ke kelas / kelompok / siswa. **Menit = batas waktu game** (0 = tanpa batas) | `Aktivitas`, `Misi`, `Penugasan` |
| Guru: **Bank Soal** | buat, ubah, nonaktifkan, dan hapus soal Game 1 dan Game 2 per bagian dan level | `SoalGame` |
| Guru: Asesmen Diagnostik | kirim asesmen lewat game atau input manual; hasilnya menetapkan level awal dan P(L) awal | `HasilDiagnostik`, `Penguasaan` |
| Guru: Laporan & Ekspor | ringkasan yang bisa dicetak jadi PDF + 3 CSV (peta, log jawaban, diagnostik) | `ringkasan_kelas()`, `Jawaban` |
| Guru: Pengaturan | siswa, kode kelas, kartu masuk, ambang perlu perhatian, **target Peta Kompetensi (jumlah bagian)**, ganti sandi | `Siswa`, `Kelas` |
| Admin: Kelola Kelas / Guru / Laporan | kelas, siswa, akun guru, dan laporan untuk kelas mana pun | `Kelas`, `Siswa`, `User`, `Profile` |

## 8. Memperbarui dari versi sebelumnya
Ganti file proyek (jangan timpa `db.sqlite3` Anda), lalu `python manage.py migrate`. Migrasi yang relevan:
- `belajar.0004` (bagian, waktu, skor, `target_bagian`, tabel `SoalGame`) dan `belajar.0005` (mengisi Bank Soal dari soal bawaan).
- **`belajar.0006` (baru): mengganti kompetensi.** `LT-PAHAM` dan keempat kompetensi Numerasi lama (`NM-NILAI-TEMPAT`, `NM-REGROUPING`, `NM-POLA`, `NM-CERITA`)
  **dihapus beserta data turunannya** (aktivitas, penguasaan, misi, jawaban, hasil diagnostik, dan soal Numerasi lama). Alasannya: level yang sudah selesai pada
  kompetensi lama tidak boleh terhitung sebagai level selesai pada bagian baru. Data murid, kelas, guru, poin, dan misi Literasi yang sudah selesai **tidak berubah**.
  Soal susun kalimat bergambar yang dulu bertanda `LT-PAHAM` tetap dipakai (kini `LT-KOSAKATA`). Soal bawaan baru (4 operasi hitung dan 25 kalimat baca) diimpor;
  soal buatan guru tidak disentuh. Migrasi ini tidak bisa dibalik, jadi **cadangkan `db.sqlite3` dulu**.
- Soal yang nanti dihapus guru tidak dibuat ulang saat `migrate` dijalankan lagi.

## 9. Uji otomatis
`python manage.py test belajar` menguji jalur zig-zag dan lampu, level Literasi campuran (susun + baca), penentuan kompetensi oleh server, bank soal guru (4 operasi dan baca kalimat), API game, batas waktu, Peta dicicil, dan halaman guru/murid.

## 10. Struktur
- `accounts/` login dan role (murid | guru | admin), perintah `buat_guru` dan `buat_admin`
- `belajar/engine.py` BKT, level, taksonomi, pola kesalahan (+ saran remedialnya), Item Selector, skor asesmen (tanpa Django)
- `belajar/jalur.py` Jalan Lentera: bagian, level, lampu, misi aktif, batas waktu dan denda, lencana, Peta dicicil
- `belajar/bank_soal.py` validasi form guru, jawaban otomatis, dan konversi soal ke format asli Game 1 dan Game 2
- `belajar/soal_views.py` halaman Bank Soal guru
- `belajar/services.py` `catat_jawaban()` (satu-satunya pintu tulis data belajar), asesmen, kelas/siswa/guru, bank aktivitas, Teacher Insight
- `belajar/views.py` dashboard murid dan guru; `belajar/admin_views.py` dashboard admin
- `belajar/game_api.py` token dan API untuk game/quiz (`soal`, `mulai`, `event`); `belajar/cors.py` izin lintas origin
- `belajar/data/soal_bawaan.json` soal bawaan kedua game untuk mengisi Bank Soal; `media/soal/bawaan/` gambar soal Game 1
- `belajar/migrations/_bawaan.py` bantuan impor soal bawaan (dipakai migrasi 0005 dan 0006)
- `static/js/item-selector.js` port JS Item Selector untuk game

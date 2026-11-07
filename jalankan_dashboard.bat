@echo off
setlocal EnableExtensions
title LENTERA Dashboard - port 8000
cd /d "%~dp0"

rem --- cari Python (python, atau py bila python tidak ada di PATH) ---
set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 set "PY=py"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python belum terpasang atau belum ada di PATH.
  echo Pasang dari https://www.python.org/downloads/ lalu centang "Add python.exe to PATH".
  pause
  exit /b 1
)

rem --- pasang paket bila belum ada ---
%PY% -c "import django, jwt" >nul 2>nul
if errorlevel 1 (
  echo Memasang paket yang dibutuhkan...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Gagal memasang paket. Periksa koneksi internet lalu coba lagi.
    pause
    exit /b 1
  )
)

rem --- database: buat + isi data demo bila belum ada, selain itu cukup migrate ---
if not exist "db.sqlite3" (
  echo Database belum ada. Membuat database dan data demo...
  %PY% manage.py migrate
  %PY% manage.py seed_demo
) else (
  %PY% manage.py migrate
)

echo.
echo Dashboard LENTERA: http://127.0.0.1:8000/login/
echo Data demo - murid: kode kelas 3B-LENTERA, guru: guru_sari / guru12345
echo Jalankan juga Game 1 (port 5000) dan Game 2 (port 5500) agar tombol Mulai misi berfungsi.
echo Tutup jendela ini untuk menghentikan server.
echo.

rem buka browser 5 detik lagi, setelah server siap
start "" /min cmd /c "timeout /t 5 /nobreak >nul & explorer http://127.0.0.1:8000/login/"

%PY% manage.py runserver 8000
pause

# Scraping Data MagangHub

Repositori ini berisi beberapa script Python untuk mengambil dan mengolah
informasi lowongan dari situs
[MagangHub Kemnaker](https://maganghub.kemnaker.go.id/).

Script dapat memfilter lowongan berdasarkan kota, jenjang pendidikan, dan
program studi. Hasil scraping disimpan dalam format CSV agar mudah dibuka
dengan Microsoft Excel atau diolah lebih lanjut menggunakan Python.

> **Catatan:** Proyek ini dibuat untuk kebutuhan pembelajaran dan analisis
> data. Gunakan secara wajar, batasi frekuensi permintaan, dan patuhi
> ketentuan penggunaan situs MagangHub.

## Fitur

- Mengambil seluruh lowongan berdasarkan filter yang ditentukan.
- Mendukung pagination atau beberapa halaman hasil pencarian.
- Menghapus URL lowongan yang duplikat.
- Mengambil informasi dari halaman detail lowongan.
- Menyimpan hasil secara otomatis dalam format CSV.
- Membuat checkpoint selama proses scraping.
- Menghitung rasio jumlah pelamar terhadap kuota.
- Mengurutkan peluang berdasarkan tingkat persaingan.
- Menampilkan ringkasan keberhasilan dan kegagalan scraping.

## Script Utama

| File | Keterangan |
| --- | --- |
| `padang.py` | Scraper lowongan untuk Kota Padang |
| `pekanbaru.py` | Scraper lowongan untuk Kota Pekanbaru |
| `batam.py` | Scraper lowongan untuk Kota Batam |
| `cek_tombol.py` | Membantu mendiagnosis elemen dan tombol pada halaman |

Beberapa file lain di dalam repositori merupakan versi pengembangan atau
percobaan dari scraper.

## Data yang Dikumpulkan

Kolom hasil dapat mencakup:

- judul posisi;
- nama penyelenggara;
- lokasi magang;
- tingkat pendidikan;
- program studi;
- jumlah hari kerja;
- hari libur;
- kuota;
- jumlah pelamar;
- deskripsi dan kualifikasi;
- keterampilan yang diperoleh;
- URL lowongan;
- rasio pelamar per kuota;
- kategori persaingan; dan
- status scraping.

Kolom yang tersedia dapat sedikit berbeda pada setiap versi script atau
bergantung pada informasi yang disediakan oleh halaman lowongan.

## Persyaratan

- Python 3.10 atau versi yang lebih baru
- `pip`
- Chromium untuk Playwright

Paket Python utama yang digunakan:

- `pandas`
- `playwright`
- `openpyxl`

## Instalasi

Clone repositori:

```bash
git clone https://github.com/Afyuadriputra/Scarping-data-magang-hub.git
cd Scarping-data-magang-hub
```

Buat virtual environment:

```bash
python -m venv .venv
```

Aktifkan virtual environment pada Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Aktifkan virtual environment pada Linux atau macOS:

```bash
source .venv/bin/activate
```

Instal dependensi:

```bash
python -m pip install --upgrade pip
pip install pandas playwright openpyxl
playwright install chromium
```

## Cara Menjalankan

Pilih script sesuai kota yang ingin diproses. Contoh:

```bash
python padang.py
```

Untuk Kota Batam:

```bash
python batam.py
```

Untuk Kota Pekanbaru:

```bash
python pekanbaru.py
```

Browser akan dibuka atau dijalankan di latar belakang sesuai nilai
konfigurasi `HEADLESS` pada masing-masing script.

## Konfigurasi Filter

Filter utama berada di bagian awal setiap script:

```python
CITY_NAME = "Kota Padang"
CITY_ID = "ID_KOTA_DARI_MAGANGHUB"

EDUCATION_LEVEL_ID = "bachelor"
EDUCATION_LEVEL_LABEL = "Sarjana"
```

Program studi ditentukan melalui `STUDY_PROGRAMS`:

```python
STUDY_PROGRAMS = [
    {
        "id": "ID_PROGRAM_STUDI",
        "label": "Ilmu Komputer",
    },
]
```

ID kota dan program studi sebaiknya disalin dari URL MagangHub setelah filter
dipilih melalui browser. Nama filter yang sama belum tentu memiliki ID yang
sama.

Pada `batam.py`, filter jenjang dapat diaktifkan atau dinonaktifkan:

```python
FILTER_EDUCATION_LEVEL = False
```

- `False`: mengambil semua jenjang yang cocok dengan filter lain.
- `True`: menerapkan `EDUCATION_LEVEL_ID` dan `EDUCATION_LEVEL_LABEL`.

## Hasil Scraping

File hasil disimpan di direktori:

```text
hasil_scraping/
```

Nama file menyertakan kota dan waktu proses, contohnya:

```text
lowongan_maganghub_kota_padang_20260725_221421.csv
```

Checkpoint juga dapat tersimpan dalam direktori yang sama apabila proses
berjalan cukup lama atau belum selesai.

## Pemecahan Masalah

### Jumlah data berbeda dari website

Pastikan URL yang dibentuk script menggunakan ID filter yang sama dengan URL
di browser. Periksa juga apakah filter jenjang pendidikan sedang aktif.

### Tombol halaman berikutnya tidak bekerja

Struktur halaman MagangHub dapat berubah. Gunakan `cek_tombol.py` untuk
menghasilkan data diagnostik, kemudian periksa elemen pagination pada folder
`hasil_diagnostik`.

### Browser Playwright belum tersedia

Jalankan:

```bash
playwright install chromium
```

### PowerShell tidak mengizinkan aktivasi virtual environment

Jalankan perintah berikut pada sesi PowerShell saat ini:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Kemudian aktifkan kembali virtual environment.

## Saran Penggunaan

- Jangan menjalankan banyak scraper secara bersamaan.
- Gunakan jeda antarkunjungan agar tidak membebani server.
- Periksa kembali hasil sebelum digunakan untuk analisis atau pengambilan
  keputusan.
- Jangan menganggap data hasil scraping selalu lengkap atau selalu terbaru.
- Jangan mengunggah informasi pribadi atau data sensitif.

## Kontribusi

Masukan, laporan masalah, dan perbaikan kode sangat diterima. Silakan membuat
issue atau pull request dengan penjelasan yang jelas mengenai perubahan yang
diajukan.

## Penafian

Repositori ini bukan produk resmi dan tidak berafiliasi dengan Kementerian
Ketenagakerjaan Republik Indonesia. Seluruh merek, nama layanan, dan data
tetap menjadi milik masing-masing pemiliknya.

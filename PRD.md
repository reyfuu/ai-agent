# PRD — AI Agent Analisis Saham

| | |
|---|---|
| **Versi** | 1.0 (draft) |
| **Tanggal** | 26 Agustus 2026 |
| **Status** | Menunggu konfirmasi asumsi terbuka (§9) |
| **Cakupan** | v1 — saham IDX, analisis harian, web app |
| **Dokumen terkait** | [BRD.md](BRD.md) · [FRD.md](FRD.md) · [TRD.md](TRD.md) · [AGENTS.md](AGENTS.md) |

---

## 1. Ringkasan & Masalah

### 1.1 Konteks

Analis dan investor ritel memantau puluhan emiten dengan alat yang terpisah-pisah: satu situs untuk harga, satu spreadsheet untuk indikator, satu dokumen untuk catatan. Setiap hari pekerjaan yang sama diulang, dan hasilnya menguap begitu file ditutup.

### 1.2 Masalah yang diselesaikan

1. **Analisis manual lambat dan tidak konsisten.** Menghitung indikator dan menyusun kesimpulan memakan puluhan menit per emiten, dan dua analis bisa menyimpulkan berbeda dari data yang sama.
2. **Tidak ada jejak alasan.** Enam bulan kemudian tidak ada yang tahu kenapa dulu sebuah emiten dinilai kuat, sehingga tidak ada yang bisa dipelajari.
3. **LLM mentah tidak bisa dipercaya soal angka.** Model bahasa mengarang harga dan rasio dengan sangat meyakinkan, yang justru berbahaya di domain uang.

### 1.3 Visi produk

Sebuah agent yang menghitung indikator secara deterministik dengan kode, lalu memakai LLM **hanya** untuk menarasikan angka yang sudah pasti. Setiap kesimpulan menyimpan snapshot datanya, sehingga bisa dibuka lagi kapan saja dan menghasilkan angka yang persis sama.

### 1.4 Prinsip desain

- **Angka dari kode, narasi dari model.** LLM tidak pernah menghitung.
- **Angka yang ditampilkan harus bisa dipercaya.** Lebih baik menolak menganalisis karena data kurang daripada menampilkan skor tebakan.
- **Analisis lama tidak boleh berubah.** Harga hari ini tidak boleh mengubah kesimpulan kemarin.
- **Decision support, bukan nasihat investasi.** Tidak ada instruksi beli/jual.

## 2. Pengguna & Hak Akses

### 2.1 Peran

| Peran | Siapa | Tujuan utama |
|---|---|---|
| **Guest** | Pengunjung | Melihat harga dan analisis publik |
| **Analyst** | Analis / investor | Menjalankan analisis, mengelola watchlist, mengekspor |
| **Admin** | Pengelola sistem | Sumber data, bobot skoring, kuota, user, audit |

### 2.2 Matriks hak akses

| Aksi | Guest | Analyst | Admin |
|---|:---:|:---:|:---:|
| Lihat harga & analisis publik | ✅ | ✅ | ✅ |
| Jalankan analisis on-demand | ❌ | ✅ | ✅ |
| Kelola watchlist sendiri | ❌ | ✅ | ✅ |
| Jalankan batch watchlist | ❌ | ❌ | ✅ |
| Ekspor CSV / cetak PDF | ❌ | ✅ | ✅ |
| Ubah bobot skoring | ❌ | ❌ | ✅ |
| Kelola sumber data & kunci API | ❌ | ❌ | ✅ |
| Kelola user | ❌ | ❌ | ✅ |
| Lihat audit log | ❌ | miliknya | semua |

## 3. Fitur v1

| # | Fitur | Ringkas |
|---|---|---|
| P-1 | Pencarian & daftar emiten | cari kode/nama emiten IDX aktif |
| P-2 | Cache harga harian | OHLCV, append-only, penanda `stale` |
| P-3 | Indikator teknikal | MA, EMA, RSI, MACD, Bollinger, volume relatif |
| P-4 | Skoring komposit | teknikal + fundamental, bobot dapat dikonfigurasi |
| P-5 | Narasi AI | ringkasan tren, sinyal kunci, risiko, keyakinan |
| P-6 | Watchlist & batch harian | analisis massal setelah bursa tutup |
| P-7 | Riwayat & reproduksi | buka analisis lama dari snapshot |
| P-8 | Ekspor | CSV dan cetak PDF, selalu dengan disclaimer |
| P-9 | Antarmuka web HTML | grafik canvas vanilla, tanpa build step |

Detail kebutuhan per fitur ada di [FRD.md §3](FRD.md).

## 4. Status Analisis

| Status | Arti | Tampilan |
|---|---|---|
| `ok` | Data lengkap, skor & narasi tersedia | normal |
| `insufficient_data` | < 60 hari bursa atau data bolong | peringatan, tanpa skor |
| `stale` | Data terakhir lebih tua dari batas kesegaran | badge kuning + tanggal data |
| `error` | Kegagalan sistem/penyedia | pesan netral, tanpa detail teknis |

## 5. Metrik Keberhasilan

- Waktu analisis satu emiten < 2 menit (dari 30 menit manual).
- ≥ 100 emiten dapat dipantau dalam satu batch harian.
- 100% analisis punya `data_snapshot` dan dapat direproduksi identik.
- 0 angka halusinasi pada sampling mingguan 20 narasi.

## 6. Tidak Termasuk v1

Eksekusi order/broker, data intraday realtime, saham luar negeri, kripto, derivatif, backtesting otomatis, aplikasi mobile native, nasihat investasi berlisensi.

## 7. Kepatuhan & Disclaimer

Setiap tampilan analisis dan setiap file ekspor wajib memuat:

> Analisis ini dihasilkan otomatis untuk keperluan informasi, bukan nasihat investasi. Keputusan dan risikonya sepenuhnya ada pada pengguna. Data per <tanggal>.

Bahasa output netral dan non-direktif. Sistem tidak pernah menuliskan "beli" atau "jual" sebagai instruksi.

## 8. Rencana Lanjutan (v2+)

Backtesting strategi, peringatan (alert) berbasis ambang, analisis sektor & korelasi, berita dan sentimen, multi-bursa, kolaborasi tim.

## 9. Asumsi Terbuka

1. Penyedia data harga IDX mana yang dipakai, dan apakah lisensinya mengizinkan cache & tampil ulang?
2. Provider LLM dan anggaran bulanan yang disetujui?
3. Berapa batas kesegaran data sebelum ditandai `stale` (usulan: 1 hari bursa)?
4. Apakah v1 perlu akses guest publik, atau seluruhnya di balik login?
5. Bobot default teknikal vs fundamental (usulan: 60/40)?

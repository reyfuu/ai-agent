# FRD — AI Agent Analisis Saham

| | |
|---|---|
| **Dokumen** | Functional Requirements Document |
| **Versi** | 1.0 |
| **Tanggal** | 26 Agustus 2026 |
| **Turunan dari** | [BRD.md](BRD.md) |
| **Diteruskan ke** | [TRD.md](TRD.md) |

---

## 1. Ikhtisar Sistem

Sistem terdiri dari empat bagian fungsional:

1. **Data Layer** — mengambil, memvalidasi, dan menyimpan harga & fundamental.
2. **Analysis Engine** — menghitung indikator dan skor komposit secara deterministik.
3. **AI Narrator** — menyusun narasi bahasa Indonesia dari snapshot angka.
4. **Web UI** — halaman HTML untuk watchlist, detail emiten, riwayat, dan ekspor.

## 2. Aktor & Hak Akses

| Fungsi | guest | analyst | admin |
|---|:---:|:---:|:---:|
| Lihat harga & analisis publik | ✅ | ✅ | ✅ |
| Jalankan analisis on-demand | ❌ | ✅ | ✅ |
| Kelola watchlist sendiri | ❌ | ✅ | ✅ |
| Ekspor CSV/PDF | ❌ | ✅ | ✅ |
| Ubah bobot skoring | ❌ | ❌ | ✅ |
| Kelola sumber data & kunci API | ❌ | ❌ | ✅ |
| Kelola user | ❌ | ❌ | ✅ |
| Lihat audit log | ❌ | miliknya | semua |

Otorisasi diperiksa di server pada setiap endpoint.

## 3. Kebutuhan Fungsional

### 3.1 Manajemen Data Pasar

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-1.1 | Sistem menarik OHLCV harian untuk emiten yang diminta dari penyedia data terkonfigurasi. | Must |
| F-1.2 | Data yang sudah ditarik disimpan ke cache SQLite; permintaan berikutnya pada hari bursa yang sama dilayani dari cache. | Must |
| F-1.3 | Setiap panggilan eksternal memakai timeout dan retry terbatas (maks 3, backoff eksponensial). | Must |
| F-1.4 | Sistem memvalidasi kelengkapan data: minimal 60 hari bursa untuk indikator penuh. Kurang dari itu → status `insufficient_data`. | Must |
| F-1.5 | Data yang lebih tua dari batas kesegaran ditandai `stale` dan ditampilkan dengan peringatan. | Must |
| F-1.6 | Sistem menarik rasio fundamental dasar (PER, PBV, ROE, DER, margin) bila tersedia. | Should |
| F-1.7 | Tabel harga bersifat append-only; koreksi dilakukan dengan baris revisi baru. | Must |

### 3.2 Analysis Engine

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-2.1 | Menghitung MA(20/50/200) dan EMA(12/26). | Must |
| F-2.2 | Menghitung RSI(14) dengan metode Wilder. | Must |
| F-2.3 | Menghitung MACD(12,26,9) beserta histogram. | Must |
| F-2.4 | Menghitung Bollinger Bands(20, 2σ). | Should |
| F-2.5 | Menghitung volume relatif terhadap rata-rata 20 hari. | Should |
| F-2.6 | Menghasilkan skor teknikal 0–100 dari kombinasi sinyal dengan bobot terkonfigurasi. | Must |
| F-2.7 | Menghasilkan skor fundamental 0–100 bila data fundamental tersedia; bila tidak, skor komposit hanya teknikal dan ditandai demikian. | Should |
| F-2.8 | Menghasilkan skor komposit + label kualitatif (`sangat lemah`…`sangat kuat`) + tingkat keyakinan. | Must |
| F-2.9 | Semua perhitungan deterministik: input sama → output identik. | Must |
| F-2.10 | Engine tidak pernah mengeluarkan kata "beli"/"jual" sebagai instruksi. | Must |

### 3.3 AI Narrator

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-3.1 | Narator menerima **hanya** snapshot angka hasil engine, bukan akses data mentah bebas. | Must |
| F-3.2 | Narasi memuat: ringkasan tren, sinyal kunci, faktor risiko, dan tingkat keyakinan. | Must |
| F-3.3 | Output narasi divalidasi: setiap angka yang muncul harus ada di snapshot; angka asing → narasi ditolak dan diulang sekali, lalu fallback ke ringkasan template. | Must |
| F-3.4 | Narasi berbahasa Indonesia, netral, non-direktif. | Must |
| F-3.5 | Narasi di-cache per (emiten, tanggal bursa, versi prompt, versi engine). | Should |
| F-3.6 | Kegagalan LLM tidak menggagalkan analisis: skor tetap tampil tanpa narasi, dengan penanda. | Must |

### 3.4 Watchlist & Batch

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-4.1 | Analis dapat menambah/menghapus emiten pada watchlist pribadinya. | Must |
| F-4.2 | Sistem dapat menjalankan analisis batch untuk seluruh watchlist. | Must |
| F-4.3 | Batch menampilkan progres dan meringkas kegagalan per emiten. | Should |
| F-4.4 | Batch menghormati kuota LLM harian; melebihi kuota → sisanya diantrikan, bukan gagal diam. | Must |

### 3.5 Riwayat & Ekspor

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-5.1 | Setiap analisis tersimpan dengan snapshot, versi engine, versi prompt, dan waktu UTC. | Must |
| F-5.2 | Analis dapat melihat riwayat analisis satu emiten secara kronologis. | Must |
| F-5.3 | Analisis lama dapat direproduksi identik dari snapshotnya. | Must |
| F-5.4 | Ekspor CSV (data + skor) dan cetak PDF via `window.print()`. | Should |
| F-5.5 | Setiap ekspor memuat disclaimer dan tanggal data. | Must |

### 3.6 Antarmuka Web (HTML)

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-6.1 | Halaman **Watchlist**: tabel emiten, harga terakhir, perubahan %, skor komposit, label, tanggal data. | Must |
| F-6.2 | Halaman **Detail Emiten**: grafik harga + MA (canvas/SVG vanilla), tabel indikator, narasi AI, faktor risiko. | Must |
| F-6.3 | Halaman **Riwayat**: daftar analisis lama, dapat dibuka sebagai snapshot. | Must |
| F-6.4 | Halaman **Pengaturan** (admin): bobot skoring, sumber data, kuota. | Should |
| F-6.5 | Disclaimer permanen terlihat di setiap halaman analisis. | Must |
| F-6.6 | Status `insufficient_data` dan `stale` ditampilkan eksplisit, bukan disembunyikan. | Must |
| F-6.7 | Aksesibilitas dasar: label form, kontras memadai, navigasi keyboard, tabel bersemantik. | Must |
| F-6.8 | Tanpa build step: HTML + CSS + JS vanilla, dilayani langsung. | Must |

### 3.7 Autentikasi & Audit

| ID | Kebutuhan | Prioritas |
|---|---|---|
| F-7.1 | Login berbasis sesi dengan cookie bertanda tangan; password di-hash (scrypt). | Must |
| F-7.2 | Semua aksi yang mengubah data tercatat di audit log append-only. | Must |
| F-7.3 | Kunci API hanya dibaca dari environment variable dan tidak pernah dikirim ke frontend. | Must |

## 4. Alur Utama

### 4.1 Analisis on-demand
1. Analis membuka detail emiten, menekan "Analisis".
2. Sistem memeriksa cache harga hari bursa berjalan; bila kosong, tarik dari penyedia.
3. Validasi kelengkapan → bila kurang, tampilkan `insufficient_data` dan berhenti.
4. Engine menghitung indikator & skor, menghasilkan snapshot.
5. Narator menyusun narasi; validasi angka.
6. Simpan analisis + snapshot; tampilkan hasil dengan disclaimer.

### 4.2 Batch harian
1. Admin/penjadwal memicu batch setelah bursa tutup (16:00 WIB).
2. Untuk setiap emiten watchlist, jalankan alur 4.1 tanpa interaksi.
3. Ringkasan hasil: sukses, `insufficient_data`, gagal, tertunda kuota.

## 5. Aturan Validasi

| Field | Aturan |
|---|---|
| Kode emiten | 4 huruf kapital, harus ada di daftar emiten aktif |
| Rentang tanggal | tanggal mulai ≤ tanggal akhir, maksimal 5 tahun |
| Bobot skoring | integer 0–100, total tepat 100 |
| Kuota LLM harian | integer ≥ 0 |
| Harga | > 0, integer rupiah |

## 6. Kebutuhan Non-Fungsional

| ID | Kebutuhan | Target |
|---|---|---|
| NF-1 | Waktu analisis satu emiten (cache hangat) | < 2 detik |
| NF-2 | Waktu analisis satu emiten (cache dingin) | < 15 detik |
| NF-3 | Batch 100 emiten | < 15 menit |
| NF-4 | Ketersediaan UI saat penyedia data mati | tetap tampil dari cache dengan penanda `stale` |
| NF-5 | Reprodusibilitas analisis lama | 100% identik |
| NF-6 | Halaman utama tanpa JS eksternal pihak ketiga | wajib |

## 7. Kriteria Penerimaan Fungsional

- [ ] Semua kebutuhan berprioritas Must terimplementasi dan teruji.
- [ ] Uji indikator terhadap dataset referensi: selisih ≤ 0,01.
- [ ] Uji reproduksi: analisis lama menghasilkan skor identik dari snapshot.
- [ ] Uji data bolong: mengembalikan `insufficient_data`, bukan angka tebakan.
- [ ] Uji otorisasi: setiap endpoint menolak peran yang tidak berhak.
- [ ] Uji anti-halusinasi: narasi dengan angka asing ditolak sistem.

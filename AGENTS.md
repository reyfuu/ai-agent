# AGENTS.md — Kontrak Proyek AI Agent Analisis Saham

Dokumen ini dibaca oleh **semua** agent yang bekerja di repo ini.
Dokumen bisnis & fungsional: [BRD.md](BRD.md), [FRD.md](FRD.md), [TRD.md](TRD.md), [PRD.md](PRD.md).
Dokumen ini hanya memuat aturan teknis yang tidak boleh dilanggar siapa pun.

## Stack (sudah diputuskan — jangan ditambah)

| Bagian | Pilihan | Alasan |
|---|---|---|
| Bahasa | Python ≥ 3.11 | tipe modern, `tomllib`, `asyncio.TaskGroup` |
| HTTP server | `http.server` / `wsgiref` stdlib, atau `fastapi` bila API publik | default: stdlib, nol dependency |
| Data pasar | satu adapter HTTP (`urllib.request`) ke penyedia harga | tidak perlu SDK berat |
| Database | `sqlite3` stdlib (file tunggal `stocks.db`) | cache harga + jejak analisis |
| Analitik | Python murni + `statistics` stdlib; `pandas`/`numpy` hanya jika terbukti perlu | indikator teknikal sederhana tidak butuh dataframe |
| LLM | LangChain (abstraksi provider) di `agent/llm.py` | provider bisa diganti tanpa menyentuh logika |
| Orkestrasi | LangGraph (alur) + CrewAI (Narrator & Critic) | lihat [ADR-001](docs/adr/001-orkestrasi-multi-agent.md) |
| Frontend | **HTML + CSS + JS vanilla** (`web/index.html`) | tidak ada build step, tidak ada framework |
| Grafik | `<canvas>` vanilla atau SVG inline | tidak perlu chart library |
| Test | `unittest` stdlib, `python -m unittest` | tidak perlu pytest |

**Menambah dependency baru butuh alasan tertulis di PR/commit.** Default jawabannya tidak.
Cek dulu: apakah stdlib Python atau fitur native browser sudah cukup?

Tiga pengecualian yang sudah disetujui (LangChain, LangGraph, CrewAI) dibatasi ketat oleh
[ADR-001](docs/adr/001-orkestrasi-multi-agent.md):

- `agent/analysis.py` **tidak boleh** mengimpor ketiganya. Semua angka lahir di sana.
- Impor bersifat **lazy**, di dalam fungsi, bukan di level modul.
- `run_analysis_fallback()` wajib ada dan menghasilkan skor identik tanpa framework.

Ketiga batas ini ditegakkan otomatis oleh `tests/test_boundaries.py`.

## Invariant — dilanggar = bug, bukan preferensi

1. **Agent tidak pernah memberi perintah beli/jual sebagai instruksi final.** Setiap output analisis wajib menyertakan disclaimer, tingkat keyakinan, dan alasan. Ini produk *decision support*, bukan robo-advisor berlisensi.

2. **Setiap angka dalam output harus punya sumber yang bisa ditelusuri.** Analisis menyimpan `data_snapshot` (harga, tanggal, sumber). LLM tidak boleh mengarang angka: angka datang dari kode, LLM hanya menarasikan.

3. **Harga historis bersifat append-only.** Tabel `prices` dan `analyses` tidak pernah di-`UPDATE`/`DELETE`. Koreksi = baris baru dengan `revision` naik.

4. **Analisis lama tidak boleh berubah.** Laporan tanggal kemarin harus reproducible dari snapshot-nya, bukan dari harga hari ini.

5. **Uang & harga disimpan sebagai integer terkecil** (sen/rupiah penuh) atau `decimal.Decimal`. **Tidak pernah** `float` untuk nilai uang. `float` hanya boleh untuk indikator statistik (RSI, MA) yang memang berbasis rasio.

6. **Panggilan API eksternal wajib punya timeout, retry terbatas, dan cache.** Tanpa timeout, satu API lambat membekukan seluruh agent. Data yang sama dalam satu hari bursa dibaca dari cache SQLite.

7. **Kunci API hanya dari environment variable.** Tidak pernah di-hardcode, tidak pernah masuk git, tidak pernah dikirim ke frontend.

8. **Waktu disimpan UTC, ditampilkan WIB (UTC+7).** Batas hari analisis mengikuti jam tutup bursa (IDX 16:00 WIB). Jangan pakai `datetime.now()` lokal untuk menentukan tanggal bursa — gunakan helper `trading_date()`.

9. **Kegagalan data ≠ diam.** Jika data tidak lengkap, agent mengembalikan status `insufficient_data`, bukan analisis berdasarkan tebakan.

10. **Framework hanya menyentuh narasi dan alur, tidak pernah angka.** LangGraph mengatur percabangan, CrewAI menarasikan, tetapi setiap angka lahir di `agent/analysis.py` yang murni stdlib. Cabut ketiga framework hari ini, angka produk tidak berubah sedikit pun. Ditegakkan oleh `tests/test_boundaries.py`.

## Peran & hak akses

| Peran | Kemampuan |
|---|---|
| `guest` | lihat harga & analisis publik |
| `analyst` | jalankan analisis, kelola watchlist sendiri, ekspor |
| `admin` | kelola sumber data, kunci API, user, lihat audit log |

Otorisasi diperiksa **di server pada setiap endpoint**. Menyembunyikan tombol di UI bukan otorisasi.

## Gaya kerja

- Kode membosankan lebih baik daripada kode pintar. Yang membaca jam 3 pagi adalah kita sendiri.
- Sedikit file lebih baik daripada banyak file. Jangan pecah file "untuk rapi" kalau isinya < 100 baris.
- Jangan bikin abstraksi untuk satu pemakai. Tidak ada `BaseAnalyzer` dengan satu subclass.
- Tandai penyederhanaan yang disengaja dengan komentar `# ponytail:` yang menyebut batas atas dan jalur upgrade-nya. Contoh: `# ponytail: hitung indikator per request, ganti job terjadwal kalau watchlist > 500 emiten`.
- **Jangan pernah** menyederhanakan dengan membuang: validasi input, penanganan error, pemeriksaan otorisasi, disclaimer risiko, atau aksesibilitas dasar.

## Verifikasi

Setiap perubahan pada logika non-trivial meninggalkan satu pemeriksaan yang bisa dijalankan.
Jalankan `python -m unittest discover -s tests -t .` sebelum menyatakan selesai.
Invariant di `tests/` (akurasi indikator, reproducibility analisis lama, penolakan data tidak lengkap,
batas arsitektur ADR-001, dan otorisasi API) harus lulus — kalau merah, itu bug produk, bukan test yang perlu dilonggarkan.

## Struktur file

```
BRD.md              kebutuhan bisnis (kenapa proyek ini ada)
FRD.md              kebutuhan fungsional (apa yang dilakukan sistem)
TRD.md              kebutuhan teknis (bagaimana dibangun)
PRD.md              spesifikasi produk (sumber kebenaran fitur)
AGENTS.md           dokumen ini (aturan teknis)
agent/__init__.py
agent/data.py       ambil & cache harga, adapter sumber data
agent/analysis.py   indikator teknikal & fundamental, skoring
agent/llm.py        klien LLM tipis, prompt, narasi
agent/db.py         skema SQLite, migrasi, query helper
agent/batch.py      runner analisis watchlist + kuota (CLI)
agent/graph.py      orkestrasi LangGraph + jalur fallback stdlib
agent/seed.py       data contoh untuk pengembangan
server.py           routing, auth, endpoint API, render HTML
run_dev_server.py   entry point server pengembangan
web/index.html      UI utama
web/app.js, web/style.css
tests/              pemeriksaan invariant
docs/adr/           catatan keputusan arsitektur
requirements.txt    dependency orkestrasi (lihat ADR-001)
stocks.db           database SQLite (tidak di-commit)
```

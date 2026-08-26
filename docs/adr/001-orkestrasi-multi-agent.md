# ADR-001 — Mengadopsi LangChain, LangGraph, dan CrewAI

| | |
|---|---|
| **Status** | Diterima |
| **Tanggal** | 26 Agustus 2026 |
| **Konteks aturan** | [AGENTS.md](../../AGENTS.md) mewajibkan alasan tertulis untuk setiap dependency baru |

---

## Konteks

AGENTS.md menetapkan stack stdlib-first dan menyatakan "default jawabannya tidak" untuk dependency baru. Tiga framework ini melanggar aturan itu, jadi keputusannya harus dibela secara eksplisit atau dibatalkan.

Kebutuhan yang mendorongnya:

1. **Alur analisis punya percabangan dan retry berkondisi** (FRD F-3.3): narasi divalidasi, gagal → ulang sekali, gagal lagi → fallback template. Ditulis manual, ini berubah menjadi rangkaian `if/else` bersarang yang sulit diuji per cabang.
2. **Validasi anti-halusinasi lebih kuat bila ada agent kedua yang mengkritik** (BRD G-4, risiko tertinggi di register risiko).
3. **Jejak orkestrasi harus bisa diaudit per node** (DATAMODEL §2.5 `agent_runs`).
4. **Provider LLM harus bisa diganti** tanpa menyentuh logika bisnis.

## Keputusan

Mengadopsi ketiganya, **masing-masing dengan peran sempit dan batas yang tegas**:

| Framework | Peran | Yang **tidak** boleh dilakukan |
|---|---|---|
| **LangChain** | abstraksi provider LLM + parser output terstruktur | tidak dipakai untuk chain bisnis, tidak untuk retrieval, tidak untuk tool calling ke data pasar |
| **LangGraph** | state machine alur analisis: `fetch → validate → compute → narrate → critique → persist`, termasuk percabangan dan retry berkondisi | tidak boleh menghitung indikator, tidak boleh menyimpan state di luar SQLite |
| **CrewAI** | dua agent bernarasi: `Narrator` dan `Critic` (pemeriksa angka) | tidak boleh memanggil API data pasar, tidak boleh mengambil keputusan skor |

## Batas arsitektur yang tidak boleh dilanggar

Ini yang membuat adopsi ini aman:

1. **`agent/analysis.py` tetap murni stdlib.** Tidak ada satu pun import dari ketiga framework di dalamnya. Semua angka dihitung di sana. Jika ketiganya dicabut besok, angka produk tidak berubah sedikit pun.
2. **Framework hanya menyentuh narasi dan alur, tidak pernah menyentuh angka.** Invariant AGENTS.md #2 tetap berlaku: angka dari kode, narasi dari model.
3. **Wajib ada jalur fallback tanpa framework.** Bila LangGraph/CrewAI gagal diimpor atau gagal berjalan, sistem menjalankan `run_analysis_fallback()` berbasis stdlib dan tetap menghasilkan skor. Diuji di test, bukan sekadar diklaim.
4. **State disimpan di SQLite, bukan di memori framework.** Checkpointer LangGraph tidak dijadikan sumber kebenaran.
5. **Validasi angka tetap dilakukan kode deterministik**, bukan hanya oleh Critic agent. Critic adalah lapisan kedua, bukan pengganti.

## Konsekuensi

**Positif**
- Percabangan retry/fallback menjadi graf eksplisit yang bisa diuji per node.
- `agent_runs` terisi otomatis per node: framework, durasi, token, digest.
- Critic agent menambah lapisan pertahanan pada risiko tertinggi produk.
- Provider LLM dapat diganti lewat konfigurasi.

**Negatif — diterima dengan sadar**
- Dependency berat: ketiganya menarik puluhan paket transitif. Ini kebalikan dari semangat AGENTS.md.
- Permukaan serangan dan beban pemeliharaan bertambah; versi di-pin ketat.
- Waktu start lebih lambat. Karena itu impor framework bersifat **lazy**, hanya saat analisis dijalankan.
- Risiko *lock-in* ditekan oleh batas #1 dan #3: inti produk tetap berjalan tanpa mereka.

## Alternatif yang ditolak

| Alternatif | Alasan ditolak |
|---|---|
| Tulis sendiri state machine dengan stdlib | Layak dan lebih ringan, tetapi kehilangan tracing per node bawaan dan pola retry yang sudah teruji. Tetap dipertahankan sebagai jalur fallback wajib. |
| Hanya LangChain | Tidak menyediakan percabangan berkondisi dan checkpoint yang dibutuhkan. |
| Hanya LangGraph | Cukup untuk alur, tetapi pola dua agent Narrator/Critic lebih ringkas dinyatakan di CrewAI. |
| Tanpa framework sama sekali | Pilihan awal AGENTS.md. Ditolak karena permintaan eksplisit pemangku kepentingan dan kebutuhan audit per node. |

## Kepatuhan

Aturan ini ditegakkan otomatis oleh `tests/test_boundaries.py`:

- `agent/analysis.py` tidak mengimpor langchain/langgraph/crewai.
- `run_analysis_fallback()` ada dan menghasilkan skor identik dengan jalur graf.
- Nilai uang tetap integer di seluruh jalur.
- Kunci API tidak pernah masuk ke `agent_runs`.

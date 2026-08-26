# API_CONTRACT — AI Agent Analisis Saham

| | |
|---|---|
| **Dokumen** | API Contract |
| **Versi** | 1.0 |
| **Tanggal** | 26 Agustus 2026 |
| **Turunan dari** | [TRD.md](TRD.md) · [USERFLOW.md](USERFLOW.md) |
| **Terkait** | [DATAMODEL.md](DATAMODEL.md) |

---

## 1. Konvensi

- Base path `/api`, seluruh body JSON UTF-8.
- Autentikasi: cookie sesi `sid` (`HttpOnly`, `SameSite=Lax`, `Secure` di produksi).
- Semua timestamp UTC ISO-8601 berakhiran `Z`. `trade_date` adalah tanggal bursa WIB.
- Nilai uang **integer rupiah**. Rasio dan indikator boleh desimal.
- Otorisasi diperiksa server-side di setiap endpoint.
- Endpoint yang mengubah data bersifat idempoten bila diberi `Idempotency-Key`.

## 2. Bentuk Error Seragam

```json
{ "error": { "code": "INSUFFICIENT_DATA", "message": "Butuh 60 hari bursa, tersedia 41." } }
```

| HTTP | `code` | Kapan |
|---|---|---|
| 400 | `VALIDATION_ERROR` | input tidak lolos validasi |
| 401 | `UNAUTHENTICATED` | tidak ada sesi valid |
| 403 | `FORBIDDEN` | peran tidak berhak |
| 404 | `NOT_FOUND` | emiten/analisis tidak ada |
| 409 | `WEIGHTS_INVALID` | bobot tidak berjumlah 100 |
| 429 | `RATE_LIMITED` / `QUOTA_EXCEEDED` | rate limit atau kuota LLM habis |
| 502 | `UPSTREAM_UNAVAILABLE` | penyedia data gagal & cache kosong |
| 500 | `INTERNAL_ERROR` | pesan netral, **tanpa** stack trace |

Stack trace tidak pernah dikirim ke klien. Kunci API tidak pernah muncul di respons mana pun.

## 3. Endpoint

### 3.1 Auth

#### `POST /api/login` — publik
```json
{ "username": "andi", "password": "..." }
```
**200** `{ "user": { "id": 2, "username": "andi", "role": "analyst" } }` + `Set-Cookie: sid=...`
**401** `{"error":{"code":"UNAUTHENTICATED","message":"Username atau password salah."}}` — pesan generik, tidak membocorkan mana yang salah.
**429** setelah 5 percobaan gagal per IP dalam 5 menit.

#### `POST /api/logout` — terautentikasi → **204**

#### `GET /api/me` — terautentikasi → **200** `{ "id": 2, "username": "andi", "role": "analyst" }`

### 3.2 Emiten & Harga

#### `GET /api/tickers?q=bbca&limit=10` — guest+
**200**
```json
{ "items": [ { "code": "BBCA", "name": "Bank Central Asia", "sector": "Keuangan", "active": true } ] }
```

#### `GET /api/prices/{code}?from=2026-01-01&to=2026-08-26` — guest+
**200**
```json
{
  "code": "BBCA",
  "stale": false,
  "last_trade_date": "2026-08-26",
  "source": "provider-a",
  "items": [
    { "trade_date": "2026-08-26", "open": 10200, "high": 10300, "low": 10150, "close": 10250, "volume": 88123400 }
  ]
}
```
Rentang maksimal 5 tahun; `from > to` → **400**. Penyedia mati tetapi cache ada → **200** dengan `"stale": true`. Cache kosong → **502**.

### 3.3 Analisis

#### `POST /api/analyze/{code}` — analyst+
Header opsional: `Idempotency-Key: <uuid>`
```json
{ "force_refresh": false }
```

**200 — berhasil**
```json
{
  "id": 1042,
  "code": "BBCA",
  "trade_date": "2026-08-26",
  "status": "ok",
  "stale": false,
  "scores": { "tech": 68, "funda": 74, "total": 70 },
  "label": "kuat",
  "confidence": "sedang",
  "flags": [],
  "narrative": "Tren jangka menengah BBCA berada di atas MA50...",
  "narrative_status": "ok",
  "data_snapshot": { "...": "lihat DATAMODEL §2.4" },
  "engine_version": "1.0.0",
  "prompt_version": "1.0.0",
  "created_at": "2026-08-26T10:05:00Z",
  "disclaimer": "Analisis otomatis untuk informasi, bukan nasihat investasi. Data per 2026-08-26."
}
```

**200 — data tidak cukup** (bukan error HTTP, ini hasil analisis yang sah)
```json
{
  "code": "GOTO", "trade_date": "2026-08-26", "status": "insufficient_data",
  "scores": null, "narrative": null, "narrative_status": "unavailable",
  "detail": { "required_days": 60, "available_days": 41 },
  "disclaimer": "..."
}
```

**200 — LLM gagal / kuota habis**: `status: "ok"`, skor lengkap, `narrative: null`, `narrative_status: "unavailable"` atau `"queued"`. Analisis **tidak pernah** hilang hanya karena narator gagal.

**403** untuk `guest`. **404** bila emiten tidak dikenal.

#### `GET /api/analyses/{code}?limit=50` — guest+
**200** daftar kronologis ringkas: `id`, `trade_date`, `status`, `scores.total`, `label`, `confidence`, `engine_version`, `created_at`.

#### `GET /api/analyses/id/{id}` — guest+
**200** satu analisis lengkap **persis seperti saat disimpan**, termasuk `data_snapshot`. Respons wajib identik bila dipanggil ulang kapan pun (invariant D-3).

#### `GET /api/analyses/id/{id}/runs` — analyst+
Jejak orkestrasi multi-agent untuk analisis tersebut.
```json
{ "items": [
  { "framework": "langgraph", "node": "fetch",    "status": "ok", "duration_ms": 412, "tokens_in": 0,   "tokens_out": 0 },
  { "framework": "langgraph", "node": "compute",  "status": "ok", "duration_ms": 38,  "tokens_in": 0,   "tokens_out": 0 },
  { "framework": "crewai",    "node": "narrate",  "status": "ok", "duration_ms": 2310,"tokens_in": 820, "tokens_out": 240 },
  { "framework": "crewai",    "node": "critique", "status": "ok", "duration_ms": 1180,"tokens_in": 640, "tokens_out": 90 }
] }
```
Tidak memuat isi prompt maupun kunci API, hanya digest dan metrik.

### 3.4 Watchlist

| Method | Path | Peran | Keterangan |
|---|---|---|---|
| `GET` | `/api/watchlist` | analyst+ | milik sendiri, dengan harga & skor terakhir |
| `POST` | `/api/watchlist` | analyst+ | body `{"code":"BBCA"}` → **201**; sudah ada → **200** (idempoten) |
| `DELETE` | `/api/watchlist/{code}` | analyst+ | **204**; riwayat analisis tetap utuh |

Server selalu menyaring berdasarkan sesi. Tidak ada parameter `user_id` yang diterima dari klien.

### 3.5 Batch

#### `POST /api/batch` — admin
```json
{ "scope": "watchlist", "user_id": null }
```
**202**
```json
{ "batch_id": "b-20260826-1", "total": 100, "status": "running" }
```

#### `GET /api/batch/{batch_id}` — admin
**200**
```json
{
  "batch_id": "b-20260826-1", "status": "done",
  "total": 100, "ok": 92, "insufficient_data": 5, "failed": 1, "queued": 2,
  "failures": [ { "code": "XYZA", "reason": "UPSTREAM_UNAVAILABLE" } ]
}
```
Satu emiten gagal tidak menggagalkan batch.

### 3.6 Pengaturan — admin

#### `GET /api/settings`
```json
{
  "weight_tech": 60, "weight_funda": 40,
  "llm_daily_quota": 500, "llm_used_today": 128,
  "stale_after_days": 1, "min_window_days": 60,
  "market_api_key_configured": true,
  "llm_api_key_configured": true
}
```
Hanya status "terkonfigurasi", **tidak pernah** nilai kuncinya.

#### `PUT /api/settings`
Bobot tidak berjumlah 100 → **409** `WEIGHTS_INVALID` dengan `detail.current_total`. Perubahan bobot menaikkan `engine_version` dan menulis audit log.

### 3.7 Ekspor

#### `GET /api/export/{code}.csv?from=&to=` — analyst+
`Content-Type: text/csv`, `Content-Disposition: attachment`. Baris pertama berisi komentar disclaimer + tanggal data, lalu header kolom. Kolom uang berupa integer tanpa pemisah ribuan.

### 3.8 Audit — analyst (miliknya) / admin (semua)

`GET /api/audit?limit=100` → **200** daftar `{ id, user_id, action, detail, created_at }`. `analyst` otomatis tersaring ke miliknya sendiri.

## 4. Matriks Otorisasi

| Endpoint | guest | analyst | admin |
|---|:---:|:---:|:---:|
| `GET /api/tickers`, `/api/prices/*` | ✅ | ✅ | ✅ |
| `GET /api/analyses/*` | ✅ | ✅ | ✅ |
| `POST /api/analyze/*` | ❌ | ✅ | ✅ |
| `GET /api/analyses/id/*/runs` | ❌ | ✅ | ✅ |
| `* /api/watchlist*` | ❌ | ✅ | ✅ |
| `GET /api/export/*` | ❌ | ✅ | ✅ |
| `POST /api/batch`, `GET /api/batch/*` | ❌ | ❌ | ✅ |
| `GET/PUT /api/settings` | ❌ | ❌ | ✅ |
| `GET /api/audit` | ❌ | miliknya | semua |

Sama persis dengan PRD §2.2 dan FRD §2. Perbedaan di antara ketiganya adalah bug.

## 5. Rate Limit & Idempotensi

| Endpoint | Batas |
|---|---|
| `POST /api/login` | 5 gagal / IP / 5 menit |
| `POST /api/analyze/*` | 30 / user / menit |
| `POST /api/batch` | 1 batch berjalan pada satu waktu |

`Idempotency-Key` pada `POST /api/analyze/*` dalam 10 menit mengembalikan analisis yang sama, bukan membuat baris baru dan bukan memanggil LLM ulang.

## 6. Versi

Versi lewat header `Accept: application/vnd.saham.v1+json`; tanpa header dianggap v1. Perubahan yang merusak kontrak menaikkan versi mayor. Menambah field opsional bukan perubahan merusak; klien wajib mengabaikan field yang tidak dikenal.

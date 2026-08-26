"""Skema SQLite, migrasi, dan helper query.

Stdlib saja. Tidak memuat logika bisnis dan tidak mengimpor framework agent.
Tabel prices/analyses/audit_logs/agent_runs bersifat APPEND-ONLY.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

DB_PATH = os.environ.get("STOCKS_DB", "stocks.db")
WIB = timezone(timedelta(hours=7))
MARKET_CLOSE_HOUR = 16  # IDX tutup 16:00 WIB

MIGRATIONS: list[str] = [
    # 1 - skema awal
    """
    CREATE TABLE tickers (
      code TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT,
      active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE prices (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL REFERENCES tickers(code),
      trade_date TEXT NOT NULL,
      open INTEGER NOT NULL, high INTEGER NOT NULL,
      low INTEGER NOT NULL, close INTEGER NOT NULL,
      volume INTEGER NOT NULL,
      source TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
      fetched_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX ux_prices ON prices(code, trade_date, revision);
    CREATE INDEX ix_prices_win ON prices(code, trade_date DESC);
    CREATE TABLE fundamentals (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL REFERENCES tickers(code),
      period TEXT NOT NULL,
      per REAL, pbv REAL, roe REAL, der REAL, net_margin REAL,
      source TEXT NOT NULL, fetched_at TEXT NOT NULL
    );
    CREATE INDEX ix_funda ON fundamentals(code, period DESC);
    CREATE TABLE analyses (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL REFERENCES tickers(code),
      trade_date TEXT NOT NULL,
      status TEXT NOT NULL,
      stale INTEGER NOT NULL DEFAULT 0,
      score_tech INTEGER, score_funda INTEGER, score_total INTEGER,
      label TEXT, confidence TEXT,
      data_snapshot TEXT NOT NULL,
      narrative TEXT, narrative_status TEXT,
      engine_version TEXT NOT NULL, prompt_version TEXT,
      created_at TEXT NOT NULL
    );
    CREATE INDEX ix_analyses ON analyses(code, trade_date DESC);
    CREATE TABLE agent_runs (
      id INTEGER PRIMARY KEY,
      analysis_id INTEGER REFERENCES analyses(id),
      framework TEXT NOT NULL, node TEXT NOT NULL, status TEXT NOT NULL,
      input_digest TEXT, output_digest TEXT,
      tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
      duration_ms INTEGER NOT NULL DEFAULT 0,
      error TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX ix_runs ON agent_runs(analysis_id);
    CREATE TABLE users (
      id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, role TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE watchlist (
      user_id INTEGER NOT NULL REFERENCES users(id),
      code TEXT NOT NULL REFERENCES tickers(code),
      PRIMARY KEY (user_id, code)
    );
    CREATE TABLE audit_logs (
      id INTEGER PRIMARY KEY, user_id INTEGER,
      action TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX ix_audit_user ON audit_logs(user_id, created_at DESC);
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]

DEFAULT_SETTINGS = {
    "weight_tech": "60",
    "weight_funda": "40",
    "llm_daily_quota": "500",
    "stale_after_days": "1",
    "min_window_days": "60",
}


# ---------------------------------------------------------------- waktu

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trading_date(at: datetime | None = None) -> str:
    """Tanggal bursa efektif (WIB). Sebelum jam tutup, acuannya hari sebelumnya.

    Jangan pernah memakai datetime.now() lokal untuk menentukan tanggal bursa.
    """
    at = (at or datetime.now(timezone.utc)).astimezone(WIB)
    if at.hour < MARKET_CLOSE_HOUR:
        at -= timedelta(days=1)
    while at.weekday() >= 5:  # 5=Sabtu, 6=Minggu
        at -= timedelta(days=1)
    return at.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- koneksi

def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row["version"] if row else 0
    for i, script in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(script)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
        conn.commit()
        current = i
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    return current


# ---------------------------------------------------------------- password

def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


# ---------------------------------------------------------------- settings

def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        v = r["value"]
        out[r["key"]] = int(v) if v.lstrip("-").isdigit() else v
    return out


def set_weights(conn: sqlite3.Connection, tech: int, funda: int) -> None:
    """Menolak bobot yang tidak berjumlah 100. Tidak menormalisasi diam-diam."""
    if not (isinstance(tech, int) and isinstance(funda, int)):
        raise ValueError("bobot harus integer")
    if tech + funda != 100:
        raise ValueError(f"bobot harus berjumlah 100, saat ini {tech + funda}")
    if not (0 <= tech <= 100 and 0 <= funda <= 100):
        raise ValueError("bobot harus di rentang 0..100")
    conn.execute("UPDATE settings SET value=? WHERE key='weight_tech'", (str(tech),))
    conn.execute("UPDATE settings SET value=? WHERE key='weight_funda'", (str(funda),))
    conn.commit()


# ---------------------------------------------------------------- harga

def insert_prices(conn: sqlite3.Connection, code: str, bars: Sequence[dict], source: str) -> int:
    """Append-only. Baris yang sudah ada diabaikan, bukan ditimpa."""
    n = 0
    for b in bars:
        for field in ("open", "high", "low", "close", "volume"):
            if not isinstance(b[field], int):
                raise TypeError(f"{field} harus integer, dapat {type(b[field]).__name__}")
        cur = conn.execute(
            "INSERT OR IGNORE INTO prices "
            "(code, trade_date, open, high, low, close, volume, source, revision, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,1,?)",
            (code, b["trade_date"], b["open"], b["high"], b["low"], b["close"],
             b["volume"], source, now_utc()),
        )
        n += cur.rowcount
    conn.commit()
    return n


def get_bars(conn: sqlite3.Connection, code: str, limit: int = 400) -> list[dict]:
    """N bar terakhir, urut menaik. Hanya revisi tertinggi per tanggal."""
    rows = conn.execute(
        """
        SELECT p.trade_date, p.open, p.high, p.low, p.close, p.volume
        FROM prices p
        JOIN (SELECT trade_date, MAX(revision) r FROM prices
              WHERE code=? GROUP BY trade_date) m
          ON m.trade_date=p.trade_date AND m.r=p.revision
        WHERE p.code=? ORDER BY p.trade_date DESC LIMIT ?
        """,
        (code, code, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def latest_fundamentals(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute(
        "SELECT per, pbv, roe, der, net_margin FROM fundamentals "
        "WHERE code=? ORDER BY period DESC LIMIT 1",
        (code,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- analisis

def save_analysis(
    conn: sqlite3.Connection,
    snapshot: dict,
    narrative: str | None,
    narrative_status: str,
    prompt_version: str | None,
    stale: bool = False,
) -> int:
    """Simpan satu analisis. APPEND-ONLY: tidak pernah memperbarui baris lama."""
    scores = snapshot.get("scores") or {}
    cur = conn.execute(
        "INSERT INTO analyses (code, trade_date, status, stale, score_tech, score_funda,"
        " score_total, label, confidence, data_snapshot, narrative, narrative_status,"
        " engine_version, prompt_version, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            snapshot["code"], snapshot["trade_date"], snapshot["status"], int(stale),
            scores.get("tech"), scores.get("funda"), scores.get("total"),
            snapshot.get("label"), snapshot.get("confidence"),
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False),
            narrative, narrative_status,
            snapshot["engine_version"], prompt_version, now_utc(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_analysis(conn: sqlite3.Connection, analysis_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["data_snapshot"] = json.loads(out["data_snapshot"])
    return out


_SECRET_HINTS = ("api_key", "apikey", "authorization", "secret", "token=", "bearer")


def log_agent_run(
    conn: sqlite3.Connection, analysis_id: int | None, framework: str, node: str,
    status: str, input_digest: str = "", output_digest: str = "",
    tokens_in: int = 0, tokens_out: int = 0, duration_ms: int = 0, error: str | None = None,
) -> None:
    """Catat satu langkah orkestrasi. Menyimpan digest, bukan isi prompt."""
    if error:
        low = error.lower()
        if any(h in low for h in _SECRET_HINTS):
            error = "redacted: pesan galat berpotensi memuat kredensial"
        error = error[:500]
    conn.execute(
        "INSERT INTO agent_runs (analysis_id, framework, node, status, input_digest,"
        " output_digest, tokens_in, tokens_out, duration_ms, error, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (analysis_id, framework, node, status, input_digest, output_digest,
         tokens_in, tokens_out, duration_ms, error, now_utc()),
    )
    conn.commit()


def digest(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def audit(conn: sqlite3.Connection, user_id: int | None, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?,?,?,?)",
        (user_id, action, detail, now_utc()),
    )
    conn.commit()


def init(path: str | None = None) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn

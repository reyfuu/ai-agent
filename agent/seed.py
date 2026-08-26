"""Isi database dengan emiten, user, dan harga contoh untuk pengembangan.

Harga dibangkitkan deterministik (seed tetap) supaya hasil analisis
dapat direproduksi antar mesin. Ini BUKAN data pasar sungguhan.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

from agent import db

TICKERS = [
    ("BBCA", "Bank Central Asia", "Keuangan", 10000),
    ("BBRI", "Bank Rakyat Indonesia", "Keuangan", 4500),
    ("TLKM", "Telkom Indonesia", "Infrastruktur", 3200),
    ("ASII", "Astra International", "Aneka Industri", 5100),
    ("UNVR", "Unilever Indonesia", "Konsumsi", 2400),
    ("NEWX", "Emiten Baru Melantai", "Teknologi", 500),
]

USERS = [
    ("owner", "owner123", "admin"),
    ("andi", "andi123", "analyst"),
    ("tamu", "tamu123", "guest"),
]


def bars_for(seed: int, n: int, start: int, until: date) -> list[dict]:
    """Deret harga sintetis deterministik, hanya hari kerja."""
    rnd = random.Random(seed)
    hari: list[date] = []
    d = until
    while len(hari) < n:
        if d.weekday() < 5:
            hari.append(d)
        d -= timedelta(days=1)
    hari.reverse()

    out, px = [], start
    for tgl in hari:
        px = max(50, int(px * (1 + rnd.uniform(-0.025, 0.027))))
        out.append({
            "trade_date": tgl.isoformat(),
            "open": px, "high": int(px * 1.015), "low": int(px * 0.985),
            "close": px, "volume": rnd.randint(500_000, 20_000_000),
        })
    return out


def seed(conn, hari: int = 260) -> None:
    until = date.fromisoformat(db.trading_date())

    for code, nama, sektor, harga in TICKERS:
        conn.execute(
            "INSERT OR IGNORE INTO tickers (code, name, sector) VALUES (?,?,?)",
            (code, nama, sektor))
        n = 41 if code == "NEWX" else hari  # NEWX sengaja kurang data
        db.insert_prices(conn, code, bars_for(hash(code) % 1000, n, harga, until), "seed")

    conn.execute(
        "INSERT OR IGNORE INTO fundamentals"
        " (code, period, per, pbv, roe, der, net_margin, source, fetched_at)"
        " VALUES ('BBCA','2026Q2',21.4,4.1,19.2,0.32,31.5,'seed',?)", (db.now_utc(),))
    conn.execute(
        "INSERT OR IGNORE INTO fundamentals"
        " (code, period, per, pbv, roe, der, net_margin, source, fetched_at)"
        " VALUES ('TLKM','2026Q2',14.2,2.3,15.8,0.55,22.1,'seed',?)", (db.now_utc(),))

    for username, password, role in USERS:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, db.hash_password(password), role))

    row = conn.execute("SELECT id FROM users WHERE username='andi'").fetchone()
    if row:
        for code in ("BBCA", "TLKM", "NEWX"):
            conn.execute("INSERT OR IGNORE INTO watchlist (user_id, code) VALUES (?,?)",
                         (row["id"], code))
    conn.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed data pengembangan")
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=int, default=260)
    args = ap.parse_args(argv)

    conn = db.init(args.db)
    seed(conn, args.days)
    n_t = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
    n_p = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    print(f"Seed selesai: {n_t} emiten, {n_p} baris harga, {len(USERS)} user.")
    print("Login contoh: owner/owner123 (admin), andi/andi123 (analyst), tamu/tamu123 (guest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

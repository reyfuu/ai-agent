"""Runner analisis batch untuk seluruh watchlist.

Dijalankan cron pukul 17:00 WIB (setelah bursa tutup 16:00).
Satu emiten gagal tidak boleh menggagalkan batch.
"""

from __future__ import annotations

import argparse
import sys

from agent import db
from agent.graph import analyze_ticker


def run_batch(conn, codes: list[str] | None = None, quota: int | None = None) -> dict:
    """Analisis daftar emiten. Mengembalikan ringkasan per status."""
    settings = db.get_settings(conn)
    quota = settings["llm_daily_quota"] if quota is None else quota

    if codes is None:
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT code FROM watchlist ORDER BY code")]
        if not codes:
            codes = [r[0] for r in conn.execute(
                "SELECT code FROM tickers WHERE active=1 ORDER BY code")]

    hasil = {"total": len(codes), "ok": 0, "insufficient_data": 0,
             "failed": 0, "queued": 0, "failures": []}
    terpakai = 0

    for code in codes:
        try:
            if terpakai >= quota:
                # kuota habis: skor tetap dihitung, narasi diantrikan
                hasil["queued"] += 1
            out = analyze_ticker(conn, code)
            if out["status"] == "ok":
                hasil["ok"] += 1
                if out["narrative_status"] == "ok":
                    terpakai += 1
            elif out["status"] == "insufficient_data":
                hasil["insufficient_data"] += 1
        except Exception as exc:  # satu emiten gagal, batch jalan terus
            hasil["failed"] += 1
            hasil["failures"].append({"code": code, "reason": f"{type(exc).__name__}"})

    db.audit(conn, None, "batch", f"{hasil['ok']}/{hasil['total']} berhasil")
    return hasil


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Batch analisis saham")
    ap.add_argument("--codes", help="daftar kode dipisah koma; default watchlist")
    ap.add_argument("--db", default=None, help="path database")
    args = ap.parse_args(argv)

    conn = db.init(args.db)
    codes = [c.strip().upper() for c in args.codes.split(",")] if args.codes else None
    hasil = run_batch(conn, codes)

    print(f"Total          : {hasil['total']}")
    print(f"Berhasil       : {hasil['ok']}")
    print(f"Data kurang    : {hasil['insufficient_data']}")
    print(f"Gagal          : {hasil['failed']}")
    print(f"Narasi antre   : {hasil['queued']}")
    for f in hasil["failures"]:
        print(f"  - {f['code']}: {f['reason']}")
    return 0 if hasil["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

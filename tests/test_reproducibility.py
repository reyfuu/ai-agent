"""Invariant 2: analisis lama kebal terhadap perubahan data hari ini."""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from datetime import date, timedelta

from agent import db
from agent.analysis import build_snapshot
from agent.graph import analyze_ticker


def make_bars(n, seed=1, start=10000, from_day=date(2025, 1, 1)):
    rnd = random.Random(seed)
    out, px = [], start
    for i in range(n):
        px = max(100, int(px * (1 + rnd.uniform(-0.03, 0.03))))
        out.append({
            "trade_date": (from_day + timedelta(days=i)).isoformat(),
            "open": px, "high": int(px * 1.02), "low": int(px * 0.98),
            "close": px, "volume": rnd.randint(1_000_000, 9_000_000),
        })
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)
        self.conn.execute("INSERT INTO tickers (code, name) VALUES ('BBCA','Bank Central Asia')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.path):
            os.remove(self.path)


class TestReproduksi(Base):
    def test_skor_direproduksi_identik_dari_snapshot(self):
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")
        hasil = analyze_ticker(self.conn, "BBCA")
        snap = db.get_analysis(self.conn, hasil["id"])["data_snapshot"]
        ulang = build_snapshot("BBCA", snap["trade_date"], db.get_bars(self.conn, "BBCA"),
                               snap["fundamentals"], snap["weights"])
        self.assertEqual(snap["scores"], ulang["scores"])
        self.assertEqual(snap["indicators"], ulang["indicators"])
        self.assertEqual(snap["label"], ulang["label"])

    def test_harga_baru_tidak_mengubah_analisis_lama(self):
        """Inti invariant: laporan kemarin tidak boleh berubah karena data hari ini."""
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")
        lama = analyze_ticker(self.conn, "BBCA")
        skor_lama = dict(lama["scores"])

        db.insert_prices(self.conn, "BBCA",
                         make_bars(30, seed=99, start=99000,
                                   from_day=date(2026, 6, 1)), "uji")

        tersimpan = db.get_analysis(self.conn, lama["id"])
        self.assertEqual(tersimpan["data_snapshot"]["scores"], skor_lama)
        self.assertEqual(tersimpan["score_total"], skor_lama["total"])

    def test_bobot_baru_tidak_mengubah_analisis_lama(self):
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")
        self.conn.execute(
            "INSERT INTO fundamentals (code,period,per,pbv,roe,der,net_margin,source,fetched_at)"
            " VALUES ('BBCA','2026Q2',21.4,4.1,19.2,0.32,31.5,'uji','2026-08-26T00:00:00Z')")
        self.conn.commit()
        lama = analyze_ticker(self.conn, "BBCA")
        skor_lama = dict(lama["scores"])

        db.set_weights(self.conn, 20, 80)
        baru = analyze_ticker(self.conn, "BBCA")

        self.assertEqual(db.get_analysis(self.conn, lama["id"])["data_snapshot"]["scores"], skor_lama)
        self.assertEqual(baru["data_snapshot"]["weights"], {"tech": 20, "funda": 80})

    def test_snapshot_memuat_setiap_angka_yang_ditampilkan(self):
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")
        hasil = analyze_ticker(self.conn, "BBCA")
        snap = hasil["data_snapshot"]
        for kunci in ("close", "prev_close", "window_days", "indicators",
                      "weights", "scores", "engine_version"):
            self.assertIn(kunci, snap)
        self.assertEqual(hasil["scores"], snap["scores"])
        self.assertEqual(hasil["label"], snap["label"])


class TestAppendOnly(Base):
    def test_analisis_berulang_membuat_baris_baru(self):
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")
        a = analyze_ticker(self.conn, "BBCA")
        b = analyze_ticker(self.conn, "BBCA")
        self.assertNotEqual(a["id"], b["id"])
        n = self.conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        self.assertEqual(n, 2)

    def test_harga_duplikat_diabaikan_bukan_ditimpa(self):
        bars = make_bars(70)
        self.assertEqual(db.insert_prices(self.conn, "BBCA", bars, "uji"), 70)
        self.assertEqual(db.insert_prices(self.conn, "BBCA", bars, "uji"), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0], 70)

    def test_harga_wajib_integer(self):
        with self.assertRaises(TypeError):
            db.insert_prices(self.conn, "BBCA", [{
                "trade_date": "2026-08-26", "open": 100.5, "high": 110,
                "low": 95, "close": 105, "volume": 1000}], "uji")

    def test_kolom_uang_bertipe_integer(self):
        tipe = {r[1]: r[2] for r in self.conn.execute("PRAGMA table_info(prices)")}
        for kolom in ("open", "high", "low", "close", "volume"):
            self.assertEqual(tipe[kolom], "INTEGER")


if __name__ == "__main__":
    unittest.main()

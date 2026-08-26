"""Invariant 3: penjagaan data, anti-halusinasi, dan otorisasi."""

from __future__ import annotations

import os
import tempfile
import unittest

from agent import db
from agent.analysis import build_snapshot, numbers_in_snapshot, score_technical
from agent.graph import analyze_ticker
from agent.llm import template_narrative, validate_narrative
from tests.test_reproducibility import make_bars

SNAP = {
    "code": "BBCA", "trade_date": "2026-08-26", "status": "ok",
    "close": 10250, "prev_close": 10150, "change_pct": 0.99, "window_days": 250,
    "indicators": {"ma50": 9980.2, "rsi14": 58.42, "macd_hist": 29.2, "vol_ratio": 1.34},
    "fundamentals": None,
    "scores": {"tech": 68, "funda": 74, "total": 70},
    "label": "kuat", "confidence": "sedang", "flags": [],
    "engine_version": "1.0.0",
}


class TestDataTidakCukup(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)
        self.conn.execute("INSERT INTO tickers (code, name) VALUES ('KECIL','Emiten Baru')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def test_kurang_dari_60_hari_ditolak(self):
        db.insert_prices(self.conn, "KECIL", make_bars(41, start=500), "uji")
        hasil = analyze_ticker(self.conn, "KECIL")
        self.assertEqual(hasil["status"], "insufficient_data")
        self.assertIsNone(hasil["scores"])
        self.assertEqual(hasil["detail"], {"required_days": 60, "available_days": 41})

    def test_tanpa_data_sama_sekali(self):
        hasil = analyze_ticker(self.conn, "KECIL")
        self.assertEqual(hasil["status"], "insufficient_data")
        self.assertIsNone(hasil["scores"])

    def test_tepat_di_ambang_batas_diterima(self):
        db.insert_prices(self.conn, "KECIL", make_bars(60, start=500), "uji")
        self.assertEqual(analyze_ticker(self.conn, "KECIL")["status"], "ok")

    def test_disclaimer_selalu_ada(self):
        db.insert_prices(self.conn, "KECIL", make_bars(41, start=500), "uji")
        hasil = analyze_ticker(self.conn, "KECIL")
        self.assertIn("bukan nasihat investasi", hasil["disclaimer"])
        self.assertIn("2026", hasil["disclaimer"])


class TestAntiHalusinasi(unittest.TestCase):
    def test_angka_valid_diterima(self):
        for teks in ("Penutupan 10250 di atas MA50 9980,2 dengan RSI 58,42.",
                     "RSI tercatat 58,4 dan MA50 di 9980,2.",
                     "Penutupan di 10.250 rupiah.",
                     "RSI 14 hari dan MA 50 hari dipakai, penutupan 10250."):
            with self.subTest(teks=teks):
                self.assertTrue(validate_narrative(teks, SNAP)[0])

    def test_angka_karangan_ditolak(self):
        for teks in ("Harga penutupan 11750 menembus resistance.",
                     "Target harga berikutnya 12500 dalam sebulan.",
                     "PER emiten ini 33,7 kali laba."):
            with self.subTest(teks=teks):
                ok, masalah = validate_narrative(teks, SNAP)
                self.assertFalse(ok)
                self.assertTrue(any("tidak ada di snapshot" in m for m in masalah))

    def test_instruksi_beli_jual_ditolak(self):
        for teks in ("Beli sekarang selagi harga 10250.",
                     "Rekomendasi beli dengan penutupan 10250.",
                     "Jual saham ini segera."):
            with self.subTest(teks=teks):
                ok, masalah = validate_narrative(teks, SNAP)
                self.assertFalse(ok)
                self.assertTrue(any("instruksi" in m for m in masalah))

    def test_narasi_template_selalu_lolos_validasi_sendiri(self):
        self.assertTrue(validate_narrative(template_narrative(SNAP), SNAP)[0])

    def test_numbers_in_snapshot_menelusuri_bersarang(self):
        angka = numbers_in_snapshot(SNAP)
        for nilai in (10250.0, 9980.2, 58.42, 70.0):
            self.assertIn(nilai, angka)


class TestValidasiBobot(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def test_bobot_tidak_100_ditolak(self):
        for tech, funda in ((70, 40), (0, 0), (50, 49)):
            with self.subTest(t=tech, f=funda), self.assertRaises(ValueError):
                db.set_weights(self.conn, tech, funda)

    def test_bobot_tidak_dinormalisasi_diam_diam(self):
        try:
            db.set_weights(self.conn, 70, 40)
        except ValueError:
            pass
        s = db.get_settings(self.conn)
        self.assertEqual((s["weight_tech"], s["weight_funda"]), (60, 40))

    def test_build_snapshot_menolak_bobot_salah(self):
        with self.assertRaises(ValueError):
            build_snapshot("X", "2026-08-26", make_bars(70), None, {"tech": 70, "funda": 40})

    def test_bobot_valid_diterima(self):
        db.set_weights(self.conn, 30, 70)
        s = db.get_settings(self.conn)
        self.assertEqual((s["weight_tech"], s["weight_funda"]), (30, 70))


class TestKeamanan(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def test_password_di_hash_bukan_plaintext(self):
        h = db.hash_password("rahasia123")
        self.assertNotIn("rahasia123", h)
        self.assertIn("$", h)
        self.assertTrue(db.verify_password("rahasia123", h))
        self.assertFalse(db.verify_password("salah", h))

    def test_salt_berbeda_tiap_user(self):
        self.assertNotEqual(db.hash_password("sama"), db.hash_password("sama"))

    def test_kunci_api_diredaksi_dari_jejak(self):
        db.log_agent_run(self.conn, None, "crewai", "narrate", "failed",
                         error="gagal: Authorization: Bearer sk-abc123xyz")
        tersimpan = self.conn.execute("SELECT error FROM agent_runs").fetchone()[0]
        self.assertNotIn("sk-abc123xyz", tersimpan)
        self.assertIn("redacted", tersimpan)

    def test_verify_password_menolak_format_rusak(self):
        self.assertFalse(db.verify_password("apa saja", "tanpa-pemisah"))


class TestSkorMasukAkal(unittest.TestCase):
    def test_skor_selalu_0_sampai_100(self):
        for seed in range(20):
            snap = build_snapshot("X", "2026-08-26", make_bars(250, seed=seed))
            self.assertTrue(0 <= snap["scores"]["tech"] <= 100)
            self.assertTrue(0 <= snap["scores"]["total"] <= 100)

    def test_fundamental_absen_ditandai(self):
        snap = build_snapshot("X", "2026-08-26", make_bars(250), None)
        self.assertIn("funda_missing", snap["flags"])
        self.assertIsNone(snap["scores"]["funda"])
        self.assertEqual(snap["scores"]["total"], snap["scores"]["tech"])

    def test_tanpa_sinyal_menimbulkan_error_bukan_skor_palsu(self):
        with self.assertRaises(ValueError):
            score_technical({}, 0)


if __name__ == "__main__":
    unittest.main()

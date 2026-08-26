"""Uji unit fungsi yang sebelumnya hanya tercakup secara tidak langsung.

Ditulis setelah audit cakupan menemukan bahwa trading_date(), run_batch(),
node-node graf, dan skoring fundamental tidak pernah diuji langsung.
Fungsi yang menentukan tanggal bursa dan batas hari adalah tempat bug
paling mahal bersembunyi.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from agent import db, seed
from agent.analysis import confidence_for, ema_series, label_for, score_fundamental
from agent.batch import run_batch
from agent.graph import (
    build_graph,
    node_compute,
    node_critique,
    node_narrate,
    node_persist,
    node_validate,
)
from agent.llm import disclaimer_for
from tests.test_reproducibility import make_bars

WIB = timezone(timedelta(hours=7))


class TestTanggalBursa(unittest.TestCase):
    """trading_date() menentukan batas hari semua laporan. Salah di sini
    berarti seluruh laporan bergeser satu hari tanpa ada yang sadar."""

    def pada(self, y, m, d, jam_wib):
        return datetime(y, m, d, jam_wib, 0, tzinfo=WIB).astimezone(timezone.utc)

    def test_sebelum_jam_tutup_memakai_hari_sebelumnya(self):
        # Rabu 2026-08-26 pukul 10:00 WIB, bursa belum tutup
        self.assertEqual(db.trading_date(self.pada(2026, 8, 26, 10)), "2026-08-25")

    def test_setelah_jam_tutup_memakai_hari_ini(self):
        # Rabu 2026-08-26 pukul 17:00 WIB, bursa sudah tutup
        self.assertEqual(db.trading_date(self.pada(2026, 8, 26, 17)), "2026-08-26")

    def test_tepat_jam_tutup_memakai_hari_ini(self):
        self.assertEqual(db.trading_date(self.pada(2026, 8, 26, 16)), "2026-08-26")

    def test_akhir_pekan_mundur_ke_jumat(self):
        # Minggu 2026-08-30 sore -> Jumat 2026-08-28
        self.assertEqual(db.trading_date(self.pada(2026, 8, 30, 17)), "2026-08-28")
        # Sabtu 2026-08-29 sore -> Jumat 2026-08-28
        self.assertEqual(db.trading_date(self.pada(2026, 8, 29, 17)), "2026-08-28")

    def test_senin_pagi_mundur_ke_jumat(self):
        # Senin 2026-08-31 pukul 09:00 WIB -> mundur ke Minggu, lalu Jumat 28
        self.assertEqual(db.trading_date(self.pada(2026, 8, 31, 9)), "2026-08-28")

    def test_beda_zona_waktu_tidak_menggeser_hasil(self):
        """Jam UTC yang sama harus memberi tanggal bursa yang sama."""
        saat = self.pada(2026, 8, 26, 17)
        self.assertEqual(
            db.trading_date(saat),
            db.trading_date(saat.astimezone(timezone(timedelta(hours=-5)))))

    def test_now_utc_berformat_zulu(self):
        t = db.now_utc()
        self.assertTrue(t.endswith("Z"))
        self.assertEqual(len(t), 20)


class TestSkoringFundamental(unittest.TestCase):
    def test_tanpa_data_mengembalikan_none(self):
        self.assertIsNone(score_fundamental(None))
        self.assertIsNone(score_fundamental({}))

    def test_skor_di_rentang_0_100(self):
        for f in ({"per": 5, "pbv": 0.5, "roe": 40, "der": 0, "net_margin": 50},
                  {"per": 200, "pbv": 30, "roe": 0, "der": 10, "net_margin": 0},
                  {"per": 21.4, "pbv": 4.1, "roe": 19.2, "der": 0.32, "net_margin": 31.5}):
            with self.subTest(f=f):
                self.assertTrue(0 <= score_fundamental(f) <= 100)

    def test_emiten_sehat_lebih_tinggi_dari_yang_lemah(self):
        sehat = score_fundamental({"per": 8, "pbv": 1.0, "roe": 25, "der": 0.2, "net_margin": 30})
        lemah = score_fundamental({"per": 90, "pbv": 12, "roe": 1, "der": 4.0, "net_margin": 1})
        self.assertGreater(sehat, lemah)

    def test_sebagian_field_tetap_menghasilkan_skor(self):
        self.assertIsNotNone(score_fundamental({"roe": 20}))


class TestLabelDanKeyakinan(unittest.TestCase):
    def test_label_naik_monoton(self):
        urut = [label_for(s) for s in (0, 25, 45, 65, 85)]
        self.assertEqual(urut, ["sangat lemah", "lemah", "netral", "kuat", "sangat kuat"])

    def test_label_batas_ekstrem(self):
        self.assertEqual(label_for(0), "sangat lemah")
        self.assertEqual(label_for(100), "sangat kuat")

    def test_keyakinan_naik_seiring_kelengkapan_data(self):
        rendah = confidence_for(60, None, False)
        sedang = confidence_for(130, 1.0, False)
        tinggi = confidence_for(250, 1.2, True)
        self.assertEqual((rendah, tinggi), ("rendah", "tinggi"))
        self.assertIn(sedang, ("sedang", "tinggi"))

    def test_data_pendek_tidak_pernah_keyakinan_tinggi(self):
        self.assertNotEqual(confidence_for(60, None, False), "tinggi")

    def test_disclaimer_memuat_tanggal_data(self):
        d = disclaimer_for({"trade_date": "2026-08-26"})
        self.assertIn("2026-08-26", d)
        self.assertIn("bukan nasihat investasi", d)


class TestEmaSeries(unittest.TestCase):
    def test_panjang_seri_benar(self):
        self.assertEqual(len(ema_series(list(range(1, 11)), 3)), 8)  # 10 - 3 + 1

    def test_seed_adalah_sma_periode_pertama(self):
        self.assertEqual(ema_series([1.0, 2.0, 3.0, 4.0], 3)[0], 2.0)

    def test_data_kurang_mengembalikan_none(self):
        self.assertIsNone(ema_series([1.0, 2.0], 5))


class TestNodeGraf(unittest.TestCase):
    """Setiap node diuji terpisah supaya kegagalan bisa dilokalisasi."""

    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)
        self.conn.execute("INSERT INTO tickers (code, name) VALUES ('BBCA','B')")
        self.conn.commit()
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")
        self.state = {
            "code": "BBCA", "trade_date": "2026-08-26",
            "bars": db.get_bars(self.conn, "BBCA"), "fundamentals": None,
            "weights": {"tech": 60, "funda": 40}, "min_window": 60,
        }

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def test_node_validate_mencatat_jejak(self):
        s = node_validate(dict(self.state))
        self.assertEqual(s["runs"][0]["node"], "validate")
        self.assertEqual(s["runs"][0]["status"], "ok")

    def test_node_validate_menandai_data_kurang(self):
        st = dict(self.state)
        st["bars"] = st["bars"][:10]
        s = node_validate(st)
        self.assertEqual(s["runs"][0]["status"], "skipped")

    def test_node_compute_menghasilkan_snapshot(self):
        s = node_compute(node_validate(dict(self.state)))
        self.assertEqual(s["snapshot"]["status"], "ok")
        self.assertIn("indicators", s["snapshot"])

    def test_node_narrate_melewati_status_bukan_ok(self):
        st = dict(self.state)
        st["bars"] = st["bars"][:10]
        s = node_narrate(node_compute(node_validate(st)))
        self.assertEqual(s["narrative_status"], "unavailable")

    def test_node_critique_memberi_template_saat_tanpa_narasi(self):
        s = node_critique(node_narrate(node_compute(node_validate(dict(self.state)))))
        self.assertEqual(s["narrative_status"], "fallback")
        self.assertIsNotNone(s["narrative"])

    def test_node_persist_tanpa_koneksi_tidak_meledak(self):
        s = node_persist(node_compute(node_validate(dict(self.state))), None)
        self.assertIsNone(s.get("analysis_id"))

    def test_node_persist_menyimpan_dan_mencatat_runs(self):
        s = node_critique(node_narrate(node_compute(node_validate(dict(self.state)))))
        s = node_persist(s, self.conn)
        self.assertIsNotNone(s["analysis_id"])
        n = self.conn.execute("SELECT COUNT(*) FROM agent_runs WHERE analysis_id=?",
                              (s["analysis_id"],)).fetchone()[0]
        self.assertGreaterEqual(n, 5)

    def test_build_graph_mengembalikan_none_atau_graf(self):
        g = build_graph(None)
        if g is not None:  # LangGraph terpasang
            nodes = {n for n in g.get_graph().nodes if not n.startswith("__")}
            self.assertEqual(nodes, {"validate", "compute", "narrate", "critique", "persist"})


class TestBatch(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)
        seed.seed(self.conn, hari=80)

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def test_batch_meringkas_per_status(self):
        h = run_batch(self.conn, ["BBCA", "TLKM", "NEWX"])
        self.assertEqual(h["total"], 3)
        self.assertEqual(h["ok"], 2)
        self.assertEqual(h["insufficient_data"], 1)  # NEWX hanya 41 hari
        self.assertEqual(h["failed"], 0)

    def test_satu_emiten_gagal_tidak_menggagalkan_batch(self):
        import agent.batch as b
        asli = b.analyze_ticker
        panggil = {"n": 0}

        def kadang_gagal(conn, code, trade_date=None):
            panggil["n"] += 1
            if code == "TLKM":
                raise RuntimeError("penyedia mati")
            return asli(conn, code, trade_date)

        b.analyze_ticker = kadang_gagal
        try:
            h = run_batch(self.conn, ["BBCA", "TLKM", "ASII"])
            self.assertEqual(h["failed"], 1)
            self.assertEqual(h["failures"][0]["code"], "TLKM")
            self.assertEqual(panggil["n"], 3)  # tetap lanjut ke ASII
        finally:
            b.analyze_ticker = asli

    def test_batch_default_memakai_watchlist(self):
        h = run_batch(self.conn)
        self.assertGreater(h["total"], 0)

    def test_batch_menulis_audit(self):
        run_batch(self.conn, ["BBCA"])
        n = self.conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='batch'").fetchone()[0]
        self.assertGreaterEqual(n, 1)

    def test_kuota_nol_menandai_narasi_antre(self):
        h = run_batch(self.conn, ["BBCA", "TLKM"], quota=0)
        self.assertEqual(h["queued"], 2)


class TestSesiServer(unittest.TestCase):
    def test_tanda_tangan_sesi_bulak_balik(self):
        import server as srv
        srv.SESSION_SECRET = "rahasia-uji"
        sid = srv.sign_session(7, "analyst")
        u = srv.read_session(f"sid={sid}")
        self.assertEqual((u["id"], u["role"]), (7, "analyst"))

    def test_tanda_tangan_palsu_ditolak(self):
        import server as srv
        srv.SESSION_SECRET = "rahasia-uji"
        self.assertIsNone(srv.read_session("sid=7:admin:9999999999:palsu"))

    def test_kunci_berbeda_menolak_sesi(self):
        import server as srv
        srv.SESSION_SECRET = "kunci-a"
        sid = srv.sign_session(1, "admin")
        srv.SESSION_SECRET = "kunci-b"
        self.assertIsNone(srv.read_session(f"sid={sid}"))

    def test_sesi_kedaluwarsa_ditolak(self):
        import time
        import server as srv
        srv.SESSION_SECRET = "rahasia-uji"
        payload = f"1:admin:{int(time.time()) - srv.SESSION_TTL - 10}"
        import hmac
        from hashlib import sha256
        mac = hmac.new(srv.SESSION_SECRET.encode(), payload.encode(), sha256).hexdigest()[:32]
        self.assertIsNone(srv.read_session(f"sid={payload}:{mac}"))

    def test_cookie_kosong_ditolak(self):
        import server as srv
        self.assertIsNone(srv.read_session(None))
        self.assertIsNone(srv.read_session("lain=1"))


class TestMigrasi(unittest.TestCase):
    def test_migrasi_idempoten(self):
        path = tempfile.mktemp(suffix=".db")
        try:
            c = db.init(path)
            v1 = db.migrate(c)
            v2 = db.migrate(c)  # dijalankan ulang tidak boleh merusak
            self.assertEqual(v1, v2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0], 1)
            c.close()
        finally:
            os.path.exists(path) and os.remove(path)

    def test_settings_default_terisi(self):
        path = tempfile.mktemp(suffix=".db")
        try:
            c = db.init(path)
            s = db.get_settings(c)
            self.assertEqual(s["weight_tech"] + s["weight_funda"], 100)
            self.assertEqual(s["min_window_days"], 60)
            c.close()
        finally:
            os.path.exists(path) and os.remove(path)

    def test_latest_fundamentals_ambil_periode_terbaru(self):
        path = tempfile.mktemp(suffix=".db")
        try:
            c = db.init(path)
            c.execute("INSERT INTO tickers (code,name) VALUES ('BBCA','B')")
            for periode, per in (("2026Q1", 10.0), ("2026Q2", 20.0)):
                c.execute("INSERT INTO fundamentals (code,period,per,source,fetched_at)"
                          " VALUES ('BBCA',?,?,'uji',?)", (periode, per, db.now_utc()))
            c.commit()
            self.assertEqual(db.latest_fundamentals(c, "BBCA")["per"], 20.0)
            c.close()
        finally:
            os.path.exists(path) and os.remove(path)


if __name__ == "__main__":
    unittest.main()

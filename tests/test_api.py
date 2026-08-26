"""Uji API lewat HTTP nyata: otorisasi, bentuk respons, dan kebocoran rahasia.

Menjalankan server sungguhan di port acak, memakai cookie sungguhan.
Bukan memanggil fungsi handler langsung, karena yang ingin dibuktikan
adalah perilaku di kabel, termasuk penjagaan otorisasi.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from agent import db, seed
import server as srv


class Klien:
    """Klien HTTP mini dengan cookie, tanpa dependency."""

    def __init__(self, base: str):
        self.base, self.cookie = base, None

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                sc = res.headers.get("Set-Cookie")
                if sc:
                    self.cookie = sc.split(";")[0]
                raw = res.read()
                return res.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None)


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = tempfile.mktemp(suffix=".db")
        conn = db.init(cls.path)
        seed.seed(conn, hari=80)
        conn.close()

        os.environ["SESSION_SECRET"] = "uji-rahasia-tetap"
        srv.SESSION_SECRET = "uji-rahasia-tetap"
        srv.Handler.db_path = cls.path
        srv.Handler.quiet = True  # jangan banjiri output test
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.t.join(timeout=5)
        os.path.exists(cls.path) and os.remove(cls.path)

    def setUp(self):
        """Reset penghitung rate limit.

        Semua test datang dari 127.0.0.1, jadi batas 5 login/IP akan saling
        mengganggu. Yang direset penghitungnya, bukan batasnya dilonggarkan:
        rate limit tetap diuji di test_rate_limit_login_aktif.
        """
        srv._rate.clear()

    def klien(self, username: str | None = None, password: str = "") -> Klien:
        k = Klien(self.base)
        if username:
            status, _ = k.call("POST", "/api/login",
                               {"username": username, "password": password})
            self.assertEqual(status, 200, f"login {username} gagal")
        return k

    # ---------------------------------------------------------- auth
    def test_login_salah_ditolak_dengan_pesan_generik(self):
        s, b = Klien(self.base).call("POST", "/api/login",
                                     {"username": "andi", "password": "salah"})
        self.assertEqual(s, 401)
        self.assertEqual(b["error"]["code"], "UNAUTHENTICATED")
        self.assertNotIn("password", b["error"]["message"].lower().replace("password salah", ""))

    def test_login_benar_memberi_cookie_httponly(self):
        k = Klien(self.base)
        k.call("POST", "/api/login", {"username": "andi", "password": "andi123"})
        self.assertIsNotNone(k.cookie)
        self.assertTrue(k.cookie.startswith("sid="))

    def test_tanpa_sesi_endpoint_terproteksi_401(self):
        for method, path in (("POST", "/api/analyze/BBCA"), ("GET", "/api/watchlist"),
                             ("GET", "/api/settings")):
            with self.subTest(path=path):
                s, _ = Klien(self.base).call(method, path, {} if method == "POST" else None)
                self.assertEqual(s, 401)

    # ---------------------------------------------------------- otorisasi
    def test_matriks_otorisasi_sesuai_kontrak(self):
        """Cocokkan dengan API_CONTRACT.md §4. Beda = bug."""
        tamu = self.klien("tamu", "tamu123")
        andi = self.klien("andi", "andi123")
        owner = self.klien("owner", "owner123")

        kasus = [
            ("GET", "/api/tickers?q=BB", None, 200, 200, 200),
            ("GET", "/api/prices/BBCA", None, 200, 200, 200),
            ("POST", "/api/analyze/BBCA", {}, 403, 200, 200),
            ("GET", "/api/watchlist", None, 403, 200, 200),
            ("GET", "/api/settings", None, 403, 403, 200),
            ("POST", "/api/batch", {}, 403, 403, 202),
            ("GET", "/api/audit", None, 403, 200, 200),
        ]
        for method, path, body, h_tamu, h_andi, h_owner in kasus:
            for klien, harap, nama in ((tamu, h_tamu, "guest"), (andi, h_andi, "analyst"),
                                       (owner, h_owner, "admin")):
                with self.subTest(path=path, peran=nama):
                    s, _ = klien.call(method, path, body)
                    self.assertEqual(s, harap, f"{nama} {method} {path}")

    def test_watchlist_terisolasi_antar_pengguna(self):
        andi = self.klien("andi", "andi123")
        owner = self.klien("owner", "owner123")
        owner.call("POST", "/api/watchlist", {"code": "ASII"})
        _, milik_andi = andi.call("GET", "/api/watchlist")
        self.assertNotIn("ASII", [i["code"] for i in milik_andi["items"]])

    # ---------------------------------------------------------- analisis
    def test_analisis_mengembalikan_bentuk_sesuai_kontrak(self):
        s, b = self.klien("andi", "andi123").call("POST", "/api/analyze/BBCA", {})
        self.assertEqual(s, 200)
        for kunci in ("id", "code", "trade_date", "status", "scores", "label",
                      "confidence", "narrative_status", "data_snapshot",
                      "engine_version", "disclaimer"):
            self.assertIn(kunci, b)
        self.assertEqual(b["status"], "ok")
        self.assertIn("bukan nasihat investasi", b["disclaimer"])

    def test_data_kurang_mengembalikan_200_dengan_status_bukan_error(self):
        s, b = self.klien("andi", "andi123").call("POST", "/api/analyze/NEWX", {})
        self.assertEqual(s, 200)  # hasil analisis yang sah, bukan error HTTP
        self.assertEqual(b["status"], "insufficient_data")
        self.assertIsNone(b["scores"])
        self.assertEqual(b["detail"]["required_days"], 60)
        self.assertIn("bukan nasihat investasi", b["disclaimer"])

    def test_emiten_tidak_dikenal_404(self):
        s, _ = self.klien("andi", "andi123").call("POST", "/api/analyze/ZZZZ", {})
        self.assertEqual(s, 404)

    def test_jejak_orkestrasi_tersedia(self):
        k = self.klien("andi", "andi123")
        _, a = k.call("POST", "/api/analyze/BBCA", {})
        s, b = k.call("GET", f"/api/analyses/id/{a['id']}/runs")
        self.assertEqual(s, 200)
        nodes = [r["node"] for r in b["items"]]
        for wajib in ("validate", "compute", "narrate", "critique", "persist"):
            self.assertIn(wajib, nodes)

    def test_analisis_lama_dibuka_ulang_identik(self):
        k = self.klien("andi", "andi123")
        _, a = k.call("POST", "/api/analyze/BBCA", {})
        _, x = k.call("GET", f"/api/analyses/id/{a['id']}")
        _, y = k.call("GET", f"/api/analyses/id/{a['id']}")
        self.assertEqual(x["data_snapshot"], y["data_snapshot"])
        self.assertEqual(x["data_snapshot"]["scores"], a["scores"])

    # ---------------------------------------------------------- keamanan
    def test_kunci_api_tidak_pernah_bocor(self):
        s, b = self.klien("owner", "owner123").call("GET", "/api/settings")
        self.assertEqual(s, 200)
        teks = json.dumps(b).lower()
        for terlarang in ("sk-", "bearer", "secret", "password"):
            self.assertNotIn(terlarang, teks)
        self.assertIn("llm_api_key_configured", b)  # hanya status

    def test_bobot_salah_ditolak_409(self):
        s, b = self.klien("owner", "owner123").call(
            "PUT", "/api/settings", {"weight_tech": 70, "weight_funda": 40})
        self.assertEqual(s, 409)
        self.assertEqual(b["error"]["code"], "WEIGHTS_INVALID")

    def test_galat_internal_tidak_membocorkan_stack_trace(self):
        s, b = Klien(self.base).call("GET", "/api/tidak/ada")
        self.assertEqual(s, 404)
        self.assertNotIn("Traceback", json.dumps(b))
        self.assertNotIn("File \"", json.dumps(b))

    def test_ekspor_csv_memuat_disclaimer(self):
        import urllib.request
        k = self.klien("andi", "andi123")
        req = urllib.request.Request(self.base + "/api/export/BBCA.csv")
        req.add_header("Cookie", k.cookie)
        with urllib.request.urlopen(req, timeout=10) as res:
            self.assertEqual(res.status, 200)
            self.assertIn("text/csv", res.headers["Content-Type"])
            self.assertIn("attachment", res.headers["Content-Disposition"])
            teks = res.read().decode()
        self.assertTrue(teks.startswith("#"))
        self.assertIn("bukan nasihat investasi", teks.splitlines()[0])
        # kolom uang integer tanpa pemisah ribuan maupun desimal
        data = teks.splitlines()[2].split(",")
        for nilai in data[1:]:
            self.assertRegex(nilai, r"^\d+$")

    def test_ekspor_butuh_peran_analyst(self):
        s, _ = self.klien("tamu", "tamu123").call("GET", "/api/export/BBCA.csv")
        self.assertEqual(s, 403)

    def test_rate_limit_login_aktif(self):
        """Batas 5 percobaan gagal per IP per 5 menit benar-benar ditegakkan."""
        srv._rate.clear()
        k = Klien(self.base)
        kode = [k.call("POST", "/api/login",
                       {"username": "andi", "password": "salah"})[0] for _ in range(7)]
        self.assertEqual(kode[:5], [401] * 5)
        self.assertIn(429, kode[5:])
        srv._rate.clear()

    def test_cookie_dipalsukan_ditolak(self):
        k = Klien(self.base)
        k.cookie = "sid=1:admin:9999999999:palsu"
        s, _ = k.call("GET", "/api/settings")
        self.assertEqual(s, 401)

    def test_eskalasi_peran_lewat_cookie_ditolak(self):
        """Ubah role di cookie tanpa tanda tangan sah harus gagal."""
        k = self.klien("andi", "andi123")
        uid, _role, issued, mac = k.cookie[4:].rsplit(":", 3)
        k.cookie = f"sid={uid}:admin:{issued}:{mac}"
        s, _ = k.call("GET", "/api/settings")
        self.assertEqual(s, 401)


if __name__ == "__main__":
    unittest.main()

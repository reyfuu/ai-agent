"""Penegakan batas arsitektur ADR-001.

Tanpa test ini, framework perlahan merembes ke inti perhitungan dan produk
terkunci pada mereka. Ini menjaga janji: angka dari kode, narasi dari model.
"""

from __future__ import annotations

import ast
import os
import pathlib
import tempfile
import unittest

from agent import db
from agent.graph import analyze_ticker, run_analysis, run_analysis_fallback
from tests.test_reproducibility import make_bars

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAMEWORKS = ("langchain", "langgraph", "crewai")


def imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


class TestKemurnianInti(unittest.TestCase):
    """agent/analysis.py adalah sumber semua angka; harus bebas framework."""

    def test_analysis_tidak_impor_framework(self):
        found = imports_of(ROOT / "agent" / "analysis.py")
        for fw in FRAMEWORKS:
            self.assertNotIn(fw, found, f"agent/analysis.py mengimpor {fw}")

    def test_analysis_tidak_impor_io(self):
        """Murni berarti tanpa database, jaringan, maupun jam."""
        found = imports_of(ROOT / "agent" / "analysis.py")
        for terlarang in ("sqlite3", "urllib", "requests", "datetime", "time", "random"):
            self.assertNotIn(terlarang, found, f"analysis.py mengimpor {terlarang}")

    def test_db_tidak_impor_framework(self):
        found = imports_of(ROOT / "agent" / "db.py")
        for fw in FRAMEWORKS:
            self.assertNotIn(fw, found, f"agent/db.py mengimpor {fw}")

    def test_framework_diimpor_lazy(self):
        """Impor framework harus di dalam fungsi, bukan di level modul."""
        for nama in ("llm.py", "graph.py"):
            modul = ast.parse((ROOT / "agent" / nama).read_text())
            for node in modul.body:  # hanya level teratas
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    src = getattr(node, "module", "") or ""
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
                    for fw in FRAMEWORKS:
                        self.assertFalse(
                            src.startswith(fw) or any(n.startswith(fw) for n in names),
                            f"agent/{nama} mengimpor {fw} di level modul, harus lazy")


class TestFallbackWajib(unittest.TestCase):
    """Sistem harus tetap berjalan bila framework dicabut."""

    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)
        self.conn.execute("INSERT INTO tickers (code, name) VALUES ('BBCA','Bank Central Asia')")
        self.conn.commit()
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def _state(self):
        s = db.get_settings(self.conn)
        return {
            "code": "BBCA", "trade_date": "2026-08-26",
            "bars": db.get_bars(self.conn, "BBCA"), "fundamentals": None,
            "weights": {"tech": s["weight_tech"], "funda": s["weight_funda"]},
            "min_window": s["min_window_days"],
        }

    def test_fallback_menghasilkan_analisis_lengkap(self):
        out = run_analysis_fallback(self._state(), self.conn)
        self.assertEqual(out["snapshot"]["status"], "ok")
        self.assertIsNotNone(out["snapshot"]["scores"]["total"])
        self.assertIsNotNone(out.get("analysis_id"))

    def test_graf_dan_fallback_menghasilkan_skor_identik(self):
        """Janji inti ADR: framework tidak mengubah satu angka pun."""
        via_graph = run_analysis(self._state(), None, prefer_graph=True)
        via_fallback = run_analysis_fallback(self._state(), None)
        self.assertEqual(via_graph["snapshot"]["scores"], via_fallback["snapshot"]["scores"])
        self.assertEqual(via_graph["snapshot"]["indicators"], via_fallback["snapshot"]["indicators"])
        self.assertEqual(via_graph["snapshot"]["label"], via_fallback["snapshot"]["label"])

    def test_analisis_tetap_jalan_tanpa_kunci_llm(self):
        lama = os.environ.pop("LLM_API_KEY", None)
        try:
            hasil = analyze_ticker(self.conn, "BBCA")
            self.assertEqual(hasil["status"], "ok")
            self.assertIsNotNone(hasil["scores"])
            self.assertIn(hasil["narrative_status"], ("fallback", "unavailable"))
            self.assertIsNotNone(hasil["narrative"])  # template tetap ada
        finally:
            if lama:
                os.environ["LLM_API_KEY"] = lama

    def test_kegagalan_narator_tidak_menghilangkan_skor(self):
        import agent.graph as g
        asli = g.narrate_with_crew
        g.narrate_with_crew = lambda s: (_ for _ in ()).throw(RuntimeError("provider mati"))
        try:
            out = run_analysis_fallback(self._state(), self.conn)
            self.assertEqual(out["snapshot"]["status"], "ok")
            self.assertIsNotNone(out["snapshot"]["scores"]["total"])
            self.assertEqual(out["narrative_status"], "fallback")
        finally:
            g.narrate_with_crew = asli

    def test_narasi_halusinasi_dijatuhkan_ke_template(self):
        import agent.graph as g
        asli = g.narrate_with_crew
        g.narrate_with_crew = lambda s: ("Target harga 99999 tercapai.", {})
        try:
            out = run_analysis_fallback(self._state(), self.conn)
            self.assertEqual(out["narrative_status"], "fallback")
            self.assertNotIn("99999", out["narrative"])
        finally:
            g.narrate_with_crew = asli


class TestJejakOrkestrasi(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = db.init(self.path)
        self.conn.execute("INSERT INTO tickers (code, name) VALUES ('BBCA','B')")
        self.conn.commit()
        db.insert_prices(self.conn, "BBCA", make_bars(250), "uji")

    def tearDown(self):
        self.conn.close()
        os.path.exists(self.path) and os.remove(self.path)

    def test_setiap_node_tercatat(self):
        hasil = analyze_ticker(self.conn, "BBCA")
        nodes = [r[0] for r in self.conn.execute(
            "SELECT node FROM agent_runs WHERE analysis_id=? ORDER BY id", (hasil["id"],))]
        for wajib in ("validate", "compute", "narrate", "critique", "persist"):
            self.assertIn(wajib, nodes)

    def test_jejak_tidak_memuat_isi_prompt(self):
        hasil = analyze_ticker(self.conn, "BBCA")
        for row in self.conn.execute("SELECT input_digest, output_digest FROM agent_runs"):
            for d in row:
                if d:
                    self.assertLessEqual(len(d), 32)  # digest, bukan isi


if __name__ == "__main__":
    unittest.main()

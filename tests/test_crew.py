"""Uji jalur CrewAI yang SEBENARNYA, tanpa jaringan dan tanpa kunci API.

Ditulis setelah tracing menemukan bahwa 43 dari 46 baris narrate_with_crew()
tidak pernah dieksekusi: test lama hanya menyentuh early-return saat kunci
API kosong. Klaim "CrewAI diterapkan" karena itu belum terbukti.

LLM tiruan mewarisi crewai.llms.base_llm.BaseLLM, sehingga Agent, Task,
Crew, dan kickoff() yang dijalankan adalah milik CrewAI sungguhan.
"""

from __future__ import annotations

import logging
import os
import unittest

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

try:
    from crewai.llms.base_llm import BaseLLM
    CREWAI_ADA = True
except ImportError:  # dijalankan di lingkungan tanpa framework
    CREWAI_ADA = False
    BaseLLM = object  # type: ignore

from agent.llm import narrate_with_crew, validate_narrative

SNAP = {
    "code": "BBCA", "trade_date": "2026-08-26", "status": "ok",
    "close": 10250, "prev_close": 10150, "window_days": 250,
    "indicators": {"ma50": 9980.2, "rsi14": 58.42, "macd_hist": 29.2},
    "fundamentals": None,
    "scores": {"tech": 68, "funda": None, "total": 68},
    "label": "kuat", "confidence": "sedang", "flags": ["funda_missing"],
    "engine_version": "1.0.0",
}


class LLMTiruan(BaseLLM):
    """LLM yang mengembalikan teks tetap. Tidak pernah menyentuh jaringan."""

    def __init__(self, balasan: str):
        super().__init__(model="uji-tiruan")
        self._balasan = balasan
        self.panggilan = 0
        self.prompt_terakhir = ""

    def call(self, messages, **kwargs):
        self.panggilan += 1
        self.prompt_terakhir = str(messages)
        return self._balasan

    def supports_function_calling(self):
        return False

    def supports_stop_words(self):
        return False

    def get_context_window_size(self):
        return 8192


@unittest.skipUnless(CREWAI_ADA, "CrewAI tidak terpasang")
class TestJalurCrewAI(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_crew_sungguhan_menghasilkan_narasi(self):
        """Agent, Task, Crew, dan kickoff() CrewAI benar-benar dijalankan."""
        llm = LLMTiruan("Tren BBCA berada di atas MA50 dengan skor 68 dan RSI 58,42.")
        teks, metrik = narrate_with_crew(SNAP, llm=llm)
        self.assertIsNotNone(teks)
        self.assertIn("BBCA", teks)
        self.assertGreaterEqual(llm.panggilan, 2)  # Narrator + Critic
        self.assertIn("tokens_in", metrik)

    def test_snapshot_dikirim_ke_prompt(self):
        """Narator harus menerima angka snapshot, bukan data mentah."""
        llm = LLMTiruan("Skor komposit 68 dengan keyakinan sedang.")
        narrate_with_crew(SNAP, llm=llm)
        self.assertIn("BBCA", llm.prompt_terakhir)
        self.assertIn("58.42", llm.prompt_terakhir.replace(",", "."))

    def test_narasi_crew_tetap_melewati_validasi_deterministik(self):
        """Critic bukan pengganti validasi kode. Angka karangan tetap ditolak."""
        llm = LLMTiruan("Target harga 99999 akan tercapai bulan depan.")
        teks, _ = narrate_with_crew(SNAP, llm=llm)
        self.assertIsNotNone(teks)
        ok, masalah = validate_narrative(teks, SNAP)
        self.assertFalse(ok, "angka 99999 seharusnya ditolak")
        self.assertTrue(any("tidak ada di snapshot" in m for m in masalah))

    def test_narasi_crew_yang_bersih_lolos_validasi(self):
        llm = LLMTiruan("Skor teknikal 68 dengan RSI 58,42 dan MA50 di 9980,2.")
        teks, _ = narrate_with_crew(SNAP, llm=llm)
        self.assertTrue(validate_narrative(teks, SNAP)[0])

    def test_instruksi_beli_dari_crew_ditolak(self):
        llm = LLMTiruan("Beli sekarang karena skor 68 sudah kuat.")
        teks, _ = narrate_with_crew(SNAP, llm=llm)
        ok, masalah = validate_narrative(teks, SNAP)
        self.assertFalse(ok)
        self.assertTrue(any("instruksi" in m for m in masalah))

    def test_kegagalan_llm_merambat_sebagai_exception(self):
        """Pemanggil (node_narrate) yang menangkap, bukan modul ini menelan diam."""
        class LLMMati(LLMTiruan):
            def call(self, messages, **kwargs):
                raise RuntimeError("provider mati")

        with self.assertRaises(Exception):
            narrate_with_crew(SNAP, llm=LLMMati("x"))

    def test_balasan_kosong_dianggap_tidak_tersedia(self):
        teks, _ = narrate_with_crew(SNAP, llm=LLMTiruan("   "))
        self.assertIsNone(teks)


class TestTanpaKunciApi(unittest.TestCase):
    def test_tanpa_kunci_mengembalikan_none(self):
        lama = os.environ.pop("LLM_API_KEY", None)
        try:
            teks, metrik = narrate_with_crew(SNAP)
            self.assertIsNone(teks)
            self.assertEqual(metrik, {"tokens_in": 0, "tokens_out": 0})
        finally:
            if lama:
                os.environ["LLM_API_KEY"] = lama


class TestIntegrasiGrafDenganCrew(unittest.TestCase):
    """Graf harus memakai narasi Crew bila lolos, dan fallback bila tidak."""

    @unittest.skipUnless(CREWAI_ADA, "CrewAI tidak terpasang")
    def test_graf_memakai_narasi_crew_yang_valid(self):
        import agent.graph as g
        from tests.test_reproducibility import make_bars

        llm = LLMTiruan("Skor teknikal terbaca dengan RSI 58,42.")
        asli = g.narrate_with_crew
        g.narrate_with_crew = lambda s: narrate_with_crew(s, llm=llm)
        try:
            state = {
                "code": "BBCA", "trade_date": "2026-08-26",
                "bars": make_bars(250), "fundamentals": None,
                "weights": {"tech": 60, "funda": 40}, "min_window": 60,
            }
            out = g.run_analysis_fallback(state, None)
            self.assertIn(out["narrative_status"], ("ok", "fallback"))
            self.assertIsNotNone(out["narrative"])
            self.assertIsNotNone(out["snapshot"]["scores"]["total"])
        finally:
            g.narrate_with_crew = asli


if __name__ == "__main__":
    unittest.main()

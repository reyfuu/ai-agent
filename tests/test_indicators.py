"""Invariant 1: akurasi indikator terhadap implementasi referensi independen.

Referensi ditulis ulang langsung dari definisi Wilder di file ini, bukan
diimpor dari agent.analysis, supaya kesalahan yang sama tidak lolos dua kali.
"""

from __future__ import annotations

import random
import unittest

from agent.analysis import bollinger, ema, macd, rsi, sma, volume_ratio

TOLERANCE = 0.01


def rsi_ref(p, n=14):
    d = [p[i + 1] - p[i] for i in range(len(p) - 1)]
    g = [max(x, 0.0) for x in d]
    l = [max(-x, 0.0) for x in d]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + l[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def ema_ref(p, n):
    a = 2 / (n + 1)
    out = [sum(p[:n]) / n]
    for v in p[n:]:
        out.append((v - out[-1]) * a + out[-1])
    return out


def macd_ref(p, f=12, s=26, sg=9):
    ef, es = ema_ref(p, f), ema_ref(p, s)
    off = len(ef) - len(es)
    ml = [a - b for a, b in zip(ef[off:], es)]
    return ml[-1], ema_ref(ml, sg)[-1]


def series(n, seed):
    rnd = random.Random(seed)
    p = [100.0]
    for _ in range(n):
        p.append(max(1.0, p[-1] * (1 + rnd.uniform(-0.05, 0.05))))
    return p


class TestIndikatorReferensi(unittest.TestCase):
    def test_rsi_wilder_nilai_kanonik(self):
        """Deret referensi Wilder: RSI(14) pada 15 bar pertama = 70.46."""
        w = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
             45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        self.assertAlmostEqual(rsi(w, 14), 70.4636, delta=0.01)

    def test_rsi_cocok_dengan_referensi(self):
        for seed in range(30):
            p = series(random.Random(seed).randint(60, 400), seed)
            self.assertAlmostEqual(rsi(p, 14), rsi_ref(p, 14), delta=TOLERANCE)

    def test_ema_cocok_dengan_referensi(self):
        for seed in range(30):
            p = series(random.Random(seed).randint(60, 400), seed)
            for period in (12, 26):
                self.assertAlmostEqual(ema(p, period), ema_ref(p, period)[-1], delta=TOLERANCE)

    def test_macd_cocok_dengan_referensi(self):
        for seed in range(30):
            p = series(random.Random(seed).randint(60, 400), seed)
            got, exp = macd(p), macd_ref(p)
            self.assertAlmostEqual(got["macd"], exp[0], delta=TOLERANCE)
            self.assertAlmostEqual(got["signal"], exp[1], delta=TOLERANCE)
            self.assertAlmostEqual(got["hist"], exp[0] - exp[1], delta=TOLERANCE)

    def test_sma_dan_bollinger_nilai_pasti(self):
        self.assertEqual(sma([1, 2, 3, 4, 5], 5), 3.0)
        bb = bollinger([2, 4, 4, 4, 5, 5, 7, 9], 8, 2.0)  # pstdev = 2.0
        self.assertEqual((bb["lower"], bb["mid"], bb["upper"]), (1.0, 5.0, 9.0))

    def test_bollinger_pakai_stdev_populasi(self):
        """Kalau memakai stdev sampel, upper akan ~9.14, bukan 9.0."""
        bb = bollinger([2, 4, 4, 4, 5, 5, 7, 9], 8, 2.0)
        self.assertAlmostEqual(bb["upper"], 9.0, delta=1e-9)


class TestSifatMatematis(unittest.TestCase):
    def test_rsi_selalu_di_rentang_0_100(self):
        for seed in range(50):
            p = series(120, seed)
            self.assertTrue(0.0 <= rsi(p, 14) <= 100.0)

    def test_rsi_ekstrem(self):
        self.assertEqual(rsi([float(x) for x in range(1, 80)], 14), 100.0)
        self.assertAlmostEqual(rsi([float(x) for x in range(80, 0, -1)], 14), 0.0, delta=1e-9)

    def test_bollinger_selalu_berurutan(self):
        for seed in range(50):
            bb = bollinger(series(120, seed))
            self.assertLessEqual(bb["lower"], bb["mid"])
            self.assertLessEqual(bb["mid"], bb["upper"])

    def test_deterministik(self):
        """Input sama harus memberi output identik, dipanggil berkali-kali."""
        p = series(200, 1)
        self.assertEqual([rsi(p, 14) for _ in range(5)], [rsi(p, 14)] * 5)
        self.assertEqual([macd(p)["hist"] for _ in range(5)], [macd(p)["hist"]] * 5)

    def test_data_kurang_mengembalikan_none(self):
        """Lebih baik None daripada angka dari jendela yang tidak cukup."""
        self.assertIsNone(rsi([1.0, 2.0, 3.0], 14))
        self.assertIsNone(sma([1.0], 5))
        self.assertIsNone(macd([1.0, 2.0, 3.0]))
        self.assertIsNone(bollinger([1.0] * 5, 20))
        self.assertIsNone(volume_ratio([1] * 5, 20))

    def test_periode_tidak_valid_ditolak(self):
        for fn in (sma, ema, rsi):
            with self.assertRaises(ValueError):
                fn([1.0, 2.0, 3.0], 0)


if __name__ == "__main__":
    unittest.main()

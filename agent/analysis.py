"""Indikator teknikal, skoring, dan snapshot.

Modul ini MURNI: tidak menyentuh database, jaringan, maupun jam.
Tidak mengimpor langchain/langgraph/crewai (lihat docs/adr/001).
Semua angka produk berasal dari sini.
"""

from __future__ import annotations

import statistics
from typing import Any, Iterable, Sequence

ENGINE_VERSION = "1.0.0"
MIN_WINDOW_DAYS = 60

LABELS = (
    (0, "sangat lemah"),
    (20, "lemah"),
    (40, "netral"),
    (60, "kuat"),
    (80, "sangat kuat"),
)


# ---------------------------------------------------------------- indikator

def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average dari `period` nilai terakhir."""
    if period <= 0:
        raise ValueError("period harus > 0")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values: Sequence[float], period: int) -> list[float] | None:
    """EMA dengan alpha=2/(n+1), diseed SMA n periode pertama."""
    if period <= 0:
        raise ValueError("period harus > 0")
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append((v - out[-1]) * alpha + out[-1])
    return out


def ema(values: Sequence[float], period: int) -> float | None:
    series = ema_series(values, period)
    return series[-1] if series else None


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    """RSI metode Wilder.

    Wilder smoothing, bukan SMA sederhana. SMA memberi hasil yang mirip
    tetapi salah, dan kemiripannya itulah yang membuatnya lolos review.
    """
    if period <= 0:
        raise ValueError("period harus > 0")
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(values, values[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, float] | None:
    """MACD = EMA(fast) - EMA(slow); signal = EMA(signal) dari garis MACD."""
    fast_s = ema_series(values, fast)
    slow_s = ema_series(values, slow)
    if not fast_s or not slow_s:
        return None
    # samakan panjang: EMA cepat mulai lebih awal
    offset = len(fast_s) - len(slow_s)
    macd_line = [f - s for f, s in zip(fast_s[offset:], slow_s)]
    signal_s = ema_series(macd_line, signal)
    if not signal_s:
        return None
    line, sig = macd_line[-1], signal_s[-1]
    return {"macd": line, "signal": sig, "hist": line - sig}


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0):
    """Bollinger Bands dengan stdev POPULASI (pstdev), bukan sampel."""
    if len(values) < period:
        return None
    window = values[-period:]
    mid = sum(window) / period
    sd = statistics.pstdev(window)
    return {"upper": mid + mult * sd, "mid": mid, "lower": mid - mult * sd}


def volume_ratio(volumes: Sequence[int], period: int = 20) -> float | None:
    """Volume terakhir dibanding rata-rata `period` hari."""
    if len(volumes) < period:
        return None
    avg = sum(volumes[-period:]) / period
    if avg == 0:
        return None
    return volumes[-1] / avg


# ---------------------------------------------------------------- skoring

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def score_technical(ind: dict[str, Any], close: int) -> int:
    """Skor teknikal 0-100 dari sinyal yang tersedia.

    Tiap sinyal menyumbang 0-100, lalu dirata-rata dengan bobot setara.
    Sinyal yang datanya tidak ada tidak ikut dihitung (bukan dianggap nol).
    """
    parts: list[float] = []

    ma20, ma50 = ind.get("ma20"), ind.get("ma50")
    if ma20:
        parts.append(_clamp(50 + (close - ma20) / ma20 * 500))
    if ma50:
        parts.append(_clamp(50 + (close - ma50) / ma50 * 250))
    if ma20 and ma50:
        parts.append(75.0 if ma20 > ma50 else 25.0)

    r = ind.get("rsi14")
    if r is not None:
        # 50 netral; menjauh dari 50 menaikkan/menurunkan skor, jenuh dihukum
        parts.append(_clamp(30.0 if r > 70 else 70.0 if r < 30 else 50 + (r - 50) * 1.2)) 

    m = ind.get("macd_hist")
    if m is not None and close:
        parts.append(_clamp(50 + (m / close) * 2000))

    vr = ind.get("vol_ratio")
    if vr is not None:
        parts.append(_clamp(50 + (vr - 1.0) * 25))

    if not parts:
        raise ValueError("tidak ada sinyal teknikal untuk diskor")
    return round(sum(parts) / len(parts))


def score_fundamental(f: dict[str, Any] | None) -> int | None:
    """Skor fundamental 0-100. None bila data tidak ada."""
    if not f:
        return None
    parts: list[float] = []
    if f.get("per"):
        parts.append(_clamp(100 - (f["per"] - 5) * 3))
    if f.get("pbv"):
        parts.append(_clamp(100 - (f["pbv"] - 0.5) * 20))
    if f.get("roe") is not None:
        parts.append(_clamp(f["roe"] * 4))
    if f.get("der") is not None:
        parts.append(_clamp(100 - f["der"] * 40))
    if f.get("net_margin") is not None:
        parts.append(_clamp(f["net_margin"] * 3))
    if not parts:
        return None
    return round(sum(parts) / len(parts))


def label_for(score: int) -> str:
    out = LABELS[0][1]
    for threshold, name in LABELS:
        if score >= threshold:
            out = name
    return out


def confidence_for(window_days: int, vol_ratio: float | None, has_funda: bool) -> str:
    """Keyakinan dari kelengkapan data, likuiditas, dan kelengkapan fundamental."""
    pts = 0
    pts += 2 if window_days >= 250 else 1 if window_days >= 120 else 0
    if vol_ratio is not None and vol_ratio >= 0.5:
        pts += 1
    if has_funda:
        pts += 1
    return "tinggi" if pts >= 4 else "sedang" if pts >= 2 else "rendah"


# ---------------------------------------------------------------- snapshot

def build_snapshot(
    code: str,
    trade_date: str,
    bars: Sequence[dict[str, Any]],
    fundamentals: dict[str, Any] | None = None,
    weights: dict[str, int] | None = None,
    min_window: int = MIN_WINDOW_DAYS,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bangun snapshot analisis lengkap dan deterministik.

    `bars` urut menaik menurut tanggal, tiap item: open/high/low/close/volume
    dengan harga INTEGER rupiah.

    Mengembalikan dict dengan `status` = ok | insufficient_data.
    """
    weights = weights or {"tech": 60, "funda": 40}
    if weights["tech"] + weights["funda"] != 100:
        raise ValueError(
            f"bobot harus berjumlah 100, saat ini {weights['tech'] + weights['funda']}"
        )

    if len(bars) < min_window:
        return {
            "code": code,
            "trade_date": trade_date,
            "status": "insufficient_data",
            "detail": {"required_days": min_window, "available_days": len(bars)},
            "engine_version": ENGINE_VERSION,
        }

    closes = [float(b["close"]) for b in bars]
    volumes = [int(b["volume"]) for b in bars]
    close = int(bars[-1]["close"])
    prev_close = int(bars[-2]["close"])

    mc = macd(closes)
    bb = bollinger(closes)
    ind: dict[str, Any] = {
        "ma20": sma(closes, 20),
        "ma50": sma(closes, 50),
        "ma200": sma(closes, 200),
        "ema12": ema(closes, 12),
        "ema26": ema(closes, 26),
        "rsi14": rsi(closes, 14),
        "macd": mc["macd"] if mc else None,
        "macd_signal": mc["signal"] if mc else None,
        "macd_hist": mc["hist"] if mc else None,
        "bb_upper": bb["upper"] if bb else None,
        "bb_mid": bb["mid"] if bb else None,
        "bb_lower": bb["lower"] if bb else None,
        "vol_ratio": volume_ratio(volumes),
    }
    ind = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ind.items()}

    st = score_technical(ind, close)
    sf = score_fundamental(fundamentals)
    flags: list[str] = []
    if sf is None:
        flags.append("funda_missing")
        total = st
    else:
        total = round(weights["tech"] * st / 100 + weights["funda"] * sf / 100)

    return {
        "code": code,
        "trade_date": trade_date,
        "status": "ok",
        "close": close,
        "prev_close": prev_close,
        "change_pct": round((close - prev_close) / prev_close * 100, 4) if prev_close else 0.0,
        "window_days": len(bars),
        "indicators": ind,
        "fundamentals": fundamentals,
        "weights": weights,
        "scores": {"tech": st, "funda": sf, "total": total},
        "label": label_for(total),
        "confidence": confidence_for(len(bars), ind["vol_ratio"], sf is not None),
        "flags": flags,
        "sources": sources or {},
        "engine_version": ENGINE_VERSION,
    }


def numbers_in_snapshot(snapshot: dict[str, Any]) -> set[float]:
    """Semua nilai numerik di snapshot, untuk validasi anti-halusinasi."""
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.add(float(node))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(snapshot)
    return found

"""Orkestrasi alur analisis dengan LangGraph, plus jalur fallback stdlib.

Alur: fetch -> validate -> compute -> narrate -> critique -> persist

Batas dari docs/adr/001:
- Node TIDAK menghitung indikator; semuanya memanggil agent.analysis.
- run_analysis_fallback() wajib ada dan menghasilkan hasil identik dengan
  jalur graf. Bila LangGraph tidak terpasang, sistem tetap berjalan.
- State disimpan ke SQLite, bukan di memori framework.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from agent import db
from agent.analysis import build_snapshot
from agent.llm import (
    PROMPT_VERSION,
    disclaimer_for,
    narrate_with_crew,
    template_narrative,
    validate_narrative,
)


class AnalysisState(TypedDict, total=False):
    code: str
    trade_date: str
    bars: list[dict]
    fundamentals: dict | None
    weights: dict
    min_window: int
    stale: bool
    snapshot: dict
    narrative: str | None
    narrative_status: str
    attempts: int
    analysis_id: int
    runs: list[dict]


# ---------------------------------------------------------------- node

def _track(state: AnalysisState, node: str, framework: str, status: str,
           started: float, error: str | None = None, tokens: dict | None = None) -> None:
    state.setdefault("runs", []).append({
        "node": node, "framework": framework, "status": status,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "tokens_in": (tokens or {}).get("tokens_in", 0),
        "tokens_out": (tokens or {}).get("tokens_out", 0),
        "error": error,
    })


def node_validate(state: AnalysisState) -> AnalysisState:
    """Periksa kecukupan jendela data sebelum menghitung apa pun."""
    started = time.monotonic()
    n, need = len(state.get("bars") or []), state.get("min_window", 60)
    ok = n >= need
    _track(state, "validate", "langgraph", "ok" if ok else "skipped", started,
           None if ok else f"butuh {need} hari, tersedia {n}")
    return state


def node_compute(state: AnalysisState) -> AnalysisState:
    """Semua angka dihitung di sini, lewat agent.analysis yang murni."""
    started = time.monotonic()
    state["snapshot"] = build_snapshot(
        code=state["code"], trade_date=state["trade_date"], bars=state["bars"],
        fundamentals=state.get("fundamentals"), weights=state.get("weights"),
        min_window=state.get("min_window", 60), sources=state.get("sources"),
    )
    _track(state, "compute", "langgraph", "ok", started)
    return state


def node_narrate(state: AnalysisState) -> AnalysisState:
    """Narasi via CrewAI. Kegagalan apa pun tidak boleh membatalkan analisis."""
    started = time.monotonic()
    state["attempts"] = state.get("attempts", 0) + 1
    if state["snapshot"].get("status") != "ok":
        state["narrative"], state["narrative_status"] = None, "unavailable"
        _track(state, "narrate", "crewai", "skipped", started, "status bukan ok")
        return state
    try:
        text, metrics = narrate_with_crew(state["snapshot"])
        state["narrative"] = text
        state["narrative_status"] = "ok" if text else "unavailable"
        _track(state, "narrate", "crewai", "ok" if text else "skipped", started, None, metrics)
    except Exception as exc:  # provider mati, kuota habis, timeout
        state["narrative"], state["narrative_status"] = None, "unavailable"
        _track(state, "narrate", "crewai", "failed", started, f"{type(exc).__name__}: {exc}")
    return state


def node_critique(state: AnalysisState) -> AnalysisState:
    """Validasi deterministik. Angka asing -> ulang sekali -> template."""
    started = time.monotonic()
    text = state.get("narrative")
    if not text:
        if state["snapshot"].get("status") == "ok":
            state["narrative"] = template_narrative(state["snapshot"])
            state["narrative_status"] = "fallback"
        _track(state, "critique", "langgraph", "skipped", started, "tidak ada narasi LLM")
        return state

    ok, problems = validate_narrative(text, state["snapshot"])
    if ok:
        state["narrative_status"] = "ok"
        _track(state, "critique", "langgraph", "ok", started)
    elif state.get("attempts", 1) < 2:
        state["narrative"] = None  # picu satu kali percobaan ulang
        _track(state, "critique", "langgraph", "failed", started, "; ".join(problems[:3]))
    else:
        state["narrative"] = template_narrative(state["snapshot"])
        state["narrative_status"] = "fallback"
        _track(state, "critique", "langgraph", "failed", started,
               "fallback template: " + "; ".join(problems[:3]))
    return state


def _needs_retry(state: AnalysisState) -> str:
    """Percabangan berkondisi: ulang narasi atau lanjut menyimpan."""
    if state.get("narrative") is None and state.get("attempts", 0) == 1 \
            and state.get("narrative_status") not in ("unavailable", "fallback"):
        return "narrate"
    return "persist"


def node_persist(state: AnalysisState, conn=None) -> AnalysisState:
    """Simpan analisis + jejak orkestrasi. Append-only."""
    started = time.monotonic()
    if conn is None:
        _track(state, "persist", "langgraph", "skipped", started, "tanpa koneksi db")
        return state
    aid = db.save_analysis(
        conn, state["snapshot"], state.get("narrative"),
        state.get("narrative_status", "unavailable"),
        PROMPT_VERSION if state.get("narrative_status") == "ok" else None,
        stale=bool(state.get("stale")),
    )
    state["analysis_id"] = aid
    _track(state, "persist", "langgraph", "ok", started)
    for r in state.get("runs", []):
        db.log_agent_run(
            conn, aid, r["framework"], r["node"], r["status"],
            input_digest=db.digest(state["code"] + state["trade_date"]),
            output_digest=db.digest(state.get("snapshot", {})),
            tokens_in=r["tokens_in"], tokens_out=r["tokens_out"],
            duration_ms=r["duration_ms"], error=r.get("error"),
        )
    return state


# ---------------------------------------------------------------- runner

def run_analysis_fallback(state: AnalysisState, conn=None) -> AnalysisState:
    """Jalur stdlib tanpa LangGraph. WAJIB menghasilkan hasil identik.

    Inilah yang membuat produk tidak terkunci pada framework: bila
    LangGraph dicabut, angka dan keputusan tetap sama.
    """
    state = node_validate(state)
    state = node_compute(state)
    state = node_narrate(state)
    state = node_critique(state)
    if _needs_retry(state) == "narrate":
        state = node_narrate(state)
        state = node_critique(state)
    return node_persist(state, conn)


def build_graph(conn=None):
    """Bangun StateGraph LangGraph. None bila LangGraph tidak terpasang."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None

    g = StateGraph(AnalysisState)
    g.add_node("validate", node_validate)
    g.add_node("compute", node_compute)
    g.add_node("narrate", node_narrate)
    g.add_node("critique", node_critique)
    g.add_node("persist", lambda s: node_persist(s, conn))
    g.set_entry_point("validate")
    g.add_edge("validate", "compute")
    g.add_edge("compute", "narrate")
    g.add_edge("narrate", "critique")
    g.add_conditional_edges("critique", _needs_retry,
                            {"narrate": "narrate", "persist": "persist"})
    g.add_edge("persist", END)
    return g.compile()


def run_analysis(state: AnalysisState, conn=None, prefer_graph: bool = True) -> AnalysisState:
    """Jalankan analisis. Pakai LangGraph bila ada, jika tidak jalur fallback."""
    if prefer_graph:
        graph = build_graph(conn)
        if graph is not None:
            try:
                return dict(graph.invoke(state))
            except Exception:
                pass  # graf bermasalah: turun ke jalur stdlib, jangan gagalkan analisis
    return run_analysis_fallback(state, conn)


def analyze_ticker(conn, code: str, trade_date: str | None = None) -> dict[str, Any]:
    """Ujung ke ujung dari database. Dipakai server dan batch."""
    settings = db.get_settings(conn)
    bars = db.get_bars(conn, code)
    state: AnalysisState = {
        "code": code,
        "trade_date": trade_date or db.trading_date(),
        "bars": bars,
        "fundamentals": db.latest_fundamentals(conn, code),
        "weights": {"tech": settings["weight_tech"], "funda": settings["weight_funda"]},
        "min_window": settings["min_window_days"],
        "stale": bool(bars) and bars[-1]["trade_date"] < (trade_date or db.trading_date()),
    }
    out = run_analysis(state, conn)
    snap = out["snapshot"]
    return {
        "id": out.get("analysis_id"),
        "code": code,
        "trade_date": snap["trade_date"],
        "status": snap["status"],
        "stale": bool(state["stale"]),
        "scores": snap.get("scores"),
        "label": snap.get("label"),
        "confidence": snap.get("confidence"),
        "flags": snap.get("flags", []),
        "detail": snap.get("detail"),
        "narrative": out.get("narrative"),
        "narrative_status": out.get("narrative_status", "unavailable"),
        "data_snapshot": snap,
        "engine_version": snap["engine_version"],
        "disclaimer": disclaimer_for(snap),
    }

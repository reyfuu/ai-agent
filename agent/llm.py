"""Lapisan narasi: LangChain (abstraksi provider) + CrewAI (Narrator & Critic).

Batas dari docs/adr/001:
- Modul ini TIDAK PERNAH menghitung angka. Semua angka datang dari data_snapshot.
- Validasi anti-halusinasi dilakukan kode deterministik di sini, bukan hanya oleh Critic.
- Framework diimpor secara lazy; ketiadaannya tidak boleh menggagalkan analisis.
"""

from __future__ import annotations

import os
import re
from typing import Any

from agent.analysis import numbers_in_snapshot

PROMPT_VERSION = "1.0.0"

DISCLAIMER = (
    "Analisis ini dihasilkan otomatis untuk keperluan informasi, bukan nasihat "
    "investasi. Keputusan dan risikonya sepenuhnya ada pada pengguna. Data per {tanggal}."
)

SYSTEM_PROMPT = """Kamu adalah analis pasar modal yang menulis dalam bahasa Indonesia.

ATURAN MUTLAK:
1. Kamu HANYA boleh menyebut angka yang ada di data JSON yang diberikan.
   Dilarang keras menghitung, memperkirakan, atau mengarang angka apa pun.
2. Jangan pernah menuliskan "beli", "jual", "buy", atau "sell" sebagai instruksi.
   Tulis netral: sinyal menguat, tekanan jual meningkat, dan sejenisnya.
3. Struktur wajib: ringkasan tren, sinyal kunci, faktor risiko, tingkat keyakinan.
4. Maksimal 200 kata. Tidak ada pembukaan basa-basi.
"""

# kata yang menandakan instruksi transaksi (direktif)
_DIRECTIVE = re.compile(
    r"\b(beli|jual|buy|sell)\s+(sekarang|saham|segera)\b|"
    r"\b(rekomendasi|saran)\s+(beli|jual)\b|"
    r"^\s*(beli|jual)\b",
    re.IGNORECASE | re.MULTILINE,
)

_NUMBER = re.compile(r"-?\d[\d.,]*")


def _parse_number(token: str) -> float | None:
    """Baca angka bergaya Indonesia (10.250,5) maupun internasional (10250.5)."""
    t = token.strip().rstrip(".,")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) != 3 else t.replace(",", "")
    elif t.count(".") == 1 and len(t.split(".")[-1]) == 3:
        t = t.replace(".", "")  # 10.250 -> pemisah ribuan
    else:
        t = t.replace(".", "") if t.count(".") > 1 else t
    try:
        return float(t)
    except ValueError:
        return None


def validate_narrative(
    narrative: str, snapshot: dict[str, Any], tolerance: float = 0.05
) -> tuple[bool, list[str]]:
    """Setiap angka di narasi wajib ada di snapshot.

    Toleransi menampung pembulatan tampilan (58,42 -> 58,4). Mengembalikan
    (valid, daftar_masalah). Ini pemeriksaan deterministik, bukan penilaian LLM.
    """
    problems: list[str] = []

    if _DIRECTIVE.search(narrative):
        problems.append("narasi memuat instruksi beli/jual")

    allowed = numbers_in_snapshot(snapshot)
    # izinkan juga bentuk pembulatan umum dari nilai snapshot
    rounded = {round(v, d) for v in allowed for d in (0, 1, 2)}
    allowed = allowed | rounded

    for token in _NUMBER.findall(narrative):
        val = _parse_number(token)
        if val is None:
            continue
        if abs(val) <= 100 and float(val).is_integer():
            continue  # angka kecil bulat: penomoran, periode indikator (14, 20, 50)
        if not any(abs(val - a) <= max(tolerance, abs(a) * 0.001) for a in allowed):
            problems.append(f"angka {token} tidak ada di snapshot")

    return (not problems), problems


def template_narrative(snapshot: dict[str, Any]) -> str:
    """Narasi cadangan tanpa LLM. Hanya menyusun ulang angka snapshot."""
    if snapshot.get("status") != "ok":
        return "Analisis tidak tersedia karena data historis belum mencukupi."
    ind, sc = snapshot["indicators"], snapshot["scores"]
    arah = "di atas" if ind.get("ma50") and snapshot["close"] > ind["ma50"] else "di bawah"
    bagian = [
        f"Ringkasan tren: harga penutupan {snapshot['code']} berada {arah} MA50, "
        f"dengan skor teknikal {sc['tech']} dan skor komposit {sc['total']} "
        f"({snapshot['label']}).",
    ]
    if ind.get("rsi14") is not None:
        kondisi = "jenuh beli" if ind["rsi14"] > 70 else "jenuh jual" if ind["rsi14"] < 30 else "netral"
        bagian.append(f"Sinyal kunci: RSI berada di wilayah {kondisi}.")
    if "funda_missing" in snapshot.get("flags", []):
        bagian.append("Faktor risiko: data fundamental tidak tersedia, skor hanya berbasis teknikal.")
    else:
        bagian.append("Faktor risiko: perhatikan volatilitas pasar dan perubahan kondisi emiten.")
    bagian.append(f"Tingkat keyakinan: {snapshot['confidence']}.")
    return " ".join(bagian)


def disclaimer_for(snapshot: dict[str, Any]) -> str:
    return DISCLAIMER.format(tanggal=snapshot.get("trade_date", "-"))


# ---------------------------------------------------------------- provider

def _get_llm():
    """Bangun klien LLM lewat LangChain. None bila tidak terkonfigurasi.

    Impor lazy: ketiadaan langchain tidak boleh menggagalkan analisis.
    """
    if not os.environ.get("LLM_API_KEY"):
        return None
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None
    return ChatOpenAI(
        model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        temperature=0,
        timeout=30,
        max_retries=1,
        api_key=os.environ["LLM_API_KEY"],
    )


def narrate_with_crew(snapshot: dict[str, Any]) -> tuple[str | None, dict[str, int]]:
    """Narasi dua agent CrewAI: Narrator menulis, Critic memeriksa angka.

    Mengembalikan (narasi | None, metrik). None berarti pemanggil harus
    memakai template_narrative().
    """
    llm = _get_llm()
    if llm is None:
        return None, {"tokens_in": 0, "tokens_out": 0}
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        return None, {"tokens_in": 0, "tokens_out": 0}

    import json

    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    narrator = Agent(
        role="Analis Pasar Modal",
        goal="Menarasikan angka analisis saham secara akurat dalam bahasa Indonesia",
        backstory="Analis yang disiplin dan hanya menyebut angka dari data yang diberikan.",
        llm=llm, verbose=False, allow_delegation=False,
    )
    critic = Agent(
        role="Pemeriksa Angka",
        goal="Menemukan angka yang tidak ada di data sumber dan kalimat yang bersifat instruksi",
        backstory="Auditor yang menolak setiap angka yang tidak bisa ditelusuri ke data.",
        llm=llm, verbose=False, allow_delegation=False,
    )
    t1 = Task(
        description=f"{SYSTEM_PROMPT}\n\nDATA JSON:\n{data}\n\nTulis narasinya.",
        expected_output="Narasi bahasa Indonesia maksimal 200 kata.",
        agent=narrator,
    )
    t2 = Task(
        description=(
            "Periksa narasi terhadap DATA JSON berikut. Hapus atau perbaiki setiap angka "
            f"yang tidak ada di data, dan hilangkan kalimat yang berupa instruksi beli/jual.\n\n{data}"
        ),
        expected_output="Narasi final yang setiap angkanya ada di data.",
        agent=critic, context=[t1],
    )
    crew = Crew(agents=[narrator, critic], tasks=[t1, t2], process=Process.sequential, verbose=False)
    result = crew.kickoff()
    text = str(result).strip()

    usage = getattr(crew, "usage_metrics", None)
    metrics = {
        "tokens_in": int(getattr(usage, "prompt_tokens", 0) or 0),
        "tokens_out": int(getattr(usage, "completion_tokens", 0) or 0),
    }
    return (text or None), metrics

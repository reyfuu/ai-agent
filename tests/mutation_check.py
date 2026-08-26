"""Mutation testing: rusak invariant secara sengaja, pastikan test MERAH.

Test yang tidak pernah gagal tidak membuktikan apa pun. Harness ini
menyuntikkan 14 kerusakan pada invariant paling mahal (RSI Wilder, ambang
60 hari, validasi bobot, anti-halusinasi, otorisasi peran, tanda tangan
sesi) lalu memastikan test suite menangkap semuanya.

Jalankan: python tests/mutation_check.py
File dipulihkan otomatis lewat try/finally, termasuk saat gagal.
"""
import pathlib
import subprocess
import sys

MUTASI = [
 ("agent/analysis.py","avg_gain = (avg_gain * (period - 1) + g) / period","avg_gain = (avg_gain + g) / 2","RSI Wilder -> rata-rata sederhana"),
 ("agent/analysis.py","sd = statistics.pstdev(window)","sd = statistics.stdev(window)","Bollinger stdev populasi -> sampel"),
 ("agent/analysis.py","alpha = 2.0 / (period + 1)","alpha = 1.0 / period","EMA alpha salah"),
 ("agent/analysis.py","if len(bars) < min_window:","if len(bars) < 5:","ambang 60 hari dilonggarkan"),
 ("agent/analysis.py",'raise ValueError(\n            f"bobot harus berjumlah 100, saat ini {weights[\'tech\'] + weights[\'funda\']}"\n        )',"pass","validasi bobot dibuang"),
 ("agent/db.py","if at.hour < MARKET_CLOSE_HOUR:","if False:","jam tutup bursa diabaikan"),
 ("agent/db.py","while at.weekday() >= 5:","while False:","akhir pekan tidak dimundurkan"),
 ("agent/db.py","if not isinstance(b[field], int):","if False:","harga float diizinkan"),
 ("agent/db.py","if tech + funda != 100:","if False:","bobot != 100 diterima"),
 ("agent/llm.py","if not any(abs(val - a) <= max(tolerance, abs(a) * 0.001) for a in allowed):","if False:","anti-halusinasi dimatikan"),
 ("agent/llm.py","if _DIRECTIVE.search(narrative):","if False:","deteksi instruksi beli/jual dimatikan"),
 ("agent/graph.py","ok, problems = validate_narrative(text, state[\"snapshot\"])","ok, problems = True, []","validasi narasi di graf dilewati"),
 ("server.py","if RANK.get(user[\"role\"], -1) < RANK[minimum]:","if False:","pemeriksaan peran dimatikan"),
 ("server.py","if not hmac.compare_digest(mac, expected):","if False:","verifikasi tanda tangan sesi dimatikan"),
 ("server.py",'if not str(f).startswith(str(WEB_DIR.resolve())) or not f.is_file():','if not f.is_file():',"penjagaan path traversal dilucuti"),
 ("server.py","if time.time() - int(issued) > SESSION_TTL:","if False:","masa berlaku sesi diabaikan"),
 ("agent/llm.py","return (text or None), metrics","return (text or 'kosong'), metrics","balasan kosong dianggap narasi sah"),
]
py = ".venv/bin/python" if pathlib.Path(".venv/bin/python").exists() else sys.executable
tertangkap = lolos = 0
for f, lama, baru, nama in MUTASI:
    p = pathlib.Path(f); asli = p.read_text()
    if lama not in asli:
        print(f"  ??  LEWAT   {nama} (pola tidak ditemukan)"); continue
    p.write_text(asli.replace(lama, baru, 1))
    try:
        r = subprocess.run([py,"-m","unittest","discover","-s","tests","-t","."],
                           capture_output=True, timeout=300)
        if r.returncode != 0:
            tertangkap += 1; print(f"  OK  TERTANGKAP  {nama}")
        else:
            lolos += 1; print(f"  !!  LOLOS       {nama}  <-- test tidak menjaga ini")
    finally:
        p.write_text(asli)
print(f"\n{tertangkap}/{tertangkap+lolos} mutasi tertangkap oleh test")
sys.exit(1 if lolos else 0)

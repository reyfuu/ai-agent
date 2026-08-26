"""HTTP server: routing, auth, otorisasi, dan penyajian UI.

Stdlib http.server. Tidak mengimpor framework agent secara langsung.
Otorisasi diperiksa di setiap endpoint, sesuai API_CONTRACT.md §4.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
import traceback
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agent import db
from agent.batch import run_batch
from agent.graph import analyze_ticker

WEB_DIR = Path(__file__).parent / "web"
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
SESSION_TTL = 12 * 3600

ROLES = ("guest", "analyst", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}

_rate: dict[str, list[float]] = {}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


# ---------------------------------------------------------------- sesi

def sign_session(user_id: int, role: str) -> str:
    payload = f"{user_id}:{role}:{int(time.time())}"
    mac = hmac.new(SESSION_SECRET.encode(), payload.encode(), sha256).hexdigest()[:32]
    return f"{payload}:{mac}"


def read_session(cookie: str | None) -> dict | None:
    if not cookie:
        return None
    m = re.search(r"sid=([^;]+)", cookie)
    if not m:
        return None
    try:
        uid, role, issued, mac = m.group(1).rsplit(":", 3)
    except ValueError:
        return None
    payload = f"{uid}:{role}:{issued}"
    expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    if time.time() - int(issued) > SESSION_TTL:
        return None
    return {"id": int(uid), "role": role}


def require(user: dict | None, minimum: str) -> dict:
    """Penjagaan otorisasi server-side. Menyembunyikan tombol bukan otorisasi."""
    if user is None:
        if minimum == "guest":
            return {"id": None, "role": "guest"}
        raise ApiError(401, "UNAUTHENTICATED", "Silakan masuk terlebih dahulu.")
    if RANK.get(user["role"], -1) < RANK[minimum]:
        raise ApiError(403, "FORBIDDEN", "Peran Anda tidak berhak atas aksi ini.")
    return user


def rate_limit(key: str, limit: int, window: int) -> None:
    now = time.time()
    hits = [t for t in _rate.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise ApiError(429, "RATE_LIMITED", "Terlalu banyak percobaan. Coba lagi nanti.")
    hits.append(now)
    _rate[key] = hits


# ---------------------------------------------------------------- handler

_local = threading.local()


class Handler(BaseHTTPRequestHandler):
    server_version = "SahamAgent/1.0"
    db_path = None

    @property
    def conn(self):
        """Koneksi SQLite per-thread.

        ThreadingHTTPServer melayani tiap permintaan di thread berbeda,
        sedangkan objek sqlite3 terikat pada thread pembuatnya.
        """
        c = getattr(_local, "conn", None)
        if c is None:
            c = _local.conn = db.connect(self.db_path)
        return c

    quiet = False

    def log_message(self, fmt, *args):  # log terstruktur ringkas ke stdout
        if not self.quiet:
            print(f"{self.address_string()} {fmt % args}")

    # -- util
    def _send(self, status: int, body: dict | list | None, headers: dict | None = None):
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _error(self, e: ApiError):
        body = {"error": {"code": e.code, "message": e.message}}
        if e.extra:
            body["error"].update(e.extra)
        self._send(e.status, body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            raise ApiError(400, "VALIDATION_ERROR", "Body bukan JSON yang sah.")

    def _user(self) -> dict | None:
        return read_session(self.headers.get("Cookie"))

    def _static(self, path: str):
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        f = (WEB_DIR / name).resolve()
        if not str(f).startswith(str(WEB_DIR.resolve())) or not f.is_file():
            self._send(404, {"error": {"code": "NOT_FOUND", "message": "Halaman tidak ada."}})
            return
        mime = {"html": "text/html", "css": "text/css", "js": "application/javascript"}
        raw = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime.get(f.suffix[1:], 'text/plain')}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # -- routing
    def do_GET(self):
        try:
            self._route_get()
        except ApiError as e:
            self._error(e)
        except Exception:
            traceback.print_exc()  # ke log server, tidak pernah ke klien
            self._send(500, {"error": {"code": "INTERNAL_ERROR",
                                       "message": "Terjadi kesalahan internal."}})

    def do_POST(self):
        try:
            self._route_post()
        except ApiError as e:
            self._error(e)
        except Exception:
            traceback.print_exc()  # ke log server, tidak pernah ke klien
            self._send(500, {"error": {"code": "INTERNAL_ERROR",
                                       "message": "Terjadi kesalahan internal."}})

    def do_DELETE(self):
        try:
            u = require(self._user(), "analyst")
            m = re.fullmatch(r"/api/watchlist/([A-Z]{4})", urlparse(self.path).path)
            if not m:
                raise ApiError(404, "NOT_FOUND", "Endpoint tidak ada.")
            self.conn.execute("DELETE FROM watchlist WHERE user_id=? AND code=?",
                              (u["id"], m.group(1)))
            self.conn.commit()
            self._send(204, None)
        except ApiError as e:
            self._error(e)

    def do_PUT(self):
        try:
            u = require(self._user(), "admin")
            if urlparse(self.path).path != "/api/settings":
                raise ApiError(404, "NOT_FOUND", "Endpoint tidak ada.")
            b = self._body()
            try:
                db.set_weights(self.conn, int(b.get("weight_tech", -1)),
                               int(b.get("weight_funda", -1)))
            except (ValueError, TypeError) as exc:
                raise ApiError(409, "WEIGHTS_INVALID", str(exc))
            db.audit(self.conn, u["id"], "settings.update", "bobot diubah")
            self._send(200, self._settings_body())
        except ApiError as e:
            self._error(e)

    def _export_csv(self, code: str):
        """Ekspor CSV. Baris pertama disclaimer, uang integer tanpa pemisah."""
        bars = db.get_bars(self.conn, code)
        if not bars:
            raise ApiError(404, "NOT_FOUND", "Data harga tidak tersedia.")
        a = self.conn.execute(
            "SELECT trade_date, status, score_tech, score_funda, score_total, label,"
            " confidence FROM analyses WHERE code=? ORDER BY created_at DESC LIMIT 1",
            (code,)).fetchone()

        baris = [
            f"# Analisis otomatis untuk keperluan informasi, bukan nasihat investasi."
            f" Data per {bars[-1]['trade_date']}.",
            "tanggal,buka,tertinggi,terendah,penutupan,volume",
        ]
        for b in bars:
            baris.append(f"{b['trade_date']},{b['open']},{b['high']},{b['low']},"
                         f"{b['close']},{b['volume']}")
        if a:
            baris += ["", "# skor analisis terakhir",
                      "tanggal,status,skor_teknikal,skor_fundamental,skor_total,label,keyakinan",
                      ",".join(str(x if x is not None else "") for x in tuple(a))]

        raw = ("\n".join(baris) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{code}.csv"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _settings_body(self) -> dict:
        s = db.get_settings(self.conn)
        return {
            "weight_tech": s["weight_tech"], "weight_funda": s["weight_funda"],
            "llm_daily_quota": s["llm_daily_quota"],
            "stale_after_days": s["stale_after_days"],
            "min_window_days": s["min_window_days"],
            # hanya status, tidak pernah nilai kuncinya
            "market_api_key_configured": bool(os.environ.get("MARKET_API_KEY")),
            "llm_api_key_configured": bool(os.environ.get("LLM_API_KEY")),
        }

    def _route_get(self):
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)

        if not path.startswith("/api/"):
            return self._static(path)

        if path == "/api/me":
            u = require(self._user(), "analyst")
            row = self.conn.execute("SELECT id, username, role FROM users WHERE id=?",
                                    (u["id"],)).fetchone()
            return self._send(200, dict(row) if row else {})

        if path == "/api/tickers":
            require(self._user(), "guest")
            term = f"%{(q.get('q') or [''])[0].upper()}%"
            rows = self.conn.execute(
                "SELECT code, name, sector, active FROM tickers "
                "WHERE active=1 AND (code LIKE ? OR UPPER(name) LIKE ?) ORDER BY code LIMIT 10",
                (term, term)).fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})

        m = re.fullmatch(r"/api/prices/([A-Z]{4})", path)
        if m:
            require(self._user(), "guest")
            bars = db.get_bars(self.conn, m.group(1))
            if not bars:
                raise ApiError(502, "UPSTREAM_UNAVAILABLE", "Data harga belum tersedia.")
            hari_ini = db.trading_date()
            return self._send(200, {
                "code": m.group(1), "stale": bars[-1]["trade_date"] < hari_ini,
                "last_trade_date": bars[-1]["trade_date"], "items": bars})

        m = re.fullmatch(r"/api/analyses/id/(\d+)", path)
        if m:
            require(self._user(), "guest")
            a = db.get_analysis(self.conn, int(m.group(1)))
            if not a:
                raise ApiError(404, "NOT_FOUND", "Analisis tidak ditemukan.")
            return self._send(200, a)

        m = re.fullmatch(r"/api/analyses/id/(\d+)/runs", path)
        if m:
            require(self._user(), "analyst")
            rows = self.conn.execute(
                "SELECT framework, node, status, duration_ms, tokens_in, tokens_out "
                "FROM agent_runs WHERE analysis_id=? ORDER BY id", (m.group(1),)).fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})

        m = re.fullmatch(r"/api/analyses/([A-Z]{4})", path)
        if m:
            require(self._user(), "guest")
            rows = self.conn.execute(
                "SELECT id, trade_date, status, score_total, label, confidence,"
                " engine_version, created_at FROM analyses WHERE code=?"
                " ORDER BY created_at DESC LIMIT 50", (m.group(1),)).fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})

        if path == "/api/watchlist":
            u = require(self._user(), "analyst")
            rows = self.conn.execute(
                "SELECT w.code, t.name FROM watchlist w JOIN tickers t ON t.code=w.code"
                " WHERE w.user_id=? ORDER BY w.code", (u["id"],)).fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})

        m = re.fullmatch(r"/api/export/([A-Z]{4})\.csv", path)
        if m:
            require(self._user(), "analyst")
            return self._export_csv(m.group(1))

        if path == "/api/settings":
            require(self._user(), "admin")
            return self._send(200, self._settings_body())

        if path == "/api/audit":
            u = require(self._user(), "analyst")
            if u["role"] == "admin":
                rows = self.conn.execute(
                    "SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM audit_logs WHERE user_id=? ORDER BY id DESC LIMIT 100",
                    (u["id"],)).fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})

        raise ApiError(404, "NOT_FOUND", "Endpoint tidak ada.")

    def _route_post(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            rate_limit(f"login:{self.address_string()}", 5, 300)
            b = self._body()
            row = self.conn.execute(
                "SELECT id, username, role, password_hash FROM users"
                " WHERE username=? AND active=1", (b.get("username", ""),)).fetchone()
            if not row or not db.verify_password(b.get("password", ""), row["password_hash"]):
                raise ApiError(401, "UNAUTHENTICATED", "Username atau password salah.")
            db.audit(self.conn, row["id"], "login")
            return self._send(200, {"user": {"id": row["id"], "username": row["username"],
                                             "role": row["role"]}},
                              {"Set-Cookie": f"sid={sign_session(row['id'], row['role'])};"
                                             " HttpOnly; SameSite=Lax; Path=/"})

        if path == "/api/logout":
            return self._send(204, None, {"Set-Cookie": "sid=; Max-Age=0; Path=/"})

        m = re.fullmatch(r"/api/analyze/([A-Z]{4})", path)
        if m:
            u = require(self._user(), "analyst")
            rate_limit(f"analyze:{u['id']}", 30, 60)
            code = m.group(1)
            if not self.conn.execute("SELECT 1 FROM tickers WHERE code=?", (code,)).fetchone():
                raise ApiError(404, "NOT_FOUND", "Emiten tidak dikenal.")
            hasil = analyze_ticker(self.conn, code)
            db.audit(self.conn, u["id"], "analyze", code)
            return self._send(200, hasil)

        if path == "/api/watchlist":
            u = require(self._user(), "analyst")
            code = (self._body().get("code") or "").upper()
            if not re.fullmatch(r"[A-Z]{4}", code):
                raise ApiError(400, "VALIDATION_ERROR", "Kode emiten harus 4 huruf kapital.")
            if not self.conn.execute("SELECT 1 FROM tickers WHERE code=?", (code,)).fetchone():
                raise ApiError(404, "NOT_FOUND", "Emiten tidak dikenal.")
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, code) VALUES (?,?)", (u["id"], code))
            self.conn.commit()
            return self._send(201 if cur.rowcount else 200, {"code": code})

        if path == "/api/batch":
            u = require(self._user(), "admin")
            hasil = run_batch(self.conn)
            db.audit(self.conn, u["id"], "batch")
            return self._send(202, hasil)

        raise ApiError(404, "NOT_FOUND", "Endpoint tidak ada.")


def serve(port: int = 8000, db_path: str | None = None):
    db.init(db_path).close()  # migrasi sekali di thread utama
    Handler.db_path = db_path
    if os.environ.get("SESSION_SECRET") is None:
        print("PERINGATAN: SESSION_SECRET tidak diset, sesi hilang saat restart.")
    print(f"Server berjalan di http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(int(os.environ.get("PORT", 8000)))

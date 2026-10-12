"""Local dashboard server: python3 serve.py  ->  http://127.0.0.1:8787

Serves web/ and a small JSON API. Listens on this computer only. Every POST needs the
header X-Requested-With: chilli, and the Host header must be localhost, which blocks
other websites from driving the dashboard.
"""
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chat
import export_status
import guardrails
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
PORT = 8787


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _host_ok(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        path = self.path.split("?")[0]
        if path == "/api/status":
            return self._send(200, export_status.build_status())
        rel = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.realpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.realpath(WEB_DIR) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Requested-With") != "chilli":
            return self._send(403, {"error": "forbidden"})
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 10000)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        if self.path == "/api/chat":
            status = export_status.build_status()
            return self._send(200, {"reply": chat.reply(body.get("message", ""), status, workforce.default_llm())})
        if self.path == "/api/kill":
            guardrails.kill("dashboard button")
            return self._send(200, {"kill_switch": True})
        if self.path == "/api/resume":
            guardrails.resume()
            return self._send(200, {"kill_switch": False})
        if self.path == "/api/decide":
            ok = guardrails.decide(int(body.get("id", 0)), bool(body.get("approve")))
            return self._send(200, {"ok": ok})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)

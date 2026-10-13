"""Local dashboard server: python3 serve.py  ->  http://127.0.0.1:8787

Serves web/ and a small JSON API. Listens on this computer only by default.
For Tailscale remote access: CHILLI_BIND=0.0.0.0 python3 serve.py
Then open http://<tailscale-ip>:8787 from phone/Windows machine.

Every POST needs the header X-Requested-With: chilli. When running on localhost,
the Host header must also be localhost (blocks CSRF from other sites).
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
PORT = int(os.environ.get("CHILLI_PORT", "8787"))
BIND = os.environ.get("CHILLI_BIND", "127.0.0.1")
_REMOTE = BIND not in ("127.0.0.1", "::1", "localhost")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _host_ok(self):
        if _REMOTE:
            return True  # Tailscale private network
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
        if self.path == "/api/recipes":
            try:
                import recipe_runner
                return self._send(200, {"recipes": recipe_runner.list_recipes()})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if self.path == "/api/run_recipe":
            name = str(body.get("name", "")).strip()
            if not name:
                return self._send(400, {"error": "name required"})
            try:
                import recipe_runner, os as _os
                path = _os.path.join(recipe_runner.RECIPES_DIR, name)
                if not path.endswith(".yaml"):
                    path += ".yaml"
                result = recipe_runner.run_recipe(path)
                if "error" in result:
                    return self._send(400, result)
                return self._send(200, result)
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if self.path == "/api/enqueue":
            kind = str(body.get("kind", "")).strip()
            payload = body.get("payload", {})
            if kind not in {"research", "draft", "plan", "review", "code_proposal"}:
                return self._send(400, {"error": "invalid kind"})
            try:
                task_id = workforce.enqueue(kind, payload)
                return self._send(200, {"id": task_id, "kind": kind})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    if _REMOTE:
        import socket
        local_ip = socket.gethostbyname(socket.gethostname())
        print(f"[REMOTE MODE] Binding {BIND}:{PORT} — ensure Tailscale is running")
        print(f"Dashboard: http://<tailscale-ip>:{PORT}  or  http://{local_ip}:{PORT}")
    else:
        print(f"Dashboard: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)

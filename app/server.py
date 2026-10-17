"""RecoverIQ operator app — stdlib HTTP server, zero dependencies.

    python3 -m app.server   →   http://localhost:8420

Serves the operator console (app/static/index.html) and a small JSON API
over the live engine. Deliberately framework-free: the whole product runs
on `pip install numpy` and nothing else.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.engine import LiveEngine

PORT = 8420
STATIC = os.path.join(os.path.dirname(__file__), "static")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Repo docs the console footer links to; served read-only from the repo root.
DOCS = {"/CHANGELOG.md", "/policy.json"}
class EngineHolder:
    def __init__(self):
        self.engine = LiveEngine()

    def reset(self):
        """Fresh demo run — new incoming traffic, but the agent keeps
        everything it has learned so far."""
        old = self.engine
        fresh = LiveEngine()
        with old.lock:
            fresh.bandit = old.bandit
        fresh._feed(fresh.clock, "agent", "win",
                    "Fresh demo started — the agent remembers everything it learned")
        self.engine = fresh


holder = EngineHolder()


def clock_loop():
    prev = time.monotonic()
    while True:
        time.sleep(0.1)
        now = time.monotonic()
        holder.engine.tick(now - prev)
        prev = now


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal clean for the demo
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            return self._json(holder.engine.snapshot())
        if self.path in DOCS:
            doc = os.path.join(ROOT, self.path.lstrip("/"))
            if os.path.isfile(doc):
                with open(doc, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            return self._json({"error": "not found"}, 404)
        path = "/index.html" if self.path in ("/", "") else self.path
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if full.startswith(os.path.realpath(STATIC)) and os.path.isfile(full):
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            ctype = "text/html; charset=utf-8" if full.endswith(".html") else "text/plain"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if self.path == "/api/reset":
            holder.reset()
        elif self.path == "/api/run":
            holder.engine.set_running(body.get("running", True))
        elif self.path == "/api/speed":
            holder.engine.set_speed(body.get("speed", 6))
        elif self.path == "/api/approve":
            if not holder.engine.resolve_approval(body.get("case_id"),
                                           bool(body.get("approved"))):
                return self._json({"error": "no such approval"}, 404)
        elif self.path == "/api/case":
            cid = holder.engine.inject_case(body.get("merchant", ""),
                                     body.get("amount", 500),
                                     body.get("bank", "HDFC"),
                                     body.get("failure_code", ""))
            return self._json({"ok": True, "case_id": cid})
        elif self.path == "/api/policy":
            holder.engine.update_policy(body)
        else:
            return self._json({"error": "not found"}, 404)
        return self._json({"ok": True})


def main():
    print("warm-starting the agent on 1,000 historical cases…")
    holder.engine.warm_start()
    threading.Thread(target=clock_loop, daemon=True).start()
    print(f"RecoverIQ operator console → http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

"""A read-only window into the desk, in a browser.

    python run_ui.py                 http://127.0.0.1:8787
    python run_ui.py --port 9000
    python run_ui.py --open          ...and open the browser

Standard library only, on purpose: the desk has no web framework dependency and
`pip install` should not be a prerequisite for looking at your own paper account.
The server is a viewer, not a control panel. It reads the journal, the watchlist
and the ledger and serves them as JSON. It cannot trade, cannot clear the bench
and cannot delete a position, because the only methods it implements are GET.
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from desk import ops, telemetry

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
ASSETS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    server_version = "DeskUI/1.0"

    def do_GET(self):                                  # noqa: N802 (stdlib naming)
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self.serve_file(UI_DIR / "index.html")
        if path == "/api/state":
            return self.serve_state()
        return self.serve_file(UI_DIR / path.lstrip("/"))

    def serve_state(self):
        try:
            body = json.dumps(telemetry.snapshot(), default=str).encode("utf-8")
        except Exception as e:                          # noqa: BLE001 - a viewer must not die
            self.send_json({"error": f"{type(e).__name__}: {e}"}, status=500)
            return
        self.respond(body, "application/json; charset=utf-8")

    def serve_file(self, path: Path):
        """Only ever serve a file that is inside ui/, and never a dotfile."""
        try:
            resolved = path.resolve()
        except OSError:
            return self.not_found()
        if UI_DIR.resolve() not in resolved.parents or resolved.name.startswith("."):
            return self.not_found()
        if not resolved.is_file():
            return self.not_found()
        self.respond(resolved.read_bytes(),
                     ASSETS.get(resolved.suffix, "application/octet-stream"))

    def respond(self, body: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A stale view of a live desk is worse than no view.
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: int = 200):
        self.respond(json.dumps(payload).encode("utf-8"),
                     "application/json; charset=utf-8", status)

    def not_found(self):
        self.send_json({"error": "not found", "path": self.path}, status=404)

    def log_message(self, fmt, *args):
        """Quiet by default: a 2-second poll would otherwise spam the console."""
        pass


def main():
    ap = argparse.ArgumentParser(description="Read-only web view of the paper desk.")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address. Default is loopback: this shows your account.")
    ap.add_argument("--open", action="store_true", help="open the browser on start")
    args = ap.parse_args()

    ops.make_output_safe()
    ops.load_env()                     # so the key pill can tell the truth
    if not UI_DIR.is_dir():
        raise SystemExit(f"[!] No ui/ directory next to run_ui.py (looked in {UI_DIR})")

    url = f"http://{args.host}:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Desk UI on {url}   (read-only; Ctrl-C to stop)", flush=True)
    print(f"  journal.jsonl  {'found' if ops.JOURNAL.exists() else 'not yet, run a cycle'}",
          flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosed.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
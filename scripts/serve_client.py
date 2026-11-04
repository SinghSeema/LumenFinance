"""Minimal browser client for a local test call.

    python3 scripts/serve_client.py      # then open http://localhost:8080

Serves scripts/client/index.html and /token (a LiveKit token for the borrower, same room as the agent).
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from livekit import api

from emi_agent import config as config_mod

PAGE = Path(__file__).parent / "client" / "index.html"
PORT = 8080


def borrower_token(cfg: dict) -> str:
    lk = cfg["livekit"]
    grants = api.VideoGrants(room_join=True, room=lk["room_name"], can_publish=True, can_subscribe=True)
    return (api.AccessToken(lk["api_key"], lk["api_secret"])
            .with_identity("borrower").with_name("Borrower").with_grants(grants).to_jwt())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
        elif self.path == "/token":
            body = {"url": CFG["livekit"]["url"], "token": borrower_token(CFG)}
            self._send(200, "application/json", json.dumps(body).encode())
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet: only errors
        if not str(args[1]).startswith("2"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    config_mod.load_dotenv()
    CFG = config_mod.load(require_keys=["livekit"])
    print(f"Open http://localhost:{PORT}  (room {CFG['livekit']['room_name']})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

"""Test helpers: make skills/grok-image importable, fixtures, stub server, key files."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "grok-image"))

import grok_image  # noqa: E402,F401

FIXTURES = Path(__file__).resolve().parent / "fixtures"

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def err(name: str):
    status, body = load_fixture("errors.json")[name]
    return status, {}, body.encode()


def iso(seconds: float) -> str:
    return (NOW + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_login(home: Path, entries) -> None:
    """Write home/.grok/auth.json from (key, expires_at_or_None) pairs."""
    data = {}
    for i, (key, expires_at) in enumerate(entries):
        entry = {"key": key}
        if expires_at is not None:
            entry["expires_at"] = expires_at
        data[f"https://auth.x.ai::client{i}"] = entry
    path = Path(home) / ".grok" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_dotenv(home: Path, text: str) -> None:
    path = Path(home) / ".omp" / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class StubServer:
    """Serve canned (status, headers, body) responses in order on 127.0.0.1."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                with stub._lock:
                    stub.requests.append(
                        (self.command, self.path, dict(self.headers), body)
                    )
                    if stub._responses:
                        status, headers, payload = stub._responses.pop(0)
                    else:
                        status, headers, payload = (
                            500, {}, b'{"error":"stub exhausted"}')
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(payload)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01},
            daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        return False

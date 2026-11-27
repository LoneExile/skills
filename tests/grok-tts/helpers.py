"""Test helpers: make skills/grok-tts importable and load fixtures."""
import struct
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "grok-tts"))

import grok_tts  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def streaming_wav(seconds: float, rate: int = 44100) -> bytes:
    """Mono 16-bit PCM silence with the streaming header the API sends."""
    pcm = bytes(int(seconds * rate) * 2)
    return (
        b"RIFF" + struct.pack("<I", 0x7FFFFFFF + 36) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", 0x7FFFFFFF) + pcm
    )


import base64  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


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


def tts_ok(text: str = "Hi there", seconds: float = 0.5, fmt: str = "wav"):
    audio = streaming_wav(seconds) if fmt == "wav" else b"\xff\xfb\x90" + bytes(400)
    step = seconds / len(text)
    envelope = {
        "audio": base64.b64encode(audio).decode(),
        "content_type": "audio/wav" if fmt == "wav" else "audio/mpeg",
        "duration": seconds,
        "audio_timestamps": {
            "graph_chars": list(text),
            "graph_times": [[i * step, (i + 1) * step] for i in range(len(text))],
        },
    }
    return 200, {}, json.dumps(envelope).encode()


def err(name: str):
    status, body = load_fixture("errors.json")[name]
    return status, {}, body.encode()

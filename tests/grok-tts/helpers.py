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

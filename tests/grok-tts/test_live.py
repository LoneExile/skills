"""One real xAI call. Skipped unless GROK_TTS_LIVE=1."""
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path

from helpers import grok_tts


@unittest.skipUnless(os.environ.get("GROK_TTS_LIVE") == "1", "set GROK_TTS_LIVE=1 to call xAI")
class LiveTest(unittest.TestCase):
    def test_live_single_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code = grok_tts.main([
                "Live check.", "-o", str(tmp / "live.wav"),
                "--words", str(tmp / "live.json"), "--no-cache",
            ])
            self.assertEqual(code, 0)
            with wave.open(str(tmp / "live.wav")) as w:
                duration = w.getnframes() / w.getframerate()
            self.assertGreater(duration, 0.3)
            words = json.loads((tmp / "live.json").read_text(encoding="utf-8"))
            self.assertEqual(words[0]["text"], "Live")


if __name__ == "__main__":
    unittest.main()

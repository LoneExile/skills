import io, os, stat, struct, tempfile, unittest, wave
from pathlib import Path
from unittest import mock
from helpers import grok_tts, streaming_wav

class RepairWavTest(unittest.TestCase):
    def test_sets_real_sizes(self):
        out = grok_tts.repair_wav(streaming_wav(0.5), 0.5)
        self.assertEqual(struct.unpack_from("<I", out, 4)[0], len(out) - 8)
        self.assertEqual(struct.unpack_from("<I", out, 40)[0], 44100)
        self.assertEqual(wave.open(io.BytesIO(out)).getnframes(), 22050)
    def test_duration_mismatch_raises_naming_both(self):
        with self.assertRaises(grok_tts.AudioError) as cm:
            grok_tts.repair_wav(streaming_wav(0.5), 6.39)
        self.assertIn("0.5", str(cm.exception)); self.assertIn("6.39", str(cm.exception))
    def test_tolerance_is_005(self):
        grok_tts.repair_wav(streaming_wav(0.5), 0.54)
        with self.assertRaises(grok_tts.AudioError):
            grok_tts.repair_wav(streaming_wav(0.5), 0.56)
    def test_trailing_odd_byte_dropped(self):
        out = grok_tts.repair_wav(streaming_wav(0.5) + b"\x00", 0.5)
        self.assertEqual(struct.unpack_from("<I", out, 40)[0], 44100)
    def test_not_riff_raises(self):
        with self.assertRaises(grok_tts.AudioError):
            grok_tts.repair_wav(b"ID3\x04" + bytes(100), 0.5)

class CheckMp3Test(unittest.TestCase):
    def fake_ffprobe(self, seconds):
        d = tempfile.mkdtemp(); p = Path(d, "ffprobe")
        p.write_text(f"#!/bin/sh\necho {seconds}\n"); p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return str(p)
    def test_headers(self):
        grok_tts.check_mp3(b"ID3" + bytes(10), 1.0, None)
        grok_tts.check_mp3(b"\xff\xfb\x90" + bytes(10), 1.0, None)
        with self.assertRaises(grok_tts.AudioError):
            grok_tts.check_mp3(b"<html>", 1.0, None)
    def test_ffprobe_duration_checked(self):
        grok_tts.check_mp3(b"\xff\xfb\x90" + bytes(10), 2.87, self.fake_ffprobe(2.899583))
        with self.assertRaises(grok_tts.AudioError):
            grok_tts.check_mp3(b"\xff\xfb\x90" + bytes(10), 2.87, self.fake_ffprobe(2.0))

class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()); self.a = self.dir / "a.wav"; self.b = self.dir / "a.words.json"
        self.a.write_bytes(b"old-a"); self.b.write_bytes(b"old-b")
    def test_replaces_both(self):
        grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        self.assertEqual((self.a.read_bytes(), self.b.read_bytes()), (b"new-a", b"new-b"))
        self.assertEqual(list(self.dir.glob("*.part")), [])
    def test_atomic_write_interrupt_keeps_old_file(self):
        with mock.patch.object(grok_tts.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        self.assertEqual(self.a.read_bytes(), b"old-a")
        self.assertEqual(list(self.dir.glob("*.part")), [])

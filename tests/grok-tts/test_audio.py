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
    def test_nan_expected_duration_fails_closed(self):
        with self.assertRaises(grok_tts.AudioError):
            grok_tts.repair_wav(streaming_wav(0.5), float("nan"))

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
    def test_nan_expected_duration_fails_closed(self):
        with self.assertRaises(grok_tts.AudioError):
            grok_tts.check_mp3(b"\xff\xfb\x90" + bytes(10), float("nan"), self.fake_ffprobe(2.0))

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
    def failing_second_rename(self):
        real = os.replace
        def fake(src, dst):
            if Path(dst) == self.b:
                raise OSError("boom")
            real(src, dst)
        return mock.patch.object(grok_tts.os, "replace", side_effect=fake)
    def test_second_rename_failure_restores_old_files(self):
        with self.failing_second_rename():
            with self.assertRaises(OSError):
                grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        self.assertEqual(self.a.read_bytes(), b"old-a")
        self.assertEqual(self.b.read_bytes(), b"old-b")
        self.assertEqual(list(self.dir.glob("*.part")) + list(self.dir.glob("*.bak")), [])
    def test_second_rename_failure_fresh_destinations_leaves_nothing(self):
        self.a.unlink(); self.b.unlink()
        with self.failing_second_rename():
            with self.assertRaises(OSError):
                grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        self.assertFalse(self.a.exists()); self.assertFalse(self.b.exists())
        self.assertEqual(list(self.dir.glob("*.part")) + list(self.dir.glob("*.bak")), [])
    def test_partial_snapshot_never_restored(self):
        real_copy = grok_tts.shutil.copy2
        def fake_copy(src, dst, *args, **kwargs):
            if "words" in Path(src).name:
                Path(dst).write_bytes(b"ol")
                raise OSError(28, "No space left on device")
            return real_copy(src, dst, *args, **kwargs)
        with mock.patch.object(grok_tts.os, "link", side_effect=OSError), \
                mock.patch.object(grok_tts.shutil, "copy2", side_effect=fake_copy):
            with self.assertRaises(OSError):
                grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        self.assertEqual(self.a.read_bytes(), b"old-a")
        self.assertEqual(self.b.read_bytes(), b"old-b")
        self.assertEqual(list(self.dir.glob("*.part")) + list(self.dir.glob("*.bak")), [])
    def test_failed_restore_keeps_snapshot_and_names_it(self):
        with self.failing_second_rename(), \
                mock.patch.object(grok_tts.shutil, "move", side_effect=OSError("restore failed")):
            with self.assertRaises(OSError) as cm:
                grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        notes = "\n".join(getattr(cm.exception, "__notes__", []))
        holders = [p for p in self.dir.iterdir() if p.read_bytes() == b"old-a" and p != self.a]
        self.assertTrue(holders)
        self.assertTrue(any(str(p) in notes for p in holders))
    def test_user_bak_and_part_files_untouched(self):
        bak = self.dir / "a.wav.bak"; part = self.dir / "a.wav.part"
        bak.write_bytes(b"USER BAK"); part.write_bytes(b"USER PART")
        def user_files_intact():
            self.assertEqual((bak.read_bytes(), part.read_bytes()), (b"USER BAK", b"USER PART"))
            self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                             sorted(["a.wav", "a.words.json", "a.wav.bak", "a.wav.part"]))
        grok_tts.atomic_write_many([(self.a, b"new-a"), (self.b, b"new-b")])
        user_files_intact()
        with self.failing_second_rename():
            with self.assertRaises(OSError):
                grok_tts.atomic_write_many([(self.a, b"x-a"), (self.b, b"x-b")])
        user_files_intact()

class NoteHelperTest(unittest.TestCase):
    def test_notes_collect_on_exceptions_and_on_objects_without_add_note(self):
        exc = OSError("x")
        grok_tts._add_note(exc, "one"); grok_tts._add_note(exc, "two")
        self.assertEqual(exc.__notes__, ["one", "two"])
        class Bare: pass  # no add_note, as on Python before 3.11
        bare = Bare()
        grok_tts._add_note(bare, "one"); grok_tts._add_note(bare, "two")
        self.assertEqual(bare.__notes__, ["one", "two"])

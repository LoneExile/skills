import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from helpers import StubServer, err, grok_tts, tts_ok

KEY = "test-key-zz"


class CliTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.home = root / "home"
        self.out = root / "out"
        self.cache = root / "cache"
        for d in (self.home, self.out, self.cache):
            d.mkdir()
        self.env = {"XAI_API_KEY": KEY, "XDG_CACHE_HOME": str(self.cache)}

    def run_cli(self, argv, responses=(), env=None):
        out, errbuf = io.StringIO(), io.StringIO()
        with StubServer(responses) as srv, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(errbuf):
            code = grok_tts.main(
                argv, api_base=srv.url, home=self.home,
                env=self.env if env is None else env, sleep=lambda s: None)
        return code, out.getvalue(), errbuf.getvalue(), srv.requests

    def test_single_wav_outputs(self):
        a, j = self.out / "a.wav", self.out / "a.json"
        code, out, err_, _ = self.run_cli(
            ["Hi there", "-o", str(a), "--words", str(j)], [tts_ok()])
        self.assertEqual(code, 0, err_)
        with wave.open(str(a)) as w:
            self.assertGreater(w.getnframes(), 0)
        self.assertEqual(json.loads(j.read_text("utf-8"))[0]["text"], "Hi")
        self.assertIn(f"MEDIA:{a.resolve()}", out)
        self.assertIn(f"Audio saved to: {a.resolve()}", out)
        self.assertIn(f"Words saved to: {j.resolve()}", out)
        self.assertNotIn(KEY, out + err_)

    def test_text_file_bom_stripped(self):
        t = self.out / "t.txt"
        t.write_text("Hello file", encoding="utf-8-sig")
        code, _, e, reqs = self.run_cli(
            [str(t), "-o", str(self.out / "a.wav")], [tts_ok("Hello file")])
        self.assertEqual(code, 0, e)
        self.assertEqual(reqs[0][3]["text"], "Hello file")

    def test_dash_text_after_separator(self):
        code, _, e, reqs = self.run_cli(
            ["-o", str(self.out / "a.wav"), "--", "--and then"],
            [tts_ok("--and then")])
        self.assertEqual(code, 0, e)
        self.assertEqual(reqs[0][3]["text"], "--and then")

    def test_speed_out_of_range(self):
        code, _, e, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav"), "--speed", "1.6"])
        self.assertEqual(code, 2)
        self.assertEqual(reqs, [])
        self.assertIn("speed", e)

    def test_lang_typo(self):
        code, _, _, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav"), "--lang", "english"])
        self.assertEqual(code, 2)
        self.assertEqual(reqs, [])

    def test_undocumented_lang_warns(self):
        code, _, e, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav"), "--lang", "th"], [tts_ok("Hi")])
        self.assertEqual(code, 0, e)
        self.assertIn("WARNING", e)
        self.assertEqual(reqs[0][3]["language"], "th")

    def test_length_limit(self):
        a = str(self.out / "a.wav")
        text = "a" * 60000
        code, _, e, reqs = self.run_cli([text, "-o", a], [tts_ok(text)])
        self.assertEqual((code, len(reqs)), (0, 1), e)
        code, _, _, reqs = self.run_cli(["a" * 60001, "-o", a])
        self.assertEqual((code, len(reqs)), (2, 0))

    def test_tag_only_text(self):
        code, _, _, reqs = self.run_cli(["[laugh]", "-o", str(self.out / "a.wav")])
        self.assertEqual((code, reqs), (2, []))

    def test_bad_extension(self):
        code, _, _, reqs = self.run_cli(["Hi", "-o", str(self.out / "a.ogg")])
        self.assertEqual((code, reqs), (2, []))

    def test_mode_errors(self):
        s = self.out / "s.json"
        s.write_text("[]")
        for argv in ([], ["--lines", str(s), "-o", str(self.out / "a.wav")]):
            code, _, _, reqs = self.run_cli(argv)
            self.assertEqual((code, reqs), (2, []), argv)

    def test_cache_hit_skips_api_and_key(self):
        a = self.out / "a.wav"
        argv = ["Hi there", "-o", str(a), "--words", str(self.out / "a.json")]
        self.assertEqual(self.run_cli(argv, [tts_ok()])[0], 0)
        first = a.read_bytes()
        a.unlink()
        code, out, e, reqs = self.run_cli(
            argv, env={"XDG_CACHE_HOME": str(self.cache)})
        self.assertEqual((code, reqs), (0, []), e)
        self.assertIn("(cached)", out)
        self.assertEqual(a.read_bytes(), first)

    def test_no_cache_forces_request(self):
        a = str(self.out / "a.wav")
        self.run_cli(["Hi there", "-o", a], [tts_ok()])
        code, out, e, reqs = self.run_cli(
            ["Hi there", "-o", a, "--no-cache"], [tts_ok()])
        self.assertEqual((code, len(reqs)), (0, 1), e)
        self.assertNotIn("(cached)", out)

    def test_cache_key_changes_with_voice_speed_format(self):
        def k(**kw):
            args = dict(text="Hi", voice="eve", lang="en", speed=1.0,
                        normalize=False, fmt="wav")
            args.update(kw)
            return grok_tts.cache_key(**args)
        base = k()
        for change in ({"voice": "ara"}, {"speed": 1.2}, {"fmt": "mp3"},
                       {"normalize": True}, {"text": "Hi!"}):
            self.assertNotEqual(base, k(**change), change)
        self.assertEqual(base, k(voice="Eve"))
        self.assertEqual(base, k(lang="EN"))

    def test_list_prints_voices(self):
        code, out, e, _ = self.run_cli(["--list"], [err("voices")])
        self.assertEqual(code, 0, e)
        self.assertIn("eve\tEve\tfemale", out)
        self.assertIn("rex", out)

    def test_missing_key_lists_sources(self):
        code, _, e, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav")],
            env={"XDG_CACHE_HOME": str(self.cache)})
        self.assertEqual((code, reqs), (1, []))
        self.assertIn("XAI_API_KEY (environment): unset", e)

    def test_rejected_key_does_not_fall_through(self):
        (self.home / ".omp").mkdir()
        (self.home / ".omp" / ".env").write_text("GROK_API_KEY=other-key\n")
        code, out, e, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav")], [err("bad_key")])
        self.assertEqual(code, 1)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0][2]["Authorization"], "Bearer test-key-zz")
        self.assertIn("the key from XAI_API_KEY (environment) was rejected", e)
        self.assertNotIn("other-key", out + e)

    def test_failure_leaves_no_files(self):
        a = self.out / "a.wav"
        code, _, _, _ = self.run_cli(
            ["Hi", "-o", str(a)], [(500, {}, b'{"error":"x"}')] * 4)
        self.assertEqual(code, 1)
        self.assertFalse(a.exists())
        self.assertEqual(list(self.out.glob("*.part")), [])

    def test_failed_restore_is_reported_with_the_snapshot_name(self):
        a, j = self.out / "a.wav", self.out / "a.json"
        a.write_bytes(b"old-a")
        j.write_bytes(b"old-b")
        real = os.replace

        def fake(src, dst):
            if Path(dst) == j:
                raise OSError("boom")
            real(src, dst)

        with mock.patch.object(grok_tts.os, "replace", side_effect=fake), \
                mock.patch.object(grok_tts.shutil, "move",
                                  side_effect=OSError("restore failed")):
            code, _, e, _ = self.run_cli(
                ["Hi there", "-o", str(a), "--words", str(j)], [tts_ok()])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: boom", e)
        holders = [p for p in self.out.iterdir()
                   if p != a and p.read_bytes() == b"old-a"]
        self.assertTrue(holders)
        self.assertTrue(any(str(p) in e for p in holders), e)


if __name__ == "__main__":
    unittest.main()

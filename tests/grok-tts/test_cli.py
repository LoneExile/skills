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

from helpers import StubServer, err, grok_tts, lock_dir, tts_edit, tts_ok

KEY = "test-key-zz"


class CliTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.root = root
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

    def test_text_file_not_utf8_is_a_usage_error(self):
        t = self.out / "t.txt"
        t.write_bytes("it\u2019s fine".encode("cp1252"))  # the apostrophe is byte 0x92
        code, _, e, reqs = self.run_cli(
            [str(t), "-o", str(self.out / "a.wav")], [tts_ok("x")])
        self.assertEqual((code, reqs), (2, []))
        self.assertIn(f"ERROR: {t} is not readable UTF-8 text; save it as UTF-8", e)
        self.assertNotIn("Traceback", e)

    def test_unreadable_text_file_is_a_usage_error(self):
        t = self.out / "t.txt"
        t.write_text("Hi")
        t.chmod(0)
        self.addCleanup(t.chmod, 0o600)
        try:
            t.read_bytes()
        except OSError:
            pass
        else:
            self.skipTest("this account ignores file permissions (root?)")
        code, _, e, reqs = self.run_cli(
            [str(t), "-o", str(self.out / "a.wav")], [tts_ok("Hi")])
        self.assertEqual((code, reqs), (2, []))
        self.assertIn("is not readable UTF-8 text", e)

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
        a = self.out / "a.wav"
        self.run_cli(["Hi there", "-o", str(a)], [tts_ok()])
        old = a.read_bytes()
        entry = self.cache / "grok-tts" / (
            grok_tts.cache_key("Hi there", "eve", "en", 1.0, False, "wav") + ".wav")
        self.assertEqual(entry.read_bytes(), old)
        # The API now answers differently: --no-cache must ask again and leave the
        # new audio in the cache entry.
        code, out, e, reqs = self.run_cli(
            ["Hi there", "-o", str(a), "--no-cache"], [tts_ok("Hi there", seconds=0.7)])
        self.assertEqual((code, len(reqs)), (0, 1), e)
        self.assertNotIn("(cached)", out)
        new = a.read_bytes()
        self.assertNotEqual(new, old)
        self.assertEqual(entry.read_bytes(), new)
        a.unlink()
        code, out, e, reqs = self.run_cli(["Hi there", "-o", str(a)])
        self.assertEqual((code, reqs), (0, []), e)
        self.assertEqual(a.read_bytes(), new)

    def test_cache_key_changes_with_voice_speed_format(self):
        def k(**kw):
            args = dict(text="Hi", voice="eve", lang="en", speed=1.0,
                        normalize=False, fmt="wav")
            args.update(kw)
            return grok_tts.cache_key(**args)
        base = k()
        for change in ({"voice": "ara"}, {"speed": 1.2}, {"fmt": "mp3"},
                       {"normalize": True}, {"text": "Hi!"}, {"lang": "fr"}):
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

    def test_unwritable_cache_only_warns(self):
        locked_home = self.root / "locked-home"
        locked_folder = self.root / "locked-folder"
        locked_home.mkdir()
        (locked_folder / "grok-tts").mkdir(parents=True)
        lock_dir(self, locked_home)
        lock_dir(self, locked_folder / "grok-tts")
        a, j = self.out / "a.wav", self.out / "a.json"
        for home in (locked_home, locked_folder):
            with self.subTest(cache_home=home.name):
                env = {"XAI_API_KEY": KEY, "XDG_CACHE_HOME": str(home)}
                code, out, e, reqs = self.run_cli(
                    ["Hi there", "-o", str(a), "--words", str(j)], [tts_ok()], env=env)
                self.assertEqual((code, len(reqs)), (0, 1), e)
                self.assertTrue(a.is_file() and j.is_file())
                self.assertIn("WARNING: cache not updated (", e)
                self.assertNotIn(KEY, out + e)
                a.unlink()
                j.unlink()

    def test_output_failure_after_a_paid_request_keeps_the_cache_entry(self):
        a = self.out / "a.wav"
        a.mkdir()  # a folder where the file should go: the write fails after the request
        code, _, e, reqs = self.run_cli(["Hi there", "-o", str(a)], [tts_ok()])
        self.assertEqual((code, len(reqs)), (1, 1), e)
        a.rmdir()
        code, out, e, reqs = self.run_cli(["Hi there", "-o", str(a)])
        self.assertEqual((code, len(reqs)), (0, 0), e)
        self.assertIn("(cached)", out)
        self.assertTrue(a.is_file())

    def test_single_mode_creates_missing_folders(self):
        a = self.out / "new" / "deeper" / "a.wav"
        j = self.out / "other" / "a.json"
        code, _, e, reqs = self.run_cli(
            ["Hi there", "-o", str(a), "--words", str(j)], [tts_ok()])
        self.assertEqual((code, len(reqs)), (0, 1), e)
        self.assertTrue(a.is_file())
        self.assertTrue(j.is_file())

    def test_relative_output_names_in_the_working_folder(self):
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.out)
        code, _, e, reqs = self.run_cli(
            ["Hi there", "-o", "a.wav", "--words", "a.json"], [tts_ok()])
        self.assertEqual((code, len(reqs)), (0, 1), e)
        self.assertTrue((self.out / "a.wav").is_file())
        self.assertTrue((self.out / "a.json").is_file())

    def test_unusable_output_folder_fails_before_any_request(self):
        blocker = self.out / "blocker"
        blocker.write_text("not a folder")
        code, _, e, reqs = self.run_cli(
            ["Hi there", "-o", str(blocker / "sub" / "a.wav")], [tts_ok()])
        self.assertEqual((code, reqs), (1, []))
        self.assertIn("ERROR:", e)

    def test_audio_and_words_cannot_be_the_same_file(self):
        a = self.out / "a.wav"
        for words in (a, self.out / "A.WAV", self.out / "sub" / ".." / "a.wav"):
            with self.subTest(words=str(words)):
                code, _, e, reqs = self.run_cli(
                    ["Hi", "-o", str(a), "--words", str(words)], [tts_ok("Hi")])
                self.assertEqual((code, reqs), (2, []), e)
                self.assertIn("same file", e)
        self.assertFalse((self.out / "sub").exists())

    def assert_response_rejected(self, response, message, text="Hi there"):
        """The API sends `response`: exit 1 with an ERROR line, no files, no cache entry."""
        a = self.out / "a.wav"
        code, _, e, reqs = self.run_cli([text, "-o", str(a)], [response])
        self.assertEqual((code, len(reqs)), (1, 1), e)
        self.assertIn(f"ERROR: {message}", e)
        self.assertNotIn("Traceback", e)
        self.assertFalse(a.exists())
        self.assertFalse((self.cache / "grok-tts").exists())

    def test_nan_duration_is_an_api_error(self):
        response = tts_edit(tts_ok(), lambda env: env.update(duration=float("nan")))
        self.assert_response_rejected(response, "unexpected response shape: duration")

    def test_empty_word_list_is_an_api_error(self):
        response = tts_edit(tts_ok(), lambda env: env.update(
            audio_timestamps={"graph_chars": [], "graph_times": []}))
        self.assert_response_rejected(response, "unexpected response shape: no word timings")

    def test_malformed_timestamps_are_api_errors(self):
        def stamps(**change):
            return lambda env: env["audio_timestamps"].update(change)

        cases = {
            "non-numeric times": stamps(graph_times=[["a", "b"], ["c", "d"]]),
            "times that are not pairs": stamps(graph_times=[5, 6]),
            "characters that are not text": stamps(graph_chars=[1, 2]),
            "a time too large for a float": stamps(graph_times=[[0, 10 ** 400], [0, 1]]),
        }
        for name, edit in cases.items():
            with self.subTest(case=name):
                self.assert_response_rejected(
                    tts_edit(tts_ok("Hi"), edit),
                    "unexpected response shape: timestamps", text="Hi")

    def test_list_with_a_voice_that_is_not_an_object(self):
        body = json.dumps({"voices": ["x"]}).encode()
        code, _, e, reqs = self.run_cli(["--list"], [(200, {}, body)])
        self.assertEqual((code, len(reqs)), (1, 1), e)
        self.assertIn("ERROR: unexpected response shape", e)
        self.assertNotIn("Traceback", e)

    def test_timeout_must_be_a_positive_number(self):
        script = self.out / "s.json"
        script.write_text(json.dumps([{"id": "a", "text": "Hi"}]))
        keyless = {"XDG_CACHE_HOME": str(self.cache)}  # a key lookup would exit 1, not 2
        modes = {"single": ["Hi", "-o", str(self.out / "a.wav")],
                 "batch": ["--lines", str(script), "--project", str(self.out)],
                 "list": ["--list"]}
        for bad in ("0", "-1", "nan", "inf"):
            for mode, argv in modes.items():
                with self.subTest(timeout=bad, mode=mode):
                    code, _, e, reqs = self.run_cli([*argv, "--timeout", bad], env=keyless)
                    self.assertEqual((code, reqs), (2, []), e)
                    self.assertIn("--timeout", e)
        code, _, e, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav"), "--timeout", "5"], [tts_ok("Hi")])
        self.assertEqual((code, len(reqs)), (0, 1), e)

    def test_lang_with_a_trailing_newline_is_refused(self):
        code, _, e, reqs = self.run_cli(
            ["Hi", "-o", str(self.out / "a.wav"), "--lang", "en\n"], [tts_ok("Hi")])
        self.assertEqual((code, reqs), (2, []), e)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from helpers import StubServer, err, grok_tts, tts_ok

KEY = "test-key-zz"
TWO = [{"id": "intro", "text": "Hi there"}, {"id": "outro", "text": "Bye now"}]


class BatchTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.root = root
        self.home = root / "home"
        self.cache = root / "cache"
        self.proj = root / "proj"
        for d in (self.home, self.cache, self.proj):
            d.mkdir()
        self.env = {"XAI_API_KEY": KEY, "XDG_CACHE_HOME": str(self.cache)}

    def script(self, data):
        path = self.root / "script.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def run_cli(self, argv, responses=(), env=None):
        out, errbuf = io.StringIO(), io.StringIO()
        with StubServer(responses) as srv, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(errbuf):
            code = grok_tts.main(
                argv, api_base=srv.url, home=self.home,
                env=self.env if env is None else env, sleep=lambda s: None)
        return code, out.getvalue(), errbuf.getvalue(), srv.requests

    def batch(self, data, responses=(), extra=()):
        return self.run_cli(
            ["--lines", self.script(data), "--project", str(self.proj), *extra],
            responses)

    def words(self, name):
        return json.loads((self.proj / "voiceover" / name).read_text("utf-8"))

    def test_batch_writes_files_and_lines_json(self):
        code, out, e, _ = self.batch(
            TWO, [tts_ok("Hi there"), tts_ok("Bye now")])
        self.assertEqual(code, 0, e)
        vo = self.proj / "voiceover"
        for name in ("intro", "outro"):
            self.assertTrue((vo / f"{name}.wav").is_file())
            self.assertTrue((vo / f"{name}.words.json").is_file())
        lines = json.loads((vo / "lines.json").read_text("utf-8"))
        self.assertEqual(lines, [
            {"id": "intro", "audio": "voiceover/intro.wav",
             "words": self.words("intro.words.json")},
            {"id": "outro", "audio": "voiceover/outro.wav",
             "words": self.words("outro.words.json")},
        ])
        self.assertIn(f"Lines saved to: {(vo / 'lines.json').resolve()}", out)
        self.assertIn(f"intro: {(vo / 'intro.wav').resolve()}", out)
        self.assertEqual(e, "")
        self.assertNotIn(KEY, out + e)

    def test_batch_ids_validated(self):
        cases = ["a/b", "..", ".", "", "c:d", "a\\b"]
        for bad in cases:
            with self.subTest(id=bad):
                code, _, _, reqs = self.batch([{"id": bad, "text": "Hi"}])
                self.assertEqual((code, len(reqs)), (2, 0))
        with self.subTest(id="duplicate"):
            code, _, _, reqs = self.batch(
                [{"id": "x", "text": "Hi"}, {"id": "x", "text": "Yo"}])
            self.assertEqual((code, len(reqs)), (2, 0))
        for a, b in (("Intro", "intro"), ("caf\u00e9", "cafe\u0301")):
            with self.subTest(pair=(a, b)):
                code, _, e, reqs = self.batch(
                    [{"id": a, "text": "Hi"}, {"id": b, "text": "Yo"}],
                    [tts_ok("Hi"), tts_ok("Yo")])
                self.assertEqual((code, len(reqs)), (2, 0), e)

    def test_batch_out_dir_outside_project(self):
        code, _, _, reqs = self.batch(
            TWO, extra=["--out-dir", "../elsewhere"])
        self.assertEqual((code, len(reqs)), (2, 0))
        self.assertFalse((self.root / "elsewhere").exists())

    def test_batch_script_shape_errors(self):
        for bad in ({"id": "a"}, [{"id": "a"}], [{"id": 1, "text": "x"}], ["a"]):
            with self.subTest(script=bad):
                code, _, _, reqs = self.batch(bad)
                self.assertEqual((code, len(reqs)), (2, 0))

    def test_batch_tag_only_line_rejected(self):
        code, _, e, reqs = self.batch(
            [{"id": "ok", "text": "Hi"}, {"id": "laugh", "text": "[laugh]"}])
        self.assertEqual((code, len(reqs)), (2, 0))
        self.assertIn("laugh", e)

    def test_batch_stops_and_keeps_successes(self):
        code, _, _, reqs = self.batch(
            TWO, [tts_ok("Hi there"), err("empty_text")])
        vo = self.proj / "voiceover"
        self.assertEqual(code, 1)
        self.assertTrue((vo / "intro.wav").exists())
        self.assertFalse((vo / "lines.json").exists())
        code, out, e, reqs = self.batch(TWO, [tts_ok("Bye now")])
        self.assertEqual(code, 0, e)
        self.assertEqual(len(reqs), 1)
        self.assertIn(f"intro: {(vo / 'intro.wav').resolve()} (cached)", out)
        self.assertEqual(len(json.loads((vo / "lines.json").read_text("utf-8"))), 2)

    def test_batch_fully_cached_needs_no_key(self):
        self.batch(TWO, [tts_ok("Hi there"), tts_ok("Bye now")])
        code, _, e, reqs = self.run_cli(
            ["--lines", self.script(TWO), "--project", str(self.proj)],
            env={"XDG_CACHE_HOME": str(self.cache)})
        self.assertEqual((code, len(reqs)), (0, 0), e)

    def test_batch_lines_match_media_use_contract(self):
        code, _, e, _ = self.batch(TWO, [tts_ok("Hi there"), tts_ok("Bye now")])
        self.assertEqual(code, 0, e)
        lines = json.loads(
            (self.proj / "voiceover" / "lines.json").read_text("utf-8"))
        for line in lines:
            audio = line["audio"]
            self.assertNotIn("\\", audio)
            self.assertFalse(Path(audio).is_absolute())
            self.assertNotIn("..", PurePosixPath(audio).parts)
            self.assertTrue((self.proj / audio).is_file())
            self.assertTrue(line["words"])
            for w in line["words"]:
                self.assertIsInstance(w["text"], str)
                self.assertTrue(math.isfinite(w["start"]) and math.isfinite(w["end"]))
                self.assertLessEqual(w["start"], w["end"])


if __name__ == "__main__":
    unittest.main()

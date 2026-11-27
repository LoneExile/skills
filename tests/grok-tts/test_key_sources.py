import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import StubServer, grok_tts, tts_ok

LOGIN_KEY = "grok-login-key"


class KeySourceTests(unittest.TestCase):
    """Which key pays. A valid grok login sits in the home as a decoy: a lookup that
    falls through shows in the Authorization header the stub receives."""

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.home = root / "home"
        self.out = root / "out"
        self.cache = root / "cache"
        (self.home / ".omp").mkdir(parents=True)
        (self.home / ".grok").mkdir()
        self.out.mkdir()
        self.cache.mkdir()
        (self.home / ".grok" / "auth.json").write_text(json.dumps({
            "https://auth.x.ai::cid": {
                "key": LOGIN_KEY, "expires_at": "2099-01-01T00:00:00Z"}}))
        self.dotenv = self.home / ".omp" / ".env"
        self.env = {"XDG_CACHE_HOME": str(self.cache)}

    def run_cli(self, argv, responses=(), env=None):
        out, errbuf = io.StringIO(), io.StringIO()
        with StubServer(responses) as srv, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(errbuf):
            code = grok_tts.main(
                argv, api_base=srv.url, home=self.home,
                env=self.env if env is None else env, sleep=lambda s: None)
        return code, out.getvalue(), errbuf.getvalue(), srv.requests

    def speak(self, text="Hi there", env=None):
        return self.run_cli(
            [text, "-o", str(self.out / "a.wav")], [tts_ok(text)], env=env)

    def assert_dotenv_pays(self, key, raw):
        self.dotenv.write_bytes(raw)
        code, out, e, reqs = self.speak()
        self.assertEqual(code, 0, e)
        self.assertEqual(reqs[0][2]["Authorization"], f"Bearer {key}")
        self.assertIn("Key: ~/.omp/.env (XAI_API_KEY)\n", e)
        self.assertNotIn(key, out + e)

    def test_a_later_value_beats_an_empty_first_line(self):
        self.assert_dotenv_pays("k-real", b"XAI_API_KEY=\nXAI_API_KEY=k-real\n")

    def test_the_last_of_two_values_wins(self):
        self.assert_dotenv_pays("k-new", b"XAI_API_KEY=k-old\nXAI_API_KEY=k-new\n")

    def test_a_bom_before_the_first_line_is_ignored(self):
        self.assert_dotenv_pays("k-real", b"\xef\xbb\xbfXAI_API_KEY=k-real\n")

    def test_a_stray_byte_in_a_comment_is_harmless(self):
        self.assert_dotenv_pays("k-real", b"# caf\x92\nXAI_API_KEY=k-real\n")

    def test_a_damaged_key_stops_the_run(self):
        self.dotenv.write_bytes(b"XAI_API_KEY=k-\x92real\n")
        code, _, e, reqs = self.speak()
        self.assertEqual((code, reqs), (1, []))
        self.assertIn(
            "the key from ~/.omp/.env (XAI_API_KEY) is not a valid header value", e)

    def test_the_key_source_is_named_on_a_paid_run_only(self):
        cases = [
            ("environment", {"XAI_API_KEY": "k-env"}, None, "k-env",
             "XAI_API_KEY (environment)"),
            ("dotenv", {}, b"GROK_API_KEY=k-dot\n", "k-dot",
             "~/.omp/.env (GROK_API_KEY)"),
            ("login", {}, None, LOGIN_KEY, "grok CLI login (~/.grok/auth.json)"),
        ]
        for name, extra, dotenv, key, label in cases:
            with self.subTest(source=name):
                self.dotenv.unlink(missing_ok=True)
                if dotenv is not None:
                    self.dotenv.write_bytes(dotenv)
                text = f"Say {name}"
                env = dict(self.env, **extra)
                code, out, e, reqs = self.speak(text, env=env)
                self.assertEqual((code, len(reqs)), (0, 1), e)
                self.assertEqual(reqs[0][2]["Authorization"], f"Bearer {key}")
                self.assertEqual(e.count("Key: "), 1, e)
                self.assertIn(f"Key: {label}\n", e)
                self.assertNotIn(key, out + e)
                # The same request again comes from the cache and needs no key.
                code, out, e, reqs = self.speak(text, env=self.env)
                self.assertEqual((code, reqs), (0, []), e)
                self.assertIn("(cached)", out)
                self.assertNotIn("Key:", e)


if __name__ == "__main__":
    unittest.main()

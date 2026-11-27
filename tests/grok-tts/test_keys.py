import json, os, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from helpers import grok_tts

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)

class KeysTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
    def omp_env(self, text):
        (self.home / ".omp").mkdir(exist_ok=True); (self.home / ".omp/.env").write_text(text)
    def grok_login(self, key, expires):
        (self.home / ".grok").mkdir(exist_ok=True)
        (self.home / ".grok/auth.json").write_text(json.dumps(
            {"https://auth.x.ai::cid": {"key": key, "expires_at": expires}}))
    def resolve(self, env=None):
        return grok_tts.resolve_key(env or {}, self.home, NOW)

    def test_env_xai_first(self):
        self.omp_env("XAI_API_KEY=k4\n")
        self.assertEqual(self.resolve({"XAI_API_KEY": "k1", "GROK_API_KEY": "k2"}), ("k1", "XAI_API_KEY (environment)"))
    def test_env_grok_second_and_empty_is_unset(self):
        self.assertEqual(self.resolve({"XAI_API_KEY": "", "GROK_API_KEY": "k2"}), ("k2", "GROK_API_KEY (environment)"))
    def test_omp_env_file(self):
        self.omp_env('# c\nOTHER=1\nexport GROK_API_KEY="k3"\nXAI_API_KEY=\'k4\'\n')
        self.assertEqual(self.resolve(), ("k4", "~/.omp/.env (XAI_API_KEY)"))
        self.omp_env('export GROK_API_KEY="k3"\n')
        self.assertEqual(self.resolve(), ("k3", "~/.omp/.env (GROK_API_KEY)"))
    def test_grok_login_unexpired(self):
        self.grok_login("k5", "2026-12-01T00:00:00Z")
        self.assertEqual(self.resolve(), ("k5", "grok CLI login (~/.grok/auth.json)"))
    def test_grok_login_expired_reported_not_used(self):
        self.grok_login("k5-secret", "2026-10-07T12:33:48.337905Z")
        with self.assertRaises(grok_tts.KeyMissingError) as cm:
            self.resolve()
        msg = str(cm.exception)
        self.assertIn("expired at 2026-10-07T12:33:48", msg); self.assertNotIn("k5-secret", msg)
        self.assertEqual(cm.exception.exit_code, 1)
    def test_nothing_found_lists_every_source(self):
        with self.assertRaises(grok_tts.KeyMissingError) as cm:
            self.resolve()
        for part in ("XAI_API_KEY (environment): unset", "GROK_API_KEY (environment): unset",
                     "~/.omp/.env: file missing", "grok CLI login (~/.grok/auth.json): file missing"):
            self.assertIn(part, str(cm.exception))
    def test_unreadable_login_file_reported(self):
        (self.home / ".grok").mkdir(); (self.home / ".grok/auth.json").write_text("not json")
        with self.assertRaises(grok_tts.KeyMissingError) as cm:
            self.resolve()
        self.assertIn("unreadable", str(cm.exception))
    def test_project_env_ignored(self):
        cwd = tempfile.mkdtemp(); Path(cwd, ".env").write_text("XAI_API_KEY=k6\n")
        old = os.getcwd(); os.chdir(cwd)
        try:
            with self.assertRaises(grok_tts.KeyMissingError):
                self.resolve()
        finally:
            os.chdir(old)

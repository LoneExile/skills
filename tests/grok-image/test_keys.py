"""Key lookup: login first, then XAI_API_KEY, GROK_API_KEY, ~/.omp/.env."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from helpers import NOW, grok_image, iso, write_dotenv, write_login

LOGIN = grok_image.LOGIN_LABEL
MIN = 180


class KeyLookupTests(unittest.TestCase):
    def home(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def resolve(self, home, env=None):
        return grok_image.resolve_key(env or {}, home, NOW, MIN)

    def missing(self, home, env=None) -> str:
        with self.assertRaises(grok_image.KeyMissingError) as cm:
            self.resolve(home, env)
        return str(cm.exception)

    def test_long_login_wins(self):
        home = self.home()
        write_login(home, [("k-login", iso(3600))])
        write_dotenv(home, "XAI_API_KEY=k-dot\n")
        self.assertEqual(self.resolve(home, {"XAI_API_KEY": "k-env"}),
                         ("k-login", LOGIN, None))

    def test_expired_login_skipped_for_env_xai(self):
        home = self.home()
        write_login(home, [("k-login", iso(-60))])
        self.assertEqual(self.resolve(home, {"XAI_API_KEY": "k-env"}),
                         ("k-env", "XAI_API_KEY (environment)", None))

    def test_grok_env_after_xai(self):
        home = self.home()
        key, label, note = self.resolve(home, {"GROK_API_KEY": "k-g"})
        self.assertEqual((key, label, note),
                         ("k-g", "GROK_API_KEY (environment)", None))

    def test_empty_env_is_unset(self):
        home = self.home()
        write_dotenv(home, "XAI_API_KEY=k-dot\n")
        self.assertEqual(self.resolve(home, {"XAI_API_KEY": ""}),
                         ("k-dot", "~/.omp/.env (XAI_API_KEY)", None))

    def test_dotenv_rules(self):
        home = self.home()
        write_dotenv(home, "GROK_API_KEY=g1\nexport XAI_API_KEY=\"x1\"\nXAI_API_KEY='x2'\n")
        self.assertEqual(self.resolve(home),
                         ("x2", "~/.omp/.env (XAI_API_KEY)", None))

    def test_dotenv_grok_line(self):
        home = self.home()
        write_dotenv(home, "GROK_API_KEY=g1\n")
        self.assertEqual(self.resolve(home),
                         ("g1", "~/.omp/.env (GROK_API_KEY)", None))

    def test_key_source_precedence(self):
        home = self.home()
        write_dotenv(home, "XAI_API_KEY=k-dot-x\nGROK_API_KEY=k-dot-g\n")
        env = {"XAI_API_KEY": "k-env-x", "GROK_API_KEY": "k-env-g"}
        self.assertEqual(self.resolve(home, env),
                         ("k-env-x", "XAI_API_KEY (environment)", None))
        del env["XAI_API_KEY"]
        self.assertEqual(self.resolve(home, env),
                         ("k-env-g", "GROK_API_KEY (environment)", None))
        del env["GROK_API_KEY"]
        self.assertEqual(self.resolve(home, env),
                         ("k-dot-x", "~/.omp/.env (XAI_API_KEY)", None))
        write_dotenv(home, "GROK_API_KEY=k-dot-g\n")
        self.assertEqual(self.resolve(home, env),
                         ("k-dot-g", "~/.omp/.env (GROK_API_KEY)", None))

    def test_nothing_found_lists_sources(self):
        home = self.home()
        write_login(home, [("k-old", iso(-60))])
        write_dotenv(home, "OTHER=1\n")
        self.assertEqual(self.missing(home), (
            "no xAI key found. Checked:\n"
            f"  - grok CLI login (~/.grok/auth.json): expired at {iso(-60)}\n"
            "  - XAI_API_KEY (environment): unset\n"
            "  - GROK_API_KEY (environment): unset\n"
            "  - ~/.omp/.env: no XAI_API_KEY or GROK_API_KEY line\n"
            "Run `grok` to log in, set XAI_API_KEY, or add it to ~/.omp/.env."))
        self.assertNotIn("k-old", self.missing(home))

    def test_missing_files_reported(self):
        message = self.missing(self.home())
        self.assertIn("grok CLI login (~/.grok/auth.json): file missing", message)
        self.assertIn("~/.omp/.env: file missing", message)

    def test_short_login_passed_over_for_key(self):
        home = self.home()
        write_login(home, [("k-login", iso(100))])
        self.assertEqual(
            self.resolve(home, {"XAI_API_KEY": "k-env"}),
            ("k-env", "XAI_API_KEY (environment)",
             f"Note: the grok CLI login expires at {iso(100)}, too soon for this run; "
             "using XAI_API_KEY (environment)."))

    def test_short_login_last_resort(self):
        home = self.home()
        write_login(home, [("k-short", iso(100))])
        self.assertEqual(
            self.resolve(home),
            ("k-short", LOGIN,
             f"Note: the grok CLI login expires at {iso(100)}, before this run may end; "
             "no API key found, so using it anyway."))

    def test_first_counting_entry_wins(self):
        home = self.home()
        write_login(home, [("k-exp", iso(-5)), ("k-a", iso(3600)), ("k-b", iso(7200))])
        self.assertEqual(self.resolve(home)[0], "k-a")

    def test_missing_expiry_is_usable(self):
        home = self.home()
        write_login(home, [("k-none", None), ("k-a", iso(3600))])
        key, _, note = self.resolve(home)
        self.assertEqual((key, note), ("k-none", None))

    def test_z_and_naive_times(self):
        for stamp in ("2026-11-27T13:00:00Z", "2026-11-27T13:00:00"):
            home = self.home()
            write_login(home, [("k-t", stamp)])
            self.assertEqual(self.resolve(home), ("k-t", LOGIN, None), stamp)

    def test_unreadable_login_file(self):
        home = self.home()
        (home / ".grok" / "auth.json").mkdir(parents=True)
        self.assertIn("grok CLI login (~/.grok/auth.json): unreadable (IsADirectoryError)",
                      self.missing(home))
        home = self.home()
        (home / ".grok").mkdir()
        (home / ".grok" / "auth.json").write_text("{not json", encoding="utf-8")
        self.assertIn("grok CLI login (~/.grok/auth.json): unreadable (JSONDecodeError)",
                      self.missing(home))

    def test_project_dotenv_ignored(self):
        proj = self.home()
        (proj / ".env").write_text("XAI_API_KEY=k-proj\n", encoding="utf-8")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(proj)
        self.assertNotIn("k-proj", self.missing(self.home()))


if __name__ == "__main__":
    unittest.main()

"""generate_image: which key is sent, rejection handling, redaction."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import NOW, StubServer, err, grok_image, iso, write_dotenv, write_login


class GenerateImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "o" / "img.jpg"

    def run_gen(self, srv, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = grok_image.generate_image(
                "a cat", out=self.out, env=env, home=self.tmp, now=NOW,
                api_url=srv.url + "/v1/images/generations")
        self.assertEqual(stdout.getvalue(), "")
        return result, stderr.getvalue()

    @staticmethod
    def success(srv):
        return [
            (200, {}, json.dumps({"data": [{"url": srv.url + "/img.jpg"}]}).encode()),
            (200, {}, b"\xff\xd8fake"),
        ]

    def stub(self, responses):
        """A StubServer whose success responses may need the server's own URL."""
        srv = StubServer([])
        self.addCleanup(srv.__exit__)
        srv.__enter__()
        srv._responses = list(responses(srv) if callable(responses) else responses)
        return srv

    def test_success_sends_chosen_key(self):
        srv = self.stub(self.success)
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env-123456"})
        self.assertEqual(result, str(self.out.resolve()))
        self.assertTrue(self.out.exists())
        method, path, headers, body = srv.requests[0]
        self.assertEqual((method, path), ("POST", "/v1/images/generations"))
        self.assertEqual(headers["Authorization"], "Bearer k-env-123456")
        self.assertNotIn("Authorization", srv.requests[1][2])
        self.assertIn("Key: XAI_API_KEY (environment)", stderr)
        self.assertNotIn("k-env-123456", stderr)

    def test_expired_login_never_sent(self):
        write_login(self.tmp, [("k-old", iso(-60))])
        write_dotenv(self.tmp, "XAI_API_KEY=k-dot\n")
        srv = self.stub(self.success)
        result, _ = self.run_gen(srv, {})
        self.assertIsNotNone(result)
        self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer k-dot")
        for request in srv.requests:
            self.assertNotIn("k-old", json.dumps(request[2]))

    def test_rejected_login_stops(self):
        write_login(self.tmp, [("k-login-abcdef", iso(3600))])
        srv = self.stub([err("expired_oauth")])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn(
            "the key from grok CLI login (~/.grok/auth.json) was rejected (HTTP 403)",
            stderr)
        self.assertIn(
            "HINT: Run `grok` to log in again, or move ~/.grok/auth.json aside "
            "to use your API key.", stderr)
        self.assertNotIn("k-login-abcdef", stderr)
        self.assertNotIn("k-env", stderr)

    def test_rejected_key_hint(self):
        srv = self.stub([err("bad_key")])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn("was rejected (HTTP 400)", stderr)
        self.assertIn("HINT: Check the key at https://console.x.ai.", stderr)

    def test_echoed_key_redacted_even_across_cut(self):
        key = "K-SECRET-0123456789"
        srv = self.stub([(500, {}, ("x" * 390 + key).encode())])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": key})
        self.assertIsNone(result)
        self.assertIn("ERROR: xAI API HTTP 500", stderr)
        self.assertIn("[redacted]", stderr)
        self.assertNotIn("K-SECRET", stderr)
        self.assertNotIn("0123456789", stderr)

    def test_unusable_key_refused_before_request(self):
        srv = self.stub([])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k\nbad"})
        self.assertIsNone(result)
        self.assertEqual(srv.requests, [])
        self.assertIn(
            "ERROR: the key from XAI_API_KEY (environment) is not a valid header value",
            stderr)
        self.assertNotIn("k\nbad", stderr)

    def test_redirect_not_followed(self):
        srv = self.stub(lambda srv: [(302, {"Location": srv.url + "/elsewhere"}, b"{}")])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn("HTTP 302", stderr)

    def test_short_login_note_before_key_line(self):
        write_login(self.tmp, [("k-short", iso(100))])
        srv = self.stub(self.success)
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNotNone(result)
        self.assertIn("Note: the grok CLI login expires at", stderr)
        self.assertLess(stderr.index("Note:"), stderr.index("Key:"))

    def test_unexpected_response_redacted(self):
        key = "K-SECRET-0123456789"
        srv = self.stub([(200, {}, json.dumps("x" * 380 + key).encode())])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": key})
        self.assertIsNone(result)
        self.assertIn("unexpected API response", stderr)
        self.assertIn("[redacted]", stderr)
        self.assertNotIn("K-SECRET", stderr)
        self.assertNotIn("0123456789", stderr)

    def test_unwritable_output_stops_before_request(self):
        (self.tmp / "afile").write_text("x")
        self.out = self.tmp / "afile" / "img.jpg"
        srv = self.stub(self.success)
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 0)
        self.assertIn("ERROR: cannot write the output:", stderr)

    def test_other_400_is_not_a_rejection(self):
        srv = self.stub([(400, {}, b'{"error":"Argument not supported: size"}')])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn("ERROR: xAI API HTTP 400", stderr)
        self.assertNotIn("was rejected", stderr)
        self.assertNotIn("HINT:", stderr)

    def test_401_is_a_rejection(self):
        srv = self.stub([(401, {}, b'{"error":"unauthorized"}')])
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn(
            "the key from XAI_API_KEY (environment) was rejected (HTTP 401)", stderr)
        self.assertIn("HINT: Check the key at https://console.x.ai.", stderr)

    def test_default_now_is_timezone_aware(self):
        write_login(self.tmp, [("k-old", "2020-01-01T00:00:00Z")])
        srv = self.stub(self.success)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = grok_image.generate_image(
                "a cat", out=self.out, env={"XAI_API_KEY": "k-env"}, home=self.tmp,
                api_url=srv.url + "/v1/images/generations")
        self.assertIsNotNone(result)
        self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer k-env")

    def test_image_margin_is_timeout_plus_60(self):
        write_login(self.tmp, [("k-login", iso(150))])
        srv = self.stub(self.success)
        result, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNotNone(result)
        self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer k-env")
        self.assertIn(
            f"Note: the grok CLI login expires at {iso(150)}, too soon for this run; "
            "using XAI_API_KEY (environment).", stderr)


class CliContractTests(unittest.TestCase):
    def test_cli_failure_contract(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"HOME": tmp}, clear=True), \
                patch.object(sys, "argv", ["grok_image.py", "a cat"]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                grok_image.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(stdout.getvalue(), "")
        text = stderr.getvalue()
        self.assertTrue(text.startswith("ERROR: no xAI key found. Checked:"))
        self.assertEqual(text.splitlines()[-1], "Failed to generate image.")


if __name__ == "__main__":
    unittest.main()

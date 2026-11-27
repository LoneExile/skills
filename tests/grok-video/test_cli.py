"""generate_video: which key is sent, rejection handling, redaction."""
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

from helpers import (FakeClock, NOW, StubServer, err, grok_video, iso, write_dotenv,
                     write_login)

SUBMIT = (200, {}, b'{"request_id":"r1"}')


class GenerateVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "o" / "v.mp4"
        self.clock = FakeClock()
        self.sleeps = self.clock.sleeps

    def run_gen(self, srv, env, prompt="a cat", **kwargs):
        kwargs.setdefault("out", self.out)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = grok_video.generate_video(
                prompt, env=env, home=self.tmp, now=NOW,
                api_url=srv.url + "/v1/videos/generations",
                status_url=srv.url + "/v1/videos/{request_id}",
                sleep=self.clock.sleep, clock=self.clock, **kwargs)
        return result, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def success(srv):
        return [
            SUBMIT,
            (200, {}, b'{"status":"pending","progress":10}'),
            (200, {}, json.dumps({"status": "done", "progress": 100,
                                  "video": {"url": srv.url + "/v.mp4"}}).encode()),
            (200, {}, b"mp4"),
        ]

    def stub(self, responses):
        """A StubServer whose success responses may need the server's own URL."""
        srv = StubServer([])
        self.addCleanup(srv.__exit__)
        srv.__enter__()
        srv._responses = list(responses(srv) if callable(responses) else responses)
        return srv

    def test_success_polls_and_downloads(self):
        srv = self.stub(self.success)
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env-123456"})
        self.assertEqual(result, str(self.out.resolve()))
        self.assertEqual(self.out.read_bytes(), b"mp4")
        self.assertEqual(stdout.splitlines(), [
            "Submitted generation request r1 (this can take a minute)...",
            "  progress: 10%",
            "  progress: 100%",
        ])
        self.assertEqual(self.sleeps, [5])
        self.assertEqual(
            [(m, p) for m, p, _, _ in srv.requests],
            [("POST", "/v1/videos/generations"), ("GET", "/v1/videos/r1"),
             ("GET", "/v1/videos/r1"), ("GET", "/v.mp4")])
        for request in srv.requests[:3]:
            self.assertEqual(request[2]["Authorization"], "Bearer k-env-123456")
        self.assertNotIn("Authorization", srv.requests[3][2])
        self.assertNotIn("k-env-123456", stderr)

    def test_rejected_on_submit(self):
        write_login(self.tmp, [("k-login-abcdef", iso(3600))])
        srv = self.stub([err("expired_oauth")])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertEqual(stdout, "")
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

    def test_rejected_while_polling(self):
        srv = self.stub([SUBMIT, err("expired_oauth")])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 2)
        self.assertIn("Submitted generation request r1", stdout)
        self.assertIn(
            "the key from XAI_API_KEY (environment) was rejected (HTTP 403)", stderr)
        self.assertIn("HINT: Check the key at https://console.x.ai.", stderr)

    def test_poll_error_redacted(self):
        key = "K-SECRET-0123456789"
        srv = self.stub([SUBMIT, (500, {}, b"boom " + key.encode())])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": key})
        self.assertIsNone(result)
        self.assertIn("ERROR: poll HTTP 500", stderr)
        self.assertIn("[redacted]", stderr)
        self.assertNotIn("K-SECRET", stderr)

    def test_login_short_for_polling_passed_over(self):
        write_login(self.tmp, [("k-short", iso(300))])
        srv = self.stub(self.success)
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNotNone(result)
        for request in srv.requests[:3]:
            self.assertEqual(request[2]["Authorization"], "Bearer k-env")
        self.assertIn("Note: the grok CLI login expires at", stderr)
        self.assertLess(stderr.index("Note:"), stderr.index("Key: XAI_API_KEY (environment)"))

    def test_expired_login_never_sent(self):
        write_login(self.tmp, [("k-old", iso(-60))])
        write_dotenv(self.tmp, "XAI_API_KEY=k-dot\n")
        srv = self.stub(self.success)
        result, _, _ = self.run_gen(srv, {})
        self.assertIsNotNone(result)
        for request in srv.requests:
            self.assertNotIn("k-old", json.dumps(request[2]))

    def test_unusable_key_refused_before_request(self):
        srv = self.stub([])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k\nbad"})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(srv.requests, [])
        self.assertIn(
            "ERROR: the key from XAI_API_KEY (environment) is not a valid header value",
            stderr)
        self.assertNotIn("k\nbad", stderr)

    def test_redirect_not_followed(self):
        srv = self.stub(lambda srv: [(302, {"Location": srv.url + "/x"}, b"{}")])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)

    def test_other_400_is_not_a_rejection(self):
        srv = self.stub([(400, {}, b'{"error":"Argument not supported: size"}')])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn("ERROR: xAI API HTTP 400", stderr)
        self.assertNotIn("was rejected", stderr)
        self.assertNotIn("HINT:", stderr)

    def test_401_is_a_rejection(self):
        srv = self.stub([(401, {}, b'{"error":"unauthorized"}')])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 1)
        self.assertIn(
            "the key from XAI_API_KEY (environment) was rejected (HTTP 401)", stderr)
        self.assertIn("HINT: Check the key at https://console.x.ai.", stderr)

    def test_default_now_is_timezone_aware(self):
        write_login(self.tmp, [("k-old", "2020-01-01T00:00:00Z")])
        srv = self.stub(self.success)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = grok_video.generate_video(
                "a cat", out=self.out, env={"XAI_API_KEY": "k-env"}, home=self.tmp,
                api_url=srv.url + "/v1/videos/generations",
                status_url=srv.url + "/v1/videos/{request_id}",
                sleep=self.clock.sleep, clock=self.clock)
        self.assertIsNotNone(result)
        self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer k-env")

    def test_unexpected_response_redacted(self):
        key = "K-SECRET-0123456789"
        srv = self.stub([(200, {}, json.dumps("x" * 384 + key).encode())])
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": key})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertIn("unexpected API response", stderr)
        self.assertIn("[redacted]", stderr)
        self.assertNotIn("K-SECRET", stderr)
        self.assertNotIn("0123456789", stderr)

    def test_unwritable_output_stops_before_request(self):
        (self.tmp / "afile").write_text("x")
        self.out = self.tmp / "afile" / "v.mp4"
        srv = self.stub(self.success)
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 0)
        self.assertIn("ERROR: cannot write the output:", stderr)

    def test_output_that_is_a_folder_stops_before_request(self):
        key = "K-SECRET-0123456789"
        self.out = self.tmp / f"{key}.mp4"
        self.out.mkdir()
        srv = self.stub(self.success)
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": key})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 0)
        self.assertIn("ERROR: cannot write the output:", stderr)
        self.assertIn("[redacted]", stderr)
        self.assertNotIn("K-SECRET", stderr)
        self.assertNotIn("0123456789", stderr)

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0,
                     "a read-only folder stops only a non-root POSIX user")
    def test_read_only_folder_stops_before_request(self):
        folder = self.tmp / "readonly"
        folder.mkdir()
        folder.chmod(0o500)
        self.addCleanup(folder.chmod, 0o700)
        self.out = folder / "v.mp4"
        srv = self.stub(self.success)
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertEqual(stdout, "")
        self.assertIsNone(result)
        self.assertEqual(len(srv.requests), 0)
        self.assertIn("ERROR: cannot write the output:", stderr)
        self.assertIn("is not writable", stderr)

    def test_missing_prompt_with_an_image_still_names_the_file(self):
        image = self.tmp / "start.png"
        image.write_bytes(b"\x89PNG")
        srv = self.stub(self.success)
        result, _, stderr = self.run_gen(
            srv, {"XAI_API_KEY": "k-env"}, prompt=None, image=str(image),
            out=self.tmp / "vids")
        self.assertIsNotNone(result, stderr)
        self.assertTrue(result.endswith("_video.mp4"), result)
        body = srv.requests[0][3]
        self.assertNotIn("prompt", body)
        self.assertTrue(body["image"]["url"].startswith("data:image/png;base64,"))

    def test_non_http_download_url_refused(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("do not copy me")
        urls = (secret.as_uri(), secret.as_uri().replace("file:", "FILE:", 1),
                "ftp://127.0.0.1:1/v.mp4", "data:video/mp4;base64,AAAA")
        for url in urls:
            with self.subTest(url=url):
                body = json.dumps({"status": "done", "video": {"url": url}}).encode()
                srv = self.stub([SUBMIT, (200, {}, body)])
                result, _, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
                self.assertIsNone(result)
                self.assertIn(
                    "ERROR: refusing to download from a non-HTTP(S) URL: " + url, stderr)
                self.assertFalse(self.out.exists())
                self.assertEqual(len(srv.requests), 2)

    def test_http_url_accepts_only_http_and_https(self):
        for url in ("http://127.0.0.1:1/v.mp4", "https://vidgen.x.ai/v.mp4",
                    "HTTPS://vidgen.x.ai/v.mp4"):
            self.assertTrue(grok_video._http_url(url), url)
        for url in ("file:///etc/hosts", "ftp://h/v.mp4", "data:text/plain,x", "httpx://h",
                    "http:/h", " http://h", "", None, 5):
            self.assertFalse(grok_video._http_url(url), url)

    def test_video_margin_includes_max_wait(self):
        write_login(self.tmp, [("k-login", iso(700))])
        srv = self.stub(self.success)
        result, stdout, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNotNone(result)
        self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer k-env")
        self.assertIn("too soon for this run", stderr)

    def poll_stub(self, *bodies):
        return self.stub([SUBMIT] + [(200, {}, b) for b in bodies])

    def test_failed_with_non_dict_error_does_not_raise(self):
        srv = self.poll_stub(b'{"status":"failed","error":"boom"}')
        result, _, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertIn("ERROR: generation failed", stderr)
        self.assertIn("boom", stderr)

    def test_done_with_non_dict_video_does_not_raise(self):
        srv = self.poll_stub(b'{"status":"done","video":"x"}')
        result, _, stderr = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
        self.assertIsNone(result)
        self.assertIn("ERROR: done but no video url", stderr)

    def test_non_object_poll_body_counts_as_pending(self):
        srv = self.stub([SUBMIT] + [(200, {}, b"[]")] * 3)
        result, _, stderr = self.run_gen(
            srv, {"XAI_API_KEY": "k-env"}, max_wait=15, poll_interval=5)
        self.assertIsNone(result)
        self.assertEqual(self.sleeps, [5, 5, 5])
        self.assertIn("ERROR: timed out after 15s", stderr)

    def test_every_printed_server_text_is_redacted(self):
        key = "K-SECRET-0123456789"
        body = ("echo " + key).encode()
        cases = {
            "submit 403": [(403, {}, body)],
            "poll 403": [SUBMIT, (403, {}, body)],
            "failed message": [SUBMIT, (200, {}, json.dumps(
                {"status": "failed", "error": {"code": "x", "message": key}}).encode())],
            "done no url": [SUBMIT, (200, {}, json.dumps(
                {"status": "done", "note": key}).encode())],
            "unknown status": [SUBMIT, (200, {}, json.dumps({"status": key}).encode())],
        }
        for name, responses in cases.items():
            with self.subTest(name):
                srv = self.stub(responses)
                result, _, stderr = self.run_gen(srv, {"XAI_API_KEY": key})
                self.assertIsNone(result)
                self.assertIn("[redacted]", stderr)
                self.assertNotIn("K-SECRET", stderr)
                self.assertNotIn("0123456789", stderr)

    def test_margin_bracketed_at_780(self):
        for offset, sent in ((770, "k-env"), (790, "k-login")):
            with self.subTest(offset=offset):
                write_login(self.tmp, [("k-login", iso(offset))])
                srv = self.stub(self.success)
                result, _, _ = self.run_gen(srv, {"XAI_API_KEY": "k-env"})
                self.assertIsNotNone(result)
                self.assertEqual(srv.requests[0][2]["Authorization"], f"Bearer {sent}")

    def test_max_wait_of_zero_or_less_refused_before_anything(self):
        for max_wait in (0, -5):
            with self.subTest(max_wait=max_wait):
                srv = self.stub(self.success)
                result, stdout, stderr = self.run_gen(
                    srv, {"XAI_API_KEY": "k-env"}, max_wait=max_wait)
                self.assertIsNone(result)
                self.assertEqual(srv.requests, [])
                self.assertEqual(stdout, "")
                self.assertEqual(
                    stderr, "ERROR: --max-wait must be more than 0 seconds.\n")

    def test_poll_budget_is_wall_clock(self):
        deadline = self.clock() + 20
        starts = []

        def slow_poll(method, path):  # every poll takes 4 s of its own
            if path.startswith("/v1/videos/r1"):
                starts.append(self.clock())
                self.clock.advance(4)

        srv = self.stub([SUBMIT] + [(200, {}, b'{"status":"pending"}')] * 6)
        srv.on_request = slow_poll
        result, _, stderr = self.run_gen(
            srv, {"XAI_API_KEY": "k-env"}, max_wait=20, poll_interval=5)
        self.assertIsNone(result)
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(s < deadline for s in starts), starts)
        self.assertTrue(all(0 <= s <= 5 for s in self.sleeps), self.sleeps)
        self.assertIn("ERROR: timed out after 20s waiting for generation.", stderr)

    def test_last_sleep_never_runs_past_the_deadline(self):
        start = self.clock()
        srv = self.stub([SUBMIT] + [(200, {}, b'{"status":"pending"}')] * 4)
        result, _, stderr = self.run_gen(
            srv, {"XAI_API_KEY": "k-env"}, max_wait=7, poll_interval=5)
        self.assertIsNone(result)
        self.assertEqual(self.sleeps, [5, 2])
        self.assertEqual(self.clock() - start, 7)
        self.assertIn("ERROR: timed out after 7s waiting for generation.", stderr)

    def test_submit_time_counts_against_max_wait(self):
        def slow_submit(method, path):  # the submit itself takes 10 s
            if method == "POST":
                self.clock.advance(10)

        srv = self.stub([SUBMIT] + [(200, {}, b'{"status":"pending"}')] * 3)
        srv.on_request = slow_submit
        result, _, stderr = self.run_gen(
            srv, {"XAI_API_KEY": "k-env"}, max_wait=12, poll_interval=5)
        self.assertIsNone(result)
        self.assertEqual([m for m, _, _, _ in srv.requests], ["POST", "GET"])
        self.assertEqual(self.sleeps, [2])
        self.assertIn("ERROR: timed out after 12s waiting for generation.", stderr)


class CliContractTests(unittest.TestCase):
    def test_cli_failure_contract(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"HOME": tmp}, clear=True), \
                patch.object(sys, "argv", ["grok_video.py", "a cat"]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                grok_video.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(stdout.getvalue(), "")
        text = stderr.getvalue()
        self.assertTrue(text.startswith("ERROR: no xAI key found. Checked:"))
        self.assertEqual(text.splitlines()[-1], "Failed to generate video.")


if __name__ == "__main__":
    unittest.main()

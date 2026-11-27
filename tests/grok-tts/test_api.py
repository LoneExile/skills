import json
import socket
import unittest

from helpers import StubServer, err, grok_tts, tts_edit, tts_ok

KEY = "test-key-zz"
SRC = "GROK_API_KEY (environment)"


def call(srv, sleeps=None, **kw):
    sleeps = [] if sleeps is None else sleeps
    args = dict(voice="eve", lang="en", speed=1.0, normalize=False, fmt="wav",
                key=KEY, source=SRC, api_base=srv.url, sleep=sleeps.append)
    args.update(kw)
    return grok_tts.synthesize("Hi there", **args)


class ApiTests(unittest.TestCase):
    def test_request_body_and_auth(self):
        with StubServer([tts_ok()]) as srv:
            result = call(srv)
        method, path, headers, body = srv.requests[0]
        self.assertEqual((method, path), ("POST", "/v1/tts"))
        self.assertEqual(headers["Authorization"], "Bearer test-key-zz")
        self.assertEqual(body, {
            "text": "Hi there", "voice_id": "eve", "language": "en",
            "speed": 1.0, "with_timestamps": True,
            "output_format": {"codec": "wav", "sample_rate": 44100}})
        self.assertEqual(result.duration, 0.5)
        self.assertEqual([w["text"] for w in result.words], ["Hi", "there"])
        self.assertEqual(int.from_bytes(result.audio[4:8], "little"),
                         len(result.audio) - 8)

    def test_normalize_and_mp3_body(self):
        with StubServer([tts_ok(fmt="mp3")]) as srv:
            call(srv, normalize=True, fmt="mp3")
        body = srv.requests[0][3]
        self.assertIs(body["text_normalization"], True)
        self.assertEqual(body["output_format"],
                         {"codec": "mp3", "sample_rate": 44100, "bit_rate": 192000})

    def test_retry_after_429(self):
        sleeps = []
        with StubServer([(429, {"Retry-After": "0"}, b'{"error":"rate limited"}'),
                         tts_ok()]) as srv:
            call(srv, sleeps)
        self.assertEqual(sleeps, [0.0])
        self.assertEqual(len(srv.requests), 2)

    def test_backoff_on_503_exhausts(self):
        sleeps = []
        with StubServer([(503, {}, b'{"error":"busy"}')] * 4) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv, sleeps, rand=lambda: 0)
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("after 4 attempts", str(cm.exception))
        self.assertEqual(sleeps, [2, 4, 8])
        self.assertEqual(len(srv.requests), 4)

    def _rejected(self, name):
        with StubServer([err(name)]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv)
        self.assertEqual(len(srv.requests), 1)
        return str(cm.exception)

    def test_bad_key_400_not_retried(self):
        msg = self._rejected("bad_key")
        self.assertIn("the key from GROK_API_KEY (environment) was rejected", msg)
        self.assertNotIn(KEY, msg)

    def test_expired_oauth_403_rejected(self):
        self.assertIn("the key from GROK_API_KEY (environment) was rejected",
                      self._rejected("expired_oauth"))

    def test_unknown_voice_hint(self):
        with StubServer([err("unknown_voice")]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv, voice="nonexistent-voice-zz")
        self.assertIn("not found", str(cm.exception))
        self.assertIn("--list", str(cm.exception))
        self.assertEqual(len(srv.requests), 1)

    def test_other_400_shows_api_error(self):
        self.assertIn("Field `text` must not be empty", self._rejected("empty_text"))

    def test_bad_shape_raises(self):
        with StubServer([(200, {}, b'{"audio":"eA=="}')]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv)
        self.assertIn("unexpected response", str(cm.exception))

    def test_duration_mismatch_raises_audio_error(self):
        status, headers, raw = tts_ok(seconds=0.5)
        env = json.loads(raw)
        env["duration"] = 6.39
        with StubServer([(status, headers, json.dumps(env).encode())]) as srv:
            with self.assertRaises(grok_tts.AudioError):
                call(srv)

    def test_unreachable_host_retries_then_fails(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        sleeps = []
        with self.assertRaises(grok_tts.ApiError) as cm:
            grok_tts.synthesize(
                "Hi", voice="eve", lang="en", speed=1.0, normalize=False,
                fmt="wav", key=KEY, source=SRC,
                api_base=f"http://127.0.0.1:{port}", sleep=sleeps.append,
                rand=lambda: 0)
        self.assertIn("could not reach", str(cm.exception))
        self.assertEqual(sleeps, [2, 4, 8])

    def test_list_voices(self):
        with StubServer([err("voices")]) as srv:
            result = grok_tts.list_voices(key=KEY, source=SRC, api_base=srv.url)
        self.assertEqual([v["voice_id"] for v in result], ["eve", "rex"])
        self.assertEqual(srv.requests[0][1], "/v1/tts/voices")

    def test_unusable_key_is_a_static_error(self):
        for bad in ("secret-zz\nX-Evil: 1", "secret-zz\u2603"):
            with self.subTest(key=repr(bad)):
                with StubServer([tts_ok()]) as srv:
                    with self.assertRaises(grok_tts.ApiError) as cm:
                        call(srv, key=bad)
                exc = cm.exception
                self.assertNotIn("secret-zz", str(exc))
                self.assertNotIn("secret-zz", repr(exc))
                self.assertIn(SRC, str(exc))
                self.assertIsNone(exc.__cause__)
                self.assertTrue(exc.__suppress_context__)
                self.assertIsNone(exc.__context__)
                self.assertEqual(srv.requests, [])

    def test_truncated_body_is_retried_as_unreachable(self):
        for status in (200, 503):
            with self.subTest(status=status):
                sleeps = []
                short = (status, {"Content-Length": "1000"}, b'{"audio"')
                with StubServer([short] * 4) as srv:
                    with self.assertRaises(grok_tts.ApiError) as cm:
                        call(srv, sleeps, rand=lambda: 0)
                self.assertIn("could not reach", str(cm.exception))
                self.assertEqual(sleeps, [2, 4, 8])
                self.assertEqual(len(srv.requests), 4)

    def _assert_key_hidden(self, exc):
        self.assertNotIn(KEY, str(exc))
        self.assertNotIn(KEY, repr(exc))
        self.assertIn("[redacted]", str(exc))
        self.assertIsNone(exc.__cause__)
        self.assertIsNone(exc.__context__)

    def test_echoed_key_is_redacted_400(self):
        body = json.dumps({"error": f"Incorrect API key provided: {KEY}"}).encode()
        with StubServer([(400, {}, body)]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv)
        self._assert_key_hidden(cm.exception)

    def test_echoed_key_is_redacted_503(self):
        body = json.dumps({"error": f"busy for {KEY}"}).encode()
        with StubServer([(503, {}, body)] * 4) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv, rand=lambda: 0)
        self._assert_key_hidden(cm.exception)

    def test_transport_reason_is_redacted(self):
        import urllib.error
        import urllib.request
        orig = urllib.request.OpenerDirector.open

        def boom(self, *a, **k):
            raise urllib.error.URLError(f"boom {KEY}")

        urllib.request.OpenerDirector.open = boom
        try:
            with StubServer([]) as srv:
                with self.assertRaises(grok_tts.ApiError) as cm:
                    call(srv, rand=lambda: 0)
        finally:
            urllib.request.OpenerDirector.open = orig
        self.assertIn("could not reach", str(cm.exception))
        self._assert_key_hidden(cm.exception)

    def test_send_time_bad_header_is_static_error(self):
        orig = grok_tts._usable_header_value
        grok_tts._usable_header_value = lambda key: True
        try:
            with StubServer([tts_ok()]) as srv:
                with self.assertRaises(grok_tts.ApiError) as cm:
                    call(srv, key="secret-zz\nX: 1")
        finally:
            grok_tts._usable_header_value = orig
        exc = cm.exception
        self.assertNotIn("secret-zz", str(exc))
        self.assertNotIn("secret-zz", repr(exc))
        self.assertIn(SRC, str(exc))
        self.assertIsNone(exc.__cause__)
        self.assertTrue(exc.__suppress_context__)
        self.assertEqual(srv.requests, [])

    def test_redirect_is_not_followed(self):
        with StubServer([(302, {"Location": "http://127.0.0.1:1/x"}, b"")]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv)
        self.assertEqual(cm.exception.status, 302)
        self.assertEqual(len(srv.requests), 1)

    def test_unusable_duration_is_an_api_error(self):
        for bad in (float("nan"), float("inf"), 0, -1):
            with self.subTest(duration=bad):
                response = tts_edit(tts_ok(), lambda env: env.update(duration=bad))
                with StubServer([response]) as srv:
                    with self.assertRaises(grok_tts.ApiError) as cm:
                        call(srv)
                self.assertIn("unexpected response shape: duration", str(cm.exception))

    def test_no_words_is_an_api_error(self):
        response = tts_edit(tts_ok(), lambda env: env.update(
            audio_timestamps={"graph_chars": [], "graph_times": []}))
        with StubServer([response]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv)
        self.assertIn("no word timings", str(cm.exception))

    def test_malformed_timestamps_raise_a_plain_api_error(self):
        response = tts_edit(tts_ok("Hi"), lambda env: env["audio_timestamps"].update(
            graph_times=[["a", "b"], ["c", "d"]]))
        with StubServer([response]) as srv:
            with self.assertRaises(grok_tts.ApiError) as cm:
                call(srv)
        exc = cm.exception
        self.assertEqual(str(exc), "unexpected response shape: timestamps")
        self.assertIsNone(exc.__cause__)
        self.assertIsNone(exc.__context__)

    def test_unusable_timestamps_stay_timing_errors(self):
        response = tts_edit(tts_ok("Hi"), lambda env: env["audio_timestamps"].update(
            graph_times=[[0, 1]]))
        with StubServer([response]) as srv:
            with self.assertRaises(grok_tts.TimingError):
                call(srv)


if __name__ == "__main__":
    unittest.main()

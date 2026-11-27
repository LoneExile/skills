from helpers import grok_tts, load_fixture
import unittest

def ints(text):  # chars with whole-second times: char i spans [i, i+1]
    return list(text), [[i, i + 1] for i in range(len(text))]

class WordsTest(unittest.TestCase):
    def test_tagged_fixture_words(self):
        ts = load_fixture("tagged_envelope.json")["audio_timestamps"]
        got = grok_tts.build_words(ts["graph_chars"], ts["graph_times"])
        expected = [("It's", 0.08, 0.22), ("2026,", 0.3, 1.62), ("Dr.", 1.7, 2.03),
                    ("Smith", 2.06, 2.4), ("and", 3.31, 3.57), ("this", 4.23, 4.41),
                    ("part", 4.49, 4.73), ("is", 4.77, 4.87), ("quiet.", 4.93, 5.39),
                    ("Done!", 5.91, 6.39)]
        self.assertEqual(got, [{"id": f"w{i}", "text": t, "start": s, "end": e}
                               for i, (t, s, e) in enumerate(expected)])
    def test_tag_glued_between_words_splits_them(self):
        self.assertEqual(grok_tts.build_words(*ints("a[pause]b")),
            [{"id": "w0", "text": "a", "start": 0, "end": 1},
             {"id": "w1", "text": "b", "start": 8, "end": 9}])
    def test_unknown_brackets_stay(self):
        self.assertEqual([w["text"] for w in grok_tts.build_words(*ints("[1] go [sic]"))],
                         ["[1]", "go", "[sic]"])
    def test_uppercase_tag_is_not_a_tag(self):
        self.assertEqual([w["text"] for w in grok_tts.build_words(*ints("[Pause] x"))], ["[Pause]", "x"])
    def test_only_tags_gives_no_words(self):
        self.assertEqual(grok_tts.build_words(*ints("[laugh] <soft></soft>")), [])
    def test_length_mismatch_raises(self):
        with self.assertRaises(grok_tts.TimingError):
            grok_tts.build_words(["a", "b"], [[0, 1]])
    def test_end_before_start_raises(self):
        with self.assertRaises(grok_tts.TimingError):
            grok_tts.build_words(["a"], [[2, 1]])
    def test_non_finite_time_raises(self):
        with self.assertRaises(grok_tts.TimingError):
            grok_tts.build_words(["a"], [[0, float("nan")]])
    def test_strip_tags(self):
        self.assertEqual(grok_tts.strip_tags("Hi <whisper>there</whisper> [pause]"), "Hi there ")
        self.assertEqual(grok_tts.strip_tags("[laugh]").strip(), "")

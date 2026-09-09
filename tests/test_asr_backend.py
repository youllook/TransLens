"""辨識引擎切換（asr_backend）：選 URL、不送 prompt、依引擎取 interval、解析回應。

全部離線 —— 不需要 whisper-server 也不需要 Parakeet 服務。真的打服務的
對照驗收在 tests/test_parakeet_replay.py。

執行：python -m pytest tests/test_asr_backend.py -v
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import audio_subtitle as audio  # noqa: E402
from engines import streaming  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "parakeet_verbose_json.json")


def load_fixture():
    """錄下來的真實 Parakeet 回應（8179 服務、seg.wav 的前 5 秒與整段 15 秒）。"""
    with io.open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class TestBackendSelection(unittest.TestCase):
    """設定 -> 引擎 / URL。"""

    def test_default_is_whisper(self):
        self.assertEqual(audio.asr_backend({}), audio.WHISPER_BACKEND)
        self.assertEqual(audio.asr_backend(None), audio.WHISPER_BACKEND)

    def test_unknown_value_falls_back_to_whisper(self):
        # 打錯字不該讓字幕整個壞掉，退回預設引擎就好
        for bad in ("PARAKEET_TYPO", "", "  ", "gpt", None, 5):
            self.assertEqual(audio.asr_backend({"asr_backend": bad}),
                             audio.WHISPER_BACKEND, f"值 {bad!r}")

    def test_parakeet_is_case_insensitive(self):
        for good in ("parakeet", "Parakeet", " PARAKEET "):
            self.assertEqual(audio.asr_backend({"asr_backend": good}),
                             audio.PARAKEET_BACKEND, f"值 {good!r}")

    def test_url_follows_backend(self):
        cfg = {"whisper_server_url": "http://w/inference",
               "parakeet_server_url": "http://p/inference"}
        self.assertEqual(audio.asr_url(dict(cfg)), "http://w/inference")
        self.assertEqual(audio.asr_url(dict(cfg, asr_backend="parakeet")),
                         "http://p/inference")

    def test_url_defaults_when_unset(self):
        # 沒設 parakeet_server_url 也要能用（預設 8179）
        self.assertEqual(audio.asr_url({"asr_backend": "parakeet"}),
                         audio.DEFAULTS["parakeet_server_url"])
        self.assertEqual(audio.asr_url({}), audio.DEFAULTS["whisper_server_url"])
        # 兩個引擎用不同的埠，不該指到同一個地方
        self.assertNotEqual(audio.DEFAULTS["whisper_server_url"],
                            audio.DEFAULTS["parakeet_server_url"])

    def test_empty_url_string_falls_back_to_default(self):
        self.assertEqual(audio.asr_url({"asr_backend": "parakeet",
                                        "parakeet_server_url": ""}),
                         audio.DEFAULTS["parakeet_server_url"])

    def test_label_round_trip(self):
        for label, value in audio.ASR_BACKENDS:
            self.assertEqual(audio.asr_backend_label(value), label)


class TestPromptGating(unittest.TestCase):
    """Parakeet 不吃 prompt（CTC/TDT 沒有 prompt 條件化）。"""

    def test_backend_wants_prompt(self):
        self.assertTrue(audio.backend_wants_prompt(audio.WHISPER_BACKEND))
        self.assertFalse(audio.backend_wants_prompt(audio.PARAKEET_BACKEND))

    def _post_data(self, cfg):
        """跑一次 recognize_and_translate，回傳實際 POST 出去的 form 欄位。"""
        seen = {}

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"text": "テスト。"}

        def fake_post(url, files=None, data=None, timeout=None):
            seen["url"] = url
            seen["data"] = dict(data or {})
            return FakeResp()

        with mock.patch.object(audio.requests, "post", fake_post), \
                mock.patch("engines.translator.translate",
                           lambda *a, **k: ("譯文", "ja", _Info())), \
                mock.patch("engines.translator.load_glossary",
                           lambda *a, **k: (None, "")):
            audio.recognize_and_translate(b"RIFFfake", cfg, language="ja",
                                          duration=5.0)
        return seen

    def test_whisper_sends_prompt(self):
        seen = self._post_data({"whisper_server_url": "http://w/inference"})
        self.assertIn("prompt", seen["data"],
                      "whisper 要帶標點種子 prompt（串流靠標點 flush）")
        self.assertTrue(seen["data"]["prompt"].strip())
        self.assertEqual(seen["url"], "http://w/inference")

    def test_parakeet_omits_prompt(self):
        seen = self._post_data({"asr_backend": "parakeet",
                                "parakeet_server_url": "http://p/inference"})
        self.assertNotIn("prompt", seen["data"],
                         "Parakeet 不吃 prompt，不該送出去")
        self.assertEqual(seen["url"], "http://p/inference")

    def test_explicit_prompt_still_dropped_for_parakeet(self):
        """呼叫端硬塞 prompt 也要擋掉 —— worker 會把組好的 prompt 傳進來。"""
        seen = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"text": "テスト。"}

        def fake_post(url, files=None, data=None, timeout=None):
            seen["data"] = dict(data or {})
            return FakeResp()

        with mock.patch.object(audio.requests, "post", fake_post), \
                mock.patch("engines.translator.translate",
                           lambda *a, **k: ("譯文", "ja", _Info())), \
                mock.patch("engines.translator.load_glossary",
                           lambda *a, **k: (None, "")):
            audio.recognize_and_translate(b"RIFFfake", {"asr_backend": "parakeet"},
                                          prompt="はい、そうですね。",
                                          language="ja", duration=5.0)
        self.assertNotIn("prompt", seen["data"])


class _Info:
    backend = "fake"
    fallback = False


class TestStreamInterval(unittest.TestCase):
    """stream_interval_sec 為 null 時依引擎取預設。"""

    def test_defaults_per_backend(self):
        self.assertEqual(audio.stream_interval_sec({}), 1.0)
        self.assertEqual(audio.stream_interval_sec({"asr_backend": "parakeet"}), 0.5)

    def test_parakeet_is_faster_than_whisper(self):
        """Parakeet 每輪只要 0.25 秒，間隔一定要比 whisper 短才划算。"""
        self.assertLess(audio.BACKEND_STREAM_INTERVAL[audio.PARAKEET_BACKEND],
                        audio.BACKEND_STREAM_INTERVAL[audio.WHISPER_BACKEND])

    def test_explicit_value_wins(self):
        for backend in ("whisper", "parakeet"):
            self.assertEqual(
                audio.stream_interval_sec({"asr_backend": backend,
                                           "stream_interval_sec": 2.5}), 2.5)

    def test_bad_values_fall_back_to_backend_default(self):
        for bad in ("abc", -1, 0, [], {}):
            self.assertEqual(
                audio.stream_interval_sec({"asr_backend": "parakeet",
                                           "stream_interval_sec": bad}), 0.5,
                f"值 {bad!r}")

    def test_session_cfg_fills_interval(self):
        s = streaming.StreamingSession(
            audio.stream_session_cfg({"asr_backend": "parakeet"}))
        self.assertEqual(s.interval, 0.5)
        s = streaming.StreamingSession(audio.stream_session_cfg({}))
        self.assertEqual(s.interval, 1.0)

    def test_session_cfg_does_not_mutate_caller(self):
        cfg = {"asr_backend": "parakeet"}
        audio.stream_session_cfg(cfg)
        self.assertNotIn("stream_interval_sec", cfg,
                         "不該把算出來的預設寫回使用者的設定")


class TestParseParakeetResponse(unittest.TestCase):
    """用錄下來的真實回應驗證解析 —— TransLens 端不用改解析邏輯。"""

    @classmethod
    def setUpClass(cls):
        cls.fx = load_fixture()

    def _transcribe_words(self, payload):
        """把 fixture 餵進 transcribe_words()，回傳它的輸出。"""
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        with mock.patch.object(audio.requests, "post",
                               lambda *a, **k: FakeResp()):
            return audio.transcribe_words(b"RIFFfake", "http://p/inference", "ja")

    def test_shape_matches_whisper(self):
        """回應要有頂層 text 與 segments[].words[]，欄位名與 whisper 一致。"""
        payload = self.fx["seg15"]
        self.assertIn("text", payload)
        self.assertTrue(payload["segments"])
        w = payload["segments"][0]["words"][0]
        for key in ("word", "start", "end"):
            self.assertIn(key, w, f"word 缺少 {key} 欄位")

    def test_transcribe_words_parses_it(self):
        words, text, lang = self._transcribe_words(self.fx["seg15"])
        self.assertIn("廊下", text)
        self.assertIn("早く", text)
        self.assertEqual(lang, "ja")
        self.assertTrue(words, "應該攤平出 words")
        self.assertTrue(all("start" in w and "end" in w for w in words))

    def test_timestamps_are_monotonic(self):
        words, _, _ = self._transcribe_words(self.fx["seg15"])
        starts = [float(w["start"]) for w in words]
        self.assertEqual(starts, sorted(starts), "時間戳應該遞增")

    def test_flatten_into_streaming_tokens(self):
        """streaming.flatten_words 吃得下 Parakeet 的 words（這是關鍵相容點）。"""
        words, text, _ = self._transcribe_words(self.fx["seg15"])
        tokens = streaming.flatten_words(words, text)
        self.assertEqual("".join(t.ch for t in tokens), text)
        # 沒有 whisper.cpp 那種被切壞的 U+FFFD
        self.assertNotIn(streaming.REPLACEMENT, text)

    def test_result_grows_monotonically(self):
        """Parakeet 的核心價值：音訊變長，結果只往後長，不改前面。

        這正是 LocalAgreement-2 需要的性質 —— whisper 在這裡會失敗
        （5 秒說「じゃあ次はもう一度」，15 秒完全換一句）。
        """
        short = streaming.normalize(self.fx["seg5"]["text"])
        full = streaming.normalize(self.fx["seg15"]["text"])
        # 5 秒的結果去掉句尾標點後，應該是 15 秒結果的前綴
        stem = short.rstrip(".!?,")
        self.assertTrue(full.startswith(stem[:8]),
                        f"5 秒的結果不是 15 秒的前綴：{short!r} vs {full!r}")

    def test_json_format_returns_text_only(self):
        """response_format=json 只要頂層 text，不必有 segments。"""
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"text": "うんとじゃあ次の問題を。"}

        with mock.patch.object(audio.requests, "post", lambda *a, **k: FakeResp()):
            text = audio.transcribe(b"RIFFfake", "http://p/inference", "ja")
        self.assertEqual(text, "うんとじゃあ次の問題を。")

    def test_error_payload_is_not_mistaken_for_text(self):
        """服務回 {"error": ...} 時不該被當成辨識到的文字。"""
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"error": "en 模型無法載入"}

        with mock.patch.object(audio.requests, "post", lambda *a, **k: FakeResp()):
            words, text, _ = audio.transcribe_words(b"RIFFfake", "http://p/inference", "en")
        self.assertEqual(text, "")
        self.assertEqual(words, [])


class TestStripEchoWithParakeet(unittest.TestCase):
    """切到 Parakeet 後 _strip_echo 不該誤動作。"""

    def test_no_prompt_means_no_stripping(self):
        s = streaming.StreamingSession({"stream_interval_sec": 0.5})
        s.set_prompt("")
        tokens = streaming.flatten_words(
            [{"word": "はい、そうですね。じゃあ", "start": 0.0, "end": 1.0}],
            "はい、そうですね。じゃあ")
        self.assertEqual(len(s._strip_echo(tokens)), len(tokens),
                         "沒有 prompt 就不該剝掉任何開頭")

    def test_whisper_still_strips(self):
        """對照：whisper 帶著種子時仍然要剝（不要因為這次改動而失效）。"""
        seed = "はい、そうですね。じゃあ、始めましょうか。"
        s = streaming.StreamingSession({"stream_interval_sec": 1.0})
        s.set_prompt(seed)
        tokens = streaming.flatten_words(
            [{"word": seed + "廊下に", "start": 0.0, "end": 2.0}], seed + "廊下に")
        out = s._strip_echo(tokens)
        self.assertEqual("".join(t.ch for t in out), "廊下に")


class TestWorkerBackendSwitch(unittest.TestCase):
    """⚙ 切換引擎：即時生效、不重開 worker、不丟緩衝。"""

    def _worker(self, cfg):
        return audio.AudioSubtitleWorker(cfg, lambda *a: None)

    def test_set_backend_updates_interval(self):
        w = self._worker({"asr_backend": "whisper"})
        self.assertEqual(w.session.interval, 1.0)
        self.assertEqual(w.set_backend("parakeet"), "parakeet")
        self.assertEqual(w.session.interval, 0.5,
                         "切到 Parakeet 後間隔要跟著變短")
        w.set_backend("whisper")
        self.assertEqual(w.session.interval, 1.0)

    def test_set_backend_keeps_buffer(self):
        """換引擎不該把使用者已經講過的話丟掉。"""
        w = self._worker({"asr_backend": "whisper"})
        session_before = w.session
        w.session.add_audio(b"\x00\x01" * 16000)
        sec_before = w.session.buffer_sec
        w.set_backend("parakeet")
        self.assertIs(w.session, session_before, "不該重建 session")
        self.assertAlmostEqual(w.session.buffer_sec, sec_before, places=4)

    def test_set_backend_clears_prompt_for_parakeet(self):
        w = self._worker({"asr_backend": "whisper"})
        w.session.set_prompt("はい、そうですね。じゃあ、始めましょうか。")
        w.set_backend("parakeet")
        self.assertEqual(w.session.prompt, "",
                         "Parakeet 沒有 prompt，session 的種子要清掉")
        self.assertEqual(w._stream_request_prompt(), "")

    def test_set_backend_is_idempotent(self):
        w = self._worker({"asr_backend": "parakeet"})
        self.assertEqual(w.set_backend("parakeet"), "parakeet")
        self.assertEqual(w.backend, "parakeet")

    def test_worker_url_follows_backend(self):
        cfg = {"asr_backend": "whisper",
               "whisper_server_url": "http://w/inference",
               "parakeet_server_url": "http://p/inference"}
        w = self._worker(cfg)
        self.assertEqual(audio.asr_url(w.cfg), "http://w/inference")
        w.set_backend("parakeet")
        self.assertEqual(audio.asr_url(w.cfg), "http://p/inference")


if __name__ == "__main__":
    unittest.main()

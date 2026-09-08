"""AudioSubtitleWorker 的段落處理：撤回事件、語言投票、prompt 帶進 whisper。

不開音訊裝置也不打網路：直接餵 PCM 給 worker._process()，把 transcribe 與
translate mock 掉。這樣「第二次出現要撤回」這種跨段落的行為才測得起來。

執行：python -m unittest tests.test_subtitle_worker -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import audio_subtitle as A, glossary as G  # noqa: E402


def pcm_of(seconds):
    """做一段指定長度的假 PCM（內容不重要，transcribe 被 mock 掉了）。"""
    return b"\x00\x10" * int(A.TARGET_RATE * seconds)


class WorkerHarness:
    """把 worker 的 callback 收集起來，方便斷言。"""

    def __init__(self, cfg=None, replies=None, langs=None):
        self.events = []
        cfg = dict(cfg or {})
        cfg.setdefault("translator", "local")
        self.worker = A.AudioSubtitleWorker(cfg, self._cb)
        self.worker.glossary = G.Glossary()
        self.worker.custom_prompt = ""
        self.replies = list(replies or [])
        self.langs = list(langs or [])
        self.prompts = []
        self.langs_sent = []

    def _cb(self, kind, payload):
        self.events.append((kind, payload))

    def kinds(self):
        return [k for k, _ in self.events]

    def of(self, kind):
        return [p for k, p in self.events if k == kind]

    def _transcribe(self, wav, url, language="auto", timeout=30, prompt="",
                    verbose=False):
        self.prompts.append(prompt)
        self.langs_sent.append(language)
        text = self.replies.pop(0) if self.replies else ""
        if verbose:
            return text, (self.langs.pop(0) if self.langs else "ja")
        return text

    def run(self, *durations):
        with mock.patch.object(A, "transcribe", self._transcribe), \
             mock.patch("engines.translator.translate",
                        side_effect=lambda text, cfg=None, source="auto",
                        glossary=None, custom_prompt=None:
                        (f"譯:{text}", "ja", _Info())):
            for d in durations:
                self.worker._process(pcm_of(d))
        return self


class _Info:
    fell_back = False

    def status(self):
        return "oMLX 1.0s"


class TestRetract(unittest.TestCase):
    def test_second_long_repeat_emits_retract(self):
        """同一句 90 秒內第二次出現且兩次都 >= 2.5 秒 → 第二條不顯示，並撤回第一條。"""
        h = WorkerHarness(replies=["お疲れ様でした", "お疲れ様でした"],
                          langs=["ja", "ja"]).run(7.0, 7.0)
        subs = h.of("subtitle")
        self.assertEqual(len(subs), 1, "第二條不該顯示")
        retracts = h.of("retract")
        self.assertEqual(len(retracts), 1, "應該要求撤回第一條")
        self.assertEqual(retracts[0]["src"], "お疲れ様でした")
        self.assertEqual(retracts[0]["filtered"], 1)

    def test_short_repeats_are_not_retracted(self):
        """「はい 1 秒 × 2」不該被殺，也不該撤回 —— 人真的會一直說「はい」。

        worker 另有「連續同一句不重複顯示」的去重，所以這裡看的是
        「沒有 retract 事件」而不是字幕條數。
        """
        h = WorkerHarness(replies=["はい", "はい"], langs=["ja", "ja"]).run(1.0, 1.0)
        self.assertEqual(h.of("retract"), [], "短句重複不該被撤回")
        self.assertEqual(h.worker.filter.total, 0)

    def test_garbage_never_shown(self):
        h = WorkerHarness(replies=["ご視聴ありがとうございました"],
                          langs=["ja"]).run(3.0)
        self.assertEqual(h.of("subtitle"), [])
        self.assertEqual(h.worker.filter.counts["garbage"], 1)

    def test_sparse_segment_dropped(self):
        """密度太低（3 個字撐 8 秒）→ 丟掉。"""
        h = WorkerHarness(replies=["あのー"], langs=["ja"]).run(8.0)
        self.assertEqual(h.of("subtitle"), [])
        self.assertEqual(h.worker.filter.counts["density"], 1)

    def test_inner_repeat_dropped(self):
        h = WorkerHarness(replies=["はいはいはいはい"], langs=["ja"]).run(2.0)
        self.assertEqual(h.of("subtitle"), [])
        self.assertEqual(h.worker.filter.counts["inner_repeat"], 1)

    def test_filtered_count_in_subtitle_payload(self):
        """狀態列要能顯示「已濾 N 條幻聽」，所以字幕 payload 要帶累計數。"""
        h = WorkerHarness(replies=["[Music]", "ここが、お前の探していた場所だ。"],
                          langs=["ja", "ja"]).run(2.0, 3.0)
        subs = h.of("subtitle")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["filtered"], 1)


class TestLanguageVotingInWorker(unittest.TestCase):
    def test_locks_after_four_segments(self):
        """前 4 段各自 auto，多數決鎖定，之後都帶鎖定語言。"""
        h = WorkerHarness({"audio_lang": "auto"},
                          replies=["いいですね一つ", "いいですね二つ", "いいですね三つ",
                                   "いいですね四つ", "いいですね五つ"],
                          langs=["ja", "ja", "en", "ja"])
        h.run(3.0, 3.0, 3.0, 3.0, 3.0)
        self.assertEqual(h.langs_sent[:4], ["auto"] * 4,
                         "前 4 段要各自 auto 才能投票")
        self.assertEqual(h.langs_sent[4], "ja", "第 5 段要帶鎖定的語言")
        self.assertIn("已鎖定", h.worker.lang_lock.status())

    def test_configured_lang_never_votes(self):
        h = WorkerHarness({"audio_lang": "ja"},
                          replies=["こんにちは", "こんばんは"])
        h.run(3.0, 3.0)
        self.assertEqual(h.langs_sent, ["ja", "ja"])

    def test_manual_selection_takes_effect_immediately(self):
        h = WorkerHarness({"audio_lang": "auto"},
                          replies=["いいですね一つ", "Hello there friend"],
                          langs=["ja", "en"])
        with mock.patch.object(A, "transcribe", h._transcribe), \
             mock.patch("engines.translator.translate",
                        side_effect=lambda text, cfg=None, source="auto",
                        glossary=None, custom_prompt=None:
                        (f"譯:{text}", "ja", _Info())):
            h.worker._process(pcm_of(3.0))
            h.worker.set_language("en")          # 使用者在下拉手動選了英文
            h.worker._process(pcm_of(3.0))
        self.assertEqual(h.langs_sent, ["auto", "en"])
        self.assertFalse(h.worker.lang_lock.voting)


class TestPromptSentToWhisper(unittest.TestCase):
    def test_seed_prompt_always_sent(self):
        """每段都要帶標點種子句，否則 whisper 不打標點、譯文會黏成一串。"""
        h = WorkerHarness({"audio_lang": "ja"}, replies=["こんにちは"]).run(3.0)
        self.assertIn("そうですね", h.prompts[0])

    def test_glossary_words_appended_after_seed(self):
        h = WorkerHarness({"audio_lang": "ja"}, replies=["こんにちは"])
        h.worker.glossary = G.parse("鹿せんべい = 鹿仙貝")
        h.run(3.0)
        self.assertIn("そうですね", h.prompts[0])
        self.assertTrue(h.prompts[0].endswith("鹿せんべい。"))

    def test_seed_can_be_disabled(self):
        h = WorkerHarness({"audio_lang": "ja", "asr_seed_prompt": ""},
                          replies=["こんにちは"]).run(3.0)
        self.assertEqual(h.prompts[0], "")

    def test_auto_sends_both_seeds(self):
        h = WorkerHarness({"audio_lang": "auto"}, replies=["こんにちは"],
                          langs=["ja"]).run(3.0)
        self.assertIn("そうですね", h.prompts[0])
        self.assertIn("get started", h.prompts[0])


class TestRecognizeAndTranslate(unittest.TestCase):
    """單次呼叫的進入點也要套過濾與 prompt（既有測試用的是這個介面）。"""

    def test_garbage_returns_empty(self):
        with mock.patch.object(A, "transcribe", return_value="[Music]"):
            src, zh, *_ = A.recognize_and_translate(
                A.to_wav_bytes(pcm_of(2.0)), {"audio_lang": "ja"},
                glossary=G.Glossary(), custom_prompt="")
        self.assertEqual(src, "")
        self.assertEqual(zh, "")

    def test_duration_from_wav_header(self):
        self.assertAlmostEqual(A._wav_duration(A.to_wav_bytes(pcm_of(3.0))),
                               3.0, places=2)

    def test_prompt_is_built_when_not_given(self):
        seen = {}

        def cap(wav, url, language="auto", timeout=30, prompt="", verbose=False):
            seen["prompt"] = prompt
            return "こんにちは"

        with mock.patch.object(A, "transcribe", cap), \
             mock.patch("engines.translator.translate",
                        return_value=("你好", "ja", _Info())):
            A.recognize_and_translate(A.to_wav_bytes(pcm_of(2.0)),
                                      {"audio_lang": "ja"},
                                      glossary=G.Glossary(), custom_prompt="")
        self.assertIn("そうですね", seen["prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

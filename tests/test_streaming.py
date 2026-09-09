"""LocalAgreement-2 串流辨識的單元測試（engines/streaming.py）。

全部離線、不打網路 —— 這個模組刻意設計成純邏輯就是為了能這樣測。
真的打 whisper-server 的重播測試在 tests/test_streaming_replay.py。

執行：python -m pytest tests/test_streaming.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import streaming  # noqa: E402
from engines.streaming import (  # noqa: E402
    Cue, StreamingSession, common_prefix_len, flatten_words,
    is_sentence_complete, normalize,
)

RATE = streaming.TARGET_RATE
WIDTH = streaming.TARGET_WIDTH


def silence(seconds):
    """指定秒數的靜音 PCM（內容不重要，這個模組只看長度）。"""
    return b"\x00" * (int(seconds * RATE) * WIDTH)


def words(*pairs):
    """(文字, 起, 迄) → whisper verbose_json 的 words 陣列。"""
    return [{"word": w, "start": s, "end": e, "probability": 0.9}
            for w, s, e in pairs]


def evenly(text, start=0.0, step=0.5):
    """把一串字每個字一個 word、等間隔攤開，省得每個測試都手寫時間戳。"""
    return [{"word": ch, "start": start + i * step,
             "end": start + (i + 1) * step, "probability": 0.9}
            for i, ch in enumerate(text)]


class TestNormalize(unittest.TestCase):
    """正規化：空白、全形半形標點、大小寫。"""

    def test_strips_whitespace(self):
        self.assertEqual(normalize("hello world"), "helloworld")
        self.assertEqual(normalize("じゃあ 次の 問題"), "じゃあ次の問題")

    def test_unifies_punctuation(self):
        self.assertEqual(normalize("えっ！"), normalize("えっ!"))
        self.assertEqual(normalize("そう？"), normalize("そう?"))
        self.assertEqual(normalize("あ、い"), normalize("あ,い"))

    def test_lowercases_latin(self):
        self.assertEqual(normalize("Hello"), normalize("hello"))

    def test_common_prefix_len(self):
        self.assertEqual(common_prefix_len("abcd", "abxd"), 2)
        self.assertEqual(common_prefix_len("abc", "abc"), 3)
        self.assertEqual(common_prefix_len("", "abc"), 0)


class TestFlatten(unittest.TestCase):
    def test_one_token_per_char(self):
        toks = flatten_words(words(("問題", 1.0, 2.0)))
        self.assertEqual([t.ch for t in toks], ["問", "題"])
        # 同一個 word 裡的字元共用時間戳
        self.assertEqual([t.end for t in toks], [2.0, 2.0])

    def test_tolerates_junk(self):
        toks = flatten_words([None, {"word": None}, {"word": "あ"}])
        self.assertEqual([t.ch for t in toks], ["あ"])

    def test_empty(self):
        self.assertEqual(flatten_words(None), [])
        self.assertEqual(flatten_words([]), [])


class TestAgreement(unittest.TestCase):
    """核心：連續兩次一致的最長前綴才確定。"""

    def setUp(self):
        # 這一組測的是前綴比對本身，不是暖機，所以把暖機門檻關掉
        # （stream_min_commit_sec=0），免得測試意外落在門檻邊界上。
        self.s = StreamingSession({"stream_interval_sec": 1.0,
                                   "stream_max_buffer_sec": 25.0,
                                   "stream_min_commit_sec": 0.0})
        self.s.add_audio(silence(3.0))

    def test_first_result_commits_nothing(self):
        """只有一次結果，沒有東西可以比對 → 全部都是未確定。"""
        out = self.s.consume_result(evenly("じゃあ次の"))
        self.assertEqual(out["committed"], "")
        self.assertEqual(out["tentative"], "じゃあ次の")
        self.assertIsNone(out["flush"])

    def test_two_agreeing_results_commit(self):
        """第二次一致 → 共同前綴確定。"""
        self.s.consume_result(evenly("じゃあ次の"))
        out = self.s.consume_result(evenly("じゃあ次の問題"))
        self.assertEqual(out["committed"], "じゃあ次の")
        self.assertEqual(out["tentative"], "問題")
        self.assertEqual(out["newly"], "じゃあ次の")

    def test_disagreement_commits_only_common_prefix(self):
        """尾巴不一致 → 只確定到分歧點之前。"""
        self.s.consume_result(evenly("じゃあ次の門台"))
        out = self.s.consume_result(evenly("じゃあ次の問題"))
        self.assertEqual(out["committed"], "じゃあ次の")
        self.assertEqual(out["tentative"], "問題")

    def test_total_disagreement_commits_nothing(self):
        self.s.consume_result(evenly("あいうえお"))
        out = self.s.consume_result(evenly("かきくけこ"))
        self.assertEqual(out["committed"], "")
        self.assertEqual(out["tentative"], "かきくけこ")

    def test_committed_never_changes(self):
        """已確定的字不會被後續結果改掉 —— LocalAgreement 的核心承諾。"""
        self.s.consume_result(evenly("じゃあ次の"))
        self.s.consume_result(evenly("じゃあ次の問題"))
        self.assertEqual(self.s.committed_text, "じゃあ次の")
        # 模型忽然改寫了開頭（實際會發生：聽到更多上下文後修正）
        out = self.s.consume_result(evenly("じゃんけん次の問題"))
        self.assertTrue(out["committed"].startswith("じゃあ"),
                        f"已確定的開頭被改掉了：{out['committed']!r}")

    def test_incremental_growth_over_many_rounds(self):
        """一輪一輪長出來：每一輪的 committed 都是前一輪的延伸。"""
        chunks = ["じゃ", "じゃあ次", "じゃあ次の問", "じゃあ次の問題を",
                  "じゃあ次の問題を山"]
        prev = ""
        for c in chunks:
            out = self.s.consume_result(evenly(c))
            self.assertTrue(out["committed"].startswith(prev),
                            f"{out['committed']!r} 不是 {prev!r} 的延伸")
            prev = out["committed"]
        # 確定的一定落後最新結果一輪（那正是 LocalAgreement-2 的代價），
        # 但不會落後兩輪 —— 倒數第二輪的內容這一輪就該全部確定。
        self.assertEqual(prev, "じゃあ次の問題を")
        self.assertEqual(self.s.tentative_text, "山")

    def test_shorter_second_result(self):
        """第二次比第一次短（模型退步）：只確定兩次都有的部分，不當機。"""
        self.s.consume_result(evenly("じゃあ次の問題を"))
        out = self.s.consume_result(evenly("じゃあ次"))
        self.assertEqual(out["committed"], "じゃあ次")
        self.assertEqual(out["tentative"], "")

    def test_empty_result(self):
        """whisper 回空的：不當機、不確定任何東西。"""
        out = self.s.consume_result([], "")
        self.assertEqual(out["committed"], "")
        self.assertEqual(out["tentative"], "")
        self.assertIsNone(out["flush"])

    def test_empty_then_real(self):
        self.s.consume_result([], "")
        out = self.s.consume_result(evenly("あい"))
        self.assertEqual(out["committed"], "")
        self.assertEqual(out["tentative"], "あい")

    def test_repeated_chars(self):
        """重複字（はいはいはい）：前綴比對照樣正確，不會多算或少算。"""
        self.s.consume_result(evenly("はいはい"))
        out = self.s.consume_result(evenly("はいはいはい"))
        self.assertEqual(out["committed"], "はいはい")
        self.assertEqual(out["tentative"], "はい")

    def test_punctuation_variant_counts_as_agreement(self):
        """全形／半形標點的差異不該打斷一致性。

        「そう？」與「そう?」視為一致 → 整句確定；因為結尾是句尾標點，
        同一輪就被 flush 成一條 cue，所以要去 flush 裡看，不是 committed。
        """
        self.s.consume_result(words(("そう", 0.0, 1.0), ("？", 1.0, 1.2)))
        out = self.s.consume_result(words(("そう", 0.0, 1.0), ("?", 1.0, 1.2),
                                          ("うん", 1.2, 2.0)))
        cue = out["flush"]
        self.assertIsNotNone(cue, "標點只差全形半形，應判定一致並確定整句")
        self.assertEqual(cue.text, "そう?")
        self.assertEqual(out["tentative"], "うん")

    def test_whitespace_only_difference(self):
        """whisper 這次加空白下次不加：視為一致。"""
        self.s.consume_result(words(("次の", 0.0, 1.0), (" 問題", 1.0, 2.0)))
        out = self.s.consume_result(words(("次の", 0.0, 1.0), ("問題", 1.0, 2.0),
                                          ("を", 2.0, 2.5)))
        self.assertIn("問題", out["committed"])


class TestEnglishWordBoundary(unittest.TestCase):
    """英文：比對到詞，不會把單字切一半。"""

    def setUp(self):
        self.s = StreamingSession()
        self.s.add_audio(silence(4.0))

    def test_commits_whole_words(self):
        self.s.consume_result(words(("Let's", 0.0, 0.5), (" get", 0.5, 0.9),
                                    (" star", 0.9, 1.3)))
        out = self.s.consume_result(words(("Let's", 0.0, 0.5), (" get", 0.5, 0.9),
                                          (" started", 0.9, 1.5)))
        # "star" 與 "started" 的共同前綴是 "star"，但那是半個單字 —— 要退回詞首
        self.assertNotIn("star", out["committed"].replace("started", ""))
        self.assertIn("get", out["committed"])

    def test_full_word_agreement_commits(self):
        self.s.consume_result(words(("Okay", 0.0, 0.5), (" so", 0.5, 0.8),
                                    (" let's", 0.8, 1.2)))
        out = self.s.consume_result(words(("Okay", 0.0, 0.5), (" so", 0.5, 0.8),
                                          (" let's", 0.8, 1.2), (" go", 1.2, 1.5)))
        self.assertIn("Okay", out["committed"])
        self.assertIn("so", out["committed"])

    def test_case_difference_is_agreement(self):
        """whisper 對句首大小寫很不穩，不該因此判成不一致。"""
        self.s.consume_result(words(("okay", 0.0, 0.5), (" so", 0.5, 0.8)))
        out = self.s.consume_result(words(("Okay", 0.0, 0.5), (" so", 0.5, 0.8),
                                          (" then", 0.8, 1.2)))
        self.assertIn("kay", out["committed"].lower())


class TestFlush(unittest.TestCase):
    """句尾標點與緩衝上限觸發 flush，以及 scroll 後的時間戳。"""

    def test_sentence_end_triggers_flush(self):
        s = StreamingSession()
        s.add_audio(silence(4.0))
        s.consume_result(words(("次の問題", 0.0, 2.0), ("。", 2.0, 2.1)))
        out = s.consume_result(words(("次の問題", 0.0, 2.0), ("。", 2.0, 2.1),
                                     ("山", 2.1, 2.5)))
        cue = out["flush"]
        self.assertIsNotNone(cue, "句尾標點應觸發 flush")
        self.assertEqual(cue.text, "次の問題。")
        self.assertAlmostEqual(cue.start, 0.0, places=3)
        self.assertAlmostEqual(cue.end, 2.1, places=3)

    def test_flush_scrolls_buffer(self):
        s = StreamingSession()
        s.add_audio(silence(5.0))
        self.assertAlmostEqual(s.buffer_sec, 5.0, places=2)
        s.consume_result(words(("あ", 0.0, 2.0), ("。", 2.0, 2.5)))
        s.consume_result(words(("あ", 0.0, 2.0), ("。", 2.0, 2.5)))
        # 切在 2.5 秒 → 緩衝剩 2.5 秒、offset 前進 2.5
        self.assertAlmostEqual(s.buffer_sec, 2.5, places=2)
        self.assertAlmostEqual(s.offset, 2.5, places=2)

    def test_timestamps_absolute_after_scroll(self):
        """scroll 之後第二句的時間戳要是絕對時間（含 offset）。"""
        s = StreamingSession()
        s.add_audio(silence(10.0))
        for _ in range(2):
            out = s.consume_result(words(("一句目", 0.0, 3.0), ("。", 3.0, 3.2)))
        self.assertAlmostEqual(out["flush"].end, 3.2, places=3)
        # 第二句在 scroll 後的相對時間 0.0~2.0，絕對時間應是 3.2~5.2
        for _ in range(2):
            out2 = s.consume_result(words(("二句目", 0.0, 2.0), ("。", 2.0, 2.1)))
        cue2 = out2["flush"]
        self.assertIsNotNone(cue2)
        self.assertEqual(cue2.text, "二句目。")
        self.assertAlmostEqual(cue2.start, 3.2, places=2)
        self.assertAlmostEqual(cue2.end, 5.3, places=2)

    def test_max_buffer_triggers_flush(self):
        """講者不打標點：緩衝超過上限也要強制送出。"""
        s = StreamingSession({"stream_max_buffer_sec": 5.0})
        s.add_audio(silence(6.0))
        s.consume_result(words(("ずっとしゃべる", 0.0, 4.0)))
        out = s.consume_result(words(("ずっとしゃべる", 0.0, 4.0), ("よ", 4.0, 4.5)))
        cue = out["flush"]
        self.assertIsNotNone(cue, "超過緩衝上限應強制 flush")
        self.assertEqual(cue.text, "ずっとしゃべる")

    def test_no_flush_without_committed_text(self):
        """緩衝超長但什麼都還沒確定：不要吐出空 cue。"""
        s = StreamingSession({"stream_max_buffer_sec": 2.0})
        s.add_audio(silence(5.0))
        out = s.consume_result(evenly("あいうえお"))
        self.assertIsNone(out["flush"])

    def test_flush_cuts_at_last_sentence_end(self):
        """一次確定了兩句：切在最後一個句尾標點，兩句一起送出。"""
        s = StreamingSession()
        s.add_audio(silence(6.0))
        w = words(("一。", 0.0, 1.0), ("二。", 1.0, 2.0), ("三", 2.0, 3.0))
        s.consume_result(w)
        out = s.consume_result(w)
        cue = out["flush"]
        self.assertIsNotNone(cue)
        self.assertEqual(cue.text, "一。二。")
        self.assertEqual(out["committed"], "三")

    def test_manual_flush_includes_tentative(self):
        """停止時 flush()：未確定的尾巴也要吐出來，不能什麼都不給。"""
        s = StreamingSession()
        s.add_audio(silence(3.0))
        s.consume_result(evenly("じゃあ"))
        s.consume_result(evenly("じゃあ次の"))
        cue = s.flush()
        self.assertIsNotNone(cue)
        self.assertEqual(cue.text, "じゃあ次の")
        self.assertIsNone(s.flush(), "flush 兩次第二次應該是空的")

    def test_flush_empty_session(self):
        self.assertIsNone(StreamingSession().flush())


class TestDueAndBuffer(unittest.TestCase):
    def test_due_after_interval(self):
        s = StreamingSession({"stream_interval_sec": 1.0})
        self.assertFalse(s.due())
        s.add_audio(silence(0.5))
        self.assertFalse(s.due())
        s.add_audio(silence(0.6))
        self.assertTrue(s.due())
        s.mark_sent()
        self.assertFalse(s.due())
        self.assertEqual(s.asr_count, 1)

    def test_buffer_wav_is_valid(self):
        import io
        import wave
        s = StreamingSession()
        s.add_audio(silence(1.0))
        with wave.open(io.BytesIO(s.buffer_wav()), "rb") as w:
            self.assertEqual(w.getframerate(), RATE)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getnframes(), RATE)

    def test_reset(self):
        s = StreamingSession()
        s.add_audio(silence(2.0))
        s.consume_result(evenly("あい"))
        s.reset()
        self.assertEqual(s.buffer_sec, 0.0)
        self.assertEqual(s.committed_text, "")
        self.assertEqual(s.offset, 0.0)


class TestFallbacks(unittest.TestCase):
    """伺服器沒回 words 時的退路。"""

    def test_text_only_still_works(self):
        s = StreamingSession()
        s.add_audio(silence(4.0))       # 要過暖機門檻才會確定
        s.consume_result([], "こんにちは")
        out = s.consume_result([], "こんにちは、元気")
        self.assertEqual(out["committed"], "こんにちは")

    def test_text_only_flush_has_timestamps(self):
        s = StreamingSession()
        s.add_audio(silence(4.0))
        s.consume_result([], "はい。")
        out = s.consume_result([], "はい。")
        cue = out["flush"]
        self.assertIsNotNone(cue)
        self.assertGreater(cue.end, 0.0)


class TestWarmup(unittest.TestCase):
    """暖機：緩衝還短的時候只顯示、不確定（見 streaming.MIN_COMMIT_SEC）。"""

    def test_short_buffer_commits_nothing(self):
        """1 秒的緩衝連兩輪吐同一個字，也不該確定它。

        這是實測踩到的坑：seg.wav 前兩秒 whisper 都回「はい」，
        連兩輪一致就被確定，而真正的第一句是「じゃあ次の問題を」。
        """
        s = StreamingSession({"stream_min_commit_sec": 3.0})
        s.add_audio(silence(1.0))
        s.consume_result(evenly("はい"))
        out = s.consume_result(evenly("はい"))
        self.assertEqual(out["committed"], "")
        self.assertEqual(out["tentative"], "はい")
        self.assertIsNone(out["flush"])

    def test_commits_once_buffer_is_long_enough(self):
        """緩衝夠長之後，暖機期間累積的一致性立刻生效，不用重新等兩輪。"""
        s = StreamingSession({"stream_min_commit_sec": 3.0})
        s.add_audio(silence(1.0))
        s.consume_result(evenly("はい"))
        s.add_audio(silence(3.0))          # 現在 4 秒，過門檻了
        out = s.consume_result(evenly("はい"))
        self.assertEqual(out["committed"], "はい")

    def test_prompt_suppressed_during_warmup(self):
        """暖機期間不送種子 prompt（它會誘導 whisper 編出像樣的假句子）。"""
        s = StreamingSession({"stream_min_commit_sec": 3.0})
        s.set_prompt("はい、そうですね。じゃあ、始めましょうか。")
        s.add_audio(silence(1.0))
        self.assertEqual(s.prompt_for_request(), "")
        s.add_audio(silence(3.0))
        self.assertTrue(s.prompt_for_request().startswith("はい"))

    def test_strip_prompt_echo(self):
        """whisper 把 prompt 整段抄回來時要剝掉，不能當成字幕。"""
        prompt = "はい、そうですね。じゃあ、始めましょうか。"
        s = StreamingSession({"stream_min_commit_sec": 0.0})
        s.set_prompt(prompt)
        s.add_audio(silence(5.0))
        out = s.consume_result(evenly(prompt + "山田"))
        self.assertNotIn("そうですね", out["tentative"])
        self.assertIn("山田", out["tentative"])

    def test_short_echo_not_stripped(self):
        """只撞到開頭兩個字（真的有人說「はい」）就不該剝。"""
        s = StreamingSession({"stream_min_commit_sec": 0.0})
        s.set_prompt("はい、そうですね。じゃあ、始めましょうか。")
        s.add_audio(silence(5.0))
        out = s.consume_result(evenly("はい、わかった"))
        self.assertIn("わかった", out["tentative"])


class TestMojibake(unittest.TestCase):
    """whisper.cpp 會在 words 裡把 CJK 字元切成兩半，變成 U+FFFD。

    實測這台 1.8.3：頂層 text 是對的，words 裡的「寝」「廊」變成兩個
    替代字元。所以字元一律以 text 為準，words 只拿來對時間軸。
    """

    def test_text_wins_over_broken_words(self):
        broken = words(("山", 5.0, 7.0), ("だ", 7.0, 7.2), ("!", 7.2, 7.3),
                       ("また", 9.0, 9.3), ("�", 9.3, 9.4),
                       ("�", 9.4, 9.5), ("て", 9.5, 9.7),
                       ("る", 9.7, 9.8), ("の", 9.8, 10.0), ("?", 10.0, 10.0))
        toks = flatten_words(broken, "山だ!また寝てるの?")
        self.assertEqual("".join(t.ch for t in toks), "山だ!また寝てるの?")
        self.assertNotIn("�", "".join(t.ch for t in toks))
        # 時間戳仍然單調遞增，切音訊才不會切錯地方
        ends = [t.end for t in toks]
        self.assertEqual(ends, sorted(ends))

    def test_newline_in_text(self):
        """text 會有換行（whisper 的分行），words 裡沒有對應項。"""
        toks = flatten_words(words(("はい", 0.0, 1.0), ("そう", 1.0, 2.0)),
                             "はい\nそう")
        self.assertEqual("".join(t.ch for t in toks), "はい\nそう")

    def test_words_only_when_no_text(self):
        toks = flatten_words(words(("あい", 0.0, 1.0)))
        self.assertEqual("".join(t.ch for t in toks), "あい")


class TestSentenceComplete(unittest.TestCase):
    def test_complete(self):
        for t in ["はい。", "そう？", "Really!", "終わり!", "なに?"]:
            self.assertTrue(is_sentence_complete(t), t)

    def test_incomplete(self):
        for t in ["", "  ", "じゃあ次の", "let's get"]:
            self.assertFalse(is_sentence_complete(t), t)


class TestCue(unittest.TestCase):
    def test_as_dict(self):
        self.assertEqual(Cue("あ", 1.0, 2.0).as_dict(),
                         {"text": "あ", "start": 1.0, "end": 2.0})


if __name__ == "__main__":
    unittest.main()

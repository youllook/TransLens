"""幻聽過濾測試：四條規則各自的命中與「不該命中」的反例。

反例比正例重要：誤殺正常對話比漏掉一條幻聽難察覺得多 —— 使用者只會覺得
「有幾句沒翻出來」，不會知道是被過濾掉的。所以每條規則都配一組反例。

執行：python -m unittest tests.test_hallucination -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import hallucination as H  # noqa: E402


class TestIsGarbage(unittest.TestCase):
    def test_known_hallucinations(self):
        for bad in ["", "   ", "。。。", "...", "！？",
                    "ご視聴ありがとうございました",
                    "ご視聴いただきありがとうございます",
                    "ご視聴誠にありがとうございました",
                    "ご清聴ありがとうございました",
                    "最後までご視聴ください",
                    "チャンネル登録お願いします",
                    "高評価よろしく",
                    "おやすみなさい。",
                    "おわり", "終わり", "おしまい",
                    "Thank you for watching!",
                    "Thanks so much for watching",
                    "thanks for listening",
                    "Please don't forget to subscribe",
                    "Like and subscribe",
                    "Subtitles by the Amara.org community",
                    "Transcribed by ESO",
                    "字幕", "字幕組", "字幕志愿者",
                    "[Music]", "[ Applause ]", "（音楽）", "(silence)", "♪♪"]:
            self.assertTrue(H.is_garbage(bad), f"應判定為垃圾：{bad!r}")

    def test_real_speech_survives(self):
        for good in ["ここが、お前の探していた場所だ。", "This is the place.",
                     "早く帰った方がいい", "ありがとうございます",
                     "Thank you.", "登録しました", "終わりが見えない",
                     "音楽が好きです"]:
            self.assertFalse(H.is_garbage(good), f"不該判定為垃圾：{good!r}")


class TestDensity(unittest.TestCase):
    """密度：字數/秒數太低 = 模型把一兩個字撐滿整個窗口。"""

    def test_sparse_japanese_dropped(self):
        # 3 個字撐 8 秒 = 0.375 字/秒 < 0.8
        self.assertTrue(H.too_sparse("あのー", 8.0))

    def test_sparse_english_dropped(self):
        # 5 字元撐 8 秒 = 0.625 字元/秒 < 1.5
        self.assertTrue(H.too_sparse("Uh...", 8.0))

    def test_normal_speech_kept(self):
        self.assertFalse(H.too_sparse("ここが、お前の探していた場所だ。", 4.0))
        self.assertFalse(H.too_sparse("This is the place I was looking for.", 4.0))

    def test_short_segment_not_judged(self):
        """短段落本來就可能只有一個字，不該用密度判（< 3 秒不判）。"""
        self.assertFalse(H.too_sparse("はい", 1.0))
        self.assertFalse(H.too_sparse("Yeah", 2.0))

    def test_can_be_disabled(self):
        self.assertFalse(H.too_sparse("あのー", 8.0,
                                      {"hallucination_density_min_dur": 0}))


class TestInnerRepeat(unittest.TestCase):
    """段內重複：同一字或同一短語連發 >= 4 次。"""

    def test_repeated_char(self):
        self.assertTrue(H.has_inner_repeat("あああああ"))
        self.assertEqual(H.inner_repeat_count("あああああ"), 5)

    def test_repeated_phrase(self):
        self.assertTrue(H.has_inner_repeat("はいはいはいはい"))
        self.assertTrue(H.has_inner_repeat("そうですねそうですねそうですねそうですね"))

    def test_repeated_with_punctuation(self):
        """標點被正規化掉，所以「はい、はい、はい、はい」也算連發。"""
        self.assertTrue(H.has_inner_repeat("はい、はい、はい、はい。"))

    def test_normal_speech_not_flagged(self):
        for good in ["はいはい", "ありがとうございます", "私は私の仕事をします",
                     "This is the place I was looking for.",
                     "そうですねそうですね"]:
            self.assertFalse(H.has_inner_repeat(good), f"不該判定為重複：{good!r}")

    def test_can_be_disabled(self):
        self.assertFalse(H.has_inner_repeat("あああああ",
                                            {"hallucination_inner_repeat_max": 0}))


class TestRepeatTracker(unittest.TestCase):
    """90 秒內第二次出現：兩次都要夠長才算幻聽。"""

    def test_second_long_occurrence_dropped_and_retracted(self):
        t = H.RepeatTracker({})
        dup, retract = t.check("あ〜とても美味しいです", 7.0, now=100.0)
        self.assertFalse(dup, "第一次出現不該被判掉")
        dup, retract = t.check("あ、とても美味しいです。", 7.0, now=140.0)
        self.assertTrue(dup, "90 秒內第二次出現、兩次都很長 → 幻聽")
        self.assertTrue(retract, "要把第一條從畫面上收回")

    def test_short_repeats_survive(self):
        """「はい 1 秒 × 2」不該被殺 —— 人真的會一直說「はい」。"""
        t = H.RepeatTracker({})
        self.assertEqual(t.check("はい", 1.0, now=100.0), (False, False))
        self.assertEqual(t.check("はい", 1.0, now=105.0), (False, False))
        self.assertEqual(t.check("はい。", 1.0, now=110.0), (False, False))

    def test_one_long_one_short_survives(self):
        """只有一次拖很長不算：要兩次都拖很長才是模型在 loop。"""
        t = H.RepeatTracker({})
        t.check("お疲れ様でした", 7.0, now=100.0)
        dup, _ = t.check("お疲れ様でした", 1.2, now=120.0)
        self.assertFalse(dup)

    def test_outside_window_survives(self):
        """超過 90 秒才又出現 = 大概是真的又講了一次。"""
        t = H.RepeatTracker({})
        t.check("お疲れ様でした", 7.0, now=100.0)
        dup, _ = t.check("お疲れ様でした", 7.0, now=200.0)
        self.assertFalse(dup)

    def test_normalization_matches_punctuation_variants(self):
        """whisper 每次吐同一句幻聽標點都不一樣，正規化後要視為同一句。"""
        self.assertEqual(H.normalize_repeat("あ〜とても美味しいです"),
                         H.normalize_repeat("あ、とても美味しいです。"))

    def test_can_be_disabled(self):
        t = H.RepeatTracker({"hallucination_repeat": False})
        t.check("お疲れ様でした", 7.0, now=100.0)
        self.assertEqual(t.check("お疲れ様でした", 7.0, now=110.0), (False, False))

    def test_expired_keys_are_pruned(self):
        """記憶體用量不該隨著講多久而無限成長。"""
        t = H.RepeatTracker({"hallucination_repeat_window_sec": 10})
        for i in range(50):
            t.check(f"句子{i}", 3.0, now=100.0 + i)
        t.check("最後", 3.0, now=1000.0)
        self.assertLessEqual(len(t._seen), 2)


class TestFilter(unittest.TestCase):
    """統一入口：計數與 summary。"""

    def test_counts_by_reason(self):
        f = H.Filter({})
        self.assertEqual(f.check("ご視聴ありがとうございました", 3.0).reason, "garbage")
        self.assertEqual(f.check("あのー", 8.0).reason, "density")
        self.assertEqual(f.check("はいはいはいはい", 2.0).reason, "inner_repeat")
        f.check("お疲れ様でした", 7.0, now=100.0)
        v = f.check("お疲れ様でした", 7.0, now=120.0)
        self.assertEqual(v.reason, "repeat")
        self.assertTrue(v.retract)
        self.assertEqual(f.total, 4)
        self.assertEqual(f.counts,
                         {"garbage": 1, "density": 1, "inner_repeat": 1, "repeat": 1})

    def test_summary_text(self):
        f = H.Filter({})
        self.assertEqual(f.summary(), "")
        f.check("[Music]", 2.0)
        self.assertEqual(f.summary(), "已濾 1 條幻聽")

    def test_real_speech_passes(self):
        f = H.Filter({})
        v = f.check("ここが、お前の探していた場所だ。", 3.5)
        self.assertFalse(v.drop)
        self.assertEqual(f.total, 0)

    def test_reset_clears_state(self):
        f = H.Filter({})
        f.check("[Music]", 2.0)
        f.reset()
        self.assertEqual(f.total, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

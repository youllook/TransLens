"""詞彙表測試：解析、三層套用、prompt 組裝、語言投票。

三層是指同一份 glossary.txt 餵三個地方：
    whisper 的 prompt（聽對）、LLM 的 system prompt（翻對）、輸出取代（一定對）

執行：python -m unittest tests.test_glossary -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import asr_prompt, glossary as G  # noqa: E402


class TestParse(unittest.TestCase):
    def test_terms_and_rules(self):
        g = G.parse("""
# 註解
鹿せんべい = 鹿仙貝
平須 => Hirasu
[src] 地下せんべい => 鹿せんべい
""")
        self.assertEqual(g.terms, {"鹿せんべい": "鹿仙貝"})
        self.assertEqual(len(g.rules), 2)
        self.assertIn(G.Rule("平須", "Hirasu", False), g.rules)
        self.assertIn(G.Rule("地下せんべい", "鹿せんべい", True), g.rules)

    def test_fullwidth_equals_and_spaces(self):
        g = G.parse("　鹿せんべい　＝　鹿仙貝　")
        self.assertEqual(g.terms, {"鹿せんべい": "鹿仙貝"})

    def test_bad_lines_become_warnings_not_exceptions(self):
        g = G.parse("這行沒有等號\n= 左邊空的\n=> 也是空的\n[src] 東大寺 = 東大寺")
        self.assertEqual(len(g.terms), 0)
        self.assertEqual(len(g.rules), 0)
        self.assertEqual(len(g.warnings), 4)

    def test_duplicate_takes_latter_with_warning(self):
        g = G.parse("鹿 = A\n鹿 = B")
        self.assertEqual(g.terms["鹿"], "B")
        self.assertTrue(g.warnings)

    def test_empty_is_falsy(self):
        self.assertFalse(G.parse("# 全部都是註解"))

    def test_parse_file_missing_is_empty(self):
        self.assertFalse(G.parse_file(r"D:\nope\nothing.txt"))


class TestApplyLayers(unittest.TestCase):
    """三層各自套在該套的地方。"""

    def setUp(self):
        self.g = G.parse("鹿せんべい = 鹿仙貝\n"
                         "平須 => Hirasu\n"
                         "[src] 地下せんべい => 鹿せんべい\n")

    def test_layer1_source_rules(self):
        """[src] 規則在翻譯之前套在原文上。"""
        self.assertEqual(self.g.apply_source("地下せんべいを買った"),
                         "鹿せんべいを買った")

    def test_layer1_does_not_touch_output_rules(self):
        self.assertEqual(self.g.apply_source("平須さん"), "平須さん")

    def test_layer2_prompt_section_only_includes_present_terms(self):
        """只附這句真的出現過的詞 —— 不然每句都要多付整張表的 token。"""
        self.assertIn("鹿せんべい → 鹿仙貝",
                      self.g.prompt_section(["鹿せんべいを買った"]))
        self.assertEqual(self.g.prompt_section(["ぜんぜん関係ない話"]), "")

    def test_layer3_output_rules(self):
        self.assertEqual(self.g.apply_output("平須さんが来た"), "Hirasuさんが来た")

    def test_longer_keys_replace_first(self):
        """短鍵不該吃掉長鍵的結果（「鹿」不該把「鹿仙貝」變成「梅花鹿仙貝」）。"""
        g = G.parse("dummy = x\n鹿せんべい => 鹿仙貝\n鹿 => 梅花鹿")
        self.assertEqual(g.apply_output("鹿せんべい"), "鹿仙貝")

    def test_replaced_text_is_not_re_replaced(self):
        g = G.parse("dummy = x\nA => B\nB => C")
        self.assertEqual(g.apply_output("A"), "B", "換好的 B 不該再被 B=>C 動到")


class TestAsrPrompt(unittest.TestCase):
    """第三層：whisper 的 prompt。"""

    def test_words_end_with_period(self):
        """句號不是裝飾：whisper 會模仿 prompt 的書寫風格，沒句號它就不打標點。"""
        g = G.parse("鹿せんべい = 鹿仙貝\n東大寺 = 東大寺")
        prompt, warn = g.asr_prompt()
        self.assertTrue(prompt.endswith("。"))
        self.assertIn("鹿せんべい", prompt)
        self.assertIn("東大寺", prompt)
        self.assertEqual(warn, "")

    def test_includes_src_rule_targets(self):
        """[src] 規則的右邊是「我們希望它聽成的寫法」，也要餵給 whisper。"""
        g = G.parse("[src] 地下せんべい => 鹿せんべい")
        prompt, _ = g.asr_prompt()
        self.assertIn("鹿せんべい", prompt)

    def test_truncates_over_limit_with_warning(self):
        g = G.parse("\n".join(f"詞彙{i:03d}あいうえお = x{i}" for i in range(60)))
        prompt, warn = g.asr_prompt(max_chars=200)
        self.assertLessEqual(len(prompt), 200)
        self.assertIn("只送前", warn)

    def test_empty_glossary_gives_empty_prompt(self):
        self.assertEqual(G.parse("").asr_prompt(), ("", ""))


class TestSeedPrompt(unittest.TestCase):
    """標點種子句：誘導 whisper 打標點。"""

    def test_ja_and_en_seeds(self):
        self.assertIn("そうですね", asr_prompt.seed_prompt("ja"))
        self.assertIn("get started", asr_prompt.seed_prompt("en"))

    def test_auto_sends_both(self):
        """還沒鎖定語言時兩個種子都送，只留下「要打標點」這個共同訊號。"""
        s = asr_prompt.seed_prompt("auto")
        self.assertIn("そうですね", s)
        self.assertIn("get started", s)

    def test_override_can_disable(self):
        self.assertEqual(asr_prompt.seed_prompt("ja", ""), "")

    def test_override_custom(self):
        self.assertEqual(asr_prompt.seed_prompt("ja", "自訂。"), "自訂。")

    def test_build_prompt_puts_seed_first(self):
        """種子句要在前面：prompt 是當「前一段轉錄」餵的，結尾影響最大，
        專有名詞要留在後面才有效。"""
        out = asr_prompt.build_prompt("鹿せんべい。", "ja")
        self.assertTrue(out.startswith(asr_prompt.SEED_PROMPTS["ja"]))
        self.assertTrue(out.endswith("鹿せんべい。"))

    def test_build_prompt_empty_when_nothing(self):
        self.assertEqual(asr_prompt.build_prompt("", "xx"), "")

    def test_build_prompt_truncates(self):
        out = asr_prompt.build_prompt("あ" * 500, "ja", max_chars=100)
        self.assertEqual(len(out), 100)


class TestLanguageVote(unittest.TestCase):
    def test_majority_wins(self):
        lang, detail = asr_prompt.vote_language(["ja", "ja", "en", "ja"])
        self.assertEqual(lang, "ja")
        self.assertIn("3 判 ja", detail)

    def test_falls_back_to_text_features(self):
        """票都不在允許清單（日文被誤判成韓文）→ 改看轉錄文字。"""
        lang, detail = asr_prompt.vote_language(
            ["ko", "ko"], ["これは日本語です", "これも"])
        self.assertEqual(lang, "ja")
        self.assertIn("文字特徵", detail)

    def test_latin_text_detected_as_en(self):
        lang, _ = asr_prompt.vote_language(["ko"], ["this is english text"])
        self.assertEqual(lang, "en")

    def test_no_votes_gives_uncertain_note(self):
        lang, detail = asr_prompt.vote_language([], [])
        self.assertEqual(lang, "ja")
        self.assertIn("不確定", detail)

    def test_guess_from_text(self):
        self.assertEqual(asr_prompt.guess_lang_from_text("これは日本語"), "ja")
        self.assertEqual(asr_prompt.guess_lang_from_text("hello world"), "en")
        self.assertEqual(asr_prompt.guess_lang_from_text(""), "")


class TestLanguageLock(unittest.TestCase):
    def test_locks_after_four_samples(self):
        lock = asr_prompt.LanguageLock("auto")
        self.assertTrue(lock.voting)
        self.assertEqual(lock.request_lang(), "auto")
        for d in ("ja", "ja", "en"):
            self.assertIsNone(lock.observe(d, "テスト"))
        self.assertEqual(lock.observe("ja", "テスト"), "ja")
        self.assertFalse(lock.voting)
        self.assertEqual(lock.request_lang(), "ja")
        self.assertIn("已鎖定", lock.status())

    def test_configured_lang_skips_voting(self):
        lock = asr_prompt.LanguageLock("ja")
        self.assertFalse(lock.voting)
        self.assertEqual(lock.request_lang(), "ja")
        self.assertNotIn("已鎖定", lock.status())

    def test_manual_override_stops_voting(self):
        """使用者在下拉手動選語言時立即改用手動值並停止投票。"""
        lock = asr_prompt.LanguageLock("auto")
        lock.observe("ja", "テスト")
        lock.set_manual("en")
        self.assertFalse(lock.voting)
        self.assertEqual(lock.request_lang(), "en")
        self.assertIsNone(lock.observe("ja", "テスト"), "手動之後不該再投票")
        self.assertEqual(lock.request_lang(), "en")

    def test_back_to_auto_restarts_voting(self):
        lock = asr_prompt.LanguageLock("ja")
        lock.set_manual("auto")
        self.assertTrue(lock.voting)
        self.assertEqual(lock.request_lang(), "auto")

    def test_status_shows_progress(self):
        lock = asr_prompt.LanguageLock("auto")
        lock.observe("ja", "テスト")
        self.assertIn("1/4", lock.status())

    def test_text_features_beat_reported_language(self):
        """whisper 對英文段落有時仍回 ja（實測混語言音訊）。文字說了算，
        否則整段會被鎖成日文，之後英文全被日文模型硬套。"""
        lock = asr_prompt.LanguageLock("auto")
        for _ in range(3):
            lock.observe("ja", "This is clearly english text here")
        lock.observe("ja", "And so is this one")
        self.assertEqual(lock.request_lang(), "en")

    def test_kana_text_votes_ja_even_if_reported_en(self):
        lock = asr_prompt.LanguageLock("auto")
        for _ in range(4):
            lock.observe("en", "ここがお前の探していた場所だ")
        self.assertEqual(lock.request_lang(), "ja")


class TestLoadFromFiles(unittest.TestCase):
    def test_extra_overrides_global(self):
        with tempfile.TemporaryDirectory() as d:
            extra = os.path.join(d, "extra.txt")
            with open(extra, "w", encoding="utf-8") as f:
                f.write("鹿 = 額外的鹿\n")
            g = G.load(extra=extra)
            self.assertEqual(g.terms.get("鹿"), "額外的鹿")

    def test_load_prompt_strips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# 這是註解\n真正的要求\n")
            self.assertEqual(G.load_prompt(extra=p).strip(), "真正的要求")

    def test_disabled_by_config(self):
        g, p = G.load_from_cfg({"glossary": False})
        self.assertFalse(g)
        self.assertEqual(p, "")

    def test_shipped_templates_are_all_comments(self):
        """commit 進去的範本必須「整份都是註解」＝預設不改變任何行為。"""
        for name in (G.GLOSSARY_NAME, G.PROMPT_NAME):
            path = os.path.join(G.app_dir(), name)
            self.assertTrue(os.path.exists(path), f"應該有範本 {name}")
        self.assertFalse(G.parse_file(os.path.join(G.app_dir(), G.GLOSSARY_NAME)),
                         "glossary.txt 範本應該整份都是註解")
        self.assertEqual(G.load_prompt().strip(), "",
                         "prompt.txt 範本應該整份都是註解")


if __name__ == "__main__":
    unittest.main(verbosity=2)

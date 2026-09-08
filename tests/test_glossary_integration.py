"""詞彙表在「翻譯入口」與「OCR 路徑」上真的生效（不打網路，把後端 mock 掉）。

重點是 OCR 模式也要走翻譯層與輸出層 —— 詞彙表不能只有音訊字幕吃得到。

執行：python -m unittest tests.test_glossary_integration -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import glossary as G, translate_local, translator  # noqa: E402

GLOSS = "鹿せんべい = 鹿仙貝\n平須 => Hirasu\n[src] 地下せんべい => 鹿せんべい\n"


def fake_local(captured):
    """假的本地 LLM：把收到的 system prompt 與原文記下來，回一句固定譯文。"""
    def _translate(text, target="zh-TW", source="auto", timeout=15, cfg=None,
                   glossary_section="", custom_prompt=""):
        captured["text"] = text
        captured["section"] = glossary_section
        captured["custom"] = custom_prompt
        captured["system"] = translate_local.build_system_prompt(
            custom_prompt, str((cfg or {}).get("prompt_mode", "append")),
            glossary_section)
        return captured.get("reply", "平須さんが鹿仙貝を買った"), "ja"
    return _translate


class TestTranslatorLayers(unittest.TestCase):
    def setUp(self):
        self.cfg = {"translator": "local", "target_lang": "zh-TW"}
        self.g = G.parse(GLOSS)

    def test_source_rule_applied_before_translation(self):
        """[src] 規則要在送給模型之前就把原文修好。"""
        cap = {}
        with mock.patch.object(translate_local, "translate", fake_local(cap)):
            translator.translate("地下せんべいを買った", self.cfg,
                                 glossary=self.g, custom_prompt="")
        self.assertEqual(cap["text"], "鹿せんべいを買った")

    def test_glossary_section_reaches_system_prompt(self):
        cap = {}
        with mock.patch.object(translate_local, "translate", fake_local(cap)):
            translator.translate("鹿せんべいを買った", self.cfg,
                                 glossary=self.g, custom_prompt="")
        self.assertIn("鹿せんべい → 鹿仙貝", cap["section"])
        self.assertIn("鹿せんべい → 鹿仙貝", cap["system"])

    def test_absent_terms_not_in_prompt(self):
        cap = {}
        with mock.patch.object(translate_local, "translate", fake_local(cap)):
            translator.translate("ぜんぜん関係ない話", self.cfg,
                                 glossary=self.g, custom_prompt="")
        self.assertEqual(cap["section"], "")

    def test_output_rule_applied_after_translation(self):
        cap = {"reply": "平須さんが来た"}
        with mock.patch.object(translate_local, "translate", fake_local(cap)):
            zh, _, _ = translator.translate("誰か来た", self.cfg,
                                            glossary=self.g, custom_prompt="")
        self.assertEqual(zh, "Hirasuさんが来た")

    def test_output_rule_applied_on_google_path_too(self):
        """Google 後端沒有 prompt 可給，但輸出取代那層照樣要生效。"""
        cfg = {"translator": "google", "target_lang": "zh-TW"}
        with mock.patch.object(translator, "_google",
                              return_value=("平須さんが来た", "ja")):
            zh, _, _ = translator.translate("誰か来た", cfg,
                                            glossary=self.g, custom_prompt="")
        self.assertEqual(zh, "Hirasuさんが来た")

    def test_output_rule_applied_when_local_falls_back(self):
        """本地失敗退到 Google 時也不能漏掉輸出取代。"""
        with mock.patch.object(translate_local, "translate",
                              side_effect=RuntimeError("boom")), \
             mock.patch.object(translator, "_google",
                               return_value=("平須さんが来た", "ja")):
            zh, _, info = translator.translate("誰か来た", self.cfg,
                                               glossary=self.g, custom_prompt="")
        self.assertTrue(info.fell_back)
        self.assertEqual(zh, "Hirasuさんが来た")

    def test_no_glossary_is_unchanged_behaviour(self):
        """沒有詞彙表時行為要跟以前一樣。"""
        cap = {"reply": "原樣"}
        with mock.patch.object(translate_local, "translate", fake_local(cap)):
            zh, _, _ = translator.translate("なにか", self.cfg,
                                            glossary=G.Glossary(), custom_prompt="")
        self.assertEqual(zh, "原樣")
        self.assertEqual(cap["section"], "")

    def test_broken_glossary_does_not_break_translation(self):
        """詞彙表讀取爆掉時要退成「沒設定」，不能讓翻譯停擺。"""
        with mock.patch.object(G, "load_from_cfg", side_effect=OSError("disk")):
            g, p = translator.load_glossary({})
        self.assertFalse(g)
        self.assertEqual(p, "")


class TestCustomPrompt(unittest.TestCase):
    def test_append_mode_keeps_style(self):
        s = translate_local.build_system_prompt("不要用您", "append")
        self.assertIn("風格規定", s)
        self.assertIn("不要用您", s)

    def test_replace_mode_drops_style_keeps_format(self):
        """replace 換掉風格，但「只輸出譯文本身」那段格式規定一定要留著。"""
        s = translate_local.build_system_prompt("我的規定", "replace")
        self.assertIn("只輸出譯文本身", s)
        self.assertNotIn("風格規定", s)
        self.assertIn("我的規定", s)

    def test_glossary_section_last(self):
        """順序是格式 → 風格 → 詞彙表：後面的規定權重比較高。"""
        s = translate_local.build_system_prompt("", "append", "詞彙表（必須遵守）：\nA → B")
        self.assertTrue(s.rstrip().endswith("A → B"))

    def test_no_custom_matches_builtin(self):
        self.assertEqual(translate_local.build_system_prompt(),
                         translate_local.SYSTEM_PROMPT)


class TestOcrPathAppliesGlossary(unittest.TestCase):
    """OCR 模式也要走翻譯層與輸出層。"""

    def test_ocr_engine_applies_output_rule(self):
        from engines import ocr_google, ocr_windows
        g = G.parse(GLOSS)
        with mock.patch.object(ocr_windows, "recognize_best",
                               return_value=(["誰か来た"], "ja")), \
             mock.patch.object(ocr_windows, "looks_garbled", return_value=False), \
             mock.patch.object(G, "load_from_cfg", return_value=(g, "")), \
             mock.patch.object(translate_local, "translate",
                               return_value=("平須さんが来た", "ja")):
            eng = ocr_google.OcrGoogleEngine({"translator": "local"})
            result = eng.translate_image(object())
        self.assertEqual(result.translation, "Hirasuさんが来た",
                         "OCR 路徑也要套輸出層的 => 規則")

    def test_ocr_engine_applies_source_rule_and_prompt(self):
        from engines import ocr_google, ocr_windows
        g = G.parse(GLOSS)
        cap = {}
        with mock.patch.object(ocr_windows, "recognize_best",
                               return_value=(["地下せんべいを買った"], "ja")), \
             mock.patch.object(ocr_windows, "looks_garbled", return_value=False), \
             mock.patch.object(G, "load_from_cfg", return_value=(g, "")), \
             mock.patch.object(translate_local, "translate", fake_local(cap)):
            eng = ocr_google.OcrGoogleEngine({"translator": "local"})
            eng.translate_image(object())
        self.assertEqual(cap["text"], "鹿せんべいを買った",
                         "OCR 路徑也要套 [src] 規則")
        self.assertIn("鹿せんべい → 鹿仙貝", cap["section"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

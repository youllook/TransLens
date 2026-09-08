"""翻譯入口與本地 LLM 引擎測試。

純函式與退版邏輯用 mock，不需要網路；另有一組需要真的 oMLX 的整合測試，
連不上就自動跳過（不會讓 CI 紅）。

執行：python -m unittest tests.test_translator -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from engines import translate_local, translator  # noqa: E402

LOCAL_URL = os.environ.get("TRANSLENS_LOCAL_LLM_URL", translate_local.DEFAULTS["local_llm_url"])
LOCAL_KEY = os.environ.get("OMLX_API_KEY", "")


def local_llm_up():
    """oMLX 活著才跑整合測試。"""
    try:
        base = LOCAL_URL.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        h = {"Authorization": f"Bearer {LOCAL_KEY}"} if LOCAL_KEY else {}
        return requests.get(base + "/models", headers=h, timeout=3).status_code == 200
    except Exception:  # noqa: BLE001
        return False


class TestCleanOutput(unittest.TestCase):
    """模型輸出清理：前綴、引號、thinking 區塊。"""

    def test_strip_prefix(self):
        for raw in ["譯文：你好", "翻譯: 你好", "中文：你好", "Translation: 你好",
                    "繁體中文：你好", "翻譯：中文：你好"]:
            self.assertEqual(translate_local.clean_output(raw), "你好", raw)

    def test_strip_wrapping_quotes(self):
        self.assertEqual(translate_local.clean_output("「你好」"), "你好")
        self.assertEqual(translate_local.clean_output('"你好"'), "你好")
        self.assertEqual(translate_local.clean_output("“你好”"), "你好")

    def test_keep_inner_quotes(self):
        # 句子中間的引號不能被吃掉
        self.assertEqual(translate_local.clean_output("他說「好」然後走了"), "他說「好」然後走了")

    def test_strip_think_block(self):
        self.assertEqual(
            translate_local.clean_output("<think>先想一下</think>你好"), "你好")

    def test_collapse_whitespace(self):
        self.assertEqual(translate_local.clean_output("你好\n  世界 "), "你好 世界")

    def test_empty(self):
        self.assertEqual(translate_local.clean_output(""), "")
        self.assertEqual(translate_local.clean_output(None), "")


class TestTraditionalGuard(unittest.TestCase):
    """簡體漏字保險：Qwen 實測會固定吐「早点回家」的「点」。"""

    def test_fixes_known_leak(self):
        out = translate_local._to_traditional("你最好早点回家")
        self.assertEqual(out, "你最好早點回家")
        self.assertNotIn("点", out)

    def test_leaves_traditional_alone(self):
        s = "喂，這是什麼意思。你在開什麼玩笑！"
        self.assertEqual(translate_local._to_traditional(s), s)

    def test_fallback_table_without_opencc(self):
        """沒裝 opencc 時，內建對照表也要能修掉常見簡體字。"""
        with mock.patch.object(translate_local, "_opencc_converter", None), \
             mock.patch.object(translate_local, "_opencc_tried", True):
            self.assertEqual(translate_local._to_traditional("早点回家"), "早點回家")
            self.assertEqual(translate_local._to_traditional("这个时候"), "這個時候")


class TestEndpointBuilder(unittest.TestCase):
    def test_variants(self):
        want = "http://h:8000/v1/chat/completions"
        for base in ["http://h:8000", "http://h:8000/", "http://h:8000/v1",
                     "http://h:8000/v1/", "http://h:8000/v1/chat/completions"]:
            self.assertEqual(translate_local._endpoint(base), want, base)


class TestApiKeyPrecedence(unittest.TestCase):
    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"OMLX_API_KEY": "from-env"}):
            self.assertEqual(translate_local.api_key({"local_llm_api_key": "from-cfg"}), "from-env")

    def test_falls_back_to_config(self):
        with mock.patch.dict(os.environ, {"OMLX_API_KEY": ""}):
            self.assertEqual(translate_local.api_key({"local_llm_api_key": "from-cfg"}), "from-cfg")


def _fake_response(content):
    r = mock.Mock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}}]}
    return r


class TestLocalTranslate(unittest.TestCase):
    """本地引擎本身（HTTP 用 mock）。"""

    def test_success_cleans_and_converts(self):
        # 清掉環境變數，才測得到 config 裡的 key（否則 OMLX_API_KEY 會優先）
        with mock.patch.dict(os.environ, {"OMLX_API_KEY": ""}), \
             mock.patch.object(translate_local.requests, "post",
                               return_value=_fake_response("譯文：「你最好早点回家」")) as post:
            zh, lang = translate_local.translate("head home early", source="en",
                                                 cfg={"local_llm_api_key": "k"})
        self.assertEqual(zh, "你最好早點回家")
        self.assertEqual(lang, "en")
        # 有 key 就要帶 Authorization
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer k")

    def test_empty_input_short_circuits(self):
        with mock.patch.object(translate_local.requests, "post") as post:
            self.assertEqual(translate_local.translate("   "), ("", None))
        post.assert_not_called()

    def test_empty_response_raises(self):
        with mock.patch.object(translate_local.requests, "post",
                               return_value=_fake_response("   ")):
            with self.assertRaises(RuntimeError):
                translate_local.translate("hello")

    def test_401_gives_readable_error(self):
        r = mock.Mock()
        r.status_code = 401
        with mock.patch.object(translate_local.requests, "post", return_value=r):
            with self.assertRaises(RuntimeError) as ctx:
                translate_local.translate("hello")
        self.assertIn("401", str(ctx.exception))


class TestTranslatorRouting(unittest.TestCase):
    """統一入口的選擇與退版行為。"""

    def test_local_success(self):
        with mock.patch.object(translator, "_local", return_value=("你好", "en")) as loc, \
             mock.patch.object(translator, "_google") as goo:
            zh, lang, info = translator.translate("hi", {"translator": "local"})
        self.assertEqual(zh, "你好")
        self.assertEqual(info.backend, "local")
        self.assertFalse(info.fell_back)
        self.assertIn("oMLX", info.status())
        loc.assert_called_once()
        goo.assert_not_called()

    def test_local_failure_falls_back_to_google(self):
        with mock.patch.object(translator, "_local", side_effect=requests.ConnectionError("boom")), \
             mock.patch.object(translator, "_google", return_value=("你好G", "en")) as goo:
            zh, lang, info = translator.translate("hi", {"translator": "local"})
        self.assertEqual(zh, "你好G")
        self.assertEqual(info.backend, "google")
        self.assertTrue(info.fell_back)
        self.assertIn("ConnectionError", info.error)
        self.assertIn("本地失敗", info.status())
        self.assertTrue(info.notes and "改用 Google" in info.notes[0])
        goo.assert_called_once()

    def test_local_timeout_falls_back(self):
        with mock.patch.object(translator, "_local", side_effect=requests.Timeout("slow")), \
             mock.patch.object(translator, "_google", return_value=("G", None)):
            _zh, _lang, info = translator.translate("hi", {"translator": "local"})
        self.assertTrue(info.fell_back)

    def test_google_choice_skips_local(self):
        with mock.patch.object(translator, "_local") as loc, \
             mock.patch.object(translator, "_google", return_value=("你好G", "en")):
            _zh, _lang, info = translator.translate("hi", {"translator": "google"})
        self.assertEqual(info.backend, "google")
        self.assertFalse(info.fell_back)
        loc.assert_not_called()

    def test_both_fail_raises(self):
        with mock.patch.object(translator, "_local", side_effect=RuntimeError("local down")), \
             mock.patch.object(translator, "_google", side_effect=RuntimeError("google down")):
            with self.assertRaises(RuntimeError) as ctx:
                translator.translate("hi", {"translator": "local"})
        self.assertIn("local down", str(ctx.exception))

    def test_empty_text(self):
        zh, lang, info = translator.translate("  ", {"translator": "local"})
        self.assertEqual((zh, lang), ("", None))
        self.assertEqual(info.backend, "none")

    def test_default_is_local(self):
        with mock.patch.object(translator, "_local", return_value=("你好", None)) as loc:
            _zh, _lang, info = translator.translate("hi", {})     # 沒給 translator 鍵
        self.assertEqual(info.backend, "local")
        loc.assert_called_once()


@unittest.skipUnless(local_llm_up(), f"本地 LLM 連不上（{LOCAL_URL}）")
class TestLocalLlmIntegration(unittest.TestCase):
    """真的打 oMLX：確認譯文是繁體、且沒有簡體字漏出來。"""

    CFG = {"local_llm_url": LOCAL_URL, "local_llm_api_key": LOCAL_KEY,
           "target_lang": "zh-TW", "translator": "local"}

    # 常見簡體字，用來確認譯文乾淨
    SIMPLIFIED = set("点这来个们时过没样东车话说读应变发当会还么产电视频质国语学习实现题问经济动务开关闭长门对间")

    def _assert_traditional(self, zh):
        bad = sorted({c for c in zh if c in self.SIMPLIFIED})
        self.assertFalse(bad, f"譯文出現簡體字 {bad}：{zh}")

    def test_japanese(self):
        zh, _lang = translate_local.translate(
            "ここが、お前の探していた場所だ。足元に気をつけろ。", source="ja", cfg=self.CFG)
        print(f"\n[local ja] {zh}")
        self.assertTrue(zh.strip())
        self._assert_traditional(zh)

    def test_english_no_simplified_leak(self):
        zh, _lang = translate_local.translate(
            "A storm is coming tonight, so you should head home early.",
            source="en", cfg=self.CFG)
        print(f"\n[local en] {zh}")
        self.assertTrue(zh.strip())
        self._assert_traditional(zh)

    def test_via_translator_entry(self):
        zh, _lang, info = translator.translate("Watch your step.", self.CFG, source="en")
        print(f"\n[local entry] {zh} | {info.status()}")
        self.assertEqual(info.backend, "local")
        self.assertTrue(zh.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)

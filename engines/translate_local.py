"""區網本地 LLM 翻譯：打 OpenAI 相容的 chat completions 端點（預設 MacMendy 上的 oMLX）。

與 translate_google.translate() 介面相同，但文字只在區網內流動，不會出外網。

設定鍵（見 translens.py DEFAULT_CONFIG）：
    local_llm_url          例 http://192.168.0.49:8000/v1（結尾有無 /v1 都吃）
    local_llm_model        例 Qwen2.5-7B-Instruct-4bit
    local_llm_api_key      伺服器的 api key；環境變數 OMLX_API_KEY 優先
    local_llm_timeout_sec  單次請求逾時秒數

繁體保險：Qwen 這類模型即使被要求「只准繁體」，仍會固定在某些詞吐簡體
（實測「早点回家」的「点」6/6 必現）。所以譯文一律再過一次簡→繁轉換：
有裝 opencc 就用 opencc（s2tw），沒裝就用內建常用字對照表兜底。
"""
import os
import re

import requests

DEFAULTS = {
    "local_llm_url": "http://192.168.0.49:8000/v1",
    "local_llm_model": "Qwen2.5-7B-Instruct-4bit",
    "local_llm_api_key": "",
    "local_llm_timeout_sec": 20,
}

# 格式規定與風格規定分開存：prompt_mode=replace 讓使用者整段換掉「風格」，
# 但「只輸出譯文本身」這段格式規定一定要留著，否則模型會加上解釋或前綴，
# 字幕面板就會顯示一堆廢話。
FORMAT_PROMPT = (
    "你是專業的日文／英文影視字幕翻譯，把使用者給的句子翻成台灣繁體中文。\n"
    "嚴格規則：\n"
    "1. 只輸出譯文本身，不要任何解釋、注音、原文、引號或「譯文：」之類的前綴。\n"
    "2. 如果原句沒有可翻譯的內容，就原樣輸出。"
)

# 風格規定：prompt_mode=replace 時整段換成使用者自己寫的。
STYLE_PROMPT = (
    "風格規定：\n"
    "* 全部使用繁體字（正體中文），絕對不可出現簡體字。\n"
    "* 用台灣慣用語（品質／影片／網路／資訊／程式），不要中國用語。\n"
    "* 保留原句語氣：口語就翻得口語，粗俗就翻得粗俗，正式就翻得正式。\n"
    "* 人名、地名採常見的中文譯法。"
)

SYSTEM_PROMPT = FORMAT_PROMPT + "\n" + STYLE_PROMPT


def build_system_prompt(custom="", mode="append", glossary_section=""):
    """組 system prompt：內建規定 + 使用者的 prompt.txt + 這句用得到的詞彙表。

    mode="append"（預設）把使用者的 prompt 接在內建規定後面；
    mode="replace" 用它整段取代「風格規定」，但**格式規定一定保留**
    —— 那段被換掉的話模型會開始加解釋與前綴，字幕就不能看了。

    後面的規定對模型的權重比較高，所以順序是：格式 → 風格 → 詞彙表。
    7B 這種尺寸的模型對「範例」的服從度高於「規則」，所以 prompt.txt 的
    範本建議使用者直接寫「原文→想要的譯法」幾行範例。
    """
    parts = [FORMAT_PROMPT]
    custom = (custom or "").strip()
    if mode == "replace" and custom:
        parts.append(custom)
    else:
        parts.append(STYLE_PROMPT)
        if custom:
            parts.append("額外要求（優先於上面的風格規定）：\n" + custom)
    if glossary_section:
        parts.append(glossary_section)
    return "\n".join(p for p in parts if p)

# --- 簡→繁兜底表 ---------------------------------------------------------
# 只收「模型實測會漏出來、且在繁中語境不具其他意義」的常用簡體字。
# 一對多有歧義的字（例如 干/乾/幹、后/後、里/裡）交給 opencc 處理，
# 沒有 opencc 時寧可不動，避免把正確的字改壞。
_SIMP_FALLBACK = {
    "点": "點", "这": "這", "来": "來", "个": "個", "们": "們", "时": "時",
    "过": "過", "没": "沒", "样": "樣", "东": "東", "车": "車", "话": "話",
    "说": "說", "读": "讀", "应": "應", "变": "變", "发": "發", "当": "當",
    "会": "會", "还": "還", "么": "麼", "产": "產", "电": "電", "视": "視",
    "频": "頻", "质": "質", "国": "國", "语": "語", "学": "學", "习": "習",
    "实": "實", "现": "現", "题": "題", "问": "問", "经": "經", "济": "濟",
    "动": "動", "务": "務", "开": "開", "关": "關", "闭": "閉", "长": "長",
    "门": "門", "对": "對", "间": "間", "认": "認", "为": "為", "样": "樣",
    "给": "給", "让": "讓", "从": "從", "众": "眾", "点": "點", "带": "帶",
    "帮": "幫", "边": "邊", "远": "遠", "进": "進", "运": "運", "过": "過",
    "选": "選", "结": "結", "终": "終", "级": "級", "线": "線", "统": "統",
    "务": "務", "备": "備", "复": "復", "态": "態", "总": "總", "间": "間",
    "证": "證", "识": "識", "记": "記", "讲": "講", "论": "論", "议": "議",
    "计": "計", "许": "許", "该": "該", "请": "請", "谁": "誰", "谢": "謝",
}

_opencc_converter = None
_opencc_tried = False


def _to_traditional(text: str) -> str:
    """把譯文裡漏出來的簡體字轉成繁體。opencc 優先，沒裝就用內建對照表。"""
    if not text:
        return text
    global _opencc_converter, _opencc_tried
    if not _opencc_tried:
        _opencc_tried = True
        try:
            import opencc
            _opencc_converter = opencc.OpenCC("s2tw")
        except Exception:  # noqa: BLE001 — 沒裝 opencc 就走兜底表
            _opencc_converter = None
    if _opencc_converter is not None:
        try:
            return _opencc_converter.convert(text)
        except Exception:  # noqa: BLE001
            pass
    return "".join(_SIMP_FALLBACK.get(ch, ch) for ch in text)


# --- 輸出清理 -------------------------------------------------------------
# 模型偶爾會加上「譯文：」「中文：」之類的前綴，或整句包在引號裡。
_PREFIX_RE = re.compile(
    r"^\s*(?:譯文|翻譯|中文|繁體中文|台灣繁體中文|translation|chinese)\s*[:：]\s*",
    re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_QUOTE_PAIRS = [("「", "」"), ("『", "』"), ('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’")]


def clean_output(text: str) -> str:
    """去掉 thinking 區塊、前綴、整句包覆的引號與多餘空白。"""
    if not text:
        return ""
    t = _THINK_RE.sub("", text).strip()
    # 前綴可能疊兩層（「翻譯：中文：…」），最多剝三次
    for _ in range(3):
        new = _PREFIX_RE.sub("", t).strip()
        if new == t:
            break
        t = new
    # 整句被引號包住才剝；句中的引號要留著
    for _ in range(2):
        stripped = False
        for lq, rq in _QUOTE_PAIRS:
            if len(t) >= 2 and t.startswith(lq) and t.endswith(rq) and rq not in t[1:-1]:
                t = t[1:-1].strip()
                stripped = True
                break
        if not stripped:
            break
    return " ".join(t.split())


def _endpoint(base: str) -> str:
    """把設定裡的 base url 補成 .../v1/chat/completions。"""
    b = (base or DEFAULTS["local_llm_url"]).strip().rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    if not b.endswith("/v1"):
        b += "/v1"
    return b + "/chat/completions"


def api_key(cfg: dict = None) -> str:
    """環境變數 OMLX_API_KEY 優先，其次才是 config。"""
    env = os.environ.get("OMLX_API_KEY", "").strip()
    if env:
        return env
    return str((cfg or {}).get("local_llm_api_key") or "").strip()


def translate(text: str, target="zh-TW", source="auto", timeout=15, cfg=None,
              glossary_section="", custom_prompt=""):
    """回傳 (譯文, 來源語言)。介面與 translate_google.translate 相同。

    來源語言這裡不做偵測（本地 LLM 不回報），直接把呼叫端給的 source 傳回去，
    讓上層狀態列仍有東西可顯示。

    glossary_section / custom_prompt 由 translator 那邊組好傳進來
    （見 build_system_prompt）；都空就等於維持原本的內建 prompt。
    """
    if not text.strip():
        return "", None
    cfg = cfg or {}
    url = _endpoint(cfg.get("local_llm_url", DEFAULTS["local_llm_url"]))
    model = cfg.get("local_llm_model") or DEFAULTS["local_llm_model"]
    timeout = float(cfg.get("local_llm_timeout_sec") or timeout or DEFAULTS["local_llm_timeout_sec"])

    # 字幕一句通常很短；上限抓輸入長度的兩倍多一點，避免模型囉嗦或跑掉。
    max_tokens = max(64, min(512, len(text) * 2 + 64))

    hint = ""
    if source and source != "auto":
        hint = {"ja": "（原文是日文）", "en": "（原文是英文）"}.get(source, "")

    system = build_system_prompt(
        custom_prompt, str(cfg.get("prompt_mode", "append") or "append").lower(),
        glossary_section)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{hint}{text}" if hint else text},
        ],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": max_tokens,
        "stream": False,
        # Qwen3 之類支援 thinking 的模型：關掉，字幕不需要推理過程且會拖慢。
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Content-Type": "application/json"}
    key = api_key(cfg)
    if key:
        headers["Authorization"] = f"Bearer {key}"

    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if r.status_code == 401:
        raise RuntimeError("本地 LLM 拒絕連線（401）：api key 不對，請檢查 local_llm_api_key 或 OMLX_API_KEY")
    r.raise_for_status()
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"本地 LLM 回應格式不符：{data}") from e

    out = _to_traditional(clean_output(content))
    if not out:
        raise RuntimeError("本地 LLM 回應為空")
    return out, (source if source != "auto" else None)

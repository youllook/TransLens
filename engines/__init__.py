"""引擎註冊表：每個引擎都提供 translate_image(PIL.Image) -> EngineResult。"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EngineResult:
    translation: str                 # 繁體中文譯文
    original: Optional[str] = None   # 原文（OCR 類引擎才有）
    source_lang: Optional[str] = None
    notes: list = field(default_factory=list)


ENGINE_SPECS = [
    # key, 顯示名, 模組, 類別
    ("ocr_google", "OCR+Google（免金鑰）", "engines.ocr_google", "OcrGoogleEngine"),
    ("gemini_api", "Gemini API", "engines.gemini_api", "GeminiApiEngine"),
    ("gemini_cli", "Gemini CLI", "engines.gemini_cli", "GeminiCliEngine"),
    ("claude_api", "Claude API", "engines.claude_api", "ClaudeApiEngine"),
]

_cache = {}


def engine_labels():
    return [(k, label) for k, label, _, _ in ENGINE_SPECS]


def get_engine(key, config):
    """依 key 取得引擎實例（延遲 import，缺套件時才在該引擎報錯）。"""
    if key in _cache:
        return _cache[key]
    for k, _label, mod_name, cls_name in ENGINE_SPECS:
        if k == key:
            import importlib
            mod = importlib.import_module(mod_name)
            inst = getattr(mod, cls_name)(config)
            _cache[key] = inst
            return inst
    raise KeyError(f"未知引擎: {key}")


AI_PROMPT = (
    "你是遊戲畫面即時翻譯助手。請辨識圖片中的所有文字（介面、對話、說明皆算），"
    "翻譯成自然流暢的繁體中文（台灣用語）。規則：\n"
    "1. 只輸出譯文，不要前言、解釋、標題或 Markdown。\n"
    "2. 保留原文的分行與段落結構；專有名詞可在後面用括號附上原文。\n"
    "3. 若圖片中沒有可辨識文字，只輸出：（未偵測到文字）"
)

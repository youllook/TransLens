"""預設引擎：Windows OCR 抓文字 → 翻譯成繁中。

翻譯走 engines.translator 統一入口：預設打區網本地 LLM（文字不出門），
連不上才自動退到免金鑰的 Google 端點。
"""
from . import EngineResult
from . import ocr_windows, translator

GARBLED_HINT = ("⚠ 辨識結果像亂碼：可能沒有這個語言的 Windows OCR 語言包。"
                "請用 ⚙ → OCR 語言 安裝，或改用 Gemini 引擎")


class OcrGoogleEngine:
    key = "ocr_google"

    def __init__(self, config):
        self.config = config

    def translate_image(self, img) -> EngineResult:
        lines, used_lang = ocr_windows.recognize_best(img, self.config.get("ocr_lang", "auto"))
        if not lines:
            return EngineResult("（未偵測到文字）", original="", notes=[f"OCR 語言: {used_lang}"])
        notes = [f"OCR 語言: {used_lang}"]
        if ocr_windows.looks_garbled(lines):
            notes.append(GARBLED_HINT)
        original = ocr_windows.merge_lines(lines)
        translated, detected, info = translator.translate(original, self.config)
        notes.append(f"翻譯: {info.status()}")
        notes.extend(info.notes)
        return EngineResult(translated, original=original, source_lang=detected, notes=notes)

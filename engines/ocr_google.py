"""預設引擎：Windows OCR 抓文字 → Google 翻譯成繁中。全程免金鑰。"""
from . import EngineResult
from . import ocr_windows, translate_google

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
        translated, detected = translate_google.translate(original, self.config.get("target_lang", "zh-TW"))
        return EngineResult(translated, original=original, source_lang=detected, notes=notes)

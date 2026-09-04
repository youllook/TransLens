"""Gemini API 直連（一步完成 OCR＋翻譯，遊戲美術字辨識最穩）。
需要環境變數 GEMINI_API_KEY（https://aistudio.google.com/apikey 免費申請）。"""
import base64
import io
import os

import requests

from . import AI_PROMPT, EngineResult


class GeminiApiEngine:
    key = "gemini_api"

    def __init__(self, config):
        self.model = config.get("gemini_model", "gemini-2.5-flash")
        self.api_key = os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key", "")

    def translate_image(self, img) -> EngineResult:
        if not self.api_key:
            raise RuntimeError("未設定 GEMINI_API_KEY（環境變數或 config.json 的 gemini_api_key）。")
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                    {"text": AI_PROMPT},
                ]
            }],
            "generationConfig": {"temperature": 0.2},
        }
        r = requests.post(url, params={"key": self.api_key}, json=body, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Gemini API {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError):
            raise RuntimeError(f"Gemini 回應格式異常: {str(data)[:300]}")
        return EngineResult(text or "（未偵測到文字）", notes=[f"模型: {self.model}"])

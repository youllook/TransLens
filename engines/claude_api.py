"""Claude API（anthropic SDK）視覺翻譯。需 pip install anthropic 並設定 ANTHROPIC_API_KEY
（或 ant auth login）。預設模型 claude-opus-5，低 effort 換速度；已開啟伺服器端 refusal fallback。"""
import base64
import io

from . import AI_PROMPT, EngineResult


class ClaudeApiEngine:
    key = "claude_api"

    def __init__(self, config):
        self.model = config.get("claude_model", "claude-opus-5")
        self._client = None

    def _client_or_raise(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise RuntimeError("未安裝 anthropic 套件：pip install anthropic")
            self._client = anthropic.Anthropic()
        return self._client

    def translate_image(self, img) -> EngineResult:
        client = self._client_or_raise()
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        response = client.beta.messages.create(
            model=self.model,
            max_tokens=4000,  # 譯文短，刻意壓低
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": AI_PROMPT},
                ],
            }],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude 拒絕處理此畫面（refusal）。")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return EngineResult(text or "（未偵測到文字）", notes=[f"模型: {self.model}"])

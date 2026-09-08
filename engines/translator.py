"""翻譯統一入口：依設定選 本地 LLM 或 Google，本地失敗自動退到 Google。

呼叫端（ocr_google、audio_subtitle）都走這裡，不要再直接 import translate_google，
這樣切換翻譯來源只要改一個設定鍵。

    zh, lang, info = translate(text, cfg, source="auto")

info 是 TranslateInfo，帶著「這句實際上是誰翻的、花多久、有沒有退版」，
讓 UI 狀態列可以顯示「翻譯: oMLX 2.1s」或「本地翻譯失敗，已改用 Google」。
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_TRANSLATOR = "local"

# 設定值 -> 狀態列顯示名
BACKEND_LABELS = {"local": "oMLX", "google": "Google"}


@dataclass
class TranslateInfo:
    backend: str                      # 實際用的後端："local" / "google"
    seconds: float = 0.0
    fell_back: bool = False           # 本地失敗退到 Google
    error: Optional[str] = None       # 本地失敗的原因（人看得懂的字串）
    notes: list = field(default_factory=list)

    @property
    def label(self):
        return BACKEND_LABELS.get(self.backend, self.backend)

    def status(self):
        """給狀態列的一小段字，例如 'oMLX 2.1s' 或 'Google 0.4s（本地失敗）'。"""
        s = f"{self.label} {self.seconds:.1f}s"
        if self.fell_back:
            s += "（本地失敗）"
        return s


def _google(text, target, source, timeout):
    from . import translate_google
    return translate_google.translate(text, target=target, source=source, timeout=timeout)


def _local(text, target, source, cfg):
    from . import translate_local
    return translate_local.translate(text, target=target, source=source, cfg=cfg)


def translate(text: str, cfg: dict = None, source="auto"):
    """回傳 (譯文, 來源語言, TranslateInfo)。

    translator=local 時先打區網 LLM；連不上、逾時、回空都自動退到 Google
    （並在 info 記一筆讓 UI 顯示）。translator=google 則直接走 Google。
    """
    cfg = cfg or {}
    target = cfg.get("target_lang", "zh-TW")
    if not text or not text.strip():
        return "", None, TranslateInfo(backend="none")

    choice = str(cfg.get("translator", DEFAULT_TRANSLATOR) or DEFAULT_TRANSLATOR).lower()

    if choice == "local":
        t0 = time.time()
        try:
            zh, lang = _local(text, target, source, cfg)
            return zh, lang, TranslateInfo(backend="local", seconds=time.time() - t0)
        except Exception as e:  # noqa: BLE001 — 本地任何失敗都要能退到 Google
            err = f"{type(e).__name__}: {e}"
            logging.warning("本地 LLM 翻譯失敗，改用 Google：%s", err)
            t1 = time.time()
            try:
                zh, lang = _google(text, target, source, cfg.get("translate_timeout_sec", 12))
            except Exception:  # noqa: BLE001 — 兩邊都掛就把本地錯誤一起帶出去
                raise RuntimeError(f"本地與 Google 翻譯都失敗（本地：{err}）")
            return zh, lang, TranslateInfo(
                backend="google", seconds=time.time() - t1, fell_back=True, error=err,
                notes=[f"⚠ 本地翻譯失敗，已改用 Google：{err}"])

    t0 = time.time()
    zh, lang = _google(text, target, source, cfg.get("translate_timeout_sec", 12))
    return zh, lang, TranslateInfo(backend="google", seconds=time.time() - t0)

"""免金鑰翻譯：Google（dict-chrome-ex 端點，最寬鬆）→ Google gtx → MyMemory 逐級備援。
皆為非官方端點，流量過大可能被暫時限流（429）。"""
import requests

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}


def _google_dict(text, target, source, timeout):
    r = requests.get("https://clients5.google.com/translate_a/t",
                     params={"client": "dict-chrome-ex", "sl": source, "tl": target, "q": text},
                     headers=_UA, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # 形式一：[["譯文","en"]]；形式二（sl 指定時）：["譯文"]
    item = data[0]
    if isinstance(item, list):
        return item[0], (item[1] if len(item) > 1 else None)
    return item, None


def _google_gtx(text, target, source, timeout):
    r = requests.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text},
                     headers=_UA, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return "".join(seg[0] for seg in data[0] if seg and seg[0]), (data[2] if len(data) > 2 else None)


def _mymemory(text, target, source, timeout):
    src = "autodetect" if source == "auto" else source
    r = requests.get("https://api.mymemory.translated.net/get",
                     params={"q": text[:500], "langpair": f"{src}|{target}"},
                     headers=_UA, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("responseStatus") not in (200, "200"):
        raise RuntimeError(f"MyMemory: {data.get('responseDetails')}")
    return data["responseData"]["translatedText"], data.get("responseData", {}).get("detectedLanguage")


def translate(text: str, target="zh-TW", source="auto", timeout=12):
    """回傳 (譯文, 偵測到的來源語言)。依序嘗試三個端點。"""
    if not text.strip():
        return "", None
    errors = []
    for fn in (_google_dict, _google_gtx, _mymemory):
        try:
            return fn(text, target, source, timeout)
        except Exception as e:  # noqa: BLE001 — 逐級備援
            errors.append(f"{fn.__name__}: {e}")
    raise RuntimeError("所有翻譯端點皆失敗：" + " | ".join(errors))

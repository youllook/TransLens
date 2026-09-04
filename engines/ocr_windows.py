"""Windows 10/11 內建 OCR（Windows.Media.Ocr），離線、免費。
可辨識語言 = 系統已安裝並勾選 OCR 功能的語言包。

注意：Windows OCR 對中日韓文字是「一個字一個詞」，OcrLine.text 會在字與字之間補空格，
所以這裡一律先把 CJK 字間空格去掉（despace）再做後續處理。"""
import asyncio
import io
import re

from PIL import Image


def available_languages():
    from winsdk.windows.media.ocr import OcrEngine
    return [(l.language_tag, l.display_name) for l in OcrEngine.available_recognizer_languages]


def _make_engine(lang_tag):
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.globalization import Language
    eng = None
    if lang_tag and lang_tag != "auto":
        eng = OcrEngine.try_create_from_language(Language(lang_tag))
    if eng is None:
        eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        raise RuntimeError("找不到可用的 Windows OCR 語言包，請到「設定 > 時間與語言 > 語言」新增語言並安裝 OCR。")
    return eng


def _prepare(img: Image.Image) -> Image.Image:
    """小圖放大有助辨識；超過引擎上限則縮小。"""
    from winsdk.windows.media.ocr import OcrEngine
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) < 1400:
        scale = 2
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
    limit = OcrEngine.max_image_dimension
    w, h = img.size
    if max(w, h) > limit:
        s = limit / max(w, h)
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    return img


async def _recognize_async(img: Image.Image, lang_tag):
    from winsdk.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    from winsdk.windows.graphics.imaging import BitmapDecoder

    buf = io.BytesIO()
    _prepare(img).save(buf, format="PNG")
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(buf.getvalue())
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = _make_engine(lang_tag)
    result = await engine.recognize_async(bitmap)
    lines = [despace(ln.text) for ln in result.lines if ln.text.strip()]
    return lines, engine.recognizer_language.language_tag


def recognize(img: Image.Image, lang_tag="auto"):
    """回傳 (lines: list[str], used_lang_tag)。可在任意執行緒呼叫。"""
    return asyncio.run(_recognize_async(img, lang_tag))


# ----------------------------------------------------------------------------- 文字分類
def _is_kana(ch):
    return 0x3040 <= ord(ch) <= 0x30FF


def _is_hangul(ch):
    return 0xAC00 <= ord(ch) <= 0xD7AF


def _is_han(ch):
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF and not _is_bad(ch)


def _is_cjk(ch):
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF
            or 0xFF00 <= o <= 0xFFEF)


def _is_bad(ch):
    """語言包不符時的典型垃圾：部件字（丶亠冂…）、反斜線、豎線等。"""
    o = ord(ch)
    return ch in "\\|~^`丶乀乁乚亠冂冖厶" or 0x2E80 <= o <= 0x2FDF or 0x31C0 <= o <= 0x31EF


_CJK_SPACE = re.compile(
    r"(?<=[぀-ヿ㐀-鿿가-힯＀-￯　-〿])\s+"
    r"(?=[぀-ヿ㐀-鿿가-힯＀-￯　-〿0-9A-Za-z])"
    r"|(?<=[0-9A-Za-z])\s+(?=[぀-ヿ㐀-鿿가-힯＀-￯　-〿])")


def despace(text):
    """去掉 Windows OCR 在 CJK 字間補的空格，拉丁單字之間的空格保留。"""
    return _CJK_SPACE.sub("", text).strip()


def _profile(lines):
    text = "".join(lines)
    chars = [c for c in text if not c.isspace()]
    n = max(1, len(chars))
    kana = sum(map(_is_kana, chars))
    hangul = sum(map(_is_hangul, chars))
    han = sum(map(_is_han, chars))
    latin = sum(1 for c in chars if c.isascii() and c.isalpha())
    bad = sum(map(_is_bad, chars))
    # 夾在 CJK 之間的孤立 ASCII 字母/數字（「夕食時O世」的 O）也算垃圾
    for i in range(1, len(chars) - 1):
        if chars[i].isascii() and chars[i].isalnum() and _is_cjk(chars[i - 1]) and _is_cjk(chars[i + 1]):
            bad += 1
    return {"n": n, "kana": kana / n, "hangul": hangul / n, "han": han / n,
            "latin": latin / n, "bad": bad / n, "total": len(chars)}


def looks_garbled(lines):
    """語言包不符（例如用繁中包讀日文）：垃圾字元比例偏高。"""
    p = _profile(lines)
    return p["total"] >= 6 and p["bad"] >= 0.10


def recognize_best(img: Image.Image, lang_tag="auto"):
    """auto 且裝了多個 OCR 語言包時，每個都跑一次，依文字特徵挑最合理的（各約 0.1 秒）。

    規則：出現足量假名 → 日文包；出現韓文 → 韓文包；文字以拉丁字母為主 → 拉丁語系的包；
    其餘（漢字為主）→ 垃圾字元最少、字數最多者。"""
    if lang_tag != "auto":
        return recognize(img, lang_tag)
    langs = [tag for tag, _ in available_languages()]
    if len(langs) <= 1:
        return recognize(img, "auto")

    results = []
    for tag in langs:
        try:
            lines, used = recognize(img, tag)
        except Exception:  # noqa: BLE001
            continue
        if lines:
            results.append((tag, lines, used, _profile(lines)))
    if not results:
        return recognize(img, "auto")

    def pick(pred):
        cands = [r for r in results if pred(r)]
        return max(cands, key=lambda r: r[3]["total"] * (1 - r[3]["bad"])) if cands else None

    chosen = (pick(lambda r: r[0].startswith("ja") and r[3]["kana"] >= 0.15)
              or pick(lambda r: r[0].startswith("ko") and r[3]["hangul"] >= 0.30))
    if chosen is None:
        latin_heavy = [r for r in results if r[3]["latin"] >= 0.6 and r[3]["han"] < 0.1]
        if latin_heavy:
            chosen = pick(lambda r: not r[0].startswith(("ja", "ko", "zh")) and r in latin_heavy) \
                or max(latin_heavy, key=lambda r: r[3]["total"])
    if chosen is None:
        # 漢字為主且沒有假名：分數平手時偏向中文包（zh-*），其次才是日文包
        chosen = max(results, key=lambda r: (round(r[3]["total"] * (1 - 2 * r[3]["bad"])),
                                             r[0].startswith("zh"), not r[0].startswith("ja")))
    return chosen[1], chosen[2]


# ----------------------------------------------------------------------------- 後處理
_MID_I = re.compile(r"(?<=[a-z])I+(?=[a-z])")
_WORD_I = re.compile(r"\bI(?=[a-z]{2,}\b)")
_KEEP = {"Ice", "Ill", "Ion", "Ink", "Inn", "Icy", "Ivy", "Iris", "Iron", "Idle", "Idea", "Item", "Into", "Isle"}


def fix_latin(text):
    """繁中 OCR 包常把小寫 l 讀成大寫 I（HoId→Hold、CoIIect→Collect），在此修正。"""
    text = _MID_I.sub(lambda m: "l" * len(m.group()), text)

    def head(m):
        word = m.group() + text[m.end():m.end() + 8]
        w = re.match(r"[A-Za-z]+", word).group()
        return "I" if w in _KEEP else "l"
    return _WORD_I.sub(head, text)


def merge_lines(lines):
    """把被排版硬切開的句子接回去，翻譯品質更好；句尾標點處保留換行。"""
    lines = [fix_latin(ln) for ln in lines]
    enders = tuple(".!?:;。！？：」』)）]")
    out, cur = [], ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not cur:
            cur = ln
        else:
            joiner = "" if _is_cjk(cur[-1]) or _is_cjk(ln[0]) else " "
            cur = cur + joiner + ln
        if cur.endswith(enders):
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return "\n".join(out)

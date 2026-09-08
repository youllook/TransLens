"""替詞表與自訂 prompt：使用者用記事本就能改的三層修正。

一份純文字的 glossary 檔同時餵三個地方：

    原文 = 譯文      對照詞。左邊餵 whisper 的 prompt（讓它比較可能聽對），
                     右邊組成 LLM 的詞彙表（讓它照著翻）。
    錯的 => 對的     輸出後的確定性字串取代，只動譯文。
    [src] 錯 => 對   同上但動原文，而且在翻譯「之前」就套，所以譯文也會受益。

為什麼要三層：whisper 的 prompt 只是「提示」不是「指令」——同一段音訊，
提示詞重複兩次會把某個詞修好，重複三次反而又壞回去。所以聽錯的字一定要有
`=>` 這種確定性的手段兜底，prompt 只是讓它少錯一點。

檔案解析刻意寬容：全形等號、前後空白、空行、`#` 註解都吃，
因為這是要給非工程師用記事本編輯的東西，壞一行不該讓整片跑不動。

TransLens 的檔案位置（與 SubLens 的「影片旁同名檔」不同——這裡沒有影片檔）：
    glossary.txt / prompt.txt   程式目錄下的全域檔，commit 一份帶註解的範本
    ⚙ 選單指定的額外一份         例如某個遊戲專用的詞彙表，疊在全域之上
"""
import os
import re

GLOSSARY_NAME = "glossary.txt"
PROMPT_NAME = "prompt.txt"

# whisper 的 prompt 有 token 上限（whisper.cpp 是 n_text_ctx/2，約 224 token）。
# 超過會被截斷，而且太長本身就會稀釋效果，所以我們自己先擋。
MAX_ASR_PROMPT_CHARS = 200

SRC_MARKER = "[src]"

# 全形等號、全形箭頭都當成半形處理
_FULLWIDTH = str.maketrans({"＝": "=", "＞": ">", "　": " "})


class Rule:
    """一條 `=>` 取代規則。"""

    __slots__ = ("old", "new", "on_source")

    def __init__(self, old, new, on_source=False):
        self.old = old
        self.new = new
        self.on_source = on_source

    def __repr__(self):  # pragma: no cover - 只給除錯看
        tag = "[src] " if self.on_source else ""
        return f"<Rule {tag}{self.old!r} => {self.new!r}>"

    def __eq__(self, other):
        return (isinstance(other, Rule) and self.old == other.old
                and self.new == other.new and self.on_source == other.on_source)


class Glossary:
    """解析後的替詞表。

    terms      dict 原文 → 譯文（`=` 行），供 LLM 詞彙表與 whisper prompt 用
    rules      list[Rule]（`=>` 行），輸出層的確定性取代
    warnings   解析時的問題（重複鍵、格式錯誤），由呼叫端決定要不要印
    """

    def __init__(self, terms=None, rules=None, warnings=None):
        self.terms = dict(terms or {})
        self.rules = list(rules or [])
        self.warnings = list(warnings or [])

    def __bool__(self):
        return bool(self.terms or self.rules)

    def __len__(self):
        return len(self.terms) + len(self.rules)

    def merge(self, other):
        """把另一份疊上來，後來的優先（額外指定的蓋全域）。"""
        if not other:
            return self
        for k, v in other.terms.items():
            if k in self.terms and self.terms[k] != v:
                self.warnings.append(
                    f"詞彙「{k}」重複定義（{self.terms[k]} → {v}），採用後者")
            self.terms[k] = v
        # 規則不去重成 dict：順序有意義（長鍵先取代），但同 old 的後者蓋前者
        for r in other.rules:
            for i, exist in enumerate(self.rules):
                if exist.old == r.old and exist.on_source == r.on_source:
                    if exist.new != r.new:
                        self.warnings.append(
                            f"取代規則「{r.old}」重複定義"
                            f"（{exist.new} → {r.new}），採用後者")
                    self.rules[i] = r
                    break
            else:
                self.rules.append(r)
        return self

    # --- 三層套用 ---------------------------------------------------------
    def asr_words(self):
        """收集所有「原文」詞：`=` 行的左邊，加上 `[src] =>` 規則的右邊
        （那是我們希望 whisper 聽成的正確寫法）。去重並保持定義順序。"""
        words, seen = [], set()
        for w in list(self.terms.keys()) + [r.new for r in self.rules
                                            if r.on_source]:
            w = (w or "").strip()
            if w and w not in seen:
                seen.add(w)
                words.append(w)
        return words

    def asr_prompt(self, max_chars=MAX_ASR_PROMPT_CHARS):
        """組 whisper 的詞彙表提示詞，回傳 (prompt, 警告或 "")。

        每個詞以「。」收尾串起來。句號不是裝飾：whisper 會模仿 prompt 的
        「書寫風格」，prompt 裡沒有句號，它整片轉錄就也不打句號。

        超過 max_chars 就只取前面的詞並回一句警告 —— whisper 的 prompt 有
        token 上限，硬塞會被截斷。
        """
        words = self.asr_words()
        if not words:
            return "", ""
        kept, warn = [], ""
        for w in words:
            trial = _join_words(kept + [w])
            if len(trial) > max_chars and kept:
                warn = (f"詞彙表有 {len(words)} 個詞，超過 whisper prompt 長度上限"
                        f"（{max_chars} 字），只送前 {len(kept)} 個")
                break
            kept.append(w)
        return _join_words(kept), warn

    def terms_in(self, texts):
        """挑出這批原文裡真的出現過的詞，回傳 dict（保持定義順序）。

        每句只附出現過的詞，沒出現就不附 —— 一份幾十個詞的表全附進
        system prompt，每句都要多付一次那些 token，而且會稀釋模型注意力。
        """
        if isinstance(texts, str):
            texts = [texts]
        blob = "\n".join(texts or [])
        return {k: v for k, v in self.terms.items() if k and k in blob}

    def prompt_section(self, texts=None):
        """組 LLM system prompt 要附的詞彙表段落；沒有適用的詞就回空字串。"""
        terms = self.terms_in(texts) if texts is not None else self.terms
        if not terms:
            return ""
        lines = "\n".join(f"{k} → {v}" for k, v in terms.items())
        return "詞彙表（必須遵守）：\n" + lines

    def apply_source(self, text):
        """把 `[src]` 規則套在原文上。翻譯之前呼叫，讓譯文也受益。"""
        return _apply(text, [r for r in self.rules if r.on_source])

    def apply_output(self, text):
        """把一般 `=>` 規則套在譯文上。翻譯之後、顯示之前呼叫。"""
        return _apply(text, [r for r in self.rules if not r.on_source])


def _join_words(words):
    """把詞用「、」串起來並以「。」收尾。"""
    if not words:
        return ""
    return "、".join(words) + "。"


def _apply(text, rules):
    """純字串取代，長鍵先取代。

    中文沒有詞界，做不到「整詞比對」，所以這是無條件的 str.replace。
    副作用是短鍵可能誤中詞的一部分，所以長鍵先套，並在範本裡說明要寫長一點。

    已經替換過的部分不會再被後面的規則動到：先換成一個不可能出現在
    字幕裡的佔位符，全部套完再換回來。否則「鹿 => 梅花鹿」會吃掉
    「鹿せんべい => 鹿仙貝」的結果，變成「梅花鹿仙貝」。
    """
    if not text or not rules:
        return text
    done = []
    for r in sorted(rules, key=lambda r: len(r.old), reverse=True):
        if not r.old or r.old not in text:
            continue
        token = f"\x00{len(done)}\x00"
        text = text.replace(r.old, token)
        done.append(r.new)
    for i, new in enumerate(done):
        text = text.replace(f"\x00{i}\x00", new)
    return text


# --- 解析 -------------------------------------------------------------------
def parse(content: str) -> Glossary:
    """解析 glossary 檔內容。壞掉的行記進 warnings，不丟例外。"""
    terms, rules, warns = {}, [], []
    for lineno, raw in enumerate((content or "").splitlines(), 1):
        line = raw.translate(_FULLWIDTH).strip()
        if not line or line.startswith("#"):
            continue

        on_source = False
        if line.lower().startswith(SRC_MARKER):
            on_source = True
            line = line[len(SRC_MARKER):].strip()

        # `=>` 要先判：它也包含 `=`
        if "=>" in line:
            old, _, new = line.partition("=>")
            old, new = old.strip(), new.strip()
            if not old:
                warns.append(f"第 {lineno} 行：`=>` 左邊是空的，已略過")
                continue
            for i, exist in enumerate(rules):
                if exist.old == old and exist.on_source == on_source:
                    if exist.new != new:
                        warns.append(
                            f"第 {lineno} 行：取代規則「{old}」重複定義"
                            f"（{exist.new} → {new}），採用後者")
                    rules[i] = Rule(old, new, on_source)
                    break
            else:
                rules.append(Rule(old, new, on_source))
            continue

        if "=" in line:
            if on_source:
                warns.append(f"第 {lineno} 行：`[src]` 只能用在 `=>` 規則，已略過")
                continue
            src, _, dst = line.partition("=")
            src, dst = src.strip(), dst.strip()
            if not src or not dst:
                warns.append(f"第 {lineno} 行：`=` 兩邊都要有字，已略過")
                continue
            if src in terms and terms[src] != dst:
                warns.append(f"第 {lineno} 行：詞彙「{src}」重複定義"
                             f"（{terms[src]} → {dst}），採用後者")
            terms[src] = dst
            continue

        warns.append(f"第 {lineno} 行：看不懂（要有 `=` 或 `=>`），已略過：{line[:40]}")
    return Glossary(terms, rules, warns)


def parse_file(path: str) -> Glossary:
    """讀檔並解析。檔案不存在回空的 Glossary（不是錯誤，只是沒設定）。"""
    if not path or not os.path.isfile(path):
        return Glossary()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            g = parse(f.read())
    except OSError as e:
        return Glossary(warnings=[f"讀不到 {path}：{e}"])
    g.warnings = [f"{os.path.basename(path)} {w}" for w in g.warnings]
    return g


def app_dir() -> str:
    """程式目錄（engines/ 的上一層）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidate_paths(name: str, extra: str = ""):
    """依優先序回傳要讀的檔案路徑（後者蓋前者）：全域 → ⚙ 指定的額外一份。"""
    paths = [os.path.join(app_dir(), name)]
    if extra:
        paths.append(os.path.abspath(extra))
    return paths


def load(extra: str = "") -> Glossary:
    """載入全域 + 額外指定的 glossary，合併成一份。"""
    merged = Glossary()
    for p in candidate_paths(GLOSSARY_NAME, extra):
        merged.merge(parse_file(p))
    return merged


def load_prompt(extra: str = "") -> str:
    """載入全域 + 額外指定的自訂 prompt，依序接起來（`#` 註解不送給模型）。"""
    parts = []
    for p in candidate_paths(PROMPT_NAME, extra):
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except OSError:
            continue
        text = "\n".join(l for l in text.splitlines()
                         if not l.lstrip().startswith("#")).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def load_from_cfg(cfg=None):
    """依 config 載入 (glossary, custom_prompt)。

    設定鍵：
        glossary_file   ⚙ 指定的額外詞彙表路徑（空字串 = 只用全域那份）
        prompt_file     ⚙ 指定的額外 prompt 路徑
        glossary        設 false 可整個關掉（連全域那份也不讀）
    """
    cfg = cfg or {}
    if not cfg.get("glossary", True):
        return Glossary(), ""
    return (load(str(cfg.get("glossary_file") or "")),
            load_prompt(str(cfg.get("prompt_file") or "")))

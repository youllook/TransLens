"""whisper 的提示詞組裝與語言投票鎖定。

兩件事，都是為了讓即時字幕少出錯：

  1. 標點種子 + 詞彙表 → prompt 欄位
     whisper 會模仿 prompt 的「書寫風格」。對話式的內容講話本來就沒什麼完整
     句子，whisper 幾乎不打句號 —— 而沒有標點的原文送進 LLM，翻出來的中文
     也會黏成一長串。所以每次都送一段「帶標點的種子句」誘導它打標點，
     後面再接詞彙表（讓它比較可能聽對專有名詞）。

     種子句的內容不重要（它不會出現在轉錄裡），重要的是**標點的密度與樣式**。

  2. 語言投票鎖定
     whisper 一個請求只鎖一種語言，而 language=auto 是每段各自判。
     BGM 大、只有一兩個字的短段落它會亂猜，日文最常被誤判成韓文
     （聽到的日文用諺文拼出來）。TransLens 的守備範圍只有日文與英文，
     所以前幾段各自 auto，多數決鎖定之後所有段都帶鎖定語言。
"""
import re

# 標點種子句：短句、逗號、句號、問號各出現一次，讓模型照抄這個節奏。
SEED_PROMPTS = {
    "ja": "はい、そうですね。じゃあ、始めましょうか。",
    "en": "Okay, so let's get started. Right?",
}

# auto 時還不知道是哪個語言，兩個種子都送（讓它自己挑）
AUTO_SEED_ORDER = ("ja", "en")

VOTE_SAMPLES = 4          # 前幾段各自 auto 來投票
VOTE_ALLOWED = ("ja", "en")

_KANA_RE = re.compile(r"[぀-ヿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def seed_prompt(lang="auto", override=None) -> str:
    """取這個語言的標點種子句。

    override 是設定檔的 asr_seed_prompt：給字串就用它、給空字串就等於關掉、
    給 None（沒設定）才用內建的。

    lang="auto"（還沒鎖定）時把 ja 與 en 的種子都送 —— 只送一種會把
    whisper 往那個語言帶偏，兩種都送則只留下「要打標點」這個共同訊號。
    """
    if override is not None:
        return str(override or "").strip()
    l = (lang or "").strip().lower()
    if l in SEED_PROMPTS:
        return SEED_PROMPTS[l]
    if l in ("", "auto"):
        return "".join(SEED_PROMPTS[k] for k in AUTO_SEED_ORDER)
    return ""


def build_prompt(glossary_prompt="", lang="auto", seed_override=None,
                 max_chars=0) -> str:
    """把種子句與詞彙表提示詞組成最後要送的 prompt。

    種子句放**前面**：whisper 的 prompt 是當成「前一段的轉錄」餵進去的，
    離結尾愈近的內容影響愈大，詞彙表那些專有名詞要留在後面才有效。
    兩者都空就回空字串（不送 prompt 欄位）。

    max_chars > 0 時從尾端截斷（保留種子句與前面的詞）—— 總長超過
    whisper 的 token 上限會被它自己截掉，不如我們自己控制切在哪。
    """
    seed = seed_prompt(lang, seed_override)
    gloss = (glossary_prompt or "").strip()
    out = f"{seed}{gloss}" if (seed and gloss) else (seed or gloss)
    if max_chars and len(out) > max_chars:
        out = out[:max_chars]
    return out


def guess_lang_from_text(text: str, allowed=VOTE_ALLOWED) -> str:
    """語言代碼不可信時的後備：直接看轉錄文字長什麼樣。

    有假名 → 日文；主要是拉丁字母 → 英文。都不像就回空字串。
    """
    t = text or ""
    if not t.strip():
        return ""
    if "ja" in allowed and _KANA_RE.search(t):
        return "ja"
    letters = len(_LATIN_RE.findall(t))
    non_space = len([c for c in t if not c.isspace()])
    if "en" in allowed and non_space and letters / non_space >= 0.6:
        return "en"
    return ""


def vote_language(votes, texts=None, allowed=VOTE_ALLOWED):
    """把樣本的偵測結果投票成一個語言碼。

    回傳 (語言, 說明字串)。規則：
      1. 只算落在 allowed 裡的票，多數決。
      2. 全部票都不在 allowed（例如整片被判成 ko / zh）→ 改看轉錄文字特徵。
      3. 還是判不出來 → 取 allowed 第一個，說明字串會帶「不確定」。
    """
    allowed = tuple(allowed) or ("ja",)
    votes = [v for v in (votes or []) if v]
    counts = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    detail = "、".join(f"{n} 判 {l}" for l, n in
                      sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    ok = [v for v in votes if v in allowed]
    if ok:
        best = max(set(ok), key=lambda l: (ok.count(l), -allowed.index(l)))
        return best, detail

    # 票都不在 allowed：看文字。whisper 把日文寫成韓文時 language 會回 ko，
    # 但只要它偶爾拼對一點假名，就還救得回來。
    for t in (texts or []):
        g = guess_lang_from_text(t, allowed)
        if g:
            note = (f"{detail}（不在允許清單，改用文字特徵判為 {g}）"
                    if detail else f"文字特徵判為 {g}")
            return g, note
    fallback = allowed[0]
    note = (f"{detail}（都不在允許清單）" if detail else "沒有有效樣本")
    return fallback, f"{note}；語言偵測不確定，已假設 {fallback}"


class LanguageLock:
    """即時模式的語言投票：前幾段各自 auto，多數決鎖定之後不再改。

    與 SubLens 的差別：離線可以先抽樣探測再開工，即時模式沒有「先看一遍」
    的機會，所以是邊跑邊投票 —— 前 VOTE_SAMPLES 段照樣出字幕，同時記票。

    使用者在下拉手動選語言時呼叫 set_manual()：立刻改用手動值並停止投票。
    """

    def __init__(self, configured="auto", samples=VOTE_SAMPLES,
                 allowed=VOTE_ALLOWED):
        self.allowed = tuple(allowed)
        self.samples = int(samples)
        self.votes = []
        self.texts = []
        self.locked = None
        self.detail = ""
        self.manual = None
        self.set_configured(configured)

    def set_configured(self, lang):
        """套用設定檔/下拉的值。非 auto 就等於手動鎖定，不投票。"""
        lang = (lang or "auto").strip().lower()
        if lang and lang != "auto":
            self.manual = lang
            self.locked = lang
        else:
            self.manual = None
            self.locked = None
            self.votes = []
            self.texts = []
            self.detail = ""

    def set_manual(self, lang):
        """使用者在下拉手動選了語言：立即生效並停止投票。"""
        self.set_configured(lang)

    @property
    def voting(self):
        """還在投票階段（沒鎖定且沒手動指定）。"""
        return self.manual is None and self.locked is None

    def request_lang(self):
        """這一段要送給 whisper 的 language 值。"""
        return self.locked or "auto"

    def observe(self, detected, text=""):
        """記一票。回傳剛剛鎖定的語言（這次才鎖定的話），否則 None。

        票以「文字特徵」為準，whisper 回的 language 只是備用：實測混語言的
        音訊（日文播完換英文）whisper 對英文段落仍會回 ja，照它的話投票就會
        把整段鎖成日文，之後英文全部被日文模型硬套（"head home early" 被
        聽成 "have to look in for"）。轉錄文字騙不了人 —— 有假名就是日文，
        整句拉丁字母就是英文，所以文字說了算。
        """
        if not self.voting:
            return None
        vote = guess_lang_from_text(text, self.allowed) or detected
        if vote:
            self.votes.append(vote)
        if text:
            self.texts.append(text)
        if len(self.votes) >= self.samples:
            lang, detail = vote_language(self.votes, self.texts, self.allowed)
            self.locked = lang
            self.detail = detail
            return lang
        return None

    def status(self):
        """狀態列要顯示的字，例如「語言：ja（已鎖定）」。"""
        if self.manual:
            return f"語言：{self.manual}"
        if self.locked:
            return f"語言：{self.locked}（已鎖定）"
        return f"語言：偵測中（{len(self.votes)}/{self.samples}）"

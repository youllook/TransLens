"""幻聽過濾：判掉 whisper 對著非語音段落硬填出來的句子。

VAD 換成 Silero 之後大部分非語音段落根本不會送去辨識，但仍有漏網的：
音樂裡的人聲、觀眾笑聲、講者換氣的長停頓。whisper 一旦拿到這種音訊，
就會吐訓練資料殘留（「ご視聴ありがとうございました」）或原地打轉
（「はいはいはいはい」）。這裡是第二道防線。

四條規則（前三條看單句就能判，第四條要記憶）：

  1. 已知垃圾句   字幕組署名、片尾致謝、[Music] 之類的標記
  2. 密度         字數 / 秒數太低 —— 模型把一兩個字撐滿整個窗口
  3. 段內重複     同一字或短語連發 ≥ 4 次
  4. 90 秒內重覆  正規化後同一句在短時間內第二次出現，而且兩次都拖很長

規則 1~3 是 SubLens 那套的移植（見 SubLens sublens/subtitle.py）。
規則 4 是即時模式的變體：SubLens 是離線處理，能看完整片再統計哪些句子
重複很多次；即時模式看不到未來，只能記住過去。所以改成「第二次出現就撤回」，
並且把第一條已經顯示出去的字幕從畫面上收回（kind="retract"）。

「兩次都 ≥ 2.5 秒」是不誤殺正常對話的關鍵門檻，理由與 SubLens 相同：
人真的會一直說「はい」，但每次都很短（≈ 1 秒）；幻聽是模型填滿整個窗口，
每次都拖很長。時長才分得開兩邊，密度分不開（實測幻聽的密度反而更高）。
"""
import re
import time

# --- 規則 1：已知垃圾句 -----------------------------------------------------
# 從 SubLens 移植（含它後來補的變體）。whisper 每次吐的寫法都不太一樣
# ——敬體/常體、有沒有「いただき」、有沒有「本日は」「最後まで」開頭
# ——所以一律寫成寬鬆的 regex 而不是字串比對。
HALLUCINATION_PATTERNS = [
    r"ご視聴(?:いただき)?(?:誠に)?ありがとう(?:ございま(?:した|す))?",
    r"ご覧いただきありがとうございました",
    r"ご清聴(?:いただき)?ありがとう(?:ございま(?:した|す))?",
    r"最後まで(?:ご視聴|お付き合い)",
    r"チャンネル登録",
    r"高評価",
    r"^\s*おやすみなさい[。!！\s]*$",
    r"^\s*字幕[：:]?\s*(?:by|バイ)?\s*[A-Za-z.\s]*$",
    r"by\s+H\.?\s*$",
    r"^\s*おわり\s*$",
    r"^\s*終わり\s*$",
    r"^\s*お(?:わり|しまい)\s*$",
    r"thank(?:s| you)(?: all| you)? (?:so much )?for watching",
    r"thanks for (?:listening|joining)",
    r"(?:please\s+)?(?:don'?t forget to\s+)?subscribe",
    r"like\s+and\s+subscribe",
    r"^\s*subtitles?\s+(?:by|provided by)",
    r"^\s*subtitle[s]?\s*[:：]",
    r"^\s*subs?\s+by",
    r"^\s*transcri(?:bed|ption)\s+by",
    r"amara\.org",
    r"^\s*字幕(?:製作|翻譯|by)?\s*$",
    r"^\s*字幕志愿者",
    r"^\s*字幕組",
    # whisper 的非語音標記，括號樣式很多變：[Music]、(silence)、＊音楽＊、
    # 或乾脆不加括號。所以括號類字元一律當成可有可無的裝飾。
    r"^\s*[\[\(（【]?\s*(?:music|音楽|音乐|拍手|applause|blank_audio|inaudible|"
    r"silence|noise|sound effect)\s*[\]\)）】]?\s*$",
    r"^\s*（?\s*(?:音楽|拍手|無音|沈黙)\s*）?\s*$",
    r"^\s*♪+\s*$",
]
_HALLUCINATION_RE = [re.compile(p, re.IGNORECASE) for p in HALLUCINATION_PATTERNS]

# 只有標點/空白（含全形）就當成沒內容
_PUNCT_ONLY_RE = re.compile(r"^[\s\W_]*$", re.UNICODE)


def is_garbage(text: str) -> bool:
    """判斷 whisper 輸出是不是空白、純標點或已知幻聽句。"""
    t = (text or "").strip()
    if not t:
        return True
    if _PUNCT_ONLY_RE.match(t):
        return True
    for rx in _HALLUCINATION_RE:
        if rx.search(t):
            return True
    return False


# --- 正規化與密度 -----------------------------------------------------------
# 比對「是不是同一句」時忽略標點、空白與長音／波浪號：whisper 每次吐同一句
# 幻聽，標點常常不一樣（「あ〜とても美味しいです」／「あ、とても美味しいです。」）。
_REPEAT_STRIP_RE = re.compile(r"[\s、。，,！!？?．.…~〜ー－—・「」『』（）()\"'']+")

_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")

DENSITY_CJK = 0.8        # 中日文：低於幾個字/秒算太稀
DENSITY_LATIN = 1.5      # 英文：低於幾個字元/秒算太稀
DENSITY_MIN_DUR = 3.0    # 只有夠長的段落才用密度判
INNER_REPEAT_MAX = 4     # 段內同一字/短語連發幾次算幻聽
REPEAT_WINDOW_SEC = 90.0  # 同一句在幾秒內第二次出現算幻聽
REPEAT_MIN_DUR = 2.5     # 兩次都要 ≥ 這麼長才判（短的是人真的在重複講）


def normalize_repeat(text: str) -> str:
    """把文字正規化成比對重複用的鍵。"""
    return _REPEAT_STRIP_RE.sub("", (text or "").strip())


def is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def text_density(text: str, duration: float) -> float:
    """字數 / 秒數。時長 0 回無限大（那是時間戳壞掉，不是密度低）。"""
    n = len(normalize_repeat(text))
    if duration <= 0:
        return float("inf")
    return n / duration


def density_floor(text: str) -> float:
    return DENSITY_CJK if is_cjk(text) else DENSITY_LATIN


def too_sparse(text: str, duration: float, cfg=None) -> bool:
    """規則 2：字數/秒數太低。夠長的段落才判 —— 短段落本來就可能只有一個字。"""
    cfg = cfg or {}
    min_dur = float(cfg.get("hallucination_density_min_dur", DENSITY_MIN_DUR))
    cjk = float(cfg.get("hallucination_density_cjk", DENSITY_CJK))
    lat = float(cfg.get("hallucination_density_latin", DENSITY_LATIN))
    floor = cjk if is_cjk(text) else lat
    if min_dur <= 0 or floor <= 0 or duration < min_dur:
        return False
    return text_density(text, duration) < floor


# --- 規則 3：段內重複 -------------------------------------------------------
def inner_repeat_count(text: str) -> int:
    """一句裡「同一單元最多連續重複幾次」。

    先看單字重複（あああああ），再看 1~6 字的短語重複（はいはいはいはい、
    そうですねそうですねそうですね）。回傳最大的連發次數，1 代表沒有重複。

    只算「連續」重複：正常句子也會用到同一個字（「私は私の…」），
    但不會把同一個字或詞黏在一起連發四次。
    """
    t = normalize_repeat(text)
    if len(t) < 2:
        return 1
    best = 1
    # 短語長度 1~6：長一點的短語（「そうですね」5 字）也要抓得到
    for unit in range(1, 7):
        if len(t) < unit * 2:
            break
        i = 0
        while i + unit <= len(t):
            piece = t[i:i + unit]
            n = 1
            j = i + unit
            while t[j:j + unit] == piece:
                n += 1
                j += unit
            if n > best:
                best = n
            i += unit if n == 1 else (n * unit)
    return best


def has_inner_repeat(text: str, cfg=None) -> bool:
    """規則 3：同一字或同一短語連發 ≥ 4 次。"""
    cfg = cfg or {}
    limit = int(cfg.get("hallucination_inner_repeat_max", INNER_REPEAT_MAX))
    if limit <= 0:
        return False
    return inner_repeat_count(text) >= limit


# --- 規則 4：90 秒內第二次出現 ----------------------------------------------
class RepeatTracker:
    """記住最近說過的句子，抓「同一句在短時間內第二次出現」。

    只記正規化後的鍵與 (時間, 時長)，不留原文，記憶體用量與講了多久無關。
    每次 check 順手把過期的鍵清掉。

    回傳的 retract 是「要不要把第一條從畫面上收回」：第一次出現時我們
    還無法判斷那是幻聽，字幕已經顯示出去了。第二次出現才確定它是幻聽，
    所以要求 UI 把先前那條清掉 —— 只在第一條還可能停在畫面上時才需要。
    """

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.window = float(cfg.get("hallucination_repeat_window_sec",
                                    REPEAT_WINDOW_SEC))
        self.min_dur = float(cfg.get("hallucination_repeat_min_dur",
                                     REPEAT_MIN_DUR))
        self.enabled = bool(cfg.get("hallucination_repeat", True))
        self._seen = {}      # 正規化鍵 -> (最後出現的時刻, 那次的時長)

    def reset(self):
        self._seen.clear()

    def check(self, text: str, duration: float, now=None):
        """回傳 (是不是幻聽, 要不要撤回第一條)。

        兩次都要 ≥ min_dur 才算幻聽：人會一直說「はい」，但每次都很短。
        """
        now = time.time() if now is None else float(now)
        key = normalize_repeat(text)
        if not key:
            return False, False
        # 順手清過期
        if self.window > 0:
            for k, (t, _) in list(self._seen.items()):
                if now - t > self.window:
                    del self._seen[k]
        if not self.enabled or self.window <= 0:
            return False, False

        prev = self._seen.get(key)
        self._seen[key] = (now, float(duration))
        if prev is None:
            return False, False
        prev_at, prev_dur = prev
        if now - prev_at > self.window:
            return False, False
        # 兩次都要夠長。長度門檻是分開幻聽與真人重複的唯一有效證據。
        if self.min_dur > 0 and (prev_dur < self.min_dur
                                 or float(duration) < self.min_dur):
            return False, False
        return True, True


# --- 統一入口 ---------------------------------------------------------------
class Filter:
    """四條規則的統一入口，附帶「擋掉幾條」的計數給狀態列。

    用法：

        f = Filter(cfg)
        verdict = f.check(text, duration)
        if verdict.drop:
            if verdict.retract: ...把前一條從畫面上收回
        else: ...正常顯示

    每條規則都可以在 config 關掉（見各個 hallucination_* 鍵）。
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.tracker = RepeatTracker(self.cfg)
        self.counts = {"garbage": 0, "density": 0, "inner_repeat": 0, "repeat": 0}

    @property
    def total(self):
        return sum(self.counts.values())

    def reset(self):
        self.tracker.reset()
        for k in self.counts:
            self.counts[k] = 0

    def check(self, text: str, duration: float = 0.0, now=None):
        """判一句話。回傳 Verdict（drop / reason / retract）。"""
        if is_garbage(text):
            self.counts["garbage"] += 1
            return Verdict(True, "garbage")
        if too_sparse(text, duration, self.cfg):
            self.counts["density"] += 1
            return Verdict(True, "density")
        if has_inner_repeat(text, self.cfg):
            self.counts["inner_repeat"] += 1
            return Verdict(True, "inner_repeat")
        dup, retract = self.tracker.check(text, duration, now=now)
        if dup:
            self.counts["repeat"] += 1
            return Verdict(True, "repeat", retract=retract)
        return Verdict(False, "")

    def summary(self):
        """狀態列用的一小段字，例如「已濾 3 條幻聽」。沒擋過就回空字串。"""
        return f"已濾 {self.total} 條幻聽" if self.total else ""


class Verdict:
    """一句話的判定結果。"""

    __slots__ = ("drop", "reason", "retract")

    def __init__(self, drop, reason="", retract=False):
        self.drop = bool(drop)
        self.reason = reason
        self.retract = bool(retract)

    def __repr__(self):  # pragma: no cover - 只給除錯看
        return f"<Verdict drop={self.drop} reason={self.reason!r} retract={self.retract}>"

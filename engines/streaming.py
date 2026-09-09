"""LocalAgreement-2 串流辨識：不等句子講完，一邊聽一邊把「已經確定的字」定案。

為什麼要有這個模組
------------------
原本的做法是「VAD 判斷句子起訖 → 整句送 whisper」。即時看影片時這條路走不通：

  * 使用者會快轉、跳播、暫停，波形突然中斷。VAD 分不出「句子講完了」與
    「被切斷了」，判錯就整段消失 —— 實測 15 秒的日文對話（四句），
    標準檔切出的 4 片全被 min_speech / min_voiced 丟光，字幕 0 條。
  * 就算判對，也要等靜音 0.6~0.9 秒才送出，那本身就是延遲。

LocalAgreement-2（arXiv:2307.14743）換一個假設：**不要問句子在哪裡結束**，
改成「每隔一小段時間，把手上累積的整段音訊重送一次，比較連續兩次的結果，
兩次都同意的最長前綴就是確定的字」。模型自己會在聽到更多上下文後修正尾巴，
但已經聽了兩輪還沒改的前綴，之後幾乎不會再變 —— 那就可以定案顯示。

成本為什麼可行
--------------
實測這台 whisper-server：同一段音訊取前 2/5/10/15 秒送出，耗時都是
1.84~1.93 秒（固定開銷為主，與音訊長度幾乎無關）。所以「反覆重送越來越長
的音訊」的成本幾乎是常數，每秒送一次就是每秒約一個固定成本的請求。

這個模組只做純邏輯
------------------
不發任何 HTTP、不碰執行緒。呼叫端（engines/audio_subtitle.py）負責:

    s = StreamingSession(cfg)
    s.add_audio(pcm)                       # 16k/int16 單聲道
    if s.due():                            # 該送辨識了嗎
        wav = s.buffer_wav()               # 或自己用 s.buffer_pcm
        words, text = <打 whisper>
        out = s.consume_result(words, text)
        out["committed"]   已確定的整段文字（含這次新確定的）
        out["tentative"]   還沒確定的尾巴（UI 用灰字顯示）
        out["flush"]       不是 None 就代表「一句完成了」，是一條 cue

    s.flush()                              # 停止時把手上的殘餘吐出來

前綴比對怎麼比
--------------
whisper 每次切詞的方式不保證一樣（「問題を」可能切成「問題」+「を」，
也可能一次吐出來），所以不能逐 word 比。這裡把 words 攤平成
「字元 → 來源 word」的對應表，用**正規化後的字元序列**比對：

  * 去掉所有空白（日文本來就沒空白，英文的空白只是分詞痕跡）
  * 統一標點的全形／半形（！→!、？→?、、→,）
  * 英文比對前轉小寫（whisper 對句首大小寫很不穩定）

比對得到的最長共同前綴長度（以正規化字元數計），再換回原始字元位置，
就知道「確定到哪一個字」以及它的結束時間戳。英文因此也是「比對到詞」——
一個英文詞的所有字元要全部一致，該詞才會整個落在共同前綴裡；
不會出現半個單字被確定的情況（見 _word_safe_cut）。

何時把一句送出去（flush）
-------------------------
兩個條件，滿足其一：

  1. 已確定文字裡出現句尾標點（。！？!?）—— 在最後一個句尾標點處切開。
  2. 緩衝超過 stream_max_buffer_sec（預設 25 秒）—— 講者不打標點也不能
     讓緩衝無限長大，whisper 的固定成本雖然與長度無關，但 30 秒是它的
     視窗上限，超過就會開始丟前面的內容。

切開之後做 scroll：把音訊緩衝從「最後一個確定字的結束時間」往前砍掉，
並把該時間累加進 self.offset，後續 words 的時間戳加上 offset 才是絕對時間。
"""
import io
import re
import wave

TARGET_RATE = 16000
TARGET_WIDTH = 2

DEFAULTS = {
    # 多久送一次辨識。1.0 秒是延遲與請求數的折衷：這台 whisper 每次固定
    # 約 1.9 秒，間隔比它短只會讓請求塞車，比它長則延遲白白增加。
    "stream_interval_sec": 1.0,
    # 標點以外的兩條斷句規則（Parakeet 幾乎不輸出標點，見 _maybe_flush）
    "stream_flush_min_sec": 2.0,    # 已確定內容短於這個就不用停頓規則切
    "stream_flush_gap_sec": 1.2,    # 字與字之間的空檔大於這個就算換句
    "stream_flush_max_sec": 6.0,    # 已確定內容超過這麼長就先送出去
    # 緩衝上限。超過就強制 flush（見模組 docstring）。
    "stream_max_buffer_sec": 25.0,
    # 「這一秒完全沒人聲」的判定門檻。VAD 在串流模式只剩這個用途：
    # 純靜音時跳過辨識省請求。低到 0.2 是刻意的 —— 寧可多送幾次，
    # 也不要因為 VAD 判錯而讓字幕消失（那正是舊做法的病）。
    "stream_silence_prob": 0.2,
    # 連續幾次一致才確定。LocalAgreement-2 就是 2；留成參數方便測試。
    "stream_agreement_n": 2,
}

# 句尾標點：出現在已確定文字裡就把這一句送出去
SENTENCE_END = "。！？!?"
_SENTENCE_END_RE = re.compile(f"[{re.escape(SENTENCE_END)}]")

# prompt 回音要抄到這麼多字才剝掉（見 StreamingSession._strip_echo）。
# 太小會誤殺真的以「はい」開頭的對話。
MIN_ECHO_CHARS = 6

# 緩衝短於這個秒數時不確定任何字（見 StreamingSession.consume_result 的暖機）。
#
# 為什麼需要：whisper 對很短的音訊會硬猜，而且會一路改。實測 seg.wav
# 的開頭（真正的內容是「じゃあ次の問題を」，起音很輕），不帶 prompt
# 逐秒送出的結果是：
#
#     1~2s 「はい」                 3s 「ちょっと動きましょう」
#     4s   「じゃあ、次は」          5s 「じゃあ次はもうだよ」
#     6s   「じゃあ、次はもう一度いいね。」
#     8s   「今は何だろう?」        10s 「じゃあ次の問題を」← 這時才對
#
# 這段音訊本身就要 10 秒才穩得下來。LocalAgreement 對這種情況其實是
# 對的：一直在變的前綴永遠不會連兩輪一致，所以不會被確定 —— 這正是
# 這個演算法該有的行為，畫面上顯示為「未定的尾巴一直在改」。
#
# 暖機門檻是額外的保險：擋掉「1~2 秒時連兩輪都吐同一個『はい』」這種
# 短音訊的假一致。3 秒實測是好的折衷：再長不但白白增加每一句的延遲，
# 還會讓後面幾句變差（實測 5 秒「廊下」變「農家」、7 秒變「まだ」），
# 因為 flush 的切點跟著位移，每一段送去辨識的音訊範圍就都不一樣了。
MIN_COMMIT_SEC = 3.0

# 正規化：全形標點統一成半形，比對才不會因為 whisper 這次吐「！」
# 下次吐「!」就判成不一致。
_PUNCT_MAP = {
    "！": "!", "？": "?", "，": ",", "、": ",", "。": ".",
    "：": ":", "；": ";", "…": "...", "‥": "..",
    "「": '"', "」": '"', "『": '"', "』": '"',
    "（": "(", "）": ")", "　": " ",
    "“": '"', "”": '"', "‘": "'", "’": "'",
}
_WS_RE = re.compile(r"\s+")


def norm_char(ch: str) -> str:
    """單一字元的正規化。回傳空字串代表這個字元在比對時被忽略（空白）。"""
    ch = _PUNCT_MAP.get(ch, ch)
    if not ch.strip():
        return ""
    return ch.lower()


def normalize(text: str) -> str:
    """整串文字的正規化（去空白、統一標點、轉小寫）。"""
    return "".join(norm_char(c) for c in (text or ""))


def _is_wordish(ch: str) -> bool:
    """這個字元屬於「不可切一半」的詞（拉丁字母、數字）嗎。

    日文／中文一個字就是一個單位，切在哪都合法；英文切在單字中間會
    冒出「wond」這種半截字，看起來像壞掉。所以只對 ASCII 詞字元設限。
    """
    return ch.isascii() and (ch.isalnum() or ch == "'")


class Cue:
    """一條完成的字幕：原文 + 絕對時間戳（秒）。"""

    __slots__ = ("text", "start", "end")

    def __init__(self, text, start, end):
        self.text = text
        self.start = float(start)
        self.end = float(end)

    def as_dict(self):
        return {"text": self.text, "start": round(self.start, 3),
                "end": round(self.end, 3)}

    def __repr__(self):  # pragma: no cover - 除錯用
        return f"Cue({self.text!r}, {self.start:.2f}, {self.end:.2f})"

    def __eq__(self, other):
        return (isinstance(other, Cue) and other.text == self.text
                and abs(other.start - self.start) < 1e-6
                and abs(other.end - self.end) < 1e-6)


class _Token:
    """攤平後的一個字元：字元本身、它的正規化形式、以及來源 word 的時間戳。"""

    __slots__ = ("ch", "norm", "start", "end", "wordish")

    def __init__(self, ch, start, end):
        self.ch = ch
        self.norm = norm_char(ch)
        self.start = float(start)
        self.end = float(end)
        self.wordish = _is_wordish(ch)


# whisper.cpp 在 words 陣列裡會把某些 CJK 字元「切成兩半」——
# 一個 UTF-8 三位元組的字被拆進兩個相鄰的 word，每半都不是合法字元，
# 伺服器 JSON 編碼時就各自變成 U+FFFD（替代字元）。實測這台 1.8.3：
#
#     text  : "山だ!また寝てるの?廊下に立ってなさい。"     ← 正確
#     words : ["山","だ","!","また","�","�","て","る","の","?",
#              "�","�","下",...]                  ← 寝 與 廊 沒了
#
# 原始位元組在伺服器端就已經丟失（拿到的就是 EF BF BD），救不回來。
# 所以：**字元一律以頂層 text 為準，words 只拿來對時間軸**。
REPLACEMENT = "�"


def flatten_words(words, text=""):
    """whisper 的 verbose_json → _Token list（一個字元一個 token）。

    words 每項長這樣（實測 whisper.cpp 1.8.3）：
        {"word": "問題", "start": 3.19, "end": 4.4, "probability": 0.96}

    一個 word 裡的字元共用同一組時間戳。這樣做的代價是「問題」兩個字
    的時間戳一樣，但我們只用結束時間來切音訊與標時間軸，夠精確了；
    好處是切詞方式改變（「問題を」vs「問題」+「を」）不影響比對結果。

    給了 text 就以它為字元來源（見上面 REPLACEMENT 的說明），words 的
    時間戳按順序貼上去；沒給 text 才直接用 words 的字元。
    """
    stamps = []                     # [(字元, 起, 迄)]，可能含 U+FFFD
    for w in words or ():
        if not isinstance(w, dict):
            continue
        piece = w.get("word")
        if piece is None:
            piece = w.get("text", "")
        start = w.get("start", 0.0)
        end = w.get("end", start)
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            start = end = 0.0
        if end < start:
            end = start
        for ch in str(piece):
            stamps.append((ch, start, end))

    text = str(text or "")
    if not text:
        return [_Token(ch, s, e) for ch, s, e in stamps]
    if not stamps:
        return []
    return _align(text, stamps)


def _align(text, stamps):
    """把頂層 text 的字元對上 words 的時間戳。

    兩邊只在「壞掉的字」與空白上不同，所以用一個寬容的雙指標比對：
    字元相同就直接配對；不同就把 stamps 這邊當成壞字往前吃，
    並且用「下一個對得上的字元」把中間這段的時間戳分配掉。

    對不上的字元（例如 text 有而 words 完全沒有）沿用前一個時間戳，
    最壞情況是時間軸精度差一點 —— 字元本身永遠是對的，那才是重點。
    """
    out = []
    j = 0
    n = len(stamps)
    last_start = last_end = (stamps[0][1] if n else 0.0)
    for i, ch in enumerate(text):
        if ch.isspace():
            # text 的換行是 whisper 的分行，words 裡沒有對應項
            out.append(_Token(ch, last_start, last_end))
            continue
        # 跳過 stamps 裡的壞字與空白，但別跳過對得上的字元
        while j < n and (stamps[j][0].isspace()
                         or (stamps[j][0] == REPLACEMENT and stamps[j][0] != ch)):
            last_start, last_end = stamps[j][1], stamps[j][2]
            j += 1
        if j < n and stamps[j][0] == ch:
            _, s, e = stamps[j]
            last_start, last_end = s, e
            out.append(_Token(ch, s, e))
            j += 1
            continue
        # 對不上：可能是壞字被吃掉後 text 這邊多出來的字。用目前的時間戳
        # 頂著，並讓 stamps 前進一格避免卡住。
        if j < n:
            _, s, e = stamps[j]
            last_start, last_end = s, e
            out.append(_Token(ch, s, e))
            j += 1
        else:
            out.append(_Token(ch, last_start, last_end))
    return out


def _norm_index(tokens):
    """tokens → (正規化字串, 每個正規化字元對應的 token 索引)。"""
    buf = []
    idx = []
    for i, t in enumerate(tokens):
        if t.norm:
            buf.append(t.norm)
            idx.append(i)
    return "".join(buf), idx


def common_prefix_len(a: str, b: str) -> int:
    """兩個正規化字串的最長共同前綴長度。"""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _word_safe_cut(tokens, upto):
    """把「確定到第 upto 個 token（不含）」往回退到不切斷英文單字的位置。

    upto 落在單字中間（前一個與這一個都是詞字元）就一路往前退到詞首。
    日文／中文不受影響（不是 wordish）。
    """
    if upto <= 0 or upto >= len(tokens):
        return max(0, min(upto, len(tokens)))
    if tokens[upto - 1].wordish and tokens[upto].wordish:
        while upto > 0 and tokens[upto - 1].wordish:
            upto -= 1
    return upto


class StreamingSession:
    """LocalAgreement-2 的狀態機。純邏輯，不發 HTTP、不開執行緒。

    用法見模組 docstring。重要屬性：

        committed_text   已確定的文字（本句，尚未 flush 的部分）
        tentative_text   尚未確定的尾巴
        offset           緩衝起點對應的絕對時間（scroll 累積）
        buffer_sec       目前緩衝長度（秒）
    """

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.cfg = cfg
        self.interval = float(cfg.get("stream_interval_sec",
                                      DEFAULTS["stream_interval_sec"]))
        self.max_buffer_sec = float(cfg.get("stream_max_buffer_sec",
                                            DEFAULTS["stream_max_buffer_sec"]))
        self.agreement_n = max(2, int(cfg.get("stream_agreement_n",
                                              DEFAULTS["stream_agreement_n"])))
        self.min_commit_sec = float(cfg.get("stream_min_commit_sec",
                                            MIN_COMMIT_SEC))
        self.flush_min_sec = float(cfg.get("stream_flush_min_sec",
                                           DEFAULTS["stream_flush_min_sec"]))
        self.flush_gap_sec = float(cfg.get("stream_flush_gap_sec",
                                           DEFAULTS["stream_flush_gap_sec"]))
        self.flush_max_sec = float(cfg.get("stream_flush_max_sec",
                                           DEFAULTS["stream_flush_max_sec"]))
        self.reset()

    # --- 狀態
    def reset(self):
        self._pcm = bytearray()
        self.prompt = getattr(self, "prompt", "")
        self._prompt_norm = normalize(self.prompt)
        self._prev_norm = ""          # 上一次辨識結果的正規化字串（未確定的部分）
        self._committed = []          # 已確定的 _Token（本句）
        self._tentative = []          # 尚未確定的 _Token
        self._grew = False            # 上一輪有沒有新確定的字（見 _maybe_flush）
        self.offset = 0.0             # 緩衝起點的絕對時間
        self._since_asr = 0           # 上次送辨識之後又進來多少 bytes
        self._sent_bytes = 0          # 已經送出過辨識的 bytes（due 判斷用）
        self.asr_count = 0            # 送過幾次辨識（統計用）
        self.cue_count = 0

    @property
    def buffer_sec(self) -> float:
        return len(self._pcm) / float(TARGET_RATE * TARGET_WIDTH)

    @property
    def buffer_pcm(self) -> bytes:
        return bytes(self._pcm)

    @property
    def committed_text(self) -> str:
        return "".join(t.ch for t in self._committed)

    @property
    def tentative_text(self) -> str:
        return "".join(t.ch for t in self._tentative)

    @property
    def committed_sec(self) -> float:
        """已確定文字的結束時間（絕對）。沒有就等於 offset。"""
        if not self._committed:
            return self.offset
        return self.offset + self._committed[-1].end

    # --- 餵音訊
    def add_audio(self, pcm: bytes):
        """累積音訊。pcm 是 16kHz/int16 單聲道。"""
        if not pcm:
            return
        self._pcm += pcm
        self._since_asr += len(pcm)

    def due(self) -> bool:
        """距離上次送辨識已經累積滿一個 interval 了嗎。

        用「新進來的音訊秒數」而不是牆鐘：離線重播測試與即時擷取才會
        走同一條路徑，測試結果才代表真實行為。
        """
        if not self._pcm:
            return False
        return (self._since_asr / float(TARGET_RATE * TARGET_WIDTH)) >= self.interval

    def buffer_wav(self) -> bytes:
        """把整個緩衝包成 WAV（whisper-server 的 multipart 要完整檔頭）。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(TARGET_WIDTH)
            w.setframerate(TARGET_RATE)
            w.writeframes(bytes(self._pcm))
        return buf.getvalue()

    def mark_sent(self):
        """呼叫端把緩衝送出去了：重置 due() 的計數。

        在「真的發出請求」的當下呼叫，不要在拿到回應時才呼叫 ——
        辨識要 1.9 秒，那段時間進來的音訊屬於下一輪。
        """
        self._since_asr = 0
        self._sent_bytes = len(self._pcm)
        self.asr_count += 1

    # --- 收辨識結果
    def consume_result(self, words, text=""):
        """吃一次辨識結果，做前綴比對並回報。

        words 是 whisper verbose_json 攤平後的 words 陣列（見 flatten_words）。
        沒有 words（伺服器只回文字）時退而用 text，時間戳按緩衝長度平均分配 ——
        這樣至少 flush 還能運作，只是時間軸精度差。

        回傳 dict：
            committed  本句目前為止已確定的文字
            tentative  尚未確定的尾巴
            newly      這一次新確定的文字（UI 可以只播新的）
            flush      Cue 或 None（不是 None 代表一句完成，緩衝已 scroll）
        """
        tokens = flatten_words(words, text)
        if not tokens and (text or "").strip():
            tokens = self._fake_tokens(text)
        tokens = self._strip_echo(tokens)

        # 已確定的部分是「這次結果的前綴」，比對只針對還沒確定的尾巴做。
        # 拿掉已確定的字元數之後剩下的，才是這一輪要跟上一輪比的東西。
        committed_norm, _ = _norm_index(self._committed)
        cur_norm, cur_idx = _norm_index(tokens)

        if committed_norm and cur_norm.startswith(committed_norm):
            tail_from = len(committed_norm)
        elif committed_norm:
            # 模型改寫了已經確定的部分。**不回頭改已顯示的字** ——
            # 使用者看過的字被抽掉比留著一個小錯誤更難受，這也是
            # LocalAgreement 的核心承諾。只把重疊的部分當成已確定，
            # 其餘照樣往下接。
            tail_from = common_prefix_len(cur_norm, committed_norm)
        else:
            tail_from = 0

        tail_norm = cur_norm[tail_from:]
        tail_token_start = cur_idx[tail_from] if tail_from < len(cur_idx) else len(tokens)

        # LocalAgreement-2：這一輪的尾巴與上一輪的尾巴，共同前綴就是新確定的。
        agree = common_prefix_len(tail_norm, self._prev_norm)
        self._prev_norm = tail_norm

        # 暖機：緩衝還太短就只顯示、不確定（見 MIN_COMMIT_SEC）。
        # 這一輪的結果照樣存進 _prev_norm，所以緩衝一夠長，前面幾輪
        # 累積下來的一致性立刻就能用上，不會白等。
        if self.buffer_sec < self.min_commit_sec:
            self._grew = False
            self._tentative = tokens[tail_token_start:]
            return {"committed": self.committed_text,
                    "tentative": self.tentative_text,
                    "newly": "", "flush": None}

        # 共同前綴長度（正規化字元數）→ token 索引
        if agree <= 0:
            cut = tail_token_start
        elif tail_from + agree < len(cur_idx):
            cut = cur_idx[tail_from + agree]
        else:
            cut = len(tokens)
        cut = max(tail_token_start, _word_safe_cut(tokens, cut))

        newly = tokens[tail_token_start:cut]
        # 這一輪有沒有長出新字 —— 結尾標點要不要當真的依據（見 _maybe_flush）
        self._grew = bool(newly)
        self._committed.extend(newly)
        self._tentative = tokens[cut:]
        # 尾巴確定掉的部分不該再參與下一輪比對
        self._prev_norm = self._prev_norm[agree:] if agree else self._prev_norm

        cue = self._maybe_flush()
        return {
            "committed": self.committed_text,
            "tentative": self.tentative_text,
            "newly": "".join(t.ch for t in newly),
            "flush": cue,
        }

    def set_prompt(self, prompt):
        """記住標點種子 prompt，好把它從辨識結果裡剝掉（見 _strip_echo）。"""
        self.prompt = str(prompt or "")
        self._prompt_norm = normalize(self.prompt)

    def prompt_for_request(self):
        """這一輪該送哪個 prompt 給 whisper。

        種子 prompt 誘導 whisper 打標點（串流模式特別需要，flush 靠標點），
        但**緩衝還短的時候要拿掉**。原因是實測出來的：音訊只有一兩秒、
        起音又輕時，whisper 沒東西可聽就會照著 prompt 的語氣編一句像樣的
        話回來 ——

            帶種子：1s「はい。」 2s「はい。」 4s「じゃあ、始めましょう。」
            不帶：  1s「はい」   2s「はい」   4s「じゃあ、次は」

        帶種子那邊不但編得更像真的，還自帶句號 —— 連兩輪一致就會被確定
        並當成完整句 flush 出去，而已確定的字不回頭改，真正的第一句
        就永遠補不回來了。不帶種子則是一路在變，LocalAgreement 自然
        不會確定它，等音訊夠長再打標點也不遲。
        """
        if self.buffer_sec < self.min_commit_sec:
            return ""
        return self.prompt

    def _strip_echo(self, tokens):
        """把「whisper 把 prompt 當成語音抄回來」的開頭剝掉。

        標點種子 prompt（「はい、そうですね。じゃあ、始めましょうか。」）
        在分段模式下很少出事，但串流模式每秒重送一次，音訊短的時候
        whisper 沒東西可聽就會把 prompt 原樣吐出來 —— 實測 seg.wav 的
        第一輪就抄了「はい。じゃあ、始めましょう。」，那會被當成真的字幕
        確定下去，而且卡在句首再也拿不掉。

        剝的是「正規化後與 prompt 的前綴一致」的開頭，所以 whisper 抄到
        一半（「じゃあ、始めましょう」少了「か」）也擋得住。
        """
        if not tokens or not getattr(self, "_prompt_norm", ""):
            return tokens
        norm, idx = _norm_index(tokens)
        n = common_prefix_len(norm, self._prompt_norm)
        # 要求抄了夠長一段才剝，否則正常語音剛好與種子句開頭撞字
        # （日文對話真的常常以「はい」開頭）就會被誤砍。
        if n < MIN_ECHO_CHARS:
            return tokens
        cut = idx[n] if n < len(idx) else len(tokens)
        return tokens[cut:]

    def _fake_tokens(self, text):
        """伺服器沒回 words 時的退路：把整段文字平均攤在緩衝長度上。"""
        text = str(text or "")
        if not text:
            return []
        dur = self.buffer_sec or 1.0
        step = dur / max(1, len(text))
        return [_Token(ch, i * step, (i + 1) * step) for i, ch in enumerate(text)]

    # --- flush
    def _maybe_flush(self):
        """該把一句送出去嗎（句尾標點 / 停頓 / 長度 / 緩衝上限）。

        為什麼不能只靠標點：Parakeet 幾乎不輸出句尾標點（實測 15 秒的
        四句對話只有最後一個「。」），只看標點的話緩衝會一路長到上限，
        整段才被切出來，然後被幻聽過濾當成「太長太密」丟掉 —— 使用者
        看到的就是「完全沒反應」。所以再加兩條與標點無關的切法：
          停頓  已確定的字之間出現 flush_gap_sec 以上的空檔 = 換句了
          長度  已確定的內容超過 flush_max_sec，就在最後一個字邊界切
        """
        text = self.committed_text
        if text:
            # 找最後一個句尾標點。標點後面還有字 = 確定講完一句了，直接切。
            #
            # 標點剛好在結尾時要小心：Parakeet 每一輪都會在目前結果的尾巴
            # 補一個「。」（實測 3s「うんとじゃあ。」→ 4s「うんとじゃあ次は。」
            # → 5s「…問題は。」），那不是真的句尾。這種情況要等尾巴不再
            # 成長（tentative 空、且這一輪沒有新確定的字）才切，否則會把
            # 還在長的前綴一段段切出去，變成「とじゃあうんとじゃあ次の問題…」。
            m = last_inner = None
            for cand in _SENTENCE_END_RE.finditer(text):
                m = cand
                if cand.end() < len(text.rstrip()):
                    last_inner = cand
            if last_inner is not None:
                return self._cut_at(last_inner.end())
            # 標點落在已確定文字的結尾：後面還有未確定的尾巴在等，
            # 代表講者已經講到下一句了，這個標點是真的句尾 → 切。
            #
            # 尾巴是空的時候不切。Parakeet 每一輪都會在目前結果的尾巴補
            # 一個「。」（3s「うんとじゃあ。」→ 4s「…次は。」→ 5s「…問題は。」），
            # 照著切會把還在成長的前綴一段段切出去，字幕變成
            # 「とじゃあうんとじゃあ次の問題…」。真的講完停下來的話，
            # 下一輪會由 stream_flush_max_sec（長度）或緩衝上限收掉，
            # 頂多晚一輪，不會漏。
            if m is not None and self._tentative:
                return self._cut_at(m.end())

        toks = self._committed
        # 長度：已確定的內容夠長就先送出去，不要讓使用者一直等到緩衝上限。
        #
        # 這裡**不用「停頓」切句**：Parakeet 每一輪回報的時間戳會抖動
        # （同一個「と」在相鄰兩輪分別是 1.28s 與 1.6s），假空檔會讓句子
        # 被切得七零八落，scroll 之後殘留的 token 再與新結果疊在一起，
        # 字幕就變成「とじゃあうんとじゃあ次の問題…」。長度規則不看空檔，
        # 不受抖動影響。
        if toks and (toks[-1].end - toks[0].start) >= self.flush_max_sec:
            return self._cut_at(len(toks))

        if self.buffer_sec >= self.max_buffer_sec and self._committed:
            return self._cut_at(len(self._committed))
        return None

    def _cut_at(self, n_tokens):
        """把已確定的前 n 個 token 切成一條 cue，並 scroll 音訊緩衝。"""
        n_tokens = max(0, min(n_tokens, len(self._committed)))
        if n_tokens <= 0:
            return None
        head = self._committed[:n_tokens]
        rest = self._committed[n_tokens:]
        text = "".join(t.ch for t in head).strip()
        start = self.offset + head[0].start
        end = self.offset + head[-1].end
        cue = Cue(text, start, end)
        self.cue_count += 1

        # scroll：從最後一個確定字的結束時間往前砍掉音訊。
        # 剩下的 token 時間戳要跟著平移（它們原本是相對於舊的緩衝起點）。
        cut_sec = head[-1].end
        self._scroll(cut_sec)
        for t in rest:
            t.start = max(0.0, t.start - cut_sec)
            t.end = max(0.0, t.end - cut_sec)
        for t in self._tentative:
            t.start = max(0.0, t.start - cut_sec)
            t.end = max(0.0, t.end - cut_sec)
        self._committed = rest
        return cue if text else None

    def _scroll(self, seconds):
        """砍掉緩衝前面 seconds 秒的音訊，offset 跟著累加。"""
        seconds = max(0.0, float(seconds))
        nbytes = int(seconds * TARGET_RATE) * TARGET_WIDTH
        nbytes = min(nbytes, len(self._pcm))
        if nbytes:
            del self._pcm[:nbytes]
        self.offset += nbytes / float(TARGET_RATE * TARGET_WIDTH)
        self._sent_bytes = max(0, self._sent_bytes - nbytes)

    def flush(self):
        """停止／切換時把手上的東西吐出來：確定的 + 未確定的都算數。

        這裡刻意把未確定的尾巴也算進去 —— 使用者按停止時，寧可給他一句
        可能還會微調的字，也不要什麼都不給（那正是舊做法的病）。
        """
        tokens = self._committed + self._tentative
        if not tokens:
            self._prev_norm = ""
            return None
        text = "".join(t.ch for t in tokens).strip()
        start = self.offset + tokens[0].start
        end = self.offset + tokens[-1].end
        self._committed = []
        self._tentative = []
        self._prev_norm = ""
        self._scroll(self.buffer_sec)
        if not text:
            return None
        self.cue_count += 1
        return Cue(text, start, end)


def is_sentence_complete(text: str) -> bool:
    """這段文字是不是「完整句」（結尾有句尾標點）。

    翻譯策略用得到：只有完整句才送去翻譯，半句翻出來的中文會很怪，
    而且每句要花掉一次 oMLX 請求。
    """
    t = (text or "").strip()
    return bool(t) and t[-1] in SENTENCE_END

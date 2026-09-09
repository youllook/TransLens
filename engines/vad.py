"""語音活動偵測（VAD）：把 16k 單聲道 PCM 切成「有人在講話」的區段。

兩個實作，同一個介面：

    SileroVAD   神經網路模型（silero-vad v5 的 onnx，約 2MB），會分辨
                「人聲」與「音樂／環境音」。純 BGM 的段落判成沒有語音。
    EnergyVAD   原本的能量式門檻（RMS 超過自適應噪音底就算有聲）。
                沒裝 onnxruntime 或下載不到模型時的退路。

為什麼要換掉能量式：能量式只看音量大小，分不出「大聲的音樂」與「人在講話」。
實測 5 分鐘純 BGM 的 Vlog，能量式會整片判成有聲，每段都送去 whisper，
whisper 對著沒有人聲的音訊硬填，就吐出「ご視聴ありがとうございました」
這類訓練資料殘留（見 hallucination.py）。過濾幻聽是補救，VAD 才是根治。

介面（兩個類別都有）：

    vad.reset()                   清掉跨 buffer 的狀態（換一段音訊時呼叫）
    vad.feed(pcm) -> list[event]  餵 16k/int16 的 PCM，回傳這批觸發的事件
    vad.feed_silence(seconds)     沒有音訊封包時，用牆鐘推進靜音計時
    vad.label                     狀態列要顯示的名字（"Silero VAD" 等）
    vad.last_rms / last_prob      最近一批的音量與語音機率（0~1），給
                                  UI 的聆聽狀態帶畫量表用
    vad.prob_threshold            機率條上那條門檻刻線該畫在哪
    vad.apply_sensitivity(cfg)    不重開 worker 就換靈敏度（見 SENSITIVITY）

事件是 dict：
    {"kind": "speech_end", "pcm": bytes}   一段語音結束，pcm 是含 padding 的整段
    {"kind": "speech_start"}               開始講話（呼叫端目前只用來更新狀態）

呼叫端不必自己數靜音秒數：VAD 內部管 min_silence_ms / min_speech_ms，
講完一句、靜下來夠久，就在 feed() 的回傳裡給一個 speech_end。
"""
import logging
import os
import urllib.error
import urllib.request

TARGET_RATE = 16000
TARGET_WIDTH = 2

# silero-vad v5 固定吃 512 樣本一窗（16kHz 時是 32ms）。這不是可調參數：
# 模型的內部 state 是照這個窗長訓練的，餵別的長度結果會不準。
WINDOW_SAMPLES = 512
WINDOW_BYTES = WINDOW_SAMPLES * TARGET_WIDTH
WINDOW_SEC = WINDOW_SAMPLES / float(TARGET_RATE)

# 模型來源與存放位置。models/*.onnx 是 gitignored，第一次用到時才下載。
MODEL_URL = ("https://raw.githubusercontent.com/snakers4/silero-vad/master/"
             "src/silero_vad/data/silero_vad.onnx")
MODEL_NAME = "silero_vad.onnx"
MODEL_MIN_BYTES = 500 * 1024      # 下載到的檔案明顯太小 = 拿到錯誤頁面而不是模型

DEFAULTS = {
    "vad_threshold": 0.5,          # 語音機率超過就算有聲（silero 官方預設）
    "vad_min_speech_ms": 250,      # 短於這個的「有聲」是雜訊，不成一段
    "vad_min_silence_ms": 600,     # 靜下來這麼久才算一句講完（= audio_silence_sec）
    "vad_speech_pad_ms": 200,      # 前後各留一點，句首句尾才不會被切掉
    # 一段裡「真的被判為語音」的總秒數下限。這是擋掉音樂殘留的關鍵門檻：
    # 純 BGM 偶爾會有幾窗衝過 0.5（樂器的某些泛音很像人聲），湊成一段
    # 送去 whisper 就會冒出幻聽。實測 60 秒 BGM 切出的 5 段，語音秒數是
    # 0.00 / 0.64 / 0.54 / 0.00 / 0.00；真人講話的段落是 1.44 ~ 3.23 秒。
    # 1.0 秒切在中間，而且不會誤殺短句（「足元に気をつけろ」1.44 秒還在）。
    "vad_min_voiced_ms": 1000,
}

# 靈敏度三檔。使用者遇到的兩種相反症狀各有一邊可以調：
#   sensitive  小聲的耳語過不了 0.5 → 門檻降到 0.3，語音含量門檻也一起放寬，
#              否則門檻降了但「一段至少要有 1 秒人聲」照樣把短耳語擋掉。
#   strict     BGM 吵、幻聽多 → 門檻拉到 0.7，並要求一段至少 1.5 秒是人聲。
# 三個參數要一起動：只調 threshold 不動 min_voiced_ms，實際效果會被後者吃掉。
SENSITIVITY = {
    "sensitive": {"vad_threshold": 0.3, "vad_min_voiced_ms": 400,
                  "vad_min_speech_ms": 150},
    "normal": {"vad_threshold": 0.5, "vad_min_voiced_ms": 1000,
               "vad_min_speech_ms": 250},
    "strict": {"vad_threshold": 0.7, "vad_min_voiced_ms": 1500,
               "vad_min_speech_ms": 300},
}
DEFAULT_SENSITIVITY = "normal"

# ⚙ 選單用的顯示名（顯示名 -> 設定值）
SENSITIVITY_LABELS = [("靈敏", "sensitive"), ("標準", "normal"), ("嚴格", "strict")]


def sensitivity_params(name):
    """三檔名稱 -> 參數 dict。不認得的名字退回 normal。"""
    return dict(SENSITIVITY.get(str(name or "").strip().lower(),
                                SENSITIVITY[DEFAULT_SENSITIVITY]))


PARAM_KEYS = ("vad_threshold", "vad_min_voiced_ms", "vad_min_speech_ms")


def is_preset_value(key, value):
    """這個 config 值是不是某一檔靈敏度的原樣預設值。

    舊版把三檔的預設值直接寫進 config.json（那時候還沒有靈敏度這回事）。
    如果照單全收當成「使用者的個別覆寫」，切到「靈敏」就會被 0.5 釘住、
    完全沒有效果 —— 使用者會以為這個功能壞了。所以只有「跟任何一檔預設
    都不一樣」的值才算真的是使用者手動調過的。
    """
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return any(float(preset[key]) == v for preset in SENSITIVITY.values())


def resolve_params(cfg=None):
    """把 cfg 解析成實際生效的三個 VAD 參數。

    先取靈敏度預設，再讓 config 裡「真的被手動調過」的值覆寫過去。
    照原樣等於某一檔預設的值不算覆寫（見 is_preset_value）。
    """
    cfg = cfg or {}
    params = sensitivity_params(cfg.get("vad_sensitivity", DEFAULT_SENSITIVITY))
    for key in PARAM_KEYS:
        value = cfg.get(key)
        if value is None or is_preset_value(key, value):
            continue
        try:
            params[key] = float(value)
        except (TypeError, ValueError):
            pass
    return params


def model_dir():
    """models/ 目錄的絕對路徑（engines/ 的上一層）。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


def model_path():
    return os.path.join(model_dir(), MODEL_NAME)


def download_model(dest=None, url=MODEL_URL, progress=None, timeout=60):
    """下載 silero VAD 模型到 models/。回傳實際路徑，失敗丟 OSError。

    progress(done_bytes, total_bytes, source) 會在下載中被呼叫，讓呼叫端
    印進度與來源 —— 使用者有權知道程式從哪裡抓了什麼東西下來。

    先寫到 .part 再改名：中途失敗不會留下一個半截的檔案，
    下次啟動就不會拿半個模型去 onnxruntime（那會丟看不懂的錯）。
    """
    dest = dest or model_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    if progress:
        progress(0, 0, url)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    block = r.read(64 * 1024)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total, url)
    except (urllib.error.URLError, OSError, ValueError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise OSError(f"下載 Silero VAD 模型失敗（{url}）：{e}") from e

    if os.path.getsize(tmp) < MODEL_MIN_BYTES:
        size = os.path.getsize(tmp)
        os.remove(tmp)
        raise OSError(f"下載到的模型只有 {size} bytes，看起來不是模型檔（{url}）")
    os.replace(tmp, dest)
    return dest


def ensure_model(path=None, progress=None, allow_download=True):
    """確保模型檔存在，回傳路徑。不存在且允許下載就去抓。"""
    path = path or model_path()
    if os.path.isfile(path) and os.path.getsize(path) >= MODEL_MIN_BYTES:
        return path
    if not allow_download:
        raise OSError(f"找不到 Silero VAD 模型：{path}")
    return download_model(path, progress=progress)


# --- 共用的切段狀態機 -------------------------------------------------------
class _Segmenter:
    """把「每一窗是不是語音」的判斷串成語音區段。

    兩個 VAD 實作共用這段邏輯：差別只在怎麼算出「這一窗是不是語音」
    （Silero 用模型機率、Energy 用 RMS 門檻），切段規則完全一樣。

    緩衝策略：語音期間把 PCM 累積起來，同時保留最近 speech_pad 的「前情」
    ——語音開始的那一窗其實已經講了一點點，往前補一段才不會吃掉句首。
    """

    def __init__(self, min_speech_ms=250, min_silence_ms=600, speech_pad_ms=200,
                 max_chunk_sec=0.0, min_voiced_ms=0):
        self.min_speech_sec = max(0.0, min_speech_ms / 1000.0)
        self.min_silence_sec = max(0.0, min_silence_ms / 1000.0)
        self.pad_sec = max(0.0, speech_pad_ms / 1000.0)
        self.max_chunk_sec = max(0.0, float(max_chunk_sec or 0.0))
        # 送出去的門檻（min_voiced_sec）比「成為一段」的門檻（min_speech_sec）嚴：
        # 前者擋音樂殘留，後者只擋一兩窗的雜訊。分開兩個值才能各自調。
        self.min_voiced_sec = max(0.0, float(min_voiced_ms or 0) / 1000.0)
        self.reset()

    def reset(self):
        self.triggered = False
        self.speech = bytearray()      # 目前這段語音（含前 padding）
        self.pre = bytearray()         # 語音還沒開始時的滾動前情緩衝
        self.speech_sec = 0.0          # 這段裡「被判為語音」的累計秒數
        self.silence_sec = 0.0         # 目前連續靜音秒數

    def _pad_bytes(self):
        return int(self.pad_sec * TARGET_RATE) * TARGET_WIDTH

    def push(self, window: bytes, is_speech: bool):
        """餵一窗，回傳這窗觸發的事件 list。"""
        events = []
        if not self.triggered:
            if is_speech:
                self.triggered = True
                # 把前情（最多 pad_sec）接在最前面，句首才完整
                self.speech = bytearray(self.pre) + bytearray(window)
                self.pre.clear()
                self.speech_sec = WINDOW_SEC
                self.silence_sec = 0.0
                events.append({"kind": "speech_start"})
            else:
                self.pre += window
                extra = len(self.pre) - self._pad_bytes()
                if extra > 0:
                    del self.pre[:extra]
            return events

        # 已在語音中：靜音期間也繼續收音，句尾的餘韻才不會被切掉
        self.speech += window
        if is_speech:
            self.speech_sec += WINDOW_SEC
            self.silence_sec = 0.0
        else:
            self.silence_sec += WINDOW_SEC
            if self.silence_sec >= self.min_silence_sec:
                events.extend(self._close())
                return events

        cur_sec = len(self.speech) / float(TARGET_RATE * TARGET_WIDTH)
        if self.max_chunk_sec and cur_sec >= self.max_chunk_sec:
            # 講太久了先送一段出去，不然使用者要等很久才看到字
            events.extend(self._close(force=True))
        return events

    def add_silence(self, seconds: float):
        """沒有音訊封包時用牆鐘推進靜音（WASAPI 停播就不送封包）。"""
        if not self.triggered or seconds <= 0:
            return []
        self.silence_sec += float(seconds)
        if self.silence_sec >= self.min_silence_sec:
            return self._close()
        return []

    def _close(self, force=False):
        """收掉目前這段。語音太少就當雜訊丟掉，不回事件。"""
        pcm = bytes(self.speech)
        speech_sec = self.speech_sec
        # 尾端多收的靜音只留 pad_sec，其餘剪掉（少送幾秒靜音給 whisper）
        if not force and self.silence_sec > self.pad_sec:
            cut = int((self.silence_sec - self.pad_sec) * TARGET_RATE) * TARGET_WIDTH
            if 0 < cut < len(pcm):
                pcm = pcm[:len(pcm) - cut]
        self.triggered = False
        self.speech = bytearray()
        self.pre = bytearray()
        self.speech_sec = 0.0
        self.silence_sec = 0.0
        dur = len(pcm) / float(TARGET_RATE * TARGET_WIDTH)
        if speech_sec < self.min_speech_sec or not pcm:
            # 丟掉也要留下痕跡：使用者看著狀態帶，只知道「剛剛講的話沒出來」，
            # 沒有事件就分不出是沒聽到還是被擋掉（見 audio_subtitle 的 dropped）。
            return [{"kind": "speech_drop", "reason": "min_speech",
                     "sec": dur, "voiced_sec": speech_sec}] if pcm else []
        # 語音含量太低：這是音樂／環境音偶爾衝過門檻湊出來的一段，
        # 送去 whisper 只會換回一句幻聽。強制切段（講太久）不受這條限制。
        if not force and speech_sec < self.min_voiced_sec:
            return [{"kind": "speech_drop", "reason": "min_voiced",
                     "sec": dur, "voiced_sec": speech_sec}]
        return [{"kind": "speech_end", "pcm": pcm, "voiced_sec": speech_sec}]

    def flush(self):
        """停止時把手上這段送出去（不要丟掉已經擷取的語音）。"""
        if not self.triggered:
            return []
        return self._close(force=True)


class _BaseVAD:
    """共用的 feed/feed_silence/flush 骨架；子類別只要實作 _is_speech()。"""

    label = "VAD"
    # 只有真的能分辨人聲的 VAD 才用「語音含量」門檻（見 DEFAULTS 的說明）。
    # 能量式量不出「有多少是人聲」，套這條只會變成另一種長度門檻。
    uses_voiced_gate = False

    def __init__(self, cfg=None, max_chunk_sec=0.0):
        cfg = cfg or {}
        self.cfg = cfg
        params = resolve_params(cfg)
        self.threshold = float(params["vad_threshold"])
        self.seg = _Segmenter(
            min_speech_ms=float(params["vad_min_speech_ms"]),
            min_silence_ms=float(cfg.get("vad_min_silence_ms",
                                         _silence_ms_from(cfg))),
            speech_pad_ms=float(cfg.get("vad_speech_pad_ms",
                                        DEFAULTS["vad_speech_pad_ms"])),
            max_chunk_sec=max_chunk_sec,
            min_voiced_ms=(float(params["vad_min_voiced_ms"])
                           if self.uses_voiced_gate else 0),
        )
        self._tail = bytearray()       # 不滿一窗的尾巴，等下一批補齊
        # 最近一批的量表，給 UI 的聆聽狀態帶用（見 audio_subtitle 的 level 事件）。
        # 取「這批裡最大的一窗」而不是平均：使用者要看的是「有沒有衝到門檻」，
        # 平均會把一句話裡的靜音也算進去，把耳語壓得更看不見。
        self.last_rms = 0.0            # 0~1 正規化
        self.last_prob = 0.0           # 0~1（能量式是 rms 對門檻的比例）
        self.last_speaking = False

    def apply_sensitivity(self, cfg):
        """套用新的靈敏度設定到「正在跑」的 VAD。

        使用者在 ⚙ 切靈敏度時不該重開 worker（重開會重載模型、丟掉手上
        那段音訊）。三個參數都是純數字門檻，就地改掉就生效，不動狀態機
        內部的緩衝與 LSTM state。
        """
        self.cfg = cfg = dict(cfg or {})
        params = resolve_params(cfg)
        self.threshold = float(params["vad_threshold"])
        self.seg.min_speech_sec = max(0.0, float(params["vad_min_speech_ms"]) / 1000.0)
        if self.uses_voiced_gate:
            self.seg.min_voiced_sec = max(0.0,
                                          float(params["vad_min_voiced_ms"]) / 1000.0)
        return params

    def reset(self):
        self.seg.reset()
        self._tail = bytearray()
        self.last_rms = 0.0
        self.last_prob = 0.0
        self.last_speaking = False

    def feed(self, pcm: bytes):
        """餵 16k/int16 單聲道 PCM，回傳事件 list。"""
        events = []
        self._tail += pcm
        peak_rms = peak_prob = 0.0
        speaking = False
        n = 0
        while len(self._tail) >= WINDOW_BYTES:
            window = bytes(self._tail[:WINDOW_BYTES])
            del self._tail[:WINDOW_BYTES]
            is_speech = self._is_speech(window)
            n += 1
            speaking = speaking or is_speech
            peak_rms = max(peak_rms, _rms(window) / 32768.0)
            # _is_speech() 剛剛已經算過這一窗的機率並存進 _prob；再算一次
            # 等於把 Silero 的推論成本翻倍，所以這裡只讀不算。
            peak_prob = max(peak_prob, self._prob)
            events.extend(self.seg.push(window, is_speech))
        if n:
            # 取「上次被讀走之後的最大值」而不是「這一次 feed 的最大值」：
            # 呼叫端每 100ms 才取樣一次，中間可能 feed 了好幾批，直接覆寫
            # 會讓量表取樣到剛好最小的那一批，耳語就更看不見了。
            # last_peak_read() 讀走後才歸零。
            self.last_rms = max(self.last_rms, min(1.0, peak_rms))
            self.last_prob = max(self.last_prob, min(1.0, peak_prob))
            self.last_speaking = self.last_speaking or speaking
        return events

    def take_level(self):
        """讀走目前累積的峰值並歸零，回傳 (rms, prob, speaking)。

        讀走式（而不是單純讀取）才能讓「兩次取樣之間的最大值」被看到，
        又不會讓舊的峰值一直卡在量表上。
        """
        rms, prob, speaking = self.last_rms, self.last_prob, self.last_speaking
        self.last_rms = self.last_prob = 0.0
        self.last_speaking = False
        return rms, prob, speaking

    # 最近一窗的「語音程度」0~1，由 _is_speech() 順手寫進來，feed() 只讀不算。
    # Silero 是模型機率；能量式沒有機率可言，用 rms 對門檻的比例代替 ——
    # 兩者的共同語意是「離門檻還有多遠」，畫成同一條刻度才有意義。
    _prob = 0.0

    @property
    def prob_threshold(self) -> float:
        """狀態帶上那條門檻刻線該畫在 0~1 的哪裡。

        Silero 就是 threshold 本身；能量式的門檻是自適應的絕對音量，
        換算成同一條刻度後固定落在 0.5（見 EnergyVAD._is_speech）。
        """
        return float(self.threshold)

    def feed_silence(self, seconds: float):
        return self.seg.add_silence(seconds)

    def flush(self):
        return self.seg.flush()

    def _is_speech(self, window: bytes) -> bool:  # pragma: no cover - 抽象
        raise NotImplementedError


def _silence_ms_from(cfg):
    """沒設 vad_min_silence_ms 時，沿用既有的 audio_silence_sec。"""
    sec = cfg.get("audio_silence_sec")
    if sec:
        try:
            return float(sec) * 1000.0
        except (TypeError, ValueError):
            pass
    return DEFAULTS["vad_min_silence_ms"]


# --- 能量式（退路） ---------------------------------------------------------
class EnergyVAD(_BaseVAD):
    """RMS 超過自適應噪音底就算有聲。就是原本 audio_subtitle 的那套邏輯。

    只看音量，所以大聲的 BGM 也會被判成有聲 —— 這是它與 Silero 的關鍵差別，
    也是為什麼 Silero 拿不到時要在狀態列標明「（能量式）」。
    """

    label = "能量式 VAD"
    # 能量門檻是自適應的絕對音量，_is_speech 把它正規化成固定的 0.5
    prob_threshold = 0.5

    def __init__(self, cfg=None, max_chunk_sec=0.0):
        super().__init__(cfg, max_chunk_sec)
        self.noise_floor = 60.0
        self.abs_floor = float((cfg or {}).get("vad_energy_floor", 120.0))

    def reset(self):
        super().reset()
        self.noise_floor = 60.0

    def _is_speech(self, window: bytes) -> bool:
        level = _rms(window)
        threshold = max(self.noise_floor * 3.0, self.abs_floor)
        # 「離門檻還有多遠」換算成 0~1：剛好踩到門檻是 0.5，這樣狀態帶上
        # 的門檻刻線畫在 0.5 對兩種 VAD 都成立（Silero 的刻線畫在 threshold）。
        self._prob = min(1.0, (level / threshold) * 0.5) if threshold > 0 else 0.0
        if level < threshold:
            # 噪音底慢慢跟隨環境音量，不同片子的底噪都能自適應
            self.noise_floor = self.noise_floor * 0.95 + level * 0.05
        return level >= threshold


def _rms(pcm: bytes) -> float:
    """int16 PCM 的 RMS。有 audioop 就用它（C 實作，快），沒有就自己算。"""
    if not pcm:
        return 0.0
    try:
        import audioop
        return audioop.rms(pcm, TARGET_WIDTH)
    except Exception:  # noqa: BLE001 — 3.13 移除了 audioop，自己算
        pass
    import array
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) // 2 * 2])
    if not a:
        return 0.0
    return (sum(float(x) * x for x in a) / len(a)) ** 0.5


# --- Silero ----------------------------------------------------------------
class SileroVAD(_BaseVAD):
    """silero-vad v5 的 onnx 推論。

    模型簽名（v5）：
        input  (1, 64 + 512) float32 —— 前一窗的尾巴 64 樣本 + 這窗 512 樣本，
                                        正規化到 [-1, 1]
        state  (2, 1, 128) float32   —— LSTM 狀態，要跨窗帶著走
        sr     int64                 —— 取樣率
    輸出：
        output (1, 1) float32       —— 這窗是語音的機率
        stateN                      —— 下一窗要餵回去的 state

    兩個「要接續」的東西，少任何一個結果都是錯的：

      state    模型的 LSTM 記憶，每窗重置的話短促的語音全判不出來。
      context  前一窗的最後 64 個樣本。v5 的輸入其實是 576 寬不是 512 ——
               只餵 512 進去，模型會把每一窗都當成「從無聲突然開始」，
               機率全部壓在 0.002 以下，連清楚的人聲也判成沒有語音
               （實測 ja_test.wav 語音比例 0%）。這是照 silero-vad 官方
               utils_vad.py OnnxWrapper.__call__ 的做法補上的。
    """

    CONTEXT_SAMPLES = 64

    label = "Silero VAD"
    uses_voiced_gate = True

    def __init__(self, cfg=None, max_chunk_sec=0.0, path=None, session=None,
                 progress=None):
        # threshold 由 _BaseVAD 從 resolve_params()（靈敏度 + config 覆寫）算好
        super().__init__(cfg, max_chunk_sec)
        if session is not None:
            self.session = session
            self.path = path or "(injected)"
        else:
            import onnxruntime as ort
            self.path = ensure_model(path, progress=progress)
            opts = ort.SessionOptions()
            # VAD 是每 32ms 一次的小推論，多執行緒只會增加切換成本
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 3
            self.session = ort.InferenceSession(
                self.path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._np = __import__("numpy")
        self._state = self._zero_state()
        self._context = self._zero_context()
        self._prob = 0.0

    def _zero_state(self):
        return self._np.zeros((2, 1, 128), dtype=self._np.float32)

    def _zero_context(self):
        return self._np.zeros(self.CONTEXT_SAMPLES, dtype=self._np.float32)

    def reset(self):
        super().reset()
        self._state = self._zero_state()
        self._context = self._zero_context()
        self._prob = 0.0

    def probability(self, window: bytes) -> float:
        """一窗的語音機率。窗長不足就補零（只發生在最後一窗）。"""
        np = self._np
        samples = np.frombuffer(window[:WINDOW_BYTES], dtype=np.int16)
        if len(samples) < WINDOW_SAMPLES:
            samples = np.pad(samples, (0, WINDOW_SAMPLES - len(samples)))
        x = samples.astype(np.float32) / 32768.0
        # 前一窗的尾巴接在前面：v5 的輸入是 64 + 512 = 576 寬（見 class docstring）
        padded = np.concatenate([self._context, x]).reshape(1, -1)
        out, self._state = self.session.run(
            None, {"input": padded, "state": self._state,
                   "sr": np.array(TARGET_RATE, dtype=np.int64)})
        self._context = x[-self.CONTEXT_SAMPLES:].copy()
        self._prob = float(np.asarray(out).reshape(-1)[0])
        return self._prob

    def _is_speech(self, window: bytes) -> bool:
        return self.probability(window) >= self.threshold


# --- 工廠 -------------------------------------------------------------------
def create(cfg=None, max_chunk_sec=0.0, progress=None):
    """依設定建 VAD，回傳 (vad, 說明字串)。

    設定 vad_backend：
        auto（預設）  先試 Silero，失敗就退能量式（說明字串會帶原因）
        silero        只用 Silero，失敗照樣退能量式但把錯誤講清楚
        energy        直接用能量式

    永遠回得出一個可用的 VAD —— VAD 掛掉不該讓整個字幕模式開不起來。
    說明字串由呼叫端貼到狀態列，使用者才知道現在跑的是哪一套。
    """
    cfg = cfg or {}
    backend = str(cfg.get("vad_backend", "auto") or "auto").strip().lower()
    if backend == "energy":
        return EnergyVAD(cfg, max_chunk_sec), "VAD: 能量式（設定指定）"
    try:
        vad = SileroVAD(cfg, max_chunk_sec, progress=progress)
        return vad, "VAD: Silero"
    except Exception as e:  # noqa: BLE001 — 沒 onnxruntime、下載失敗、模型壞掉
        reason = f"{type(e).__name__}: {e}"
        logging.warning("Silero VAD 無法使用，退回能量式：%s", reason)
        short = "沒裝 onnxruntime" if isinstance(e, ImportError) else str(e)
        return EnergyVAD(cfg, max_chunk_sec), f"VAD: 能量式（Silero 不可用：{short}）"


# --- 離線分析（測試與工具用） -----------------------------------------------
def analyze(pcm: bytes, vad):
    """把整段 PCM 餵完，回傳 (區段 list, 語音比例)。

    區段是 (起秒, 迄秒)，用「餵進去的樣本數」換算，所以與時間軸對得上。
    給單元測試比較兩種 VAD 用，也方便手動檢查一段音訊被切成什麼樣。
    """
    vad.reset()
    total_sec = len(pcm) / float(TARGET_RATE * TARGET_WIDTH)
    spans = []
    pos_sec = 0.0
    start = None
    for i in range(0, len(pcm) - WINDOW_BYTES + 1, WINDOW_BYTES):
        window = pcm[i:i + WINDOW_BYTES]
        for ev in vad.seg.push(window, vad._is_speech(window)):
            if ev["kind"] == "speech_start":
                start = pos_sec
            elif ev["kind"] == "speech_end":
                dur = len(ev["pcm"]) / float(TARGET_RATE * TARGET_WIDTH)
                s = start if start is not None else max(0.0, pos_sec - dur)
                spans.append((round(s, 3), round(min(total_sec, s + dur), 3)))
                start = None
        pos_sec += WINDOW_SEC
    for ev in vad.flush():
        dur = len(ev["pcm"]) / float(TARGET_RATE * TARGET_WIDTH)
        s = start if start is not None else max(0.0, pos_sec - dur)
        spans.append((round(s, 3), round(min(total_sec, s + dur), 3)))
    voiced = sum(e - s for s, e in spans)
    return spans, (voiced / total_sec if total_sec > 0 else 0.0)

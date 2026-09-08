"""音訊字幕：把 Windows 系統聲音（WASAPI loopback）送到區網的 whisper-server 辨識，再翻成繁體中文。

流程：
    WASAPI loopback 擷取 → Silero VAD 切段（退路：能量式）→ 打包 16k 單聲道 WAV
    → POST {whisper_server_url}（whisper.cpp server 的 /inference，帶標點種子＋詞彙表 prompt）
    → 幻聽過濾（四條規則，含 90 秒內第二次出現就撤回）
    → translator.translate()（本地 LLM，失敗退 Google；套詞彙表三層）→ callback 回主執行緒

設計原則：
  * 只在真的啟動音訊模式時才 import pyaudiowpatch/numpy，沒裝套件不影響原本 OCR 功能。
  * 全部在背景執行緒跑，錯誤一律轉成人看得懂的字串，不讓主程式崩潰。
  * callback 只負責把事件塞進 queue，實際的 tk 更新由主執行緒做。
  * VAD、幻聽過濾、prompt 組裝各自獨立成模組（engines/vad.py、hallucination.py、
    asr_prompt.py），這裡只負責串起來 —— 那三個都能單獨測，不用開音訊裝置。
"""
import io
import logging
import queue
import threading
import time
import wave

import requests

from . import asr_prompt, hallucination, vad as vad_mod
# is_garbage 搬到 hallucination.py 了，這裡再匯出一次讓舊的匯入路徑仍然可用。
from .hallucination import HALLUCINATION_PATTERNS, is_garbage  # noqa: F401

# audioop 在 Python 3.13 被移除；3.13+ 可安裝 audioop-lts 取得同名模組。
try:
    import audioop
except ImportError:  # pragma: no cover - 視執行環境而定
    try:
        import audioop_lts as audioop  # noqa: F401
    except ImportError:
        audioop = None

TARGET_RATE = 16000          # whisper 要求 16kHz
TARGET_WIDTH = 2             # int16
MIN_CHUNK_SEC = 0.5          # 短於這個長度的段落直接丟掉
FRAMES_PER_BUFFER = 1024


def clean_text(text: str) -> str:
    """whisper 會回多行加前後空白，壓成單行。"""
    return " ".join((text or "").split())


# --- 音訊處理 ---------------------------------------------------------------
def to_wav_bytes(pcm: bytes, rate=TARGET_RATE, channels=1, width=TARGET_WIDTH) -> bytes:
    """把 raw PCM 包成記憶體中的 WAV 檔（whisper-server 的 multipart 需要完整檔頭）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def downmix_resample(pcm: bytes, src_rate: int, src_channels: int, src_width: int,
                     state=None):
    """任意格式 → 16kHz / 單聲道 / int16。回傳 (pcm, 新的 ratecv state)。

    ratecv 需要跨 buffer 保留 state，否則每塊邊界會有雜訊，所以 state 由呼叫端保存。
    """
    if src_width != TARGET_WIDTH:
        pcm = audioop.lin2lin(pcm, src_width, TARGET_WIDTH)
    if src_channels > 1:
        pcm = audioop.tomono(pcm, TARGET_WIDTH, 0.5, 0.5) if src_channels == 2 else \
            _tomono_n(pcm, src_channels)
    if src_rate != TARGET_RATE:
        pcm, state = audioop.ratecv(pcm, TARGET_WIDTH, 1, src_rate, TARGET_RATE, state)
    return pcm, state


def _tomono_n(pcm: bytes, channels: int) -> bytes:
    """>2 聲道：只取第一聲道（audioop.tomono 只吃立體聲）。"""
    frame = TARGET_WIDTH * channels
    out = bytearray()
    for i in range(0, len(pcm) - frame + 1, frame):
        out += pcm[i:i + TARGET_WIDTH]
    return bytes(out)


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    try:
        return audioop.rms(pcm, TARGET_WIDTH)
    except audioop.error:
        return 0.0


# --- whisper-server 客戶端 --------------------------------------------------
def transcribe(wav_bytes: bytes, url: str, language="auto", timeout=30, prompt="",
               verbose=False):
    """POST 一段 WAV 給 whisper.cpp server。

    verbose=False（預設）回傳辨識文字字串，維持原本的介面。
    verbose=True 回傳 (文字, 偵測到的語言) —— 語言投票需要知道 whisper
    判成什麼，那只有 verbose_json 才有 language 欄位。
    """
    data = {
        "language": language,
        "response_format": "verbose_json" if verbose else "json",
        "temperature": "0",
    }
    if prompt:
        data["prompt"] = prompt
    r = requests.post(
        url,
        files={"file": ("chunk.wav", wav_bytes, "audio/wav")},
        data=data,
        timeout=timeout,
    )
    r.raise_for_status()
    try:
        payload = r.json()
    except ValueError:
        text = clean_text(r.text)
        return (text, "") if verbose else text
    if not isinstance(payload, dict):
        text = clean_text(str(payload))
        return (text, "") if verbose else text
    text = clean_text(payload.get("text", ""))
    if not verbose:
        return text
    return text, _normalize_lang(payload.get("language", ""))


def _normalize_lang(lang: str) -> str:
    """whisper 回的是 'japanese' / 'english' 這種全名，轉成 ja / en。"""
    l = (lang or "").strip().lower()
    table = {
        "japanese": "ja", "ja": "ja", "jpn": "ja",
        "english": "en", "en": "en", "eng": "en",
        "chinese": "zh", "zh": "zh", "korean": "ko", "ko": "ko",
    }
    return table.get(l, l[:2] if l else "")


def recognize_and_translate(wav_bytes: bytes, cfg: dict, prompt=None, language=None,
                            duration=0.0, hall_filter=None, glossary=None,
                            custom_prompt=None):
    """WAV bytes → (原文, 譯文, 語言, whisper 耗時, 翻譯耗時, 翻譯資訊)。

    回傳原文為空字串代表這段被判定成空白/幻聽，應該丟掉。
    翻譯資訊是 engines.translator.TranslateInfo，帶著實際用的後端與是否退版。

    prompt=None 時自己組（種子句 + 詞彙表）；worker 會把組好的傳進來重用。
    hall_filter 給 Filter 實例就用它的四條規則（含跨句的 90 秒重複），
    沒給就只做「單句就能判」的那三條 —— 單元測試與單次呼叫用得到。
    """
    url = cfg.get("whisper_server_url", DEFAULTS["whisper_server_url"])
    lang = language or cfg.get("audio_lang", DEFAULTS["audio_lang"])

    if glossary is None and custom_prompt is None:
        from . import translator
        glossary, custom_prompt = translator.load_glossary(cfg)
    if prompt is None:
        gloss_prompt = glossary.asr_prompt()[0] if glossary else ""
        prompt = asr_prompt.build_prompt(gloss_prompt, lang,
                                         cfg.get("asr_seed_prompt"))

    t0 = time.time()
    text = transcribe(wav_bytes, url, lang, prompt=prompt)
    t_asr = time.time() - t0

    from .translator import TranslateInfo, translate
    if not duration:
        duration = _wav_duration(wav_bytes)
    checker = hall_filter or hallucination.Filter(cfg)
    if checker.check(text, duration).drop:
        return "", "", lang, t_asr, 0.0, TranslateInfo(backend="none")

    t1 = time.time()
    zh, detected, info = translate(text, cfg, source=("auto" if lang == "auto" else lang),
                                   glossary=glossary, custom_prompt=custom_prompt)
    t_tr = time.time() - t1
    return text, zh, (detected or lang), t_asr, t_tr, info


def _wav_duration(wav_bytes: bytes) -> float:
    """從 WAV 檔頭算秒數（密度規則要用）。壞掉的檔就回 0。"""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            return w.getnframes() / float(w.getframerate() or TARGET_RATE)
    except Exception:  # noqa: BLE001
        return 0.0


DEFAULTS = {
    "whisper_server_url": "http://192.168.0.87:8178/inference",
    "audio_lang": "auto",
    "audio_silence_sec": 0.6,
    "audio_max_chunk_sec": 6,
    "subtitle_hold_sec": 8,
}


class AudioSubtitleError(RuntimeError):
    """給使用者看的可讀錯誤。"""


def open_loopback_stream(pa_mod, audio):
    """找到預設輸出裝置的 loopback，回傳 (stream, device_info)。

    這台機器沒有任何輸出端點（例如沒插喇叭/耳機）時，WASAPI 的 defaultOutputDevice
    會是 -1、loopback 清單為空，這裡就丟出可讀錯誤而不是讓 pyaudio 拋 Errno -9996。
    """
    try:
        wasapi = audio.get_host_api_info_by_type(pa_mod.paWASAPI)
    except OSError as e:
        raise AudioSubtitleError(f"找不到 WASAPI 音訊介面：{e}") from e

    default_out = None
    idx = wasapi.get("defaultOutputDevice", -1)
    if idx is not None and idx >= 0:
        try:
            default_out = audio.get_device_info_by_index(idx)
        except OSError:
            default_out = None

    device = None
    if default_out is not None:
        if default_out.get("isLoopbackDevice"):
            device = default_out
        else:
            for lb in audio.get_loopback_device_info_generator():
                if default_out["name"] in lb["name"]:
                    device = lb
                    break
    if device is None:
        # 沒有預設輸出，就退而求其次抓任何一個 loopback
        for lb in audio.get_loopback_device_info_generator():
            device = lb
            break
    if device is None:
        raise AudioSubtitleError(
            "找不到可用的系統音訊 loopback 裝置。\n"
            "這通常代表這台電腦目前沒有啟用中的音訊輸出端點"
            "（沒接喇叭或耳機、或輸出裝置被停用）。\n"
            "請先接上耳機／喇叭，或在「設定 → 系統 → 音效」啟用一個輸出裝置。")

    rate = int(device["defaultSampleRate"])
    channels = int(device["maxInputChannels"]) or 2
    try:
        stream = audio.open(format=pa_mod.paInt16, channels=channels, rate=rate,
                            input=True, input_device_index=device["index"],
                            frames_per_buffer=FRAMES_PER_BUFFER)
    except OSError as e:
        raise AudioSubtitleError(f"無法開啟 loopback 裝置「{device['name']}」：{e}") from e
    return stream, device


class AudioSubtitleWorker:
    """背景執行緒：擷取系統聲音、切段、辨識、翻譯，結果透過 callback 送回。

    callback(kind, payload)：
        kind="status"   payload=str            狀態訊息（開始擷取、裝置名…）
        kind="subtitle" payload=dict           {"src","zh","lang","asr_sec","total_sec"}
        kind="retract"  payload=dict           {"src"} 這句被判定是幻聽，請把它從面板收回
        kind="error"    payload=str            可讀錯誤訊息（worker 會停止）
    """

    def __init__(self, cfg, callback):
        self.cfg = cfg
        self.callback = callback
        self._stop = threading.Event()
        self._thread = None
        self._sender = None
        self._queue = queue.Queue(maxsize=8)
        self._last_text = None
        self._shown = None          # 最近顯示出去的原文（撤回時要比對）
        self.filter = hallucination.Filter(cfg)
        self.lang_lock = asr_prompt.LanguageLock(
            cfg.get("audio_lang", DEFAULTS["audio_lang"]),
            samples=int(cfg.get("audio_lang_vote_samples", asr_prompt.VOTE_SAMPLES)))
        self.glossary = None
        self.custom_prompt = ""
        self.vad = None
        self.vad_note = ""

    # --- 生命週期
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_text = None
        self._shown = None
        self.filter.reset()
        self.lang_lock.set_configured(self.cfg.get("audio_lang", "auto"))
        while not self._queue.empty():        # 丟掉上一輪殘留
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        # 擷取與「辨識＋翻譯」分開兩條執行緒：網路慢時不會卡住擷取而漏音。
        self._sender = threading.Thread(target=self._send_loop, daemon=True, name="audio-subtitle-send")
        self._sender.start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="audio-subtitle")
        self._thread.start()

    def stop(self, join=False, timeout=3):
        self._stop.set()
        if join:
            if self._thread:
                self._thread.join(timeout)
            if self._sender:
                self._sender.join(timeout)

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def set_language(self, lang):
        """使用者在下拉手動選了語言：立即生效並停止投票。"""
        self.lang_lock.set_manual(lang)

    def _emit(self, kind, payload):
        try:
            self.callback(kind, payload)
        except Exception:  # noqa: BLE001 — callback 出錯不該弄死擷取執行緒
            logging.exception("audio subtitle callback 失敗")

    # --- 主迴圈
    def _run(self):
        if audioop is None:
            self._emit("error", "這個 Python 版本沒有 audioop 模組（3.13 起已移除）。"
                                "請執行：pip install audioop-lts，或改用 Python 3.11/3.12。")
            return
        try:
            import pyaudiowpatch as pa_mod
        except ImportError:
            self._emit("error", "未安裝 pyaudiowpatch，無法擷取系統聲音。"
                                "請執行：pip install pyaudiowpatch")
            return

        audio = stream = None
        try:
            self._prepare()
            audio = pa_mod.PyAudio()
            stream, device = open_loopback_stream(pa_mod, audio)
            self._emit("status", f"擷取中：{device['name']} · {self.vad_note}")
            self._capture_loop(stream, device)
        except AudioSubtitleError as e:
            self._emit("error", str(e))
        except Exception as e:  # noqa: BLE001
            logging.exception("音訊字幕執行緒異常")
            self._emit("error", f"{type(e).__name__}: {e}")
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if audio is not None:
                    audio.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _prepare(self):
        """建 VAD、載入詞彙表。VAD 第一次用要下載模型，所以會回報進度。"""
        from . import translator
        self.glossary, self.custom_prompt = translator.load_glossary(self.cfg)
        if self.glossary and self.glossary.warnings:
            for w in self.glossary.warnings[:3]:
                self._emit("status", f"詞彙表：{w}")

        max_chunk = float(self.cfg.get("audio_max_chunk_sec",
                                       DEFAULTS["audio_max_chunk_sec"]))
        self.vad, self.vad_note = vad_mod.create(
            self.cfg, max_chunk_sec=max_chunk, progress=self._download_progress)

    def _download_progress(self, done, total, source):
        """模型下載進度。使用者有權知道程式從哪裡抓了什麼下來。"""
        if not done:
            self._emit("status", f"下載 Silero VAD 模型…（來源 {source}）")
        elif total:
            self._emit("status", f"下載 Silero VAD 模型… {done * 100 // total}%"
                                 f"（{total // 1024} KB，來源 {source}）")

    def _capture_loop(self, stream, device):
        """擷取 → VAD → 佇列。VAD 決定切在哪，這裡只管把 PCM 餵進去。"""
        src_rate = int(device["defaultSampleRate"])
        src_channels = int(device["maxInputChannels"]) or 2
        rate_state = None
        self.vad.reset()

        last_tick = time.time()
        while not self._stop.is_set():
            # WASAPI loopback 在「沒有任何程式在播放」時連靜音封包都不會送，read() 會無限卡住
            # （影片播完、暫停、對話結束後停播都會遇到）。所以只在有資料時才 read；
            # 沒資料就用牆鐘累積靜音時間，讓最後一句照樣切出去，停止也不會被卡住。
            try:
                avail = stream.get_read_available()
            except OSError:
                avail = FRAMES_PER_BUFFER
            if avail < FRAMES_PER_BUFFER:
                time.sleep(0.05)
                now = time.time()
                self._handle(self.vad.feed_silence(now - last_tick))
                last_tick = now
                continue
            try:
                raw = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
            except OSError as e:
                self._emit("error", f"音訊擷取中斷：{e}")
                return
            last_tick = time.time()
            mono, rate_state = downmix_resample(raw, src_rate, src_channels,
                                                TARGET_WIDTH, rate_state)
            self._handle(self.vad.feed(mono))

        # 取消勾選時把手上這句也送出去，不要丟掉
        self._handle(self.vad.flush())

    def _handle(self, events):
        """把 VAD 的事件轉成佇列裡的工作。"""
        for ev in events or ():
            if ev.get("kind") != "speech_end":
                continue
            pcm = ev.get("pcm") or b""
            if len(pcm) / float(TARGET_RATE * TARGET_WIDTH) < MIN_CHUNK_SEC:
                continue
            try:
                self._queue.put_nowait((pcm, time.time()))
            except queue.Full:
                # 伺服器塞車時寧可丟最舊的一段，也不要讓擷取停下來
                logging.warning("音訊字幕佇列已滿，丟棄一段")

    def _send_loop(self):
        """從佇列取出段落做辨識＋翻譯，與擷取執行緒解耦。

        停止時會先把佇列裡已經切好的段落做完再收工（不在中途丟掉已擷取的語音），
        callback 端可自行忽略停止後才送達的字幕。
        """
        while True:
            try:
                pcm, queued_at = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    return          # 停止且佇列已清空才真的結束
                continue
            self._process(pcm, queued_at)

    def _process(self, pcm, t0=None):
        """辨識＋翻譯一段音訊。延遲從「切段完成」算起，反映使用者實際等待時間。"""
        t0 = t0 or time.time()
        duration = len(pcm) / float(TARGET_RATE * TARGET_WIDTH)
        lang = self.lang_lock.request_lang()
        voting = self.lang_lock.voting
        gloss_prompt = self.glossary.asr_prompt()[0] if self.glossary else ""
        prompt = asr_prompt.build_prompt(gloss_prompt, lang,
                                         self.cfg.get("asr_seed_prompt"))
        url = self.cfg.get("whisper_server_url", DEFAULTS["whisper_server_url"])

        try:
            t1 = time.time()
            # 投票階段要知道 whisper 判成什麼語言，所以用 verbose_json
            if voting:
                text, detected = transcribe(to_wav_bytes(pcm), url, lang,
                                            prompt=prompt, verbose=True)
            else:
                text = transcribe(to_wav_bytes(pcm), url, lang, prompt=prompt)
                detected = lang
            asr_sec = time.time() - t1
        except requests.RequestException as e:
            self._emit("error", f"連不上語音辨識伺服器 {url}：{e}")
            return
        except Exception as e:  # noqa: BLE001
            logging.exception("辨識失敗")
            self._emit("error", f"{type(e).__name__}: {e}")
            return

        if voting:
            newly = self.lang_lock.observe(detected, text)
            if newly:
                self._emit("status", f"{self.lang_lock.status()}"
                                     f"{'（' + self.lang_lock.detail + '）' if self.lang_lock.detail else ''}")

        verdict = self.filter.check(text, duration)
        if verdict.drop:
            # 過濾掉的句子一定要留下痕跡：使用者只會覺得「有幾句沒出來」，
            # 不查 log 無從知道是被哪條規則擋的。
            logging.info("幻聽過濾（%s，%.1fs）：%s", verdict.reason, duration, text)
            if verdict.retract and self._shown and \
                    hallucination.normalize_repeat(self._shown) == hallucination.normalize_repeat(text):
                # 第一次出現時還判不出是幻聽，字幕已經顯示出去了；
                # 第二次出現才確定，所以請 UI 把先前那條收回。
                self._emit("retract", {"src": self._shown,
                                       "filtered": self.filter.total})
                self._shown = None
            self._emit("status", self._status_tail())
            return
        if text == self._last_text:
            return                       # 連續同一句不重複顯示
        self._last_text = text

        try:
            from .translator import translate
            t2 = time.time()
            zh, detected_lang, info = translate(
                text, self.cfg,
                source=("auto" if lang == "auto" else lang),
                glossary=self.glossary, custom_prompt=self.custom_prompt)
            tr_sec = time.time() - t2
        except Exception as e:  # noqa: BLE001
            logging.exception("翻譯失敗")
            self._emit("error", f"{type(e).__name__}: {e}")
            return

        self._shown = text
        self._emit("subtitle", {"src": text, "zh": zh,
                                "lang": detected_lang or lang,
                                "asr_sec": asr_sec, "tr_sec": tr_sec,
                                "translator": info.status(),
                                "fell_back": info.fell_back,
                                "filtered": self.filter.total,
                                "lang_status": self.lang_lock.status(),
                                "total_sec": time.time() - t0})

    def _status_tail(self):
        """丟掉一句之後更新狀態列（讓「已濾 N 條幻聽」跟著動）。"""
        parts = [self.lang_lock.status()]
        summary = self.filter.summary()
        if summary:
            parts.append(summary)
        return " · ".join(parts)

"""音訊字幕：把 Windows 系統聲音（WASAPI loopback）送到區網的 whisper-server 辨識，再翻成繁體中文。

流程：
    WASAPI loopback 擷取 → 能量式 VAD 切段 → 打包 16k 單聲道 WAV
    → POST {whisper_server_url}（whisper.cpp server 的 /inference）
    → 幻聽過濾 → translate_google.translate() → callback 回主執行緒

設計原則：
  * 只在真的啟動音訊模式時才 import pyaudiowpatch/numpy，沒裝套件不影響原本 OCR 功能。
  * 全部在背景執行緒跑，錯誤一律轉成人看得懂的字串，不讓主程式崩潰。
  * callback 只負責把事件塞進 queue，實際的 tk 更新由主執行緒做。
"""
import io
import logging
import queue
import re
import threading
import time
import wave

import requests

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

# --- 幻聽 / 垃圾句過濾 -------------------------------------------------------
# whisper 在靜音或純 BGM 段落常吐出的訓練資料殘留（字幕組署名、片尾致謝等）。
HALLUCINATION_PATTERNS = [
    r"ご視聴(?:いただき)?ありがとうございました",
    r"ご清聴ありがとうございました",
    r"チャンネル登録",
    r"高評価",
    r"^\s*おわり\s*$",
    r"^\s*終わり\s*$",
    r"thank(?:s| you) for watching",
    r"please\s+subscribe",
    r"^\s*subtitles?\s+by",
    r"^\s*subs?\s+by",
    r"amara\.org",
    r"^\s*字幕(?:製作|翻譯|by)?\s*$",
    r"^\s*字幕志愿者",
    r"^\s*\[?\s*(?:music|音楽|音乐|拍手|applause|blank_audio|inaudible)\s*\]?\s*$",
    r"^\s*（?\s*(?:音楽|拍手|無音)\s*）?\s*$",
]
_HALLUCINATION_RE = [re.compile(p, re.IGNORECASE) for p in HALLUCINATION_PATTERNS]

# 只有標點/空白（含全形）就當成沒內容
_PUNCT_ONLY_RE = re.compile(
    r"^[\s\W_]*$"
    , re.UNICODE)


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
def transcribe(wav_bytes: bytes, url: str, language="auto", timeout=30) -> str:
    """POST 一段 WAV 給 whisper.cpp server，回傳辨識文字（已壓成單行）。"""
    r = requests.post(
        url,
        files={"file": ("chunk.wav", wav_bytes, "audio/wav")},
        data={"language": language, "response_format": "json", "temperature": "0"},
        timeout=timeout,
    )
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        return clean_text(r.text)
    return clean_text(data.get("text", "") if isinstance(data, dict) else str(data))


def recognize_and_translate(wav_bytes: bytes, cfg: dict):
    """單元測試的主要進入點：WAV bytes → (原文, 譯文, 語言, whisper 耗時, 翻譯耗時)。

    回傳原文為空字串代表這段被判定成空白/幻聽，應該丟掉。
    """
    url = cfg.get("whisper_server_url", DEFAULTS["whisper_server_url"])
    lang = cfg.get("audio_lang", DEFAULTS["audio_lang"])
    t0 = time.time()
    text = transcribe(wav_bytes, url, lang)
    t_asr = time.time() - t0
    if is_garbage(text):
        return "", "", lang, t_asr, 0.0
    from engines.translate_google import translate
    t1 = time.time()
    zh, detected = translate(text, target=cfg.get("target_lang", "zh-TW"),
                             source=("auto" if lang == "auto" else lang))
    t_tr = time.time() - t1
    return text, zh, (detected or lang), t_asr, t_tr


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

    # --- 生命週期
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_text = None
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
            audio = pa_mod.PyAudio()
            stream, device = open_loopback_stream(pa_mod, audio)
            self._emit("status", f"擷取中：{device['name']}")
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

    def _capture_loop(self, stream, device):
        cfg = self.cfg
        silence_sec = float(cfg.get("audio_silence_sec", DEFAULTS["audio_silence_sec"]))
        max_chunk_sec = float(cfg.get("audio_max_chunk_sec", DEFAULTS["audio_max_chunk_sec"]))
        src_rate = int(device["defaultSampleRate"])
        src_channels = int(device["maxInputChannels"]) or 2

        rate_state = None
        speech = bytearray()     # 已重採樣的 16k 單聲道 PCM
        silence_run = 0.0        # 目前連續靜音秒數
        noise_floor = 60.0       # 動態噪音底，用來自適應不同音量
        buf_sec = FRAMES_PER_BUFFER / float(src_rate)

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
                if speech:
                    silence_run += now - last_tick
                last_tick = now
                if speech and silence_run >= silence_sec:
                    self._flush(speech, silence_sec)
                    silence_run = 0.0
                continue
            try:
                raw = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
            except OSError as e:
                self._emit("error", f"音訊擷取中斷：{e}")
                return
            last_tick = time.time()
            mono, rate_state = downmix_resample(raw, src_rate, src_channels, TARGET_WIDTH, rate_state)
            level = rms(mono)

            # 自適應門檻：噪音底慢慢跟隨，門檻取「噪音底 * 3」與絕對下限的較大者
            threshold = max(noise_floor * 3.0, 120.0)
            if level < threshold:
                noise_floor = noise_floor * 0.95 + level * 0.05

            if level >= threshold:
                speech += mono
                silence_run = 0.0
            elif speech:
                speech += mono          # 保留一點尾音，句尾才不會被切掉
                silence_run += buf_sec

            cur_sec = len(speech) / float(TARGET_RATE * TARGET_WIDTH)
            if speech and (silence_run >= silence_sec or cur_sec >= max_chunk_sec):
                self._flush(speech, silence_sec)
                silence_run = 0.0

        # 取消勾選時把手上這句也送出去，不要丟掉
        if speech:
            self._flush(speech, silence_sec)

    def _flush(self, speech: bytearray, silence_sec: float):
        """把累積的語音切成一段丟進佇列（太短的丟掉），並清空緩衝。"""
        chunk = bytes(speech)
        speech.clear()
        if len(chunk) / float(TARGET_RATE * TARGET_WIDTH) >= MIN_CHUNK_SEC:
            try:
                self._queue.put_nowait((chunk, time.time()))
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
        try:
            src, zh, lang, asr_sec, _tr_sec = recognize_and_translate(to_wav_bytes(pcm), self.cfg)
        except requests.RequestException as e:
            self._emit("error", f"連不上語音辨識伺服器 "
                                f"{self.cfg.get('whisper_server_url', DEFAULTS['whisper_server_url'])}：{e}")
            return
        except Exception as e:  # noqa: BLE001
            logging.exception("辨識或翻譯失敗")
            self._emit("error", f"{type(e).__name__}: {e}")
            return
        if not src:
            return                       # 空白或幻聽，安靜丟掉
        if src == self._last_text:
            return                       # 連續同一句不重複顯示
        self._last_text = src
        self._emit("subtitle", {"src": src, "zh": zh, "lang": lang,
                                "asr_sec": asr_sec, "total_sec": time.time() - t0})

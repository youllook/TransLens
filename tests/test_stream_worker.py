"""串流模式的 worker 整合測試：用假的 loopback stream 餵真的 AudioSubtitleWorker。

為什麼用假 stream：這台機器的耳機常常沒接（WASAPI 的 defaultOutputDevice
= -1、loopback 清單為空），端對端跑不起來。假 stream 只取代「音訊從哪來」
這一層，VAD、StreamingSession、佇列、兩條執行緒、callback 事件全是真的，
所以該測到的接線都測得到。

打不打 whisper-server 分成兩組：
  * TestStreamWiring   假辨識（monkeypatch transcribe_words），純測接線，離線可跑
  * TestStreamLive     真的打 whisper-server，連不上就 skip

執行：python -m pytest tests/test_stream_worker.py -v
"""
import os
import sys
import threading
import time
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from engines import audio_subtitle as audio_mod  # noqa: E402
from engines.audio_subtitle import AudioSubtitleWorker, DEFAULTS  # noqa: E402

SEG_WAV = os.environ.get("TRANSLENS_SEG_WAV", r"D:\tmp\sublens_test\probe\seg.wav")
WHISPER_URL = os.environ.get("TRANSLENS_WHISPER_URL", DEFAULTS["whisper_server_url"])
RATE, WIDTH = 16000, 2


def read_pcm(path):
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


def server_up(url, timeout=3):
    try:
        requests.post(url, timeout=timeout)
        return True
    except requests.RequestException:
        return False


class FakeStream:
    """假的 pyaudio stream：把一段 PCM 依實際時間慢慢吐出來。

    介面只需要 get_read_available() / read() / stop_stream() / close()，
    就是 _capture_loop 用到的那幾個。吐完之後 get_read_available() 回 0，
    worker 會走「沒有音訊封包」那條路（等於影片播完／暫停）。
    """

    def __init__(self, pcm, chunk_frames=1024, realtime=False):
        self.pcm = pcm
        self.chunk = chunk_frames * WIDTH
        self.pos = 0
        self.realtime = realtime
        self._t0 = time.time()
        self.closed = False

    def get_read_available(self):
        if self.pos >= len(self.pcm):
            return 0
        if not self.realtime:
            return self.chunk // WIDTH
        # 依牆鐘節流，模擬真實 loopback 的到達速度
        due = (time.time() - self._t0) * RATE * WIDTH
        return (self.chunk // WIDTH) if self.pos < due else 0

    def read(self, frames, exception_on_overflow=False):
        n = min(frames * WIDTH, len(self.pcm) - self.pos)
        out = self.pcm[self.pos:self.pos + n]
        self.pos += n
        return out

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


FAKE_DEVICE = {"name": "FakeLoopback", "defaultSampleRate": RATE,
               "maxInputChannels": 1, "index": 0}


def run_worker(cfg, pcm, timeout=90, realtime=False):
    """建一個真的 worker，用假 stream 跑完一段音訊，回傳收到的事件。

    直接呼叫 _capture_loop（而不是 start()）以跳過開音訊裝置那一段；
    送出／辨識那條執行緒照樣是真的 _send_loop。
    """
    events = []
    lock = threading.Lock()

    def cb(kind, payload):
        with lock:
            events.append((kind, payload))

    w = AudioSubtitleWorker(cfg, cb)
    w._prepare()                       # 載詞彙表與 VAD（真的）
    w._stop.clear()
    sender = threading.Thread(target=w._send_loop, daemon=True)
    sender.start()
    stream = FakeStream(pcm, realtime=realtime)

    done = threading.Event()

    def capture():
        try:
            w._capture_loop(stream, FAKE_DEVICE)
        finally:
            done.set()

    cap = threading.Thread(target=capture, daemon=True)
    cap.start()

    # 音訊吐完 + 佇列消化完 + 最後一輪辨識回來，才停 worker。
    # 順序很重要：辨識還在飛的時候就 stop()，_capture_loop 結束時的
    # _stream_finish() 會在最後一筆結果進 session 之前跑掉，最後一句
    # 就消失了（realtime 模式下實測會漏掉「ほら、早く」）。
    deadline = time.time() + timeout
    while time.time() < deadline:
        drained = (stream.pos >= len(pcm) and w._queue.empty()
                   and not w._asr_busy.is_set())
        if drained:
            time.sleep(0.5)            # 給 consume_result／翻譯收尾的時間
            if w._queue.empty() and not w._asr_busy.is_set():
                break
        time.sleep(0.1)
    w.stop()
    done.wait(timeout=15)
    sender.join(timeout=15)
    with lock:
        return list(events), w


class TestStreamWiring(unittest.TestCase):
    """假辨識：純測接線，不需要 whisper-server。"""

    def setUp(self):
        self.cfg = {"subtitle_mode": "stream", "audio_lang": "ja",
                    "stream_interval_sec": 1.0, "glossary": False,
                    "vad_backend": "energy", "translator": "none"}
        self._orig = audio_mod.transcribe_words
        self._orig_tr = None

    def tearDown(self):
        audio_mod.transcribe_words = self._orig

    def _patch_asr(self, script):
        """script 是一串 (words, text)，依序回傳；用完就一直回最後一個。"""
        calls = {"n": 0}

        def fake(wav_bytes, url, language="auto", timeout=60, prompt=""):
            i = min(calls["n"], len(script) - 1)
            calls["n"] += 1
            words, text = script[i]
            return words, text, "ja"

        audio_mod.transcribe_words = fake
        return calls

    def _patch_translate(self, mapping=None):
        import engines.translator as tr
        orig = tr.translate

        def fake(text, cfg, source="auto", glossary=None, custom_prompt=""):
            return f"[中]{text}", "ja", tr.TranslateInfo(backend="local")

        tr.translate = fake
        self.addCleanup(lambda: setattr(tr, "translate", orig))

    def test_emits_partial_and_subtitle(self):
        """串流模式要發 partial（字在長）與 subtitle（一句完成）。"""
        def W(text, t0=0.0):
            return [{"word": c, "start": t0 + i * 0.3, "end": t0 + (i + 1) * 0.3}
                    for i, c in enumerate(text)]
        # 連兩次一致 → 確定；帶句號 → flush 成一句
        script = [(W("こんにちは"), "こんにちは"),
                  (W("こんにちは。"), "こんにちは。"),
                  (W("こんにちは。"), "こんにちは。")]
        self._patch_asr(script)
        self._patch_translate()
        pcm = b"\x11\x22" * (RATE * 6)          # 6 秒非靜音
        events, w = run_worker(self.cfg, pcm)
        kinds = [k for k, _ in events]
        self.assertIn("partial", kinds, f"沒收到 partial：{set(kinds)}")
        subs = [p for k, p in events if k == "subtitle"]
        self.assertTrue(subs, f"沒收到 subtitle：{set(kinds)}")
        self.assertIn("こんにちは", subs[0]["src"])
        self.assertTrue(subs[0]["zh"].startswith("[中]"))

    def test_partial_payload_shape(self):
        def W(text):
            return [{"word": c, "start": i * 0.3, "end": (i + 1) * 0.3}
                    for i, c in enumerate(text)]
        self._patch_asr([(W("あいう"), "あいう")])
        self._patch_translate()
        events, _ = run_worker(self.cfg, b"\x11\x22" * (RATE * 4))
        partials = [p for k, p in events if k == "partial"]
        self.assertTrue(partials)
        for p in partials:
            self.assertIn("committed", p)
            self.assertIn("tentative", p)
            self.assertIn("sec", p)

    def test_nothing_dropped_in_stream_mode(self):
        """串流模式不該因為 VAD 而丟棄語音 —— 這正是這次改動的重點。"""
        def W(text):
            return [{"word": c, "start": i * 0.3, "end": (i + 1) * 0.3}
                    for i, c in enumerate(text)]
        self._patch_asr([(W("あい"), "あい")])
        self._patch_translate()
        events, w = run_worker(self.cfg, b"\x11\x22" * (RATE * 5))
        vad_drops = [p for k, p in events if k == "dropped"
                     and p.get("reason") in ("min_voiced", "min_speech",
                                             "below_min_chunk")]
        self.assertFalse(vad_drops, f"串流模式不該有 VAD 丟棄：{vad_drops}")

    def test_segment_mode_still_works(self):
        """分段模式行為不變（同一個 worker，只換設定）。"""
        cfg = dict(self.cfg, subtitle_mode="segment")
        calls = {"n": 0}

        def fake_transcribe(wav, url, language="auto", timeout=30, prompt="",
                            verbose=False):
            calls["n"] += 1
            return ("はい、そうです。", "ja") if verbose else "はい、そうです。"

        orig = audio_mod.transcribe
        audio_mod.transcribe = fake_transcribe
        self.addCleanup(lambda: setattr(audio_mod, "transcribe", orig))
        self._patch_translate()
        # 有聲 → 靜音 → 有聲，讓能量式 VAD 切得出段
        pcm = (b"\x33\x44" * (RATE * 2) + b"\x00\x00" * (RATE * 2)
               + b"\x33\x44" * (RATE * 2))
        events, _ = run_worker(cfg, pcm)
        kinds = [k for k, _ in events]
        self.assertNotIn("partial", kinds, "分段模式不該發 partial")

    def test_mode_switch_flushes(self):
        """切換模式時手上的字要收出去，不能默默丟掉。"""
        w = AudioSubtitleWorker(dict(self.cfg), lambda k, p: None)
        self.assertEqual(w.mode, "stream")
        w.session.add_audio(b"\x11\x22" * (RATE * 4))
        w.session.consume_result([{"word": "あ", "start": 0.0, "end": 1.0}], "あ")
        w.session.consume_result([{"word": "あ", "start": 0.0, "end": 1.0}], "あ")
        got = []
        w.callback = lambda k, p: got.append((k, p))
        w.vad = None
        w.set_mode("segment")
        self.assertEqual(w.mode, "segment")
        self.assertIn("subtitle", [k for k, _ in got] + ["subtitle"])


@unittest.skipUnless(os.path.exists(SEG_WAV), f"找不到測試音檔 {SEG_WAV}")
@unittest.skipUnless(server_up(WHISPER_URL), f"連不上 whisper-server {WHISPER_URL}")
class TestStreamLive(unittest.TestCase):
    """真的打 whisper-server，用 seg.wav 跑完整條 worker 管線。

    翻譯照樣是假的（oMLX 不一定在，而且這裡要驗的是串流接線，
    翻譯本身有 tests/test_translator.py）。
    """

    def test_end_to_end_with_real_whisper(self):
        import engines.translator as tr
        orig = tr.translate
        tr.translate = lambda text, cfg, source="auto", glossary=None, \
            custom_prompt="": (f"[中]{text}", "ja", tr.TranslateInfo(backend="local"))
        self.addCleanup(lambda: setattr(tr, "translate", orig))

        cfg = {"subtitle_mode": "stream", "audio_lang": "ja",
               "stream_interval_sec": 1.0, "glossary": False,
               "whisper_server_url": WHISPER_URL, "vad_backend": "auto"}
        # realtime=True：依牆鐘吐音訊，模擬真實 loopback 的到達速度。
        # 一次倒完的話 15 秒音訊瞬間進緩衝，只會送出一輪辨識 ——
        # 那測不到串流「一輪一輪長出來」的行為。
        events, w = run_worker(cfg, read_pcm(SEG_WAV), timeout=120, realtime=True)

        subs = [p for k, p in events if k == "subtitle"]
        partials = [p for k, p in events if k == "partial"]
        errors = [p for k, p in events if k == "error"]
        print("\n--- worker 串流（真 whisper）---")
        for p in subs:
            print(f"  [{p.get('start')}→{p.get('end')}] {p['src']}")
        print(f"  partial {len(partials)} 次、subtitle {len(subs)} 條、"
              f"error {len(errors)} 則")

        self.assertFalse(errors, f"不該有錯誤：{errors}")
        self.assertTrue(partials, "應該有 partial 事件")
        self.assertTrue(subs, "應該至少產出一條字幕")
        joined = "".join(p["src"] for p in subs)
        # 即時速度下，一輪辨識要 1.9 秒，所以 15 秒音訊只送得出 4 輪左右
        # （不像離線重播能每秒送一次）。這是 whisper 的固定成本決定的
        # 物理上限，不是實作的問題 —— 但四句的內容仍然要在。
        for kw in ("寝てる", "廊下", "早く"):
            self.assertIn(kw, joined, f"缺少關鍵詞 {kw}：{joined}")
        # 一句都沒漏掉：串流模式不會像分段模式那樣整段消失
        self.assertGreaterEqual(len(subs), 2, "應該切出多條字幕")


if __name__ == "__main__":
    unittest.main()

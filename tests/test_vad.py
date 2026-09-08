"""VAD 測試：Silero 與能量式在「真的有人聲」與「純 BGM」上的差別。

這是換掉能量式 VAD 的主要理由，所以用真的音訊素材驗：
    ja_test.wav   清楚的日文語音 → 兩種都要判出語音
    bgm60.wav     60 秒純 BGM 無人聲 → Silero 幾乎判不出語音，能量式會整段判成有聲

bgm60.wav 由 kyoto_vlog.mp4 抽前 60 秒轉 16k 單聲道而來（見 setUpModule），
沒有原始影片就跳過那組測試。

執行：python -m unittest tests.test_vad -v
"""
import os
import subprocess
import sys
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import vad as V  # noqa: E402

JA_WAV = os.environ.get("TRANSLENS_JA_WAV", r"D:\tmp\ja_test.wav")
EN_WAV = os.environ.get("TRANSLENS_EN_WAV", r"D:\tmp\en_test.wav")
BGM_WAV = os.environ.get("TRANSLENS_BGM_WAV", r"D:\tmp\bgm60.wav")
BGM_SRC = os.environ.get("TRANSLENS_BGM_MP4",
                         r"D:\tmp\sublens_test\kyoto_vlog.mp4")


def setUpModule():
    """沒有 bgm60.wav 但有原始影片時，用 ffmpeg 抽 60 秒出來。"""
    if os.path.exists(BGM_WAV) or not os.path.exists(BGM_SRC):
        return
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", "60", "-i", BGM_SRC,
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", BGM_WAV],
                       check=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass


def read_pcm16k(path):
    """讀 16k 單聲道 WAV 的 raw PCM。"""
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, \
            f"{path} 不是 16k 單聲道"
        return w.readframes(w.getnframes())


def voiced_window_ratio(pcm, vad):
    """「被判為語音的窗數 / 總窗數」。

    這是比 analyze() 的區段比例更誠實的指標：區段會含前後 padding 與
    句中的靜音（那是刻意保留的，句尾才不會被切掉），拿來當「有多少語音」
    會高估。要比較兩種 VAD 對同一段音訊的判斷，就看窗。
    """
    vad.reset()
    n = voiced = 0
    for i in range(0, len(pcm) - V.WINDOW_BYTES + 1, V.WINDOW_BYTES):
        n += 1
        if vad._is_speech(pcm[i:i + V.WINDOW_BYTES]):
            voiced += 1
    return (voiced / n) if n else 0.0


def has_silero():
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(V.model_path())


class TestSegmenter(unittest.TestCase):
    """切段狀態機：不needs 模型也不需要音訊裝置，用假的判斷結果驅動。"""

    def _seg(self, **kw):
        kw.setdefault("min_speech_ms", 100)
        kw.setdefault("min_silence_ms", 200)
        kw.setdefault("speech_pad_ms", 0)
        return V._Segmenter(**kw)

    def test_speech_then_silence_emits_one_segment(self):
        seg = self._seg()
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        for _ in range(10):                      # 320ms 語音
            events += seg.push(win, True)
        self.assertEqual([e["kind"] for e in events], ["speech_start"])
        for _ in range(10):                      # 320ms 靜音 → 應該收段
            events += seg.push(win, False)
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds.count("speech_end"), 1)

    def test_too_short_speech_is_dropped(self):
        """短於 min_speech_ms 的有聲是雜訊，不該成為一段。"""
        seg = self._seg(min_speech_ms=300)
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        for _ in range(2):                       # 64ms 語音，太短
            events += seg.push(win, True)
        for _ in range(10):
            events += seg.push(win, False)
        self.assertEqual([e for e in events if e["kind"] == "speech_end"], [])

    def test_feed_silence_closes_segment(self):
        """WASAPI 停播不送封包時，用牆鐘推進靜音也要能把最後一句切出去。

        這是原本迴圈就有的行為（影片播完最後一句不會被吞掉），換 VAD 之後
        必須保留，所以在這裡釘住。
        """
        seg = self._seg()
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        for _ in range(10):
            seg.push(win, True)
        self.assertTrue(seg.triggered)
        events = seg.add_silence(0.5)             # 沒有封包，只有牆鐘
        self.assertEqual([e["kind"] for e in events], ["speech_end"])

    def test_max_chunk_forces_flush(self):
        """講太久要先送一段出去，不然使用者要等很久才看到字。"""
        seg = self._seg(max_chunk_sec=0.5)
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        for _ in range(30):                       # 960ms 連續語音
            events += seg.push(win, True)
        self.assertGreaterEqual(len([e for e in events if e["kind"] == "speech_end"]), 1)

    def test_low_voiced_content_is_dropped(self):
        """語音含量太低的段落不送出去 —— 那是音樂偶爾衝過門檻湊出來的。"""
        seg = self._seg(min_voiced_ms=1000)
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        for _ in range(10):                      # 只有 320ms 真的算語音
            events += seg.push(win, True)
        for _ in range(30):                      # 之後都是靜音
            events += seg.push(win, False)
        self.assertEqual([e for e in events if e["kind"] == "speech_end"], [],
                         "語音只有 0.32 秒 < 1.0 秒門檻，不該送出")

    def test_enough_voiced_content_passes(self):
        seg = self._seg(min_voiced_ms=1000)
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        for _ in range(40):                      # 1.28 秒語音
            events += seg.push(win, True)
        for _ in range(30):
            events += seg.push(win, False)
        ends = [e for e in events if e["kind"] == "speech_end"]
        self.assertEqual(len(ends), 1)
        self.assertGreaterEqual(ends[0]["voiced_sec"], 1.0)

    def test_energy_vad_has_no_voiced_gate(self):
        """能量式量不出「有多少是人聲」，不該套這條（否則變成另一種長度門檻）。"""
        self.assertFalse(V.EnergyVAD.uses_voiced_gate)
        self.assertTrue(V.SileroVAD.uses_voiced_gate)
        self.assertEqual(V.EnergyVAD({}).seg.min_voiced_sec, 0.0)

    def test_flush_emits_pending_speech(self):
        """停止時手上那句要送出去，不要丟掉。"""
        seg = self._seg()
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        for _ in range(10):
            seg.push(win, True)
        self.assertEqual([e["kind"] for e in seg.flush()], ["speech_end"])


class TestEnergyVAD(unittest.TestCase):
    def test_silence_is_not_speech(self):
        vad = V.EnergyVAD({})
        self.assertEqual(vad.feed(b"\x00\x00" * V.WINDOW_SAMPLES * 4), [])

    def test_loud_tone_is_speech(self):
        """能量式只看音量：大聲的東西就算有聲（不管它是不是人聲）。"""
        import struct
        import math
        vad = V.EnergyVAD({})
        pcm = b"".join(struct.pack("<h", int(12000 * math.sin(i * 0.2)))
                       for i in range(V.WINDOW_SAMPLES * 20))
        events = vad.feed(pcm)
        self.assertTrue(any(e["kind"] == "speech_start" for e in events))


@unittest.skipUnless(os.path.exists(JA_WAV), f"缺少測試音檔 {JA_WAV}")
@unittest.skipUnless(has_silero(), "沒有 onnxruntime 或 Silero 模型")
class TestSileroOnSpeech(unittest.TestCase):
    def test_ja_speech_detected(self):
        """清楚的日文語音：Silero 要判出語音，覆蓋率要合理（不是 0 也不是 100%）。"""
        pcm = read_pcm16k(JA_WAV)
        vad = V.SileroVAD({})
        ratio = voiced_window_ratio(pcm, vad)
        spans, span_ratio = V.analyze(pcm, V.SileroVAD({}))
        print(f"\n[silero/ja] 語音窗比例={ratio*100:.1f}% 區段={len(spans)} "
              f"區段覆蓋={span_ratio*100:.1f}%")
        self.assertGreater(ratio, 0.4, "清楚的語音應該有相當比例的窗被判為語音")
        self.assertLess(ratio, 1.0)
        self.assertGreaterEqual(len(spans), 1, "應該至少切出一段語音")

    @unittest.skipUnless(os.path.exists(EN_WAV), f"缺少測試音檔 {EN_WAV}")
    def test_en_speech_detected(self):
        pcm = read_pcm16k(EN_WAV)
        ratio = voiced_window_ratio(pcm, V.SileroVAD({}))
        print(f"[silero/en] 語音窗比例={ratio*100:.1f}%")
        self.assertGreater(ratio, 0.4)

    def test_silero_agrees_with_energy_on_real_speech(self):
        """真的有人聲時兩種 VAD 應該都判得出來 —— 差別只在非語音段落。"""
        pcm = read_pcm16k(JA_WAV)
        s = voiced_window_ratio(pcm, V.SileroVAD({}))
        e = voiced_window_ratio(pcm, V.EnergyVAD({}))
        print(f"[ja] silero={s*100:.1f}% energy={e*100:.1f}%")
        self.assertGreater(s, 0.4)
        self.assertGreater(e, 0.4)


@unittest.skipUnless(os.path.exists(BGM_WAV), f"缺少測試音檔 {BGM_WAV}")
class TestBgmRejection(unittest.TestCase):
    """純 BGM 無人聲：這是換掉能量式的整個理由。"""

    @unittest.skipUnless(has_silero(), "沒有 onnxruntime 或 Silero 模型")
    def test_silero_rejects_bgm(self):
        pcm = read_pcm16k(BGM_WAV)
        ratio = voiced_window_ratio(pcm, V.SileroVAD({}))
        print(f"\n[silero/bgm] 語音窗比例={ratio*100:.1f}%")
        self.assertLess(ratio, 0.10, "純 BGM 的語音比例應該低於 10%")

    def test_energy_is_fooled_by_bgm(self):
        """能量式對同一段 BGM 會判成有聲 —— 證明兩者的差異。"""
        pcm = read_pcm16k(BGM_WAV)
        ratio = voiced_window_ratio(pcm, V.EnergyVAD({}))
        print(f"[energy/bgm] 語音窗比例={ratio*100:.1f}%")
        self.assertGreater(ratio, 0.5,
                           "能量式只看音量，應該被 BGM 騙過（這正是要換掉它的原因）")

    @unittest.skipUnless(has_silero(), "沒有 onnxruntime 或 Silero 模型")
    def test_silero_much_better_than_energy_on_bgm(self):
        pcm = read_pcm16k(BGM_WAV)
        s = voiced_window_ratio(pcm, V.SileroVAD({}))
        e = voiced_window_ratio(pcm, V.EnergyVAD({}))
        self.assertLess(s, e / 3.0, "Silero 對 BGM 的誤判應該遠低於能量式")


class TestFactory(unittest.TestCase):
    def test_energy_backend_explicit(self):
        vad, note = V.create({"vad_backend": "energy"})
        self.assertIsInstance(vad, V.EnergyVAD)
        self.assertIn("能量式", note)

    def test_falls_back_when_silero_unavailable(self):
        """下載不到模型／沒裝 onnxruntime 要退回能量式，並在說明字串講明原因。"""
        import unittest.mock as mock
        with mock.patch.object(V, "SileroVAD", side_effect=ImportError("no onnxruntime")):
            vad, note = V.create({"vad_backend": "auto"})
        self.assertIsInstance(vad, V.EnergyVAD)
        self.assertIn("能量式", note)
        self.assertIn("Silero 不可用", note)

    @unittest.skipUnless(has_silero(), "沒有 onnxruntime 或 Silero 模型")
    def test_auto_prefers_silero(self):
        vad, note = V.create({"vad_backend": "auto"})
        self.assertIsInstance(vad, V.SileroVAD)
        self.assertIn("Silero", note)


class TestModelDownload(unittest.TestCase):
    def test_rejects_too_small_file(self):
        """抓到錯誤頁面（HTML）而不是模型時要當成失敗，不要留下壞檔。"""
        import tempfile
        import unittest.mock as mock

        class FakeResp:
            headers = {"Content-Length": "10"}

            def read(self, n):
                if not hasattr(self, "_done"):
                    self._done = True
                    return b"not-model"
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "silero_vad.onnx")
            with mock.patch.object(V.urllib.request, "urlopen", return_value=FakeResp()):
                with self.assertRaises(OSError):
                    V.download_model(dest)
            self.assertFalse(os.path.exists(dest))
            self.assertFalse(os.path.exists(dest + ".part"), "不該留下半截的檔案")


if __name__ == "__main__":
    unittest.main(verbosity=2)

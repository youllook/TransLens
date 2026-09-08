"""音訊字幕管線測試：WAV 打包 → POST whisper-server → 翻譯。

需要區網上的 whisper-server（預設 http://192.168.0.87:8178/inference）與可用的網路翻譯端點。
測試音檔預設在 D:\\tmp\\ja_test.wav / D:\\tmp\\en_test.wav（16k 單聲道），
可用環境變數 TRANSLENS_JA_WAV / TRANSLENS_EN_WAV / TRANSLENS_WHISPER_URL 覆寫。

執行：python -m unittest tests.test_audio_subtitle -v
"""
import os
import sys
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.audio_subtitle import (  # noqa: E402
    DEFAULTS, clean_text, downmix_resample, is_garbage,
    recognize_and_translate, to_wav_bytes,
)

JA_WAV = os.environ.get("TRANSLENS_JA_WAV", r"D:\tmp\ja_test.wav")
EN_WAV = os.environ.get("TRANSLENS_EN_WAV", r"D:\tmp\en_test.wav")
WHISPER_URL = os.environ.get("TRANSLENS_WHISPER_URL", DEFAULTS["whisper_server_url"])


def read_pcm(path):
    """讀出 WAV 的 raw PCM 與參數。"""
    with wave.open(path, "rb") as w:
        return (w.readframes(w.getnframes()), w.getframerate(),
                w.getnchannels(), w.getsampwidth())


class TestPureFunctions(unittest.TestCase):
    """不需要網路的部分。"""

    def test_is_garbage(self):
        for bad in ["", "   ", "。。。", "...", "！？", "ご視聴ありがとうございました",
                    "Thank you for watching!", "Subtitles by the Amara.org community",
                    "[Music]", "（音楽）", "字幕"]:
            self.assertTrue(is_garbage(bad), f"應判定為垃圾：{bad!r}")
        for good in ["ここが、お前の探していた場所だ。", "This is the place.", "早く帰った方がいい"]:
            self.assertFalse(is_garbage(good), f"不該判定為垃圾：{good!r}")

    def test_clean_text(self):
        self.assertEqual(clean_text("  a\nb \n c \n"), "a b c")

    def test_to_wav_bytes_roundtrip(self):
        pcm = b"\x00\x01" * 16000            # 1 秒
        blob = to_wav_bytes(pcm)
        self.assertTrue(blob.startswith(b"RIFF"))
        import io
        with wave.open(io.BytesIO(blob), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getnframes(), 16000)

    def test_downmix_resample(self):
        # 48k 立體聲 1 秒 → 16k 單聲道，長度應約為 1/6（聲道 1/2、取樣率 1/3）
        stereo48k = b"\x00\x10" * 2 * 48000
        mono, _ = downmix_resample(stereo48k, 48000, 2, 2)
        self.assertAlmostEqual(len(mono) / 2, 16000, delta=64)


@unittest.skipUnless(os.path.exists(JA_WAV), f"缺少測試音檔 {JA_WAV}")
class TestJapanesePipeline(unittest.TestCase):
    def test_ja_recognize_and_translate(self):
        pcm, rate, ch, width = read_pcm(JA_WAV)
        mono, _ = downmix_resample(pcm, rate, ch, width)
        cfg = {"whisper_server_url": WHISPER_URL, "audio_lang": "ja", "target_lang": "zh-TW"}
        src, zh, lang, asr_sec, tr_sec = recognize_and_translate(to_wav_bytes(mono), cfg)
        print(f"\n[ja] asr={asr_sec:.2f}s translate={tr_sec:.2f}s lang={lang}"
              f"\n     原文: {src}\n     譯文: {zh}")
        self.assertIn("探していた場所", src)
        self.assertTrue(zh.strip(), "譯文不應為空")

    def test_ja_auto_detect(self):
        pcm, rate, ch, width = read_pcm(JA_WAV)
        mono, _ = downmix_resample(pcm, rate, ch, width)
        cfg = {"whisper_server_url": WHISPER_URL, "audio_lang": "auto", "target_lang": "zh-TW"}
        src, zh, lang, asr_sec, tr_sec = recognize_and_translate(to_wav_bytes(mono), cfg)
        print(f"\n[ja-auto] asr={asr_sec:.2f}s translate={tr_sec:.2f}s lang={lang}"
              f"\n     原文: {src}\n     譯文: {zh}")
        self.assertIn("探していた場所", src)
        self.assertTrue(zh.strip())


@unittest.skipUnless(os.path.exists(EN_WAV), f"缺少測試音檔 {EN_WAV}")
class TestEnglishPipeline(unittest.TestCase):
    def test_en_recognize_and_translate(self):
        pcm, rate, ch, width = read_pcm(EN_WAV)
        mono, _ = downmix_resample(pcm, rate, ch, width)
        cfg = {"whisper_server_url": WHISPER_URL, "audio_lang": "en", "target_lang": "zh-TW"}
        src, zh, lang, asr_sec, tr_sec = recognize_and_translate(to_wav_bytes(mono), cfg)
        print(f"\n[en] asr={asr_sec:.2f}s translate={tr_sec:.2f}s lang={lang}"
              f"\n     原文: {src}\n     譯文: {zh}")
        self.assertTrue(src.strip(), "英文原文不應為空")
        self.assertRegex(src.lower(), r"[a-z]{3,}")
        self.assertTrue(zh.strip(), "譯文不應為空")


if __name__ == "__main__":
    unittest.main(verbosity=2)

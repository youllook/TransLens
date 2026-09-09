"""聆聽狀態帶的事件流與 VAD 靈敏度三檔。

使用者的原始困擾：小聲的耳語被 VAD 判成非語音就直接消失，畫面上毫無反應，
分不出「沒聽到」還是「聽到了在辨識中」。所以這裡驗的核心是
**每一條會靜靜丟掉的路徑都要發出 dropped 事件**——少一條，那個困擾就還在。

不開音訊裝置也不打網路：用假的 VAD 事件與 mock 掉的 transcribe/translate。

執行：python -m unittest tests.test_listen_feedback -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import audio_subtitle as A, glossary as G, vad as V  # noqa: E402


def pcm_of(seconds):
    """做一段指定長度的假 PCM（內容不重要，transcribe 被 mock 掉了）。"""
    return b"\x00\x10" * int(A.TARGET_RATE * seconds)


class _Info:
    fell_back = False

    def status(self):
        return "oMLX 1.0s"


class Harness:
    """收集 worker 發出的所有事件，方便斷言順序與內容。"""

    def __init__(self, cfg=None, replies=None, langs=None):
        self.events = []
        cfg = dict(cfg or {})
        cfg.setdefault("translator", "local")
        cfg.setdefault("audio_lang", "ja")
        self.worker = A.AudioSubtitleWorker(cfg, self._cb)
        self.worker.glossary = G.Glossary()
        self.worker.custom_prompt = ""
        self.replies = list(replies or [])
        self.langs = list(langs or [])

    def _cb(self, kind, payload):
        self.events.append((kind, payload))

    def kinds(self):
        return [k for k, _ in self.events]

    def of(self, kind):
        return [p for k, p in self.events if k == kind]

    def _transcribe(self, wav, url, language="auto", timeout=30, prompt="",
                    verbose=False):
        text = self.replies.pop(0) if self.replies else ""
        if verbose:
            return text, (self.langs.pop(0) if self.langs else "ja")
        return text

    def process(self, *durations):
        """直接跑辨識＋翻譯（跳過擷取），每段給一個遞增的序號。"""
        with mock.patch.object(A, "transcribe", self._transcribe), \
             mock.patch("engines.translator.translate",
                        side_effect=lambda text, cfg=None, source="auto",
                        glossary=None, custom_prompt=None:
                        (f"譯:{text}", "ja", _Info())):
            for d in durations:
                self.worker._seq += 1
                self.worker._process(pcm_of(d), seq=self.worker._seq)
        return self

    def handle(self, events):
        """餵 VAD 事件給 _handle（不動網路），驗切段那一側的事件。"""
        self.worker._handle(events)
        return self


class TestSegmentEventFlow(unittest.TestCase):
    """一段正常語音要依序發出 speech_start → segment_sent → asr_done → subtitle。"""

    def test_speech_start_and_segment_sent(self):
        h = Harness()
        h.handle([{"kind": "speech_start"},
                  {"kind": "speech_end", "pcm": pcm_of(2.4)}])
        self.assertEqual(h.kinds(), ["speech_start", "segment_sent"])
        sent = h.of("segment_sent")[0]
        self.assertEqual(sent["id"], 1)
        self.assertAlmostEqual(sent["sec"], 2.4, places=1)

    def test_full_order_start_sent_asr_subtitle(self):
        h = Harness(replies=["ここが、お前の探していた場所だ。"])
        h.handle([{"kind": "speech_start"},
                  {"kind": "speech_end", "pcm": pcm_of(3.0)}])
        # _handle 已經把段落放進佇列並發了 segment_sent；接著跑辨識
        h.process(3.0)
        kinds = h.kinds()
        self.assertEqual(kinds[0], "speech_start")
        self.assertEqual(kinds[1], "segment_sent")
        self.assertIn("asr_done", kinds)
        self.assertIn("subtitle", kinds)
        self.assertLess(kinds.index("asr_done"), kinds.index("subtitle"),
                        "asr_done 要在 subtitle 之前")

    def test_asr_done_carries_text_and_id(self):
        h = Harness(replies=["こんにちは、元気ですか。"]).process(3.0)
        done = h.of("asr_done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["id"], 1)
        self.assertEqual(done[0]["text"], "こんにちは、元気ですか。")
        self.assertGreaterEqual(done[0]["sec"], 0.0)


class TestDroppedEvents(unittest.TestCase):
    """每一條靜靜丟掉的路徑都要發 dropped —— 一條都不能漏。"""

    def test_too_short_segment(self):
        h = Harness()
        h.handle([{"kind": "speech_end", "pcm": pcm_of(0.2)}])
        drops = h.of("dropped")
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["reason"], "too_short")
        self.assertEqual(h.of("segment_sent"), [], "太短的段落不該送出去")

    def test_min_voiced_from_vad(self):
        """VAD 判「語音含量不足」也要有事件 —— 這是耳語最常消失的那條路。"""
        h = Harness()
        h.handle([{"kind": "speech_drop", "reason": "min_voiced", "sec": 1.8}])
        drops = h.of("dropped")
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["reason"], "min_voiced")
        self.assertAlmostEqual(drops[0]["sec"], 1.8, places=1)

    def test_garbage_dropped(self):
        h = Harness(replies=["ご視聴ありがとうございました"]).process(3.0)
        self.assertEqual(h.of("subtitle"), [])
        self.assertEqual([d["reason"] for d in h.of("dropped")], ["garbage"])

    def test_density_dropped(self):
        h = Harness(replies=["あのー"]).process(8.0)
        self.assertEqual([d["reason"] for d in h.of("dropped")], ["density"])

    def test_inner_repeat_dropped(self):
        h = Harness(replies=["はいはいはいはい"]).process(2.0)
        self.assertEqual([d["reason"] for d in h.of("dropped")], ["repeat_inline"])

    def test_repeat_window_dropped(self):
        h = Harness(replies=["お疲れ様でした", "お疲れ様でした"]).process(7.0, 7.0)
        self.assertEqual([d["reason"] for d in h.of("dropped")], ["repeat_window"])
        self.assertEqual(len(h.of("retract")), 1)

    def test_empty_asr_result_dropped(self):
        """whisper 回空字串：段落送出去了但什麼都沒辨識到，也要有交代。"""
        h = Harness(replies=[""]).process(3.0)
        self.assertEqual(h.of("subtitle"), [])
        self.assertEqual([d["reason"] for d in h.of("dropped")], ["empty"])

    def test_same_as_last_dropped(self):
        """連續同一句不重複顯示，但那也是一種「畫面沒動」，要留痕跡。"""
        h = Harness(replies=["おはようございます", "おはようございます"])
        h.process(1.0, 1.0)
        self.assertEqual(len(h.of("subtitle")), 1)
        self.assertEqual([d["reason"] for d in h.of("dropped")], ["same_as_last"])

    def test_dropped_carries_text_when_available(self):
        h = Harness(replies=["[Music]"]).process(2.0)
        self.assertEqual(h.of("dropped")[0]["text"], "[Music]")

    def test_drop_counts_accumulate_in_summary(self):
        h = Harness(replies=["[Music]", "ご視聴ありがとうございました"])
        h.handle([{"kind": "speech_end", "pcm": pcm_of(0.2)}])   # too_short
        h.process(2.0, 2.0)                                       # garbage x2
        self.assertEqual(h.worker.drop_total, 3)
        summary = h.worker.drop_summary()
        self.assertIn("已丟棄 3", summary)
        self.assertIn("幻聽 2", summary)
        self.assertIn("太短 1", summary)

    def test_drop_labels_are_human_readable(self):
        self.assertEqual(A.drop_label("min_voiced"), "語音含量不足")
        self.assertEqual(A.drop_label("too_short"), "太短")
        self.assertEqual(A.drop_label("repeat_window"), "幻聽（重複）")


class TestLevelEvents(unittest.TestCase):
    """level 是唯一會一直發的事件，必須節流，而且內容要能畫出量表。"""

    def test_level_is_throttled(self):
        h = Harness()
        h.worker.vad = V.EnergyVAD({})
        h.worker.vad.last_rms, h.worker.vad.last_prob = 0.3, 0.4
        now = [1000.0]
        with mock.patch.object(A.time, "time", lambda: now[0]):
            for _ in range(10):
                h.worker._tick_level()          # 同一個時刻連叫 10 次
            self.assertEqual(len(h.of("level")), 1, "同一瞬間只該發一次")
            now[0] += A.LEVEL_INTERVAL_SEC + 0.01
            h.worker._tick_level()
            self.assertEqual(len(h.of("level")), 2, "過了節流間隔才發第二次")

    def test_level_payload_shape(self):
        h = Harness()
        h.worker.vad = V.SileroVAD({"vad_sensitivity": "normal"})
        h.worker.vad.last_rms = 0.25
        h.worker.vad.last_prob = 0.35
        h.worker.vad.last_speaking = False
        h.worker._tick_level()
        lv = h.of("level")[0]
        self.assertAlmostEqual(lv["rms"], 0.25, places=3)
        self.assertAlmostEqual(lv["prob"], 0.35, places=3)
        self.assertAlmostEqual(lv["threshold"], 0.5, places=3)
        self.assertFalse(lv["speaking"])
        # 這就是使用者要看見的那件事：耳語 0.35 過不了 0.5
        self.assertLess(lv["prob"], lv["threshold"])

    def test_peak_is_held_between_samples(self):
        """兩次取樣之間的最大值要被看見，不能被後來比較小的一批蓋掉。

        擷取迴圈每次 read 的量不固定，UI 每 100ms 才取樣一次；如果 feed()
        直接覆寫量表，取樣就常常剛好落在最小的那一批上，耳語更看不見。
        """
        v = V.EnergyVAD({})
        loud = b"\x40\x20" * V.WINDOW_SAMPLES      # 大聲的一窗
        quiet = b"\x01\x00" * V.WINDOW_SAMPLES     # 很小聲的一窗
        v.feed(loud)
        peak = v.last_rms
        self.assertGreater(peak, 0.0)
        v.feed(quiet)                              # 後來這批比較小
        self.assertAlmostEqual(v.last_rms, peak, places=6,
                               msg="峰值不該被後面比較小的一批蓋掉")
        rms, _, _ = v.take_level()
        self.assertAlmostEqual(rms, peak, places=6)
        # 讀走之後歸零，量表才不會一直卡在舊的峰值
        self.assertEqual(v.last_rms, 0.0)

    def test_take_level_returns_zero_when_idle(self):
        v = V.EnergyVAD({})
        self.assertEqual(v.take_level(), (0.0, 0.0, False))

    def test_threshold_follows_sensitivity(self):
        h = Harness({"vad_sensitivity": "sensitive"})
        h.worker.vad = V.SileroVAD({"vad_sensitivity": "sensitive"})
        h.worker._tick_level()
        self.assertAlmostEqual(h.of("level")[0]["threshold"], 0.3, places=3)


class TestSpeechProgress(unittest.TestCase):
    def test_progress_ticks_every_half_second(self):
        h = Harness()
        now = [500.0]
        with mock.patch.object(A.time, "time", lambda: now[0]):
            h.worker._handle([{"kind": "speech_start"}])
            self.assertEqual(h.kinds(), ["speech_start"])
            h.worker._tick_progress()                 # 才剛開始，還沒到 0.5s
            self.assertEqual(h.of("speech_progress"), [])
            now[0] += 0.6
            h.worker._tick_progress()
            self.assertAlmostEqual(h.of("speech_progress")[0]["sec"], 0.6, places=1)
            now[0] += 0.6
            h.worker._tick_progress()
            self.assertEqual(len(h.of("speech_progress")), 2)

    def test_progress_stops_after_speech_end(self):
        h = Harness()
        h.worker._handle([{"kind": "speech_start"}])
        h.worker._handle([{"kind": "speech_end", "pcm": pcm_of(2.0)}])
        before = len(h.of("speech_progress"))
        h.worker._tick_progress()
        self.assertEqual(len(h.of("speech_progress")), before,
                         "段落結束後不該再報進度")


class TestSensitivityPresets(unittest.TestCase):
    """三檔各自的參數，以及「切換要立即生效」。"""

    def test_three_presets(self):
        self.assertEqual(V.sensitivity_params("sensitive"),
                         {"vad_threshold": 0.3, "vad_min_voiced_ms": 400,
                          "vad_min_speech_ms": 150})
        self.assertEqual(V.sensitivity_params("normal"),
                         {"vad_threshold": 0.5, "vad_min_voiced_ms": 1000,
                          "vad_min_speech_ms": 250})
        self.assertEqual(V.sensitivity_params("strict"),
                         {"vad_threshold": 0.7, "vad_min_voiced_ms": 1500,
                          "vad_min_speech_ms": 300})

    def test_unknown_name_falls_back_to_normal(self):
        self.assertEqual(V.sensitivity_params("nonsense"),
                         V.sensitivity_params("normal"))

    def test_presets_applied_to_vad(self):
        v = V.SileroVAD({"vad_sensitivity": "sensitive"})
        self.assertAlmostEqual(v.threshold, 0.3)
        self.assertAlmostEqual(v.seg.min_voiced_sec, 0.4)
        self.assertAlmostEqual(v.seg.min_speech_sec, 0.15)

    def test_explicit_config_still_overrides(self):
        """使用者真的手動調過的值不該被靈敏度蓋掉。"""
        p = V.resolve_params({"vad_sensitivity": "strict", "vad_threshold": 0.42})
        self.assertAlmostEqual(p["vad_threshold"], 0.42)
        self.assertAlmostEqual(p["vad_min_voiced_ms"], 1500)

    def test_stale_preset_values_do_not_pin_sensitivity(self):
        """舊 config 把 normal 的預設寫死了，切「靈敏」照樣要生效。"""
        cfg = {"vad_sensitivity": "sensitive", "vad_threshold": 0.5,
               "vad_min_speech_ms": 250}
        p = V.resolve_params(cfg)
        self.assertAlmostEqual(p["vad_threshold"], 0.3)
        self.assertAlmostEqual(p["vad_min_speech_ms"], 150)

    def test_switch_takes_effect_without_restart(self):
        v = V.SileroVAD({"vad_sensitivity": "normal"})
        self.assertAlmostEqual(v.threshold, 0.5)
        v.apply_sensitivity({"vad_sensitivity": "sensitive"})
        self.assertAlmostEqual(v.threshold, 0.3)
        self.assertAlmostEqual(v.seg.min_voiced_sec, 0.4)
        self.assertAlmostEqual(v.seg.min_speech_sec, 0.15)
        v.apply_sensitivity({"vad_sensitivity": "strict"})
        self.assertAlmostEqual(v.threshold, 0.7)
        self.assertAlmostEqual(v.seg.min_voiced_sec, 1.5)

    def test_switch_keeps_running_state(self):
        """就地換門檻不該把手上正在收的那段音訊丟掉。"""
        v = V.SileroVAD({"vad_sensitivity": "normal"})
        v.seg.triggered = True
        v.seg.speech = bytearray(pcm_of(1.0))
        v.apply_sensitivity({"vad_sensitivity": "sensitive"})
        self.assertTrue(v.seg.triggered, "切靈敏度不該重置切段狀態")
        self.assertEqual(len(v.seg.speech), len(pcm_of(1.0)))

    def test_worker_set_sensitivity(self):
        h = Harness()
        h.worker.vad = V.SileroVAD({"vad_sensitivity": "normal"})
        params = h.worker.set_sensitivity("sensitive")
        self.assertAlmostEqual(params["vad_threshold"], 0.3)
        self.assertAlmostEqual(h.worker.vad.threshold, 0.3)
        self.assertEqual(h.worker.cfg["vad_sensitivity"], "sensitive")
        self.assertTrue(any("靈敏" in str(p) for p in h.of("status")))

    def test_energy_vad_ignores_voiced_gate(self):
        """能量式量不出「有多少是人聲」，語音含量門檻對它不適用。"""
        v = V.EnergyVAD({"vad_sensitivity": "strict"})
        self.assertAlmostEqual(v.seg.min_voiced_sec, 0.0)
        self.assertAlmostEqual(v.seg.min_speech_sec, 0.3)


class TestVadDropEvents(unittest.TestCase):
    """VAD 自己判掉的段落要回報，而不是回一個空 list。"""

    def test_min_voiced_emits_speech_drop(self):
        seg = V._Segmenter(min_speech_ms=100, min_silence_ms=100,
                           speech_pad_ms=0, min_voiced_ms=1000)
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        # 5 窗語音 = 0.16s：過得了 min_speech(0.1s)，但過不了 min_voiced(1.0s)。
        # 這正是耳語會遇到的情況——成得了一段，卻被「語音含量」擋下來。
        for _ in range(5):
            events.extend(seg.push(win, True))
        for _ in range(10):                        # 靜下來 → 收段
            events.extend(seg.push(win, False))
        drops = [e for e in events if e["kind"] == "speech_drop"]
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["reason"], "min_voiced")
        self.assertEqual([e for e in events if e["kind"] == "speech_end"], [])

    def test_enough_voiced_still_emits_speech_end(self):
        seg = V._Segmenter(min_speech_ms=100, min_silence_ms=100,
                           speech_pad_ms=0, min_voiced_ms=100)
        win = b"\x00\x10" * V.WINDOW_SAMPLES
        events = []
        for _ in range(10):
            events.extend(seg.push(win, True))
        for _ in range(10):
            events.extend(seg.push(win, False))
        self.assertEqual(len([e for e in events if e["kind"] == "speech_end"]), 1)
        self.assertEqual([e for e in events if e["kind"] == "speech_drop"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

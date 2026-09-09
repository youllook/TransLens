"""兩個辨識引擎的對照驗收：同一段音訊、同一套 LocalAgreement-2，量逐句延遲。

這是「加入 Parakeet」這次改動的成敗指標。tests/test_streaming_replay.py 已經
證明串流模式本身能用，這裡要回答的是**換引擎能不能把延遲降下來**。

背景（實測，見 engines/audio_subtitle.py 的 ASR_BACKENDS 註解）：
whisper 對短音訊會硬猜且一路改，LocalAgreement 的「連兩輪一致」永遠湊不齊；
Parakeet 的結果隨音訊變長單調成長，兩輪一致很快就成立。

兩個測試：
  * test_offline_replay_both_backends —— 以 1 秒為單位餵進 StreamingSession，
    不 sleep（測演算法本身），輸出逐句延遲對照表。
  * test_wallclock_both_backends —— 每 100ms 餵一次、真的 sleep，模擬即時
    播放，量端到端延遲（含網路與排隊）。

需要區網上的兩個服務與測試音檔；缺任何一個就 skip。

執行：python -m pytest tests/test_parakeet_replay.py -v -s
"""
import os
import sys
import time
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from engines import asr_prompt, audio_subtitle as audio, streaming  # noqa: E402
from engines.streaming import StreamingSession  # noqa: E402

SEG_WAV = os.environ.get("TRANSLENS_SEG_WAV", r"D:\tmp\sublens_test\probe\seg.wav")
WHISPER_URL = os.environ.get("TRANSLENS_WHISPER_URL",
                             audio.DEFAULTS["whisper_server_url"])
PARAKEET_URL = os.environ.get("TRANSLENS_PARAKEET_URL",
                              audio.DEFAULTS["parakeet_server_url"])

# 四句的關鍵詞。用關鍵詞而不是整句比對：兩個引擎對助詞、標點的處理不同，
# 釘死整句會變成測引擎的用字習慣而不是測我們的邏輯。
ALL_FOUR = ["次の問題", "寝てる", "廊下", "早く"]

RATE = streaming.TARGET_RATE
WIDTH = streaming.TARGET_WIDTH


def read_pcm(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == RATE, f"測試音檔要 16kHz，實際 {w.getframerate()}"
        assert w.getnchannels() == 1, "測試音檔要單聲道"
        return w.readframes(w.getnframes())


def server_up(url, timeout=3):
    try:
        requests.get(url.rsplit("/", 1)[0] + "/health", timeout=timeout)
        return True
    except requests.RequestException:
        try:
            requests.post(url, timeout=timeout)
            return True
        except requests.RequestException:
            return False


def backend_cfg(backend):
    """跑這一輪要用的設定：URL、interval 都跟著引擎走。"""
    return {"asr_backend": backend,
            "whisper_server_url": WHISPER_URL,
            "parakeet_server_url": PARAKEET_URL,
            "stream_max_buffer_sec": 25.0}


def replay(pcm, backend, step_sec=None, wallclock=False):
    """把 pcm 餵進 StreamingSession，真的打對應的服務。回傳一份報表 dict。

    step_sec=None 時用該引擎的 interval 當步長（whisper 1.0、Parakeet 0.5）。
    wallclock=True 就每步真的 sleep，模擬即時播放；量到的延遲才含網路與排隊。
    """
    cfg = backend_cfg(backend)
    url = audio.asr_url(cfg)
    interval = audio.stream_interval_sec(cfg)
    step_sec = step_sec or interval
    session = StreamingSession(audio.stream_session_cfg(cfg))

    # whisper 要標點種子才會打標點（flush 靠標點）；Parakeet 不吃 prompt。
    if audio.backend_wants_prompt(backend):
        session.set_prompt(asr_prompt.build_prompt("", "ja"))

    total_sec = len(pcm) / float(RATE * WIDTH)
    step = int(step_sec * RATE) * WIDTH
    cues, asr_times, log = [], [], []
    confirmed_at = {}
    t_start = time.time()

    for i in range(0, len(pcm), step):
        session.add_audio(pcm[i:i + step])
        fed_sec = min(total_sec, (i + step) / float(RATE * WIDTH))
        if wallclock:
            # 讓牆鐘追上「這段音訊本來要播多久」，模擬即時串流的節奏
            behind = fed_sec - (time.time() - t_start)
            if behind > 0:
                time.sleep(behind)
        if not session.due():
            continue
        wav = session.buffer_wav()
        prompt = session.prompt_for_request()
        if not audio.backend_wants_prompt(backend):
            prompt = ""
        session.mark_sent()
        t0 = time.time()
        words, text, _ = audio.transcribe_words(wav, url, "ja", prompt=prompt)
        asr_times.append(time.time() - t0)
        out = session.consume_result(words, text)
        if out["flush"] is not None:
            cues.append(out["flush"])
        confirmed = "".join(c.text for c in cues) + out["committed"]
        # 延遲的量法：wallclock 用真實經過的秒數，離線用「已餵入的音訊秒數」
        stamp = (time.time() - t_start) if wallclock else fed_sec
        log.append((round(fed_sec, 1), round(stamp, 2), confirmed, out["tentative"]))
        for kw in ALL_FOUR:
            if kw not in confirmed_at and kw in confirmed:
                confirmed_at[kw] = stamp

    tail = session.flush()
    if tail is not None:
        cues.append(tail)
    final = "".join(c.text for c in cues)
    end_stamp = (time.time() - t_start) if wallclock else total_sec
    for kw in ALL_FOUR:
        if kw not in confirmed_at and kw in final:
            confirmed_at[kw] = end_stamp

    return {"backend": backend, "url": url, "interval": interval,
            "cues": cues, "final": final, "asr_times": asr_times,
            "log": log, "confirmed_at": confirmed_at,
            "total_sec": total_sec, "wall_sec": time.time() - t_start}


def print_report(rep, title):
    print(f"\n=== {title}：{rep['backend']} （{rep['url']}，間隔 {rep['interval']}s）===")
    for fed, stamp, conf, tent in rep["log"]:
        print(f"  餵到 {fed:5.1f}s / 牆鐘 {stamp:5.2f}s  確定「{conf}」 未定「{tent}」")
    print("  --- 字幕條 ---")
    for c in rep["cues"]:
        print(f"    [{c.start:5.2f} → {c.end:5.2f}] {c.text}")
    t = rep["asr_times"]
    print(f"  辨識 {len(t)} 次，平均 {sum(t) / max(1, len(t)):.3f}s、"
          f"最長 {max(t or [0]):.3f}s、最短 {min(t or [0]):.3f}s")
    print(f"  最終：{rep['final']}")


def print_compare(reps, label):
    """兩個引擎的逐句延遲對照表 —— 這次改動的成敗指標。"""
    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}")
    head = f"{'關鍵句':<10}" + "".join(f"{r['backend']:>14}" for r in reps)
    print(head)
    print("-" * len(head))
    for kw in ALL_FOUR:
        row = f"{kw:<10}"
        for r in reps:
            at = r["confirmed_at"].get(kw)
            row += f"{(f'{at:.2f}s' if at is not None else '未確定'):>14}"
        print(row)
    print("-" * len(head))
    row = f"{'每輪耗時':<10}"
    for r in reps:
        t = r["asr_times"]
        row += f"{(sum(t) / max(1, len(t))):>13.3f}s"
    print(row)
    row = f"{'辨識次數':<10}"
    for r in reps:
        row += f"{len(r['asr_times']):>14}"
    print(row)
    row = f"{'確定句數':<10}"
    for r in reps:
        row += f"{sum(1 for k in ALL_FOUR if k in r['confirmed_at']):>12}/4"
    print(row)
    print()
    for r in reps:
        print(f"  {r['backend']:>9} 最終：{r['final']}")


@unittest.skipUnless(os.path.exists(SEG_WAV), f"找不到測試音檔 {SEG_WAV}")
@unittest.skipUnless(server_up(WHISPER_URL), f"連不上 whisper-server {WHISPER_URL}")
@unittest.skipUnless(server_up(PARAKEET_URL), f"連不上 Parakeet 服務 {PARAKEET_URL}")
class TestBackendComparison(unittest.TestCase):
    """15 秒日文四句，兩個引擎各跑一次。"""

    @classmethod
    def setUpClass(cls):
        cls.pcm = read_pcm(SEG_WAV)
        cls.total_sec = len(cls.pcm) / float(RATE * WIDTH)

    def test_offline_replay_both_backends(self):
        """離線重播（1 秒一步、不 sleep）：測演算法本身，不含網路節奏。"""
        reps = [replay(self.pcm, b, step_sec=1.0) for b in ("whisper", "parakeet")]
        for r in reps:
            print_report(r, "離線重播（1 秒一步）")
        print_compare(reps, "離線重播對照：每句「音訊出現 → 被確定」（餵入秒數）")

        whisper, parakeet = reps
        # Parakeet 的核心賣點：確定得出來的句子比 whisper 多（或至少一樣多）
        got_p = sum(1 for k in ALL_FOUR if k in parakeet["confirmed_at"])
        got_w = sum(1 for k in ALL_FOUR if k in whisper["confirmed_at"])
        print(f"\n  確定句數：whisper {got_w}/4、parakeet {got_p}/4")
        self.assertGreaterEqual(
            got_p, got_w,
            f"Parakeet 確定的句子（{got_p}）竟少於 whisper（{got_w}），"
            f"這次改動的前提要重新確認")
        self.assertGreaterEqual(got_p, 2, "Parakeet 至少要確定出兩句")

        # 每輪辨識耗時：Parakeet 應該明顯更快
        avg_p = sum(parakeet["asr_times"]) / len(parakeet["asr_times"])
        avg_w = sum(whisper["asr_times"]) / len(whisper["asr_times"])
        print(f"  每輪耗時：whisper {avg_w:.3f}s、parakeet {avg_p:.3f}s")
        self.assertLess(avg_p, avg_w,
                        "Parakeet 每輪應該比 whisper 快（實測 0.25s vs 1.1s）")

        # 已確定的字不回頭改：cue 依序接起來就是最終文字
        for r in reps:
            self.assertEqual(r["final"], "".join(c.text for c in r["cues"]),
                             f"{r['backend']}：已確定的字被改掉了")

    def test_wallclock_both_backends(self):
        """牆鐘節奏（每 100ms 餵一次、真的 sleep）：量端到端延遲。

        這是最接近使用者體感的量法 —— 音訊按真實速度進來，辨識耗時與
        排隊都算在裡面。
        """
        reps = [replay(self.pcm, b, step_sec=0.1, wallclock=True)
                for b in ("whisper", "parakeet")]
        for r in reps:
            print_report(r, "牆鐘即時（每 100ms 餵一次）")
        print_compare(reps, "牆鐘即時對照：每句「音訊出現 → 被確定」（真實秒數）")

        for r in reps:
            print(f"  {r['backend']}：音訊 {r['total_sec']:.1f}s，"
                  f"實際跑 {r['wall_sec']:.1f}s")
            # 即時模式不能落後太多，否則字幕會愈拖愈遠
            self.assertLess(r["wall_sec"], r["total_sec"] * 2.5,
                            f"{r['backend']} 落後太多，跟不上即時播放")

        whisper, parakeet = reps
        got_p = sum(1 for k in ALL_FOUR if k in parakeet["confirmed_at"])
        got_w = sum(1 for k in ALL_FOUR if k in whisper["confirmed_at"])
        self.assertGreaterEqual(
            got_p, got_w,
            f"牆鐘模式下 Parakeet 確定 {got_p} 句、whisper {got_w} 句")

    def test_parakeet_result_grows_monotonically(self):
        """Parakeet 對同一段音訊由短到長，結果只往後長 —— LocalAgreement 的前提。

        whisper 在這裡會失敗（3s 說「ちょっともっともっと」、5s 換一句），
        所以只對 Parakeet 斷言；whisper 那邊只印出來對照。
        """
        cuts = [3, 5, 9, 15]
        rows = {}
        for backend in ("whisper", "parakeet"):
            cfg = backend_cfg(backend)
            url = audio.asr_url(cfg)
            texts = []
            for sec in cuts:
                nbytes = min(len(self.pcm), int(sec * RATE) * WIDTH)
                wav = audio.to_wav_bytes(self.pcm[:nbytes])
                t0 = time.time()
                _, text, _ = audio.transcribe_words(wav, url, "ja")
                texts.append((sec, text, time.time() - t0))
            rows[backend] = texts

        print("\n=== 同一段音訊由短到長（單調成長檢查）===")
        for backend, texts in rows.items():
            print(f"  --- {backend} ---")
            for sec, text, dt in texts:
                print(f"    {sec:2d}s ({dt:5.3f}s)  {text}")

        # Parakeet：每一階的正規化結果應該是下一階的前綴（容許尾巴標點差異）
        norm = [streaming.normalize(t).rstrip(".!?,")
                for _, t, _ in rows["parakeet"]]
        for i in range(len(norm) - 1):
            shorter, longer = norm[i], norm[i + 1]
            if not shorter:
                continue
            common = streaming.common_prefix_len(shorter, longer)
            ratio = common / len(shorter)
            print(f"    parakeet {cuts[i]}s → {cuts[i + 1]}s 前綴保留 "
                  f"{common}/{len(shorter)} 字（{ratio:.0%}）")
            self.assertGreaterEqual(
                ratio, 0.6,
                f"Parakeet 在 {cuts[i]}s→{cuts[i + 1]}s 改寫了前面的結果："
                f"{shorter!r} vs {longer!r}")


if __name__ == "__main__":
    unittest.main()

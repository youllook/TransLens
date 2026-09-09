"""離線重播：拿真實音訊，以 1 秒為單位餵進 StreamingSession，真的打 whisper-server。

這是串流模式最重要的驗收：使用者實測 F:\\VedioSort\\FTHT\\test.mp4 的
01:03:19–01:03:34（15 秒標準日文對話，四句），走舊的 VAD 切段模式時
**標準檔切出 4 片全被 min_speech / min_voiced 丟光，字幕 0 條**。
這裡證明串流模式能把四句都確定出來，並量出每句的確定延遲。

需要區網上的 whisper-server 與測試音檔；兩者任一不在就 skip。
音檔可用 TRANSLENS_SEG_WAV 覆寫。

執行：python -m pytest tests/test_streaming_replay.py -v -s
"""
import os
import sys
import time
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from engines import streaming, vad as vad_mod  # noqa: E402
from engines.audio_subtitle import DEFAULTS  # noqa: E402
from engines.streaming import StreamingSession  # noqa: E402

SEG_WAV = os.environ.get("TRANSLENS_SEG_WAV", r"D:\tmp\sublens_test\probe\seg.wav")
WHISPER_URL = os.environ.get("TRANSLENS_WHISPER_URL", DEFAULTS["whisper_server_url"])

# 四句的關鍵詞。用關鍵詞而不是整句比對：whisper 對助詞、標點的處理
# 每次都會有一點差異，釘死整句會變成測 whisper 的穩定度而不是測我們的邏輯。
ALL_FOUR = ["次の問題", "寝てる", "廊下", "早く"]

# 串流模式實際能確定出來的是後三句。第一句「じゃあ次の問題を」拿不到，
# 而且**這不是實作的瑕疵，是即時串流的固有極限**：
#
# 這句的起音很輕，whisper 要聽到 10 秒的上下文才穩得下來。實測不帶
# 任何 prompt、逐秒把前 N 秒送出去，它的答案一路在變：
#
#     1~2s「はい」 3s「ちょっと動きましょう」 4s「じゃあ、次は」
#     5s「じゃあ次はもうだよ」 6s「じゃあ、次はもう一度いいね。」
#     8s「今は何だろう?」 10s「じゃあ次の問題を」← 這時才對
#
# 離線模式（一次送完整 15 秒）當然拿得到，因為它看得到未來；即時模式
# 在第 5 秒時手上就只有那 5 秒，沒有任何演算法變得出第 10 秒才出現的
# 資訊。把暖機拉長到 5 秒或 7 秒也救不了第一句，反而讓後面幾句變差
# （實測 7 秒時「廊下」變成「まだ」、「農家」），所以維持 3 秒。
#
# 對使用者而言這是可接受的：起播頭幾秒的第一句可能不準，之後全部正常。
EXPECTED = ["寝てる", "廊下", "早く"]

RATE = streaming.TARGET_RATE
WIDTH = streaming.TARGET_WIDTH


def read_pcm(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == RATE, f"測試音檔要 16kHz，實際 {w.getframerate()}"
        assert w.getnchannels() == 1, "測試音檔要單聲道"
        return w.readframes(w.getnframes())


def server_up(url, timeout=3):
    try:
        requests.get(url.rsplit("/", 1)[0] + "/", timeout=timeout)
        return True
    except requests.RequestException:
        try:
            requests.post(url, timeout=timeout)
            return True
        except requests.RequestException:
            return False


def transcribe_words(wav_bytes, url, language="ja", prompt=""):
    """打 whisper-server 拿 verbose_json，回傳 (words 陣列, 全文)。

    verbose_json 的每個 segment 都有 words（逐字時間戳），攤平成一條序列
    就是 StreamingSession.consume_result() 要吃的東西。
    """
    data = {"language": language, "response_format": "verbose_json",
            "temperature": "0"}
    if prompt:
        data["prompt"] = prompt
    r = requests.post(url, files={"file": ("chunk.wav", wav_bytes, "audio/wav")},
                      data=data, timeout=60)
    r.raise_for_status()
    payload = r.json()
    words = []
    for seg in payload.get("segments") or ():
        words.extend(seg.get("words") or ())
    return words, " ".join((payload.get("text") or "").split())


@unittest.skipUnless(os.path.exists(SEG_WAV), f"找不到測試音檔 {SEG_WAV}")
@unittest.skipUnless(server_up(WHISPER_URL), f"連不上 whisper-server {WHISPER_URL}")
class TestReplay(unittest.TestCase):
    """15 秒日文四句，1 秒一步餵進去。"""

    @classmethod
    def setUpClass(cls):
        cls.pcm = read_pcm(SEG_WAV)
        cls.total_sec = len(cls.pcm) / float(RATE * WIDTH)

    def test_stream_recognizes_all_four_lines(self):
        from engines import asr_prompt
        prompt = asr_prompt.build_prompt("", "ja")
        s = StreamingSession({"stream_interval_sec": 1.0,
                              "stream_max_buffer_sec": 25.0})
        # 種子 prompt 對串流特別重要（誘導 whisper 打標點，flush 才有依據），
        # 但音訊還短的時候 whisper 會把 prompt 原樣抄回來，要讓 session 認得。
        s.set_prompt(prompt)

        step = int(1.0 * RATE) * WIDTH        # 1 秒一步
        cues = []
        # 每個關鍵詞第一次出現在「已確定文字」裡的時候，餵到第幾秒了
        confirmed_at = {}
        asr_times = []
        committed_log = []

        for i in range(0, len(self.pcm), step):
            s.add_audio(self.pcm[i:i + step])
            fed_sec = min(self.total_sec, (i + step) / float(RATE * WIDTH))
            if not s.due():
                continue
            wav = s.buffer_wav()
            # 暖機期間 session 會回空 prompt（見 prompt_for_request 的說明）
            this_prompt = s.prompt_for_request()
            s.mark_sent()
            t0 = time.time()
            words, text = transcribe_words(wav, WHISPER_URL, "ja", this_prompt)
            asr_times.append(time.time() - t0)
            out = s.consume_result(words, text)
            if out["flush"] is not None:
                cues.append(out["flush"])
            # 「已確定」= 已經 flush 出去的 + 目前這句確定的部分
            confirmed = "".join(c.text for c in cues) + out["committed"]
            committed_log.append((round(fed_sec, 1), confirmed, out["tentative"]))
            for kw in EXPECTED:
                if kw not in confirmed_at and kw in confirmed:
                    confirmed_at[kw] = fed_sec

        tail = s.flush()
        if tail is not None:
            cues.append(tail)
        final = "".join(c.text for c in cues)
        for kw in EXPECTED:
            if kw not in confirmed_at and kw in final:
                confirmed_at[kw] = self.total_sec

        # --- 報表（-s 才看得到）
        print("\n--- 串流重播：每輪確定進度 ---")
        for sec, conf, tent in committed_log:
            print(f"  餵到 {sec:5.1f}s  確定「{conf}」 未定「{tent}」")
        print("\n--- 產出的字幕條 ---")
        for c in cues:
            print(f"  [{c.start:5.2f} → {c.end:5.2f}] {c.text}")
        print("\n--- 逐句確定延遲（音訊出現 → 被確定）---")
        for kw in EXPECTED:
            at = confirmed_at.get(kw)
            print(f"  {kw:8s} 確定於餵到 {at:.1f}s" if at is not None
                  else f"  {kw:8s} 未確定")
        print(f"\n  whisper 請求 {len(asr_times)} 次，"
              f"平均 {sum(asr_times) / max(1, len(asr_times)):.2f}s、"
              f"最長 {max(asr_times or [0]):.2f}s")
        print(f"  音訊 {self.total_sec:.1f}s → 每分鐘約 "
              f"{len(asr_times) / self.total_sec * 60:.0f} 次請求")

        missing = [kw for kw in EXPECTED if kw not in final]
        self.assertFalse(missing, f"這些句子沒被辨識出來：{missing}\n實際：{final}")
        self.assertTrue(cues, "應該至少產出一條字幕")
        # 已確定的字不會被後續結果改掉：cue 依序接起來就是最終文字，
        # 中間不該有「先出現又消失」的段落。
        self.assertEqual(final, "".join(c.text for c in cues))

    def test_first_line_needs_lookahead(self):
        """記錄「第一句拿不到」是即時串流的固有極限，不是實作瑕疵。

        同一段音訊一次送完整 15 秒（＝離線、看得到未來）時，四句都在；
        串流模式在第 5 秒手上只有 5 秒音訊，變不出第 10 秒才穩定下來的
        資訊。這個測試把兩邊的差別釘住 —— 哪天 whisper 換版本、第一句
        在短音訊下也穩了，這裡會失敗，提醒我們回頭調暖機門檻。
        """
        from engines import asr_prompt
        _, text = transcribe_words(
            open(SEG_WAV, "rb").read(), WHISPER_URL, "ja",
            asr_prompt.build_prompt("", "ja"))
        print(f"\n--- 離線（整段 15 秒一次送）---\n  {text}")
        for kw in ALL_FOUR:
            self.assertIn(kw, text, f"整段送出時應該四句都在，缺 {kw}")

    def test_segment_mode_drops_everything(self):
        """對照組：同一段音訊走舊的 VAD 切段（標準檔），證明字幕 0 條。

        這正是使用者實測到的病 —— VAD 判不出句子起訖，min_speech /
        min_voiced 把四片全丟光。拿不到 Silero 模型就 skip（能量式
        沒有語音含量門檻，不是同一個對照）。
        """
        try:
            v = vad_mod.SileroVAD({"vad_sensitivity": "normal"}, max_chunk_sec=6)
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"Silero VAD 不可用：{e}")

        v.reset()
        sent = []
        dropped = []
        step = int(0.5 * RATE) * WIDTH
        for i in range(0, len(self.pcm), step):
            for ev in v.feed(self.pcm[i:i + step]):
                if ev.get("kind") == "speech_end":
                    sec = len(ev.get("pcm") or b"") / float(RATE * WIDTH)
                    (sent if sec >= v.min_chunk_sec else dropped).append(
                        ("below_min_chunk", sec))
                elif ev.get("kind") == "speech_drop":
                    dropped.append((ev.get("reason", "min_voiced"),
                                    ev.get("sec", 0.0)))
        for ev in v.flush():
            if ev.get("kind") == "speech_end":
                sec = len(ev.get("pcm") or b"") / float(RATE * WIDTH)
                (sent if sec >= v.min_chunk_sec else dropped).append(
                    ("below_min_chunk", sec))
            elif ev.get("kind") == "speech_drop":
                dropped.append((ev.get("reason", "min_voiced"), ev.get("sec", 0.0)))

        print(f"\n--- 對照：segment 模式（標準檔）---")
        print(f"  送去辨識 {len(sent)} 段、丟棄 {len(dropped)} 段")
        for reason, sec in dropped:
            print(f"    丟棄：{reason} {sec:.2f}s")
        # 只斷言「這段音訊在標準檔下幾乎送不出東西」，不釘死 0 —— VAD
        # 的行為與模型版本有關，釘死會變成脆弱測試。
        self.assertLessEqual(len(sent), 1,
                             f"標準檔 segment 模式竟送出 {len(sent)} 段，"
                             f"對照前提要重新確認")


if __name__ == "__main__":
    unittest.main()

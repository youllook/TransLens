#!/usr/bin/env python3
"""Parakeet（MLX）語音辨識 HTTP 服務 —— 介面刻意做成 whisper.cpp server 的形狀。

為什麼要有這支
--------------
TransLens 的即時字幕走 LocalAgreement-2：每隔一小段時間把手上累積的整段
音訊重送一次，連續兩輪一致的前綴才定案。這個演算法的成敗完全取決於
「辨識結果會不會隨音訊變長而單調成長」。

whisper 對短音訊會硬猜，而且一路改。同一段日文音訊由短到長餵給
whisper-server（large-v3-turbo）：

    2s 「はい」                    3s 「ちょっともっともっと」
    5s 「じゃあ次はもう一度」      9s 「じゃあ、次は何だろう?」
    11s 才對

前綴每輪都不一樣，連兩輪一致永遠湊不齊，實測延遲 13 秒。

同一段音訊給 mlx-community/parakeet-tdt_ctc-0.6b-ja：

    3s 「うんとじゃあ」（正確前綴）  5s 「うんとじゃあ次の問題は」
    9s 「次の問題を山田だ!」         15s 正確，耗時 0.25s

結果單調成長不回頭改 —— 正是 LocalAgreement 要的。

介面相容性
----------
POST /inference，multipart 欄位與 whisper.cpp server 一致：

    file             WAV（必要）
    language         ja / en / auto（預設 ja）
    response_format  json / verbose_json（預設 json）
    prompt           接受但忽略（Parakeet 是 CTC/TDT，沒有 prompt 條件化）
    temperature      接受但忽略（貪婪解碼，沒有取樣溫度）

回應 JSON 與 whisper-server 同形，所以 TransLens 端幾乎不用改解析邏輯：

    {"text": "...",
     "language": "ja",
     "segments": [{"id": 0, "start": 0.0, "end": 10.4, "text": "...",
                   "words": [{"word": "うん", "start": 0.0, "end": 0.32,
                              "probability": 0.98}, ...]}]}

engines/streaming.py 以頂層 text 為字元來源、words 只拿來對時間軸，所以
words 的切詞方式不影響前綴比對，只影響時間戳精度。

GET /health 回 {"ok": true, "model": ..., "ready": true, "loaded": [...]}。

設計取捨
--------
* 只用標準庫的 http.server（ThreadingHTTPServer），不引入 fastapi/uvicorn。
  這台只服務區網一個客戶端，相依愈少愈不會壞。
* 模型啟動時載入一次常駐（載入約 0.7 秒，但每次請求都載會讓 0.25 秒的
  辨識變成 1 秒級，串流就沒意義了）。
* **所有 MLX 的操作都在同一條專用執行緒上做**（見 _AsrWorker）。這不是
  效能考量而是正確性：MLX 的 Stream 是 thread-local，在 A 執行緒載入模型、
  到 B 執行緒呼叫 transcribe 會直接丟

      RuntimeError: There is no Stream(cpu, 1) in current thread.

  而 ThreadingHTTPServer 每個連線都開新執行緒，所以「載入時能跑、上線就爆」。
  順帶把辨識序列化了 —— 同時跑兩個 transcribe 也只是互相搶 GPU，
  而串流模式本來就是一次一個請求。
* 只預載日文模型；英文模型第一次收到 language=en 才載（懶載入）。
  載不到（例如還沒下載）就回 400 帶明確訊息與下載指令，不是 500 堆疊。
"""
import argparse
import cgi
import json
import logging
import os
import queue
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# parakeet-mlx 需要 ffmpeg 在 PATH 上（在 /opt/homebrew/bin）。
# 非互動 ssh 與 launchd 都不會讀 shell profile，所以自己補上。
if "/opt/homebrew/bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

MODELS = {
    "ja": "mlx-community/parakeet-tdt_ctc-0.6b-ja",
    "en": "mlx-community/parakeet-tdt-0.6b-v3",
}
DEFAULT_LANG = "ja"

_models = {}          # lang -> 模型物件（只有 worker 執行緒能碰）
_load_errors = {}     # lang -> 載入失敗的原因（回給客戶端，不要吞掉）


class _AsrWorker:
    """把所有 MLX 操作關進同一條執行緒。

    MLX 的 Stream 是 thread-local：模型在哪條執行緒載入，就只能在那條
    執行緒上跑。ThreadingHTTPServer 每個連線一條新執行緒，直接呼叫會丟
    "There is no Stream(cpu, 1) in current thread."。所以載入與辨識
    都用 submit() 丟進來，由這條執行緒代勞。
    """

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="parakeet-asr")
        self._thread.start()

    def _loop(self):
        while True:
            job = self._jobs.get()
            job()

    def submit(self, fn, timeout=300):
        """在 worker 執行緒上執行 fn 並回傳結果；fn 丟的例外原樣重丟。"""
        box = {}
        ev = threading.Event()

        def runner():
            try:
                box["ok"] = fn()
            except BaseException as e:  # noqa: BLE001
                box["err"] = e
            finally:
                ev.set()

        self._jobs.put(runner)
        if not ev.wait(timeout):
            raise TimeoutError(f"辨識超過 {timeout} 秒沒有回應")
        if "err" in box:
            raise box["err"]
        return box.get("ok")


_worker = None


def worker():
    global _worker
    if _worker is None:
        _worker = _AsrWorker()
    return _worker


def _load_model(lang):
    """實際載入模型 —— 只准在 worker 執行緒上呼叫。"""
    from parakeet_mlx import from_pretrained
    t0 = time.time()
    m = from_pretrained(MODELS[lang])
    logging.info("載入 %s 完成，耗時 %.2fs", MODELS[lang], time.time() - t0)
    return m


def get_model(lang):
    """取得（必要時載入）某個語言的模型。回傳 (model, error_message)。

    模型物件只是個 handle，回傳給呼叫端沒問題 —— 但它的方法一定要
    透過 worker().submit() 才能呼叫（見 _AsrWorker）。
    """
    lang = lang if lang in MODELS else DEFAULT_LANG
    if lang in _models:
        return _models[lang], ""
    if lang in _load_errors:
        return None, _load_errors[lang]
    try:
        m = worker().submit(lambda: _load_model(lang))
        _models[lang] = m
        return m, ""
    except Exception as e:  # noqa: BLE001
        msg = (f"{lang} 模型 {MODELS[lang]} 無法載入：{type(e).__name__}: {e}。"
               f"若是還沒下載，請在這台 Mac 上執行 "
               f"`~/parakeet-venv/bin/hf download {MODELS[lang]}`。")
        logging.error(msg)
        _load_errors[lang] = msg
        return None, msg


def normalize_lang(value):
    """把 whisper 慣用的語言字串正規化成 ja / en。auto 與空值一律當日文。

    Parakeet 的模型是單語的，沒有語言偵測；'auto' 只能挑一個預設，
    挑日文是因為這正是 Parakeet 存在的理由（日文短音訊比 whisper 穩）。
    """
    v = (value or "").strip().lower()
    if v in ("", "auto", "ja", "jp", "jpn", "japanese"):
        return DEFAULT_LANG
    if v in ("en", "eng", "english"):
        return "en"
    return DEFAULT_LANG


def result_to_payload(result, lang, verbose):
    """parakeet-mlx 的 AlignedResult → whisper-server 形狀的 dict。

    result.text 是全文；result.sentences 每項有 start/end/text/tokens，
    每個 token 有 text/start/end。whisper 的 segment.words 用 "word" 當鍵，
    這裡照抄那個鍵名，TransLens 的 flatten_words 才不用改。
    """
    text = " ".join((getattr(result, "text", "") or "").split())
    if not verbose:
        return {"text": text}

    segments = []
    for i, sent in enumerate(getattr(result, "sentences", None) or ()):
        words = []
        for tok in getattr(sent, "tokens", None) or ():
            piece = getattr(tok, "text", "")
            if not piece:
                continue
            words.append({
                "word": piece,
                "start": round(float(getattr(tok, "start", 0.0) or 0.0), 3),
                "end": round(float(getattr(tok, "end", 0.0) or 0.0), 3),
                # whisper 每個 word 帶 probability；Parakeet 的信心值在
                # sentence 層級，這裡用它填，讓欄位形狀一致。
                "probability": round(float(getattr(sent, "confidence", 1.0) or 1.0), 4),
            })
        segments.append({
            "id": i,
            "start": round(float(getattr(sent, "start", 0.0) or 0.0), 3),
            "end": round(float(getattr(sent, "end", 0.0) or 0.0), 3),
            "text": getattr(sent, "text", ""),
            "words": words,
        })

    # 一句都沒切出來但有文字：給一個涵蓋全長的 segment，
    # 免得客戶端拿到 text 卻沒有任何時間軸。
    if not segments and text:
        segments = [{"id": 0, "start": 0.0, "end": 0.0, "text": text, "words": []}]

    return {"text": text, "language": lang, "segments": segments}


def wav_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:  # noqa: BLE001
        return 0.0


class Handler(BaseHTTPRequestHandler):
    server_version = "ParakeetMLX/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        logging.info("%s - %s", self.client_address[0], fmt % args)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/health", "/"):
            self._send_json({
                "ok": True,
                "ready": bool(_models),
                "default_language": DEFAULT_LANG,
                "model": MODELS[DEFAULT_LANG],
                "models": MODELS,
                "loaded": sorted(_models.keys()),
                "errors": _load_errors,
            })
            return
        self._send_json({"error": f"找不到 {path}（可用：GET /health、POST /inference）"}, 404)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path not in ("/inference", "/v1/audio/transcriptions"):
            self._send_json({"error": f"找不到 {path}（可用：POST /inference）"}, 404)
            return
        try:
            self._handle_inference()
        except Exception as e:  # noqa: BLE001
            logging.exception("辨識失敗")
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_inference(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json({"error": "請用 multipart/form-data 上傳，欄位 file"}, 400)
            return
        # cgi.FieldStorage 在 Python 3.13 被移除，但這台是 3.12；
        # 換版本時這裡要改用 email 或 multipart 套件。
        form = cgi.FieldStorage(
            fp=self.rfile, headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype})

        if "file" not in form:
            self._send_json({"error": "缺少 file 欄位"}, 400)
            return
        item = form["file"]
        data = item.file.read() if getattr(item, "file", None) else (item.value or b"")
        if not data:
            self._send_json({"error": "file 欄位是空的"}, 400)
            return

        lang = normalize_lang(form.getvalue("language", DEFAULT_LANG))
        fmt = (form.getvalue("response_format", "json") or "json").strip().lower()
        verbose = fmt in ("verbose_json", "verbose")
        # prompt / temperature 收下但不用（Parakeet 沒有這兩個概念）

        model, err = get_model(lang)
        if model is None:
            self._send_json({"error": err}, 400)
            return

        # parakeet-mlx 吃檔案路徑（內部用 ffmpeg 解碼），先落地成暫存檔。
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="parakeet-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            t0 = time.time()
            # MLX 只能在載入模型的那條執行緒上跑（見 _AsrWorker）
            result = worker().submit(lambda: model.transcribe(tmp))
            elapsed = time.time() - t0
            payload = result_to_payload(result, lang, verbose)
            logging.info("辨識 %s：音訊 %.2fs、耗時 %.3fs、%d 字",
                         lang, wav_duration(tmp), elapsed, len(payload.get("text", "")))
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        self._send_json(payload)


def main():
    ap = argparse.ArgumentParser(description="Parakeet MLX 辨識服務")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8179)
    ap.add_argument("--preload", default=DEFAULT_LANG,
                    help="啟動時預載哪些語言（逗號分隔，空字串代表不預載）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")

    for lang in [x.strip() for x in (args.preload or "").split(",") if x.strip()]:
        get_model(lang)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    logging.info("Parakeet 服務啟動於 %s:%d（預設語言 %s）",
                 args.host, args.port, DEFAULT_LANG)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        logging.info("收到中斷，關閉")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

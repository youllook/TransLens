# macOS 上的常駐服務（whisper-server ／ Parakeet ／ oMLX）

## whisper-server（語音辨識）

TransLens 的「🎧字幕」模式把系統聲音送到這台伺服器辨識。安裝步驟：

1. `brew install whisper-cpp ffmpeg`，並把模型放到 `~/.summarize/models/ggml-large-v3-turbo.bin`。
2. 複製 `com.tony.whisper-server.plist` 到 `~/Library/LaunchAgents/`（非 `tony` 帳號請改掉檔內的 `/Users/tony/...` 路徑）。
3. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tony.whisper-server.plist`
4. 驗證：`launchctl list | grep whisper` 有 pid，且
   `curl -s http://127.0.0.1:8178/inference -F file=@/tmp/ja.wav -F language=auto -F response_format=json` 回得出文字。
5. 在 TransLens 的 `config.json` 把 `whisper_server_url` 指到 `http://<這台 Mac 的 IP>:8178/inference`。

移除：`launchctl bootout gui/$(id -u)/com.tony.whisper-server`。記錄檔在 `~/Library/Logs/whisper-server.log`。

`KeepAlive` 為 true，行程被砍會自動重啟（模型載入約 10 秒）。`language` 由每次請求決定，
所以伺服器不必為了換語言重開；`--convert` 讓伺服器也能吃非 WAV 的上傳（需要 ffmpeg）。


## parakeet-server（日文語音辨識，埠 8179）

`asr_backend=parakeet` 時 TransLens 打這台。日文短音訊不會亂猜，串流延遲比
whisper 低一個量級（每輪 0.25 秒 vs 1.8 秒）—— 差異與實測數字見主 README「辨識引擎」。

1. `python3.12 -m venv ~/parakeet-venv && ~/parakeet-venv/bin/pip install parakeet-mlx`
   （另需 `brew install ffmpeg`，parakeet-mlx 靠它解碼）。
2. 複製 `parakeet_server.py` 到 `~/parakeet-server/`、`com.tony.parakeet-server.plist` 到
   `~/Library/LaunchAgents/`（非 `tony` 帳號請改掉檔內的 `/Users/tony/...` 路徑）。
3. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tony.parakeet-server.plist`
4. 驗證：`launchctl list | grep parakeet` 有 pid，且
   `curl -s http://127.0.0.1:8179/health` 回 `"ready": true`、
   `curl -s http://127.0.0.1:8179/inference -F file=@/tmp/ja.wav -F language=ja -F response_format=json` 回得出文字。
5. 在 TransLens 的 ⚙ →「辨識引擎」選 Parakeet（或把 `config.json` 的 `asr_backend` 設成 `parakeet`、
   `parakeet_server_url` 指到 `http://<這台 Mac 的 IP>:8179/inference`）。

移除：`launchctl bootout gui/$(id -u)/com.tony.parakeet-server`。記錄檔在 `~/Library/Logs/parakeet-server.log`。

`KeepAlive` 為 true，行程被砍會自動重啟（日文模型預載約 2 秒）。介面刻意做成 whisper-server 的形狀
（`POST /inference`、multipart `file`/`language`/`response_format`，verbose_json 回 `segments[].words[]`），
所以 TransLens 端共用同一套解析邏輯。`prompt` 與 `temperature` 收下但忽略 —— Parakeet 是 CTC/TDT，
沒有 prompt 條件化。英文模型（`mlx-community/parakeet-tdt-0.6b-v3`）是懶載入，第一次收到
`language=en` 才載；沒下載就回 400 帶下載指令，不會丟 500 堆疊。

**注意**：所有 MLX 操作都在單一專用執行緒上做（`_AsrWorker`）。MLX 的 Stream 是 thread-local，
在 A 執行緒載入模型卻到 B 執行緒呼叫 `transcribe` 會直接丟
`RuntimeError: There is no Stream(cpu, 1) in current thread.` —— 而 `ThreadingHTTPServer`
每個連線都開新執行緒，所以會「載入時能跑、上線就爆」。改這支時別把那層繞掉。


## oMLX（本地翻譯 LLM）

TransLens 的 `translator=local` 會把文字送到這台的 oMLX（OpenAI 相容端點），**不出外網**。

1. `brew install jundot/omlx/omlx`（需 python@3.11；venv 的 interpreter 不見時整包會 bad interpreter）。
   把模型放到 `~/.omlx/models/<模型名>/`，並在 `~/.omlx/settings.json` 設 `server.host` 為 `0.0.0.0`、`auth.api_key`。
2. 複製 `com.mendy.omlx.plist` 到 `~/Library/LaunchAgents/`（非 `mendy` 帳號請改掉檔內的 `/Users/mendy/...` 路徑），
   然後 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mendy.omlx.plist`。
3. 驗證：`launchctl list | grep omlx` 有 pid，且
   `curl -s -H "Authorization: Bearer <api_key>" http://127.0.0.1:8000/v1/models` 列得出模型（沒帶 key 應回 401）。
4. 在 TransLens 把 `local_llm_url` 指到 `http://<這台 Mac 的 IP>:8000/v1`，api key 建議放環境變數 `OMLX_API_KEY`。

移除：`launchctl bootout gui/$(id -u)/com.mendy.omlx`。記錄檔在 `~/Library/Logs/omlx.log`。
`KeepAlive` 為 true，行程被砍會自動重啟（Qwen2.5-7B-4bit 預載約 3 秒，常駐 RSS 約 0.9GB）。

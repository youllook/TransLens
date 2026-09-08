# macOS 上的常駐服務（whisper-server ／ oMLX）

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

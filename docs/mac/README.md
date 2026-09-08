# macOS 上的 whisper-server 常駐服務

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

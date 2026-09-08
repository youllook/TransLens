# TransLens · 桌面透鏡翻譯框

> 日文遊戲的對話看不懂、沒中文化的軟體選單、掃描 PDF 截圖裡選不到的文字——
> **把透鏡框套上去，按一個快捷鍵，當場讀中文。**
> 一個永遠置頂、可拖曳、可縮放、框內可點穿的透明框，Python + tkinter，預設引擎**零 API 金鑰**。

[English](#english) ｜ [60 秒上手](#60-秒上手) ｜ [為什麼不用其他工具](#為什麼不用其他工具) ｜ [引擎](#引擎) ｜ [操作](#操作) ｜ [音訊字幕](#音訊字幕影片沒字幕時) ｜ [設定](#設定檔) ｜ [隱私](#隱私每個引擎會把什麼送出去)

---

## 它長什麼樣

```
┌◎ TransLens ─[翻譯 (CTRL+ALT+T)]─[OCR+Google ▾]─☐自動─────⚙ ✕┐
│                                                              │
│        （框內完全透明、可點穿，遊戲照常操作）                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
  OCR+Google（免金鑰） · OCR 語言: ja-JP · 來源: ja
  中文: 這裡就是你要找的地方。小心腳下。
  原文: ここが、お前の探していた場所だ。足元に気をつけろ。
```

![TransLens 示範：日文視覺小說對白翻成繁中，結果面板貼在透鏡正下方](docs/screenshot.png)

## 60 秒上手

```
run.bat
```

就這樣。第一次執行會自動 `pip install pillow requests winsdk`，然後以 `pythonw` 無主控台啟動。
之後直接雙擊 `run.bat` 或 `python translens.py` 都可以。

需求：Windows 10/11、Python 3.10+。預設引擎用 Windows 內建 OCR（離線）＋ Google 翻譯（免金鑰），
**不需要申請任何帳號或金鑰**。

### 選用步驟

| 想要 | 做法 |
|---|---|
| 桌面捷徑（帶圖示、無黑窗） | `powershell -ExecutionPolicy Bypass -File make_shortcut.ps1` |
| 辨識日文／英文（Windows OCR 語言包） | 雙擊 `install_ocr_lang.bat`，或 ⚙ → OCR 語言 → 安裝。其他語言：`install_ocr_lang.ps1 -Languages ko-KR,fr-FR` |
| 美術字、斜體、複雜排版辨識更準 | 設環境變數 `GEMINI_API_KEY`（[AI Studio](https://aistudio.google.com/apikey) 免費申請）→ 工具列切到 **Gemini API** |
| 用 Claude 視覺模型 | `pip install anthropic` ＋ `ANTHROPIC_API_KEY` → 切到 **Claude API** |

## 為什麼不用其他工具

| 你可能會想用 | 為什麼 TransLens 不一樣 |
|---|---|
| **瀏覽器翻譯外掛** | 它們只看得到網頁。遊戲、原生程式、圖片裡的字、掃描 PDF——全都看不到。TransLens 翻的是**螢幕像素**，畫面上有的就能翻。 |
| **手機相機翻譯** | 手要離開鍵盤、對準螢幕、還會反光。TransLens 一個快捷鍵，遊戲中也能按，手不用離開手把。 |
| **全螢幕覆蓋型翻譯工具** | 通常要裝服務、常駐背景、設定一堆。TransLens 是**一個小框**：沒有伺服器、沒有背景服務、沒有安裝程式，關掉透鏡就什麼都不剩。設定檔就是同目錄一個 `config.json`。 |

其他細節：

- **框內可點穿**：透鏡蓋在對話框上，滑鼠照樣點到底下的遊戲。
- **自動模式**：勾「自動」後每 3 秒比對一次框內畫面，有變才重翻，不會狂打 API。
- **多語言包自動挑**：裝了日／英／中多個 Windows OCR 語言包時，「自動」會每個都試一遍、依文字特徵選最合理的結果。
- **語言包不符會告訴你**：辨識出碎片時狀態列直接提示「可能沒有這個語言的 OCR 語言包」，而不是丟一串亂碼給你猜。
- **單一實例、DPI 感知**：不會開兩個搶快捷鍵；高 DPI 螢幕截圖不偏移。

## 引擎

工具列下拉選單即時切換。全部引擎都把框內畫面翻成繁體中文（台灣用語）。

| 引擎 | 原理 | 需要 | 速度 | 適合 |
|---|---|---|---|---|
| **OCR+Google**（預設） | Windows 內建 OCR 抓字（離線）→ Google 翻譯 | **免金鑰**。OCR 語言需在 Windows 安裝語言包 | 約 1 秒 | 一般清晰字體、日常使用 |
| **Gemini API** | Gemini 視覺模型一步完成辨識＋翻譯 | 環境變數 `GEMINI_API_KEY`（免費申請） | 2~5 秒 | 美術字、斜體、複雜排版、任何語言 |
| **Claude API** | Claude 視覺模型一步完成辨識＋翻譯 | `pip install anthropic` ＋ `ANTHROPIC_API_KEY` | 2~5 秒 | 同 Gemini API |
| **Gemini CLI** | 呼叫本機 `gemini -p` 無頭模式 | 已安裝並認證的 [Gemini CLI](https://github.com/google-gemini/gemini-cli) | 5~10 秒（每次啟動 Node） | 已有 CLI 認證、不想另外管金鑰的人 |

### Windows OCR 語言包

OCR+Google 引擎只能辨識「已安裝 OCR」的語言。繁中包讀英文尚可（程式會自動修正常見的 `l`→`I` 誤判），
日文、韓文則必須另裝。

**症狀**：日文畫面翻出「夕 食 時 Ｄ せ 、 芒 力…」這類碎片，狀態列出現「⚠ 辨識結果像亂碼」提示。

**一鍵安裝**：雙擊 `install_ocr_lang.bat`（或 ⚙ → OCR 語言 → 安裝日文＋英文 OCR 語言包）。
沒有管理員權限時自動跳 UAC，透過 Windows Update 裝好 ja-JP 與 en-US，重開 TransLens 即可。

```powershell
# 其他語言
powershell -ExecutionPolicy Bypass -File install_ocr_lang.ps1 -Languages ko-KR,fr-FR

# 或手動（系統管理員 PowerShell）
Add-WindowsCapability -Online -Name Language.OCR~~~ja-JP~0.0.1.0
```

圖形介面：設定 → 時間與語言 → 語言 → 新增語言 → 該語言「選項」→ 安裝「光學字元辨識」。

### 關於 Gemini CLI

Google 個人帳號的免費層已不再支援 Gemini CLI 登入；改用 API key 即可：設定 `GEMINI_API_KEY`，
並把 `~/.gemini/settings.json` 的 `security.auth.selectedType` 改成 `"gemini-api-key"`。
不過有了 `GEMINI_API_KEY`，**直接用「Gemini API」引擎更快**（不必每次啟動 Node）。

## 操作

| 動作 | 方式 |
|---|---|
| 移動透鏡 | 拖曳頂端工具列 |
| 縮放透鏡 | 拖右下角把手、右邊或下邊框 |
| 翻譯 | 工具列「翻譯」或全域快捷鍵 `Ctrl+Alt+T`（遊戲中也能按） |
| 自動模式 | 勾「自動」：每 3 秒檢查一次，框內畫面有變才重翻 |
| 音訊字幕 | 勾「🎧字幕」：聽系統聲音即時上字幕（見下節），與「自動」互斥 |
| 隱藏 / 顯示 | `Ctrl+Alt+H` |
| 切換引擎 | 工具列下拉選單 |
| 複製譯文 | 結果面板連點兩下，或右鍵選單 |
| 結果面板 | 預設貼在透鏡下方跟著走；拖開後就獨立，右鍵「貼回透鏡下方」 |
| 字級、OCR 語言、顯示原文 | 工具列 ⚙ |
| 關閉 | 工具列 ✕（會記住透鏡位置與大小） |

## 音訊字幕（影片沒字幕時）

OCR 只能翻**畫面上看得到的字**。影片本身沒有字幕、或只有聽得到的旁白時，勾工具列的
**「🎧字幕」**：TransLens 會聽 Windows 的系統聲音，即時辨識並翻成繁體中文，顯示在同一個結果面板。

**原理**

```
Windows 系統聲音（WASAPI loopback）
  → 能量式 VAD 切句（靜音 0.6 秒或滿 6 秒就切一段）
  → 16kHz 單聲道 WAV
  → POST 到區網 Mac 上的 whisper-server（whisper.cpp，large-v3-turbo）
  → 幻聽過濾
  → 翻譯（預設：區網本地 LLM；連不上才退到 Google）→ 面板顯示「中文 / 原文」
```

語音辨識**不在這台 Windows 上跑**，而是丟給區網另一台機器的 whisper-server，
所以這台電腦幾乎不吃 CPU，也不需要裝 CUDA 或大型模型。

**翻譯也可以留在區網**：預設 `translator` 為 `local`，辨識出的文字會送到區網自己的
LLM（本專案用 Mac mini 上的 [oMLX](https://github.com/jundot/omlx) 跑 Qwen2.5-7B-Instruct-4bit），
**整條管線的文字都不出門**。本地 LLM 連不上、逾時或回空時會自動退回 Google，
並在狀態列標示「本地失敗」。設定見下方「翻譯來源」。

**需要準備**

1. 一台跑 [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `whisper-server` 的機器（本專案用區網的 Mac mini M4）。
   安裝與 launchd 常駐設定見 [`docs/mac/README.md`](docs/mac/README.md)。
   同一台也可以跑 oMLX 提供本地翻譯（同上文件），不想自架就把 `translator` 設成 `google`。
2. 這台 Windows 安裝擷取套件：`pip install pyaudiowpatch`
   （**沒裝不影響原本的 OCR 翻譯**，只是勾「🎧字幕」時會提示要安裝）。
3. 這台 Windows 要有**啟用中的音訊輸出裝置**（喇叭或耳機）。loopback 是「錄下正在播放的聲音」，
   沒有輸出端點就沒有東西可錄。

**設定鍵**

| 鍵 | 預設 | 意義 |
|---|---|---|
| `whisper_server_url` | `http://192.168.0.87:8178/inference` | whisper-server 的 inference 端點 |
| `audio_lang` | `auto` | 辨識語言：`auto` / `ja` / `en`（工具列下拉可即時切換） |
| `audio_silence_sec` | `0.6` | 靜音多久算一句結束 |
| `audio_max_chunk_sec` | `6` | 一段最長幾秒就強制切開 |
| `subtitle_hold_sec` | `8` | 字幕在面板上停留幾秒後清空 |

`audio_lang` 指定成 `ja` 或 `en` 會比 `auto` 快一點也穩一點；語言會混的內容才用 `auto`。

**翻譯來源**（OCR 與音訊字幕共用，⚙ → 翻譯來源 可即時切換）

| 鍵 | 預設 | 意義 |
|---|---|---|
| `translator` | `local` | `local` = 區網本地 LLM（文字不出門）；`google` = 免金鑰 Google 端點 |
| `local_llm_url` | `http://192.168.0.49:8000/v1` | OpenAI 相容端點（結尾有沒有 `/v1` 都吃） |
| `local_llm_model` | `Qwen2.5-7B-Instruct-4bit` | 模型名稱 |
| `local_llm_api_key` | `""` | 伺服器 api key；**環境變數 `OMLX_API_KEY` 優先**，不想寫進設定檔就用它 |
| `local_llm_timeout_sec` | `20` | 單次請求逾時；逾時就退到 Google |

實測（Mac mini M4 / Qwen2.5-7B-4bit）一句字幕翻譯約 **0.9～2.3 秒**，與 Google 的 0.1～1.7 秒
在觀看體感上差不多，但語氣與台灣用語明顯較自然（例如 `Watch your step` → 本地「小心腳步」、
Google「注意你的腳步」）。

> Qwen 這類模型即使被要求只准繁體，仍會在少數詞固定吐簡體（實測「早点回家」的「点」必現），
> 所以譯文一律再過一次簡→繁轉換：有裝 `opencc` 就用它，沒裝則用內建常用字對照表兜底。
> 想要最完整的轉換可自行 `pip install opencc-python-reimplemented`。

**限制**

- 延遲約 **2～4 秒**：一句話要先講完（靜音 0.6 秒）才會送出，加上辨識約 1～1.5 秒、翻譯約 0.1～2 秒。
  這是「先聽完再翻」的必然代價，不是網路慢。
- **BGM 或音效大聲時準確度會掉**，whisper 可能吐出幻聽句；常見的垃圾句
  （「ご視聴ありがとうございました」「Thank you for watching」「[Music]」等）已內建過濾清單，
  可在 `engines/audio_subtitle.py` 的 `HALLUCINATION_PATTERNS` 自行增補。
- 與 OCR 的「自動」模式**互斥**（兩者都會搶結果面板），勾其中一個會自動取消另一個。
  手動按「翻譯」不受影響，隨時可以插一張畫面翻譯。
- 多人同時說話、口音重、專有名詞多的內容，準確度會明顯下降。

## 設定檔

設定存在同目錄的 `config.json`，首次啟動自動產生（範本見 `config.example.json`）。錯誤記錄在 `translens.log`。

| 鍵 | 意義 |
|---|---|
| `engine` | 預設引擎：`ocr_google` / `gemini_api` / `gemini_cli` / `claude_api` |
| `ocr_lang` | Windows OCR 語言標籤（如 `ja-JP`）；`auto` = 依系統語言，裝多個語言包時自動挑最佳 |
| `target_lang` | 翻譯目標語言（Google 翻譯語言碼，預設 `zh-TW`）；AI 引擎固定輸出繁中 |
| `hotkey_translate` | 翻譯快捷鍵，格式 `ctrl+alt+t`；支援 ctrl / alt / shift / win ＋ 字母、數字或 F1~F24 |
| `hotkey_toggle` | 隱藏／顯示快捷鍵 |
| `auto_interval_sec` | 自動模式的檢查間隔（秒） |
| `show_original` | 結果面板是否同時顯示辨識出的原文 |
| `font_size` | 譯文字級（9~40，也可用 ⚙ 調） |
| `font_family` | 介面與譯文字型 |
| `panel_alpha` | 結果面板不透明度（0~1） |
| `border_color` | 透鏡邊框顏色 |
| `gemini_model` | Gemini API 引擎用的模型名 |
| `gemini_cli_model` | Gemini CLI 引擎的 `-m` 參數；空字串 = CLI 預設 |
| `claude_model` | Claude API 引擎用的模型名 |
| `whisper_server_url` | 音訊字幕的 whisper-server 端點（見「音訊字幕」） |
| `audio_lang` | 音訊字幕辨識語言：`auto` / `ja` / `en` |
| `translator` | 翻譯來源：`local`（區網 LLM，預設）/ `google`（見「音訊字幕 → 翻譯來源」） |
| `local_llm_url` / `local_llm_model` / `local_llm_api_key` / `local_llm_timeout_sec` | 本地 LLM 連線設定；api key 建議改用環境變數 `OMLX_API_KEY` |
| `audio_silence_sec` | 靜音多久算一句結束（秒） |
| `audio_max_chunk_sec` | 單段最長秒數，超過就強制切開 |
| `subtitle_hold_sec` | 字幕停留幾秒後清空面板 |
| `geometry` | 透鏡的 `x` / `y` / `w` / `h`，關閉時自動更新 |

`GEMINI_API_KEY` 也可以放在 `config.json` 的 `gemini_api_key` 欄位，但建議用環境變數——`config.json` 已在 `.gitignore`，不過金鑰還是別寫進檔案比較安全。

## 隱私：每個引擎會把什麼送出去

| 引擎 | 離開這台電腦的資料 |
|---|---|
| **OCR+Google** | 截圖**不會**離開電腦（Windows OCR 全程離線）。只有辨識出的**文字**會送到 Google 翻譯端點；被限流時退到備援端點 MyMemory。 |
| **Gemini API** | 透鏡框內的**截圖**（PNG）送到 Google Generative Language API。 |
| **Claude API** | 透鏡框內的**截圖**（PNG）送到 Anthropic API。 |
| **Gemini CLI** | 截圖存到暫存目錄交給本機 `gemini` CLI，由 CLI 上傳到 Google；完成後暫存目錄即刪除。 |
| **🎧音訊字幕** | 系統聲音的片段（WAV）送到**你自己指定的** whisper-server（預設是區網內的機器，不經過任何雲端）；辨識出的**文字**在 `translator=local`（預設）時只送到**你自己區網的 LLM**，**完全不出外網**；只有在本地 LLM 連不上而自動退版、或手動選 `google` 時，文字才會送到 Google 翻譯。 |

除此之外沒有任何遙測、沒有帳號、沒有雲端設定。設定與紀錄都只在同目錄的 `config.json` 和 `translens.log`。

## 已知限制

- 只支援 Windows（依賴 Windows.Media.Ocr 與 Win32 全域快捷鍵）。
- 遊戲若為「獨佔全螢幕」，任何覆蓋視窗都不會顯示；請改用「無邊框視窗」或「視窗化」。
- Google 免金鑰翻譯是非官方端點，短時間大量請求**可能被限流（HTTP 429）**（程式會自動退到備援端點）。
  音訊字幕是「每句話打一次」，最容易踩到；把 `translator` 留在預設的 `local` 走區網 LLM 就完全沒這個問題。
- 結果面板若被拖到透鏡框內，截圖瞬間會先隱藏面板以免翻到自己的譯文。
- 音訊字幕需要另一台機器跑 whisper-server，且本機要有啟用中的音訊輸出裝置（詳見「音訊字幕」一節）。

## 除錯

```bash
python translens.py --test-image 圖片.png --engine ocr_google   # 不開 UI，直接對圖片跑引擎
python translens.py --smoke                                     # 開 UI 2.5 秒後自動關閉（自檢）
```

## 檔案

```
translens.py                 主程式（UI、快捷鍵、截圖、自動模式）
engines/__init__.py          引擎註冊表與 AI 共用提示詞
engines/ocr_windows.py       Windows 內建 OCR 封裝 + 多語言包擇優 + 行合併 + l/I 修正
engines/translate_google.py  免金鑰翻譯（Google → MyMemory 逐級備援）
engines/ocr_google.py        預設引擎（OCR+Google）
engines/gemini_api.py        Gemini API 直連
engines/gemini_cli.py        Gemini CLI 無頭模式
engines/claude_api.py        Claude API
engines/audio_subtitle.py    音訊字幕（WASAPI loopback → whisper-server → 翻譯）
engines/translator.py        翻譯統一入口（本地 LLM，失敗退 Google）
engines/translate_local.py   區網本地 LLM 翻譯（OpenAI 相容端點）
tests/test_audio_subtitle.py 音訊字幕管線測試
tests/test_translator.py     翻譯入口與本地 LLM 測試
docs/mac/                    Mac 上 whisper-server 與 oMLX 的 launchd 常駐設定
assets/make_icon.py          用 Pillow 程式化繪製圖示，重跑即可重生 translens.ico / .png
run.bat                      啟動器（缺套件自動安裝）
make_shortcut.ps1            建立桌面捷徑
install_ocr_lang.ps1 / .bat  安裝 Windows OCR 語言包（自動提權）
config.example.json          設定範本；實際設定 config.json 首次啟動自動產生
```

---

## English

**TransLens** is an always-on-top, draggable, resizable, click-through lens frame for Windows.
Playing a Japanese game, using an app that never got localized, staring at a PDF screenshot with no selectable text?
Drop the lens over the region, press one hotkey (`Ctrl+Alt+T`), and read it in Traditional Chinese right under the frame.

Python + tkinter. The default engine needs **no API key at all**: Windows' built-in OCR (offline) plus Google Translate.

### Start in 60 seconds

```
run.bat
```

That's it. First run installs `pillow`, `requests`, `winsdk`, then launches with `pythonw` (no console).
Requires Windows 10/11 and Python 3.10+.

Optional: `make_shortcut.ps1` for a desktop shortcut with icon; `install_ocr_lang.bat` to add Japanese + English Windows OCR packs (auto-elevates via UAC); set `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` to unlock the AI vision engines.

### Why this instead of…

- **Browser translate extensions** only see web pages. Games, native apps, images and scanned PDFs are invisible to them. TransLens translates *screen pixels*.
- **Phone camera translate** takes your hands off the keyboard. TransLens is one global hotkey that works while the game has focus.
- **Full-screen overlay translators** usually mean a service, a background process and a setup wizard. TransLens is one small frame: no server, no background service, nothing left running once you close it.

### Engines

| Engine | How | Needs | Speed | Best for |
|---|---|---|---|---|
| **OCR+Google** (default) | Windows OCR (offline) → Google Translate | **No key**. Windows OCR language pack for the source language | ~1 s | Clean text, everyday use |
| **Gemini API** | Gemini vision model, OCR + translation in one call | `GEMINI_API_KEY` (free tier) | 2–5 s | Stylized fonts, italics, busy layouts, any language |
| **Claude API** | Claude vision model | `pip install anthropic` + `ANTHROPIC_API_KEY` | 2–5 s | Same as Gemini API |
| **Gemini CLI** | Local `gemini -p` headless | Gemini CLI installed and authenticated | 5–10 s (Node startup) | People already authenticated with the CLI |

With several Windows OCR packs installed, `auto` mode runs each and picks the most plausible result by script profile (kana → Japanese, Hangul → Korean, Latin-heavy → a Latin pack, otherwise fewest garbage glyphs). Garbled output triggers a "missing OCR language pack?" hint in the status line instead of silent nonsense.

### Controls

| Action | How |
|---|---|
| Move / resize | Drag the top bar / the bottom-right grip, right or bottom edge |
| Translate | Toolbar button or global `Ctrl+Alt+T` |
| Auto mode | Tick "自動": checks the frame every 3 s, re-translates only when the pixels changed |
| Audio subtitles | Tick "🎧字幕": live subtitles from system audio (see below); mutually exclusive with auto mode |
| Hide / show | `Ctrl+Alt+H` |
| Copy translation | Double-click the result panel, or right-click menu |
| Result panel | Follows the lens by default; drag it away to detach, right-click to re-attach |
| Font size, OCR language, show original | Toolbar ⚙ |

Hotkeys and everything else live in `config.json` (created on first run; see `config.example.json` for every key).

### Audio subtitles (when the video has none)

OCR can only translate text you can *see*. When a video has no subtitles at all — just spoken narration —
tick **"🎧字幕"** in the toolbar: TransLens listens to Windows system audio, transcribes it, and shows a
Traditional Chinese translation in the same result panel.

```
Windows system audio (WASAPI loopback)
  → energy VAD, cut on 0.6 s of silence or at 6 s
  → 16 kHz mono WAV
  → POST to whisper-server on the LAN (whisper.cpp, large-v3-turbo)
  → hallucination filter
  → translation (local LAN LLM by default; falls back to Google) → panel shows translation + original
```

Speech recognition does **not** run on this Windows box — it is offloaded to another machine on your LAN,
so there is no CUDA setup and almost no local CPU cost.

**Translation can stay on your LAN too.** `translator` defaults to `local`, sending the transcribed text to
your own LLM (this project runs Qwen2.5-7B-Instruct-4bit under [oMLX](https://github.com/jundot/omlx) on the
same Mac mini), so **no text ever leaves your network**. If the local LLM is unreachable, times out, or
returns nothing, it falls back to Google automatically and the status bar says so.

**What you need**

1. A machine running [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `whisper-server`
   (this project uses a Mac mini M4 on the LAN). See [`docs/mac/README.md`](docs/mac/README.md)
   for the launchd service setup. The same box can host oMLX for local translation (same doc);
   set `translator` to `google` if you would rather not run one.
2. `pip install pyaudiowpatch` on this Windows machine. **Without it, OCR translation is entirely unaffected** —
   ticking "🎧字幕" just tells you to install it.
3. An **active audio output device** (speakers or headphones). Loopback records what is being *played*,
   so with no render endpoint there is nothing to capture.

**Config keys**

| Key | Default | Meaning |
|---|---|---|
| `whisper_server_url` | `http://192.168.0.87:8178/inference` | whisper-server inference endpoint |
| `audio_lang` | `auto` | Recognition language: `auto` / `ja` / `en` (also a toolbar dropdown) |
| `audio_silence_sec` | `0.6` | Silence that ends a segment |
| `audio_max_chunk_sec` | `6` | Hard cap on segment length |
| `subtitle_hold_sec` | `8` | How long a subtitle stays before the panel clears |

Pinning `audio_lang` to `ja` or `en` is slightly faster and more reliable than `auto`.

**Translation backend** (shared by OCR and audio subtitles; switch live under ⚙ → 翻譯來源)

| Key | Default | Meaning |
|---|---|---|
| `translator` | `local` | `local` = LLM on your LAN (text never leaves); `google` = keyless Google endpoints |
| `local_llm_url` | `http://192.168.0.49:8000/v1` | OpenAI-compatible endpoint (trailing `/v1` optional) |
| `local_llm_model` | `Qwen2.5-7B-Instruct-4bit` | Model name |
| `local_llm_api_key` | `""` | Server API key. **`OMLX_API_KEY` env var takes precedence** — use it to keep the key out of the config file |
| `local_llm_timeout_sec` | `20` | Per-request timeout; on timeout it falls back to Google |

Measured on a Mac mini M4 with Qwen2.5-7B-4bit: **0.9–2.3 s** per subtitle line versus 0.1–1.7 s for Google —
close enough in practice, and the tone and Taiwanese wording are noticeably better
(`Watch your step` → local "小心腳步" vs Google "注意你的腳步").

> Even when told to emit Traditional Chinese only, Qwen-class models still leak a few Simplified characters
> (in testing, the 点 in 早点回家 appeared every single time). Output therefore always goes through a
> Simplified→Traditional pass: `opencc` if installed, otherwise a small built-in table.
> For the most complete conversion, `pip install opencc-python-reimplemented`.

**Limitations**

- Latency is roughly **2–4 s**: a sentence must finish (0.6 s of silence) before it is sent, plus ~1–1.5 s
  of recognition and ~0.1–2 s of translation. That is inherent to transcribe-after-the-fact, not network lag.
- **Loud BGM or sound effects degrade accuracy** and can make Whisper hallucinate. Common junk lines
  ("ご視聴ありがとうございました", "Thank you for watching", "[Music]", …) are filtered; extend
  `HALLUCINATION_PATTERNS` in `engines/audio_subtitle.py` as needed.
- Mutually exclusive with OCR auto mode (both compete for the result panel); ticking one unticks the other.
  The manual "翻譯" button still works at any time.
- Overlapping speakers, heavy accents and dense proper nouns noticeably reduce accuracy.

### Privacy

- **OCR+Google**: the screenshot never leaves your machine; only the recognized *text* is sent to Google Translate (MyMemory as fallback).
- **Gemini API / Claude API**: the screenshot of the frame is sent to Google / Anthropic.
- **Gemini CLI**: the screenshot is written to a temp dir and handed to the local CLI, which uploads it to Google; the temp dir is deleted afterwards.
- **🎧 Audio subtitles**: audio chunks (WAV) go to the whisper-server *you* configure — by default a machine on your own LAN, never a cloud service. With `translator=local` (the default) the resulting *text* only reaches **your own LAN LLM and never the public internet**; it goes to Google Translate only if the local LLM is unreachable (automatic fallback) or you pick `google` yourself.

No telemetry, no account, no cloud config.

### Limitations

Windows only. Exclusive-fullscreen games hide every overlay — use borderless or windowed mode. The keyless Google endpoints are unofficial and **may rate-limit (HTTP 429) under heavy use** — a real risk for audio subtitles, which fire one request per spoken line; the local LLM backend avoids this entirely, and there is an automatic fallback chain either way. Audio subtitles additionally need a whisper-server on your LAN and an active audio output device on this machine.

---

Built by [Chin-Yuan Lu](https://github.com/youllook) — I build the layer that makes the hard part disappear. MIT License.

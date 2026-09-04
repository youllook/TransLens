# TransLens · 桌面透鏡翻譯框

> 日文遊戲的對話看不懂、沒中文化的軟體選單、掃描 PDF 截圖裡選不到的文字——
> **把透鏡框套上去，按一個快捷鍵，當場讀中文。**
> 一個永遠置頂、可拖曳、可縮放、框內可點穿的透明框，Python + tkinter，預設引擎**零 API 金鑰**。

[English](#english) ｜ [60 秒上手](#60-秒上手) ｜ [為什麼不用其他工具](#為什麼不用其他工具) ｜ [引擎](#引擎) ｜ [操作](#操作) ｜ [設定](#設定檔) ｜ [隱私](#隱私每個引擎會把什麼送出去)

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

示範截圖：`docs/screenshot.png`（待補；實際畫面是一句日文視覺小說對白被翻成繁中，結果面板貼在透鏡正下方）。

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
| 隱藏 / 顯示 | `Ctrl+Alt+H` |
| 切換引擎 | 工具列下拉選單 |
| 複製譯文 | 結果面板連點兩下，或右鍵選單 |
| 結果面板 | 預設貼在透鏡下方跟著走；拖開後就獨立，右鍵「貼回透鏡下方」 |
| 字級、OCR 語言、顯示原文 | 工具列 ⚙ |
| 關閉 | 工具列 ✕（會記住透鏡位置與大小） |

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
| `geometry` | 透鏡的 `x` / `y` / `w` / `h`，關閉時自動更新 |

`GEMINI_API_KEY` 也可以放在 `config.json` 的 `gemini_api_key` 欄位，但建議用環境變數——`config.json` 已在 `.gitignore`，不過金鑰還是別寫進檔案比較安全。

## 隱私：每個引擎會把什麼送出去

| 引擎 | 離開這台電腦的資料 |
|---|---|
| **OCR+Google** | 截圖**不會**離開電腦（Windows OCR 全程離線）。只有辨識出的**文字**會送到 Google 翻譯端點；被限流時退到備援端點 MyMemory。 |
| **Gemini API** | 透鏡框內的**截圖**（PNG）送到 Google Generative Language API。 |
| **Claude API** | 透鏡框內的**截圖**（PNG）送到 Anthropic API。 |
| **Gemini CLI** | 截圖存到暫存目錄交給本機 `gemini` CLI，由 CLI 上傳到 Google；完成後暫存目錄即刪除。 |

除此之外沒有任何遙測、沒有帳號、沒有雲端設定。設定與紀錄都只在同目錄的 `config.json` 和 `translens.log`。

## 已知限制

- 只支援 Windows（依賴 Windows.Media.Ocr 與 Win32 全域快捷鍵）。
- 遊戲若為「獨佔全螢幕」，任何覆蓋視窗都不會顯示；請改用「無邊框視窗」或「視窗化」。
- Google 免金鑰翻譯是非官方端點，短時間大量請求可能被限流（程式會自動退到備援端點）。
- 結果面板若被拖到透鏡框內，截圖瞬間會先隱藏面板以免翻到自己的譯文。

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
| Hide / show | `Ctrl+Alt+H` |
| Copy translation | Double-click the result panel, or right-click menu |
| Result panel | Follows the lens by default; drag it away to detach, right-click to re-attach |
| Font size, OCR language, show original | Toolbar ⚙ |

Hotkeys and everything else live in `config.json` (created on first run; see `config.example.json` for every key).

### Privacy

- **OCR+Google**: the screenshot never leaves your machine; only the recognized *text* is sent to Google Translate (MyMemory as fallback).
- **Gemini API / Claude API**: the screenshot of the frame is sent to Google / Anthropic.
- **Gemini CLI**: the screenshot is written to a temp dir and handed to the local CLI, which uploads it to Google; the temp dir is deleted afterwards.

No telemetry, no account, no cloud config.

### Limitations

Windows only. Exclusive-fullscreen games hide every overlay — use borderless or windowed mode. The keyless Google endpoints are unofficial and may rate-limit under heavy use (automatic fallback included).

---

Built by [Chin-Yuan Lu](https://github.com/youllook) — I build the layer that makes the hard part disappear. MIT License.

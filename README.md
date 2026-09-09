# TransLens · 桌面透鏡翻譯框

> 日文遊戲的對話看不懂、沒中文化的軟體選單、掃描 PDF 截圖裡選不到的文字——
> **把透鏡框套上去，按一個快捷鍵，當場讀中文。**
> 一個永遠置頂、可拖曳、可縮放、框內可點穿的透明框，Python + tkinter，預設引擎**零 API 金鑰**。

[English](#english) ｜ [60 秒上手](#60-秒上手) ｜ [為什麼不用其他工具](#為什麼不用其他工具) ｜ [引擎](#引擎) ｜ [操作](#操作) ｜ [音訊字幕](#音訊字幕影片沒字幕時) ｜ [詞彙表](#替詞表與自訂-prompt詞彙表) ｜ [設定](#設定檔) ｜ [隱私](#隱私每個引擎會把什麼送出去)

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

就這樣。第一次執行會自動安裝相依套件（OCR 三件＋音訊字幕四件，約 70MB）並下載 2MB 的語音偵測模型，然後以 `pythonw` 無主控台啟動。
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
  → Silero VAD 切句（神經網路，分辨「人聲」與「音樂／環境音」；靜音 0.6 秒或滿 6 秒就切一段）
  → 16kHz 單聲道 WAV
  → POST 到區網 Mac 上的 whisper-server（whisper.cpp，large-v3-turbo）
     （帶標點種子句＋詞彙表當 prompt；語言投票鎖定後固定送同一種語言）
  → 幻聽過濾（四條規則，含「同一句 90 秒內第二次出現就撤回」）
  → 翻譯（預設：區網本地 LLM；連不上才退到 Google）→ 面板顯示「中文 / 原文」
```

**Silero VAD：先分辨「這是人在講話嗎」**

擷取到的聲音不會整段送去辨識，而是先過一層 VAD 判斷哪裡有人聲。
預設用 [Silero VAD](https://github.com/snakers4/silero-vad) v5（onnx，約 2MB），
它是神經網路模型，**分得出「大聲的音樂」與「人在講話」**——
這是舊版能量式 VAD（只看音量大小）做不到的事。

實測 60 秒純 BGM 無人聲的影片（Vlog 片頭音樂）：

| | 送進 whisper 的段落 | 幻聽過濾擋掉 | **實際冒出的字幕** |
|---|---|---|---|
| 能量式 VAD（只看音量） | 10 段 | 7 條 | **3 條**（「おめでとうございます。」等） |
| **Silero VAD** | **0 段** | 0 條 | **0 條** |

同一份素材，能量式把整段音樂都當成人聲（99.6% 的窗判定有聲），
Silero 只有 8.7%，而且那些零星的窗湊不出足夠的「語音含量」，
所以根本不會送去辨識。**幻聽過濾是補救，VAD 才是根治。**
清楚的人聲則兩者都判得出來（日文測試音檔：Silero 67.9%、能量式 64.1%）。

模型**第一次勾「🎧字幕」時自動下載**到 `models/silero_vad.onnx`（會顯示來源與進度），
之後就離線可用。想先抓下來可執行 `install_vad.bat`（也會裝 `onnxruntime`）。
**沒裝 `onnxruntime` 或下載失敗會自動退回能量式 VAD**，並在狀態列標明
「VAD: 能量式（Silero 不可用：…）」——功能不會因此壞掉，只是比較容易吐幻聽。

**聆聽狀態帶與 VAD 靈敏度**

VAD 判掉的段落不會變成字幕。以前這件事是**靜悄悄**發生的：小聲的耳語過不了門檻就直接消失，
畫面上毫無反應，使用者分不出「程式沒聽到」還是「聽到了正在辨識」。
所以字幕模式開啟時，結果面板頂端會多一條**單行狀態帶**：

```
● 偵測到語音 1.2s…                              ♪ ▁▃▅▇  V ▁▃▅│▇
  ↑ 圓點                     ↑ 現在在做什麼        ↑ 音量  ↑ VAD 機率（│＝門檻）
```

- **圓點**：灰＝聆聽中、綠＝偵測到語音、藍＝送出／辨識中、橘＝這段被丟棄（2 秒後回灰）
- **中間文字**：「聆聽中」／「偵測到語音 1.2s…」／「送出辨識 #12（2.4s）」／
  「辨識中 0.8s → 原文前 20 字」／「已丟棄 #13：太短 0.3s」「已丟棄：語音含量不足」
- **右邊兩條**：音量與 VAD 機率，機率條上有一條**門檻刻線**——
  耳語「機率 0.35 過不了 0.5」就這樣一眼看得見
- 狀態列也會累計「已丟棄 N（幻聽 a、太短 b、語音含量不足 c）」

不想要就在 ⚙ 取消「顯示聆聽狀態帶」（設定鍵 `show_listen_bar`）。

**⚙ →「VAD 靈敏度」**三檔，字幕模式跑著也能即時切換（不會重載模型、不會丟掉手上那段音訊）：

| 檔位 | 機率門檻 | 語音含量下限 | 靜音容忍 | 什麼時候用 |
|---|---|---|---|---|
| 靈敏 `sensitive`（預設） | 0.30 | 400 ms | 900 ms | **耳語、小聲對話、短促斷續**被漏掉時 |
| 標準 `normal` | 0.50 | 1000 ms | 600 ms | **水流、風扇、冷氣、人聲吵雜**的環境 |
| 嚴格 `strict` | 0.70 | 1500 ms | 500 ms | **BGM 吵、幻聽多**時 |

⚠️ **靈敏檔不是「音量放大器」。** Silero 看的是頻譜特徵而不是音量，所以對音量其實**非常不敏感**：
把日文測試音檔衰減 −20dB、−30dB（RMS 4040 → 404 → 128），三檔的最大語音機率**全都還是 1.000**，
標準檔照樣切出 3 段、辨識出 3 條字幕——**「耳語會漏掉」在這個模型上大多不成立**。
要衰減到 −60dB（RMS 4.0，幾乎是靜音）差異才出現：

| 音量 | 靈敏 | 標準 | 嚴格 |
|---|---|---|---|
| −20 / −30dB | 3 條字幕 | 3 條字幕 | 3 條字幕 |
| −50dB（RMS 13） | 3 條 | 3 條 | 2 條（丟 1 段：含量不足） |
| −60dB（RMS 4） | **2 條** | 1 條 | **0 條**（3 段全丟） |

所以靈敏檔真正改變的是**「一段要有多少人聲才值得送去辨識」**（1000ms → 400ms），
救的是**短促、斷續、被 BGM 蓋住**的語音，不是單純「小聲」的語音。
真的整體太小聲，先調系統音量比調這裡有效。

**在水流／風聲旁邊講話會斷斷續續？切「標準」檔。**

水流、風聲、雨聲是寬頻噪音，頻譜跟氣音很像，靈敏檔的低門檻會把它們也當成人聲。
實測 60 秒純 BGM 的誤判率：靈敏 16%、標準 8.7%、嚴格 4.3%。噪音一閃一閃地跨過門檻，
一句話就被切成好幾段，字幕看起來就是斷斷續續。

靈敏檔的靜音容忍已經拉長到 900 ms（標準檔 600 ms）來把碎片接回同一句，但代價是
噪音持續時整段會被黏成一長段、字幕變慢。實測一段 10 秒的日文語音疊上水流聲：

| 檔位 | 切出的段落 | 結果 |
|---|---|---|
| 靈敏 | 1 段（10.1 秒） | 黏成一整段，要等講完才出字 |
| **標準** | **3 段（2.7 / 1.9 / 3.8 秒）** | **和沒有噪音時一樣乾淨** |
| 嚴格 | 2 段 | 少一句（被語音含量門檻擋掉） |

所以噪音環境的正解是「標準」，不是更靈敏。看狀態帶就知道該切哪一檔：
機率條在沒人講話時經常衝過刻線 → 噪音被當人聲，切標準或嚴格；
講話時機率條壓在刻線下、或一直看到「已丟棄：語音含量不足」→ 切靈敏。

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
2. 這台 Windows 的相依套件：`run.bat` 會自動裝齊（含 pyaudiowpatch、onnxruntime、numpy、opencc），手動的話 `pip install -r requirements.txt`
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
| `vad_backend` | `auto` | `auto` = 先試 Silero，拿不到退能量式；也可寫死 `silero` / `energy` |
| `vad_sensitivity` | `sensitive` | VAD 靈敏度三檔：`sensitive` / `normal` / `strict`（見下方「聆聽狀態帶與 VAD 靈敏度」） |
| `show_listen_bar` | `true` | 面板頂端是否顯示聆聽狀態帶 |
| `vad_threshold` | 跟著靈敏度 | 語音機率門檻。留 `null` = 由 `vad_sensitivity` 決定；填數字就是個別覆寫 |
| `vad_min_voiced_ms` | 跟著靈敏度 | 一段裡「真的是人聲」的秒數下限，低於就不送去辨識（擋音樂殘留） |
| `vad_min_silence_ms` | `600` | 靜下來多久算一句講完（沒設就沿用 `audio_silence_sec`） |
| `vad_speech_pad_ms` | `200` | 每段前後各留多少，句首句尾才不會被切掉 |
| `hallucination_repeat` | `true` | 是否啟用「同一句短時間內第二次出現就當幻聽」 |
| `hallucination_repeat_window_sec` | `90` | 幾秒內算「短時間」 |
| `hallucination_repeat_min_dur` | `2.5` | 兩次都要 ≥ 這麼長才判（短的是人真的在重複講） |

**語言鎖定**：`audio_lang` 指定成 `ja` 或 `en` 會比 `auto` 快一點也穩一點
（實測英文鎖定後辨識 1.05 秒／段，`auto` 是 2.7 秒／段）。
留 `auto` 時前 4 段會各自偵測語言、多數決**鎖定**（只在 ja/en 之間選），
之後所有段都送鎖定的那一種，狀態列顯示「語言：ja（已鎖定）」。
投票以**轉錄出來的文字**為準而不是 whisper 回報的語言碼——實測混語言的音訊
whisper 對英文段落仍常回 `ja`，照它的話投票會把整段鎖錯。
在工具列下拉手動選語言則**立即生效並停止投票**。

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
- **BGM 或音效大聲時準確度會掉**，whisper 可能吐出幻聽句。兩道防線：
  Silero VAD 讓非語音段落根本不會送去辨識（見上表的對照），漏網的再過四條過濾規則
  （見 `engines/hallucination.py`）：
  1. **已知垃圾句**——「ご視聴ありがとうございました」「Thank you for watching」「[Music]」等，
     清單在 `HALLUCINATION_PATTERNS`，可自行增補。
  2. **密度**——字數／秒數太低（日文 < 0.8 字/秒、英文 < 1.5 字元/秒且該段 ≥ 3 秒），
     那是模型把一兩個字撐滿整個窗口。
  3. **段內重複**——同一字或短語連發 ≥ 4 次（「はいはいはいはい」「あああああ」）。
  4. **90 秒內第二次出現**——正規化後同一句在 90 秒內又出現、而且兩次都 ≥ 2.5 秒，
     第二條不顯示，並把第一條**從面板上收回**（第一次出現時還判不出是幻聽）。
     門檻是「兩次都很長」而不是密度：人真的會一直說「はい」，但每次都很短（≈ 1 秒）；
     幻聽是模型填滿整個窗口，每次都拖很長。狀態列會累計
     「已丟棄 N（幻聽 a、太短 b、語音含量不足 c）」，狀態帶則即時顯示每一段被丟的原因。
- 與 OCR 的「自動」模式**互斥**（兩者都會搶結果面板），勾其中一個會自動取消另一個。
  手動按「翻譯」不受影響，隨時可以插一張畫面翻譯。
- 多人同時說話、口音重、專有名詞多的內容，準確度會明顯下降。

## 替詞表與自訂 prompt（詞彙表）

專有名詞（人名、店名、遊戲術語）最容易被聽錯也最容易被翻錯。TransLens 讀兩個
純文字檔，用記事本就能改，**OCR 翻譯與音訊字幕都會套用**：

- `glossary.txt` —— 替詞表
- `prompt.txt` —— 自訂翻譯風格

兩份都**已附帶範本且預設整份都是註解**＝不改變任何行為，要用就把 `#` 拿掉。
⚙ →「詞彙表／自訂 prompt」可以直接開檔編輯，也能**另外指定一份**
（例如某個遊戲專用的人名表），兩份會合併、額外那份優先。

**替詞表的兩種寫法**

```
鹿せんべい = 鹿仙貝        # 對照詞
平須 => Hirasu            # 取代規則（動譯文）
[src] 地下せんべい => 鹿せんべい   # 取代規則（動原文，翻譯前就套）
```

同一份表餵**三個地方**，一層比一層確定：

| 層 | 做什麼 | 保證 |
|---|---|---|
| whisper 的 `prompt` | 把「原文詞」餵給語音辨識，讓它比較可能聽對 | 只是提示，可能不聽 |
| LLM 的 system prompt | 把**這句真的出現過的**對照詞附成詞彙表 | 只是要求，可能不聽 |
| 輸出取代 | 翻完之後直接對字串做取代 | **一定生效** |

所以聽錯的字一定要有 `=>` 兜底——whisper 的 prompt 只是「提示」不是「指令」。
詞彙表只附「這句出現過的詞」而不是整張表：一份幾十個詞全附進 prompt，
每句都要多付那些 token，還會稀釋模型的注意力。

**標點種子句**

whisper 會模仿 prompt 的「書寫風格」。對話式的內容本來就沒什麼完整句子，
它幾乎不打句號——而沒有標點的原文送進 LLM，譯文也會黏成一長串。
所以每次都會送一段帶標點的種子句（日文「はい、そうですね。じゃあ、始めましょうか。」、
英文「Okay, so let's get started. Right?」）誘導它打標點。
種子句的內容不重要，重要的是**標點的密度與樣式**。
`asr_seed_prompt` 設成空字串可以關掉，設成別的字串可以自訂。

**自訂 prompt（`prompt.txt`）**

接在內建的翻譯規定後面，用來調整譯文風格（只在翻譯來源＝本地 LLM 時有用，
Google 端點沒有 prompt 可給）。`prompt_mode` 設 `replace` 可以整段取代內建的
「風格規定」，但「只輸出譯文本身」那段**格式規定一定保留**——被換掉的話模型會
開始加解釋與前綴，字幕就不能看了。

> 本地跑的 7B 模型對「範例」的服從度明顯高於「規則」。與其寫「語氣輕鬆一點」，
> 不如直接給它幾行「原文 → 想要的譯法」，它就會跟著模仿。

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
| `vad_backend` | VAD：`auto`（先試 Silero）/ `silero` / `energy`（見「音訊字幕 → Silero VAD」） |
| `vad_sensitivity` | VAD 靈敏度：`sensitive` / `normal` / `strict`（⚙ 選單可即時切換） |
| `show_listen_bar` | 是否顯示面板頂端的聆聽狀態帶 |
| `vad_threshold` / `vad_min_voiced_ms` / `vad_min_silence_ms` / `vad_speech_pad_ms` | VAD 細部門檻，一般不用動（前兩者留 `null` 就跟著靈敏度走） |
| `hallucination_repeat` / `hallucination_repeat_window_sec` / `hallucination_repeat_min_dur` | 幻聽「重複撤回」規則（見「音訊字幕 → 限制」） |
| `glossary` | 是否啟用替詞表／自訂 prompt（預設 `true`；設 `false` 連全域那份也不讀） |
| `glossary_file` / `prompt_file` | 額外指定的詞彙表／prompt 路徑（空字串 = 只用程式目錄那份） |
| `prompt_mode` | `append`（接在內建規定後面）/ `replace`（取代風格規定，格式規定仍保留） |
| `asr_seed_prompt` | 覆寫送給 whisper 的標點種子句；空字串 = 關掉 |
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

**Silero VAD 模型**（約 2MB）第一次啟用音訊字幕時會從 GitHub 下載一次到 `models/`，之後完全離線；語音本身**不會**送去任何地方做 VAD 判斷——判斷全在這台電腦上跑。不想讓它連 GitHub 就先跑 `install_vad.bat`，或把 `vad_backend` 設成 `energy`。

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
engines/audio_subtitle.py    音訊字幕（WASAPI loopback → VAD → whisper-server → 翻譯）
engines/vad.py               語音偵測：SileroVAD（onnx）與 EnergyVAD（退路），同介面
engines/hallucination.py     幻聽過濾四條規則（垃圾句／密度／段內重複／90 秒內重複撤回）
engines/glossary.py          替詞表與自訂 prompt 的解析與三層套用
engines/asr_prompt.py        whisper 的標點種子句組裝與語言投票鎖定
engines/translator.py        翻譯統一入口（本地 LLM，失敗退 Google；套詞彙表）
engines/translate_local.py   區網本地 LLM 翻譯（OpenAI 相容端點）
glossary.txt / prompt.txt    替詞表與自訂 prompt 範本（預設整份註解＝不改變行為）
models/silero_vad.onnx       Silero VAD 模型，第一次用到時自動下載（已 gitignore）
tests/test_audio_subtitle.py 音訊字幕管線測試
tests/test_vad.py            VAD 測試（含 Silero 與能量式對 BGM 的對照）
tests/test_listen_feedback.py 聆聽狀態帶事件流與 VAD 靈敏度三檔
tests/test_hallucination.py  幻聽過濾規則測試（含「不該誤殺」的反例）
tests/test_glossary.py       詞彙表解析、三層套用、種子 prompt、語言投票
tests/test_glossary_integration.py  詞彙表在翻譯入口與 OCR 路徑上真的生效
tests/test_subtitle_worker.py  worker 的撤回、語言投票、prompt 組裝
tests/test_translator.py     翻譯入口與本地 LLM 測試
docs/mac/                    Mac 上 whisper-server 與 oMLX 的 launchd 常駐設定
assets/make_icon.py          用 Pillow 程式化繪製圖示，重跑即可重生 translens.ico / .png
run.bat                      啟動器（缺套件自動安裝）
make_shortcut.ps1            建立桌面捷徑
install_ocr_lang.ps1 / .bat  安裝 Windows OCR 語言包（自動提權）
install_vad.bat              裝 onnxruntime 並預先下載 Silero VAD 模型（選用）
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
  → Silero VAD (neural; tells speech apart from music), cut on 0.6 s of silence or at 6 s
  → 16 kHz mono WAV
  → POST to whisper-server on the LAN (whisper.cpp, large-v3-turbo)
     (with a punctuation seed + glossary as the prompt; language locked by vote)
  → hallucination filter (four rules, incl. "same line twice in 90 s → retract")
  → translation (local LAN LLM by default; falls back to Google) → panel shows translation + original
```

**Silero VAD: decide "is a human talking?" first**

Captured audio is not sent to Whisper wholesale — a VAD pass first decides where speech is.
The default is [Silero VAD](https://github.com/snakers4/silero-vad) v5 (ONNX, ~2 MB), a neural model
that **tells loud music apart from a person speaking** — something the old energy VAD (which only
looks at volume) fundamentally cannot do.

Measured on 60 s of pure BGM with no speech (a vlog intro):

| | Segments sent to Whisper | Caught by the filter | **Subtitles actually shown** |
|---|---|---|---|
| Energy VAD (volume only) | 10 | 7 | **3** ("おめでとうございます。" etc.) |
| **Silero VAD** | **0** | 0 | **0** |

On the same clip the energy VAD called 99.6% of windows "speech"; Silero called 8.7%, and those
stray windows never add up to enough voiced audio to be worth transcribing.
**Filtering hallucinations is damage control; the VAD is the actual fix.**
Both handle clear speech fine (Japanese test clip: Silero 67.9%, energy 64.1%).

The model **downloads automatically the first time you tick "🎧字幕"** into `models/silero_vad.onnx`
(source and progress are shown), and works offline afterwards. Run `install_vad.bat` to fetch it ahead
of time (it also installs `onnxruntime`). **Without `onnxruntime`, or if the download fails, it falls back
to the energy VAD** and the status bar says so — nothing breaks, you just get more hallucinations.

**The listening bar, and VAD sensitivity**

Segments the VAD rejects never become subtitles, and that used to happen **silently**: a quiet whisper
failed the threshold and simply vanished, with nothing on screen — you could not tell "it did not hear me"
from "it heard me and is still working". So while subtitle mode is on, the result panel grows a
**single-line status bar** at the top:

```
● Speech detected 1.2s…                         ♪ ▁▃▅▇  V ▁▃▅│▇
  ↑ dot                    ↑ what it is doing     ↑ level  ↑ VAD prob (│ = threshold)
```

- **Dot**: grey = listening, green = speech detected, blue = sent / transcribing,
  orange = this segment was dropped (back to grey after 2 s)
- **Text**: listening / speech detected 1.2s… / sent for ASR #12 (2.4s) /
  transcribing 0.8s → first 20 chars / dropped #13: too short 0.3s / dropped: not enough voiced audio
- **Two meters**: input level and VAD probability, with a **threshold tick** on the probability bar —
  so "0.35 never clears 0.5" is visible at a glance
- The status line also tallies dropped segments by reason

Turn it off with ⚙ → "顯示聆聽狀態帶" (`show_listen_bar`).

**⚙ → "VAD 靈敏度"** offers three presets, switchable live (no model reload, no audio lost):

| Preset | Probability threshold | Min voiced | When to use |
|---|---|---|---|
| Sensitive | 0.30 | 400 ms | **Whispers and quiet dialogue are being missed** |
| Normal | 0.50 | 1000 ms | Default |
| Strict | 0.70 | 1500 ms | **Loud BGM, lots of hallucinations** |

⚠️ **Sensitive is not a volume boost.** Silero keys on spectral features, not loudness, so it is
remarkably **volume-insensitive**: attenuating the Japanese test clip by −20 dB and −30 dB
(RMS 4040 → 404 → 128) left peak speech probability at **1.000 on all three presets**, and Normal still
produced 3 segments and 3 subtitles. **"Whispers get missed" largely does not hold for this model.**
Differences only appear near −60 dB (RMS 4.0, effectively silence):

| Level | Sensitive | Normal | Strict |
|---|---|---|---|
| −20 / −30 dB | 3 subtitles | 3 subtitles | 3 subtitles |
| −50 dB (RMS 13) | 3 | 3 | 2 (1 dropped: not enough voiced) |
| −60 dB (RMS 4) | **2** | 1 | **0** (all 3 dropped) |

What Sensitive actually changes is **how much voiced audio a segment needs to be worth transcribing**
(1000 ms → 400 ms). It rescues speech that is **short, broken up, or buried under music** — not speech
that is merely quiet. If everything is too quiet, raising the system volume beats changing this setting.

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
2. Dependencies on this Windows machine: `run.bat` installs them all (pyaudiowpatch, onnxruntime, numpy, opencc); manually, `pip install -r requirements.txt`. **Without it, OCR translation is entirely unaffected** —
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
| `vad_backend` | `auto` | `auto` = try Silero, fall back to energy; or force `silero` / `energy` |
| `vad_sensitivity` | `normal` | VAD preset: `sensitive` / `normal` / `strict` (see "The listening bar, and VAD sensitivity") |
| `show_listen_bar` | `true` | Show the listening status bar at the top of the panel |
| `vad_threshold` | follows preset | Speech-probability threshold. `null` = decided by `vad_sensitivity`; a number overrides it |
| `vad_min_voiced_ms` | follows preset | Minimum genuinely-voiced audio in a segment before it is transcribed (this is what keeps music out) |
| `vad_min_silence_ms` | `600` | Silence that ends a segment (falls back to `audio_silence_sec`) |
| `vad_speech_pad_ms` | `200` | Padding kept on both ends so words are not clipped |
| `hallucination_repeat` | `true` | Enable "same line twice in a short window = hallucination" |
| `hallucination_repeat_window_sec` | `90` | How long "a short window" is |
| `hallucination_repeat_min_dur` | `2.5` | Both occurrences must be at least this long (short repeats are a real person) |

**Language locking.** Pinning `audio_lang` to `ja` or `en` is faster and more reliable than `auto`
(measured: 1.05 s per segment with English pinned, versus 2.7 s on `auto`). On `auto`, the first four
segments each detect their own language and a majority vote **locks** it (ja/en only); every later segment
is sent with the locked language and the status bar reads "語言：ja（已鎖定）". The vote uses the
**transcribed text**, not Whisper's reported language code — on mixed-language audio Whisper still reports
`ja` for English segments, and trusting it locks the wrong language. Picking a language from the toolbar
dropdown **takes effect immediately and stops the vote**.

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
- **Loud BGM or sound effects degrade accuracy** and can make Whisper hallucinate. Two lines of defence:
  Silero VAD keeps non-speech from being transcribed at all (see the table above), and whatever slips
  through meets four filter rules (`engines/hallucination.py`):
  1. **Known junk lines** — "ご視聴ありがとうございました", "Thank you for watching", "[Music]", …
     the list lives in `HALLUCINATION_PATTERNS` and is yours to extend.
  2. **Density** — too few characters per second (Japanese < 0.8 ch/s, English < 1.5 ch/s on segments
     ≥ 3 s): the model stretching one or two words across a whole window.
  3. **Repeats within a line** — the same character or phrase four or more times in a row
     ("はいはいはいはい", "あああああ").
  4. **Same line twice within 90 s** — if a normalized line reappears within the window and *both*
     occurrences run ≥ 2.5 s, the second is suppressed and the first is **retracted from the panel**
     (there was no way to tell it was a hallucination the first time). The test is duration, not density:
     people really do say "はい" over and over, but only ever briefly (~1 s), whereas a hallucinating model
     fills the entire window every time. The status bar keeps a running tally of dropped segments
     by reason, and the listening bar names the reason for each one as it happens.
- Mutually exclusive with OCR auto mode (both compete for the result panel); ticking one unticks the other.
  The manual "翻譯" button still works at any time.
- Overlapping speakers, heavy accents and dense proper nouns noticeably reduce accuracy.

### Glossary and custom prompt

Proper nouns — names, shops, game terms — are both the easiest thing to mishear and the easiest thing to
mistranslate. TransLens reads two plain-text files you can edit in Notepad, and **both the OCR path and
audio subtitles apply them**:

- `glossary.txt` — substitution table
- `prompt.txt` — translation style

Both ship as templates that are **entirely comments by default**, so they change nothing until you
uncomment something. ⚙ → "詞彙表／自訂 prompt" opens them for editing and can also point at **an extra
file** (say, a per-game name list); the two are merged with the extra one winning.

**Two forms**

```
鹿せんべい = 鹿仙貝        # term pair
平須 => Hirasu            # replacement (applied to the translation)
[src] 地下せんべい => 鹿せんべい   # replacement (applied to the source, before translating)
```

One table feeds **three places**, each more certain than the last:

| Layer | What it does | Guarantee |
|---|---|---|
| Whisper `prompt` | Feeds source-language terms to recognition so it is likelier to hear them right | A hint; may be ignored |
| LLM system prompt | Attaches the term pairs **that actually appear in this line** | A request; may be ignored |
| Output replacement | Plain string substitution after translating | **Always applies** |

So anything Whisper mishears needs a `=>` rule as a backstop — the Whisper prompt is a *hint*, not an
instruction. Only terms present in the current line are attached, never the whole table: a few dozen terms
in every prompt costs those tokens on every line and dilutes the model's attention.

**Punctuation seed.** Whisper imitates the writing style of its prompt. Conversational audio has few
complete sentences, so it emits almost no full stops — and unpunctuated source text makes the LLM run
everything together. Every request therefore carries a short punctuated seed
("はい、そうですね。じゃあ、始めましょうか。" / "Okay, so let's get started. Right?"). The seed's *content*
is irrelevant; its **punctuation density and style** is the point. Set `asr_seed_prompt` to an empty string
to disable it, or to your own text to override it.

**Custom prompt (`prompt.txt`)** is appended to the built-in translation rules to adjust style (it only
applies with the local LLM backend — the keyless Google endpoints take no prompt). `prompt_mode: replace`
swaps out the built-in *style* rules, but the *format* rule ("output only the translation") is always kept —
replace it and the model starts adding explanations and prefixes, which ruins subtitles.

> Locally-hosted 7B models follow *examples* far more reliably than *rules*. Rather than "keep the tone
> casual", give it a few lines of "source → the rendering I want" and it will imitate them.

### Privacy

- **OCR+Google**: the screenshot never leaves your machine; only the recognized *text* is sent to Google Translate (MyMemory as fallback).
- **Gemini API / Claude API**: the screenshot of the frame is sent to Google / Anthropic.
- **Gemini CLI**: the screenshot is written to a temp dir and handed to the local CLI, which uploads it to Google; the temp dir is deleted afterwards.
- **🎧 Audio subtitles**: audio chunks (WAV) go to the whisper-server *you* configure — by default a machine on your own LAN, never a cloud service. With `translator=local` (the default) the resulting *text* only reaches **your own LAN LLM and never the public internet**; it goes to Google Translate only if the local LLM is unreachable (automatic fallback) or you pick `google` yourself.

The **Silero VAD model** (~2 MB) is downloaded once from GitHub into `models/` the first time you enable audio subtitles, and is fully offline afterwards. Your audio is **never** sent anywhere for VAD — that decision runs entirely on this machine. To avoid the GitHub fetch, run `install_vad.bat` up front or set `vad_backend` to `energy`.

No telemetry, no account, no cloud config.

### Limitations

Windows only. Exclusive-fullscreen games hide every overlay — use borderless or windowed mode. The keyless Google endpoints are unofficial and **may rate-limit (HTTP 429) under heavy use** — a real risk for audio subtitles, which fire one request per spoken line; the local LLM backend avoids this entirely, and there is an automatic fallback chain either way. Audio subtitles additionally need a whisper-server on your LAN and an active audio output device on this machine.

---

Built by [Chin-Yuan Lu](https://github.com/youllook) — I build the layer that makes the hard part disappear. MIT License.

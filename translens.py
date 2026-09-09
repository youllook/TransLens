"""TransLens — 桌面透鏡翻譯框

一個永遠置頂、可拖曳、可縮放的「透鏡框」：把框套在螢幕任何位置（遊戲對話、說明、選單），
按「翻譯」或 Ctrl+Alt+T，框內文字就會被辨識並翻成繁體中文，結果顯示在框的下方：
    中文: ……

引擎（工具列可切換）：
  ocr_google  Windows 內建 OCR + Google 翻譯（免金鑰、離線 OCR）— 預設
  gemini_api  Gemini 視覺模型一步 OCR+翻譯（需 GEMINI_API_KEY）
  gemini_cli  本機 gemini CLI 無頭模式
  claude_api  Claude 視覺模型（需 anthropic 套件 + ANTHROPIC_API_KEY）

另有「🎧字幕」音訊字幕模式：擷取 Windows 系統聲音送到區網 whisper-server 辨識後翻成繁中，
用於影片本身沒有字幕、OCR 無從辨識的情況（見 README「音訊字幕」）。
"""
import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageGrab

from engines import glossary as glossary_mod

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "translens.log")

DEFAULT_CONFIG = {
    "engine": "ocr_google",
    "ocr_lang": "auto",
    "target_lang": "zh-TW",
    "hotkey_translate": "ctrl+alt+t",
    "hotkey_toggle": "ctrl+alt+h",
    "auto_interval_sec": 3,
    "show_original": True,
    "font_size": 15,
    "font_family": "Microsoft JhengHei UI",
    "panel_alpha": 0.92,
    "border_color": "#00b8a9",
    "gemini_model": "gemini-2.5-flash",
    "gemini_cli_model": "",
    "claude_model": "claude-opus-5",
    "whisper_server_url": "http://192.168.0.87:8178/inference",
    "audio_lang": "auto",
    "audio_silence_sec": 0.6,
    "audio_max_chunk_sec": 6,
    "subtitle_hold_sec": 8,
    # VAD：auto = 先試 Silero（會分辨人聲與音樂），拿不到就退能量式並在狀態列標明
    "vad_backend": "auto",
    # VAD 靈敏度三檔：sensitive / normal / strict（見 engines/vad.py SENSITIVITY）。
    # 耳語漏掉就調靈敏，BGM 吵、幻聽多就調嚴格。
    "vad_sensitivity": "sensitive",
    # 這三個留 null＝跟著靈敏度走；填了數字就是個別覆寫，靈敏度不會蓋掉。
    "vad_threshold": None,
    "vad_min_speech_ms": None,
    "vad_min_voiced_ms": None,
    "vad_min_silence_ms": 600,
    "vad_speech_pad_ms": 200,
    # 面板頂端的聆聽狀態帶（圓點＋說明＋音量／VAD 機率量表）
    "show_listen_bar": True,
    # 幻聽過濾（見 engines/hallucination.py）。設 false 可整條關掉。
    "hallucination_repeat": True,
    "hallucination_repeat_window_sec": 90,
    "hallucination_repeat_min_dur": 2.5,
    # 詞彙表與自訂 prompt：留空就只讀程式目錄下的 glossary.txt / prompt.txt
    "glossary": True,
    "glossary_file": "",
    "prompt_file": "",
    "prompt_mode": "append",
    # 翻譯來源：local = 區網本地 LLM（文字不出門）；google = 免金鑰 Google 端點
    "translator": "local",
    "local_llm_url": "http://192.168.0.49:8000/v1",
    "local_llm_model": "Qwen2.5-7B-Instruct-4bit",
    "local_llm_api_key": "",
    "local_llm_timeout_sec": 20,
    "geometry": {"x": 200, "y": 200, "w": 640, "h": 220},
}

# 翻譯來源下拉：顯示名 -> 設定值
TRANSLATORS = [("本地 LLM（區網，不出門）", "local"), ("Google（免金鑰）", "google")]

# 音訊字幕語言下拉：顯示名 -> whisper language code
AUDIO_LANGS = [("自動", "auto"), ("日", "ja"), ("英", "en")]

TRANSPARENT = "#ff00fe"   # 這個顏色的像素會變透明且可點穿
BORDER = 4                # 邊框粗細
TOP_H = 32                # 工具列高度
GRIP = 16                 # 右下角縮放把手
MIN_W, MIN_H = 220, TOP_H + 40

WM_HOTKEY = 0x0312
MOD_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000


# ----------------------------------------------------------------------------- 工具
def enable_dpi_awareness():
    """讓 Tk 座標與螢幕實體像素一致，截圖才不會偏移或模糊。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


_instance_mutex = None


def acquire_single_instance():
    """同時只允許一個 TransLens（第二個會搶不到全域快捷鍵）。"""
    global _instance_mutex
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\TransLens.SingleInstance")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:  # noqa: BLE001
            logging.warning("config.json 讀取失敗，使用預設值: %s", e)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        logging.warning("config.json 寫入失敗: %s", e)


def parse_hotkey(spec):
    """'ctrl+alt+t' -> (modifiers, vk)。支援字母、數字、F1~F24。"""
    mods, vk = 0, None
    for part in spec.lower().split("+"):
        part = part.strip()
        if part in MOD_FLAGS:
            mods |= MOD_FLAGS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x6F + int(part[1:])
        else:
            raise ValueError(f"無法解析快捷鍵: {spec}")
    if vk is None:
        raise ValueError(f"快捷鍵缺少主鍵: {spec}")
    return mods, vk


class HotkeyListener(threading.Thread):
    """在獨立執行緒註冊全域快捷鍵，觸發時把 id 丟進 queue。"""

    def __init__(self, bindings, out_q):
        super().__init__(daemon=True, name="hotkeys")
        self.bindings = bindings
        self.out_q = out_q

    def run(self):
        user32 = ctypes.windll.user32
        for hk_id, (mods, vk) in self.bindings.items():
            if not user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk):
                logging.warning("快捷鍵註冊失敗 id=%s（可能被其他程式佔用）", hk_id)
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                self.out_q.put(("hotkey", int(msg.wParam)))


def image_signature(img):
    """畫面內容指紋，用來判斷自動模式要不要重翻。"""
    small = img.convert("L").resize((48, 24), Image.BILINEAR)
    return hashlib.md5(small.tobytes()).hexdigest()


# ----------------------------------------------------------------------------- 聆聽狀態帶
# 狀態帶的圓點顏色。使用者原本的困擾是「小聲的耳語被判成非語音就直接消失，
# 畫面上毫無反應，分不出沒聽到還是在辨識中」，所以這四個狀態要一眼分得出來。
DOT_COLORS = {
    "idle": "#5c6470",       # 灰：聆聽中，沒偵測到語音
    "speech": "#3fb950",     # 綠：偵測到語音，正在收
    "working": "#4b9fff",    # 藍：送出去了／辨識中
    "dropped": "#e3901f",    # 橘：這段被丟掉了（停留 DROP_HOLD_MS 後回灰）
}
DROP_HOLD_MS = 2000          # 橘點停留多久才回到灰

BAR_H = 16                   # 狀態帶高度（單行）
METER_W = 54                 # 右邊兩條量表各自的寬度
METER_H = 5


class ListenBar(tk.Frame):
    """結果面板頂端那條單行的「聆聽狀態帶」。

    左：狀態圓點。中：一句話說明現在在做什麼。右：音量條與 VAD 機率條，
    機率條上有一條門檻刻線 —— 耳語「機率 0.35 過不了 0.5」要一眼看得見，
    這是整條狀態帶存在的理由。

    所有更新都是原地改（Canvas coords / itemconfig、Label configure），
    不 pack/grid 任何東西，面板高度才不會因為狀態變化而跳動。
    """

    def __init__(self, master, fam, bg="#14171c"):
        super().__init__(master, bg=bg, height=BAR_H)
        self.pack_propagate(False)
        self._bg = bg
        self._drop_job = None

        # 圓點：用 Canvas 畫，改色只要 itemconfig，不會重排版面
        self.dot = tk.Canvas(self, width=BAR_H, height=BAR_H, bg=bg,
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left")
        self._dot_id = self.dot.create_oval(4, 5, 12, 13, fill=DOT_COLORS["idle"],
                                            outline="")

        # 量表放右邊：先 pack 才不會被中間的長文字擠掉
        self.meters = tk.Canvas(self, width=METER_W * 2 + 26, height=BAR_H,
                                bg=bg, highlightthickness=0, bd=0)
        self.meters.pack(side="right")
        y = BAR_H // 2 - METER_H // 2
        self.meters.create_text(0, BAR_H // 2, text="♪", anchor="w",
                                fill="#6b7480", font=(fam, 7))
        x0 = 10
        self.meters.create_rectangle(x0, y, x0 + METER_W, y + METER_H,
                                     fill="#242932", outline="")
        self._rms_id = self.meters.create_rectangle(x0, y, x0, y + METER_H,
                                                    fill="#5c6470", outline="")
        self._rms_x0 = x0
        x1 = x0 + METER_W + 16
        self.meters.create_text(x1 - 6, BAR_H // 2, text="V", anchor="e",
                                fill="#6b7480", font=(fam, 7))
        self.meters.create_rectangle(x1, y, x1 + METER_W, y + METER_H,
                                     fill="#242932", outline="")
        self._prob_id = self.meters.create_rectangle(x1, y, x1, y + METER_H,
                                                     fill="#5c6470", outline="")
        self._prob_x0 = x1
        # 門檻刻線：畫在機率條上，跟著靈敏度移動
        self._tick_id = self.meters.create_rectangle(
            x1 + int(METER_W * 0.5), y - 2, x1 + int(METER_W * 0.5) + 1,
            y + METER_H + 2, fill="#e6edf3", outline="")

        self.lbl = tk.Label(self, text="聆聽中", font=(fam, 8), fg="#8b95a1",
                            bg=bg, anchor="w")
        self.lbl.pack(side="left", fill="x", expand=True, padx=(2, 6))

    # --- 狀態
    def set_state(self, state, text):
        """改圓點顏色與說明文字。橘（已丟棄）會自己在 2 秒後回灰。"""
        if self._drop_job is not None:
            try:
                self.after_cancel(self._drop_job)
            except Exception:  # noqa: BLE001
                pass
            self._drop_job = None
        self.dot.itemconfig(self._dot_id, fill=DOT_COLORS.get(state, DOT_COLORS["idle"]))
        self.lbl.configure(text=text)
        if state == "dropped":
            self._drop_job = self.after(DROP_HOLD_MS, self._back_to_idle)

    def _back_to_idle(self):
        self._drop_job = None
        self.dot.itemconfig(self._dot_id, fill=DOT_COLORS["idle"])
        self.lbl.configure(text="聆聽中")

    def set_level(self, rms, prob, threshold, speaking=False):
        """更新兩條量表與門檻刻線。只改 coords，不重建任何 widget。"""
        y = BAR_H // 2 - METER_H // 2
        r = max(0.0, min(1.0, float(rms or 0.0)))
        p = max(0.0, min(1.0, float(prob or 0.0)))
        t = max(0.0, min(1.0, float(threshold or 0.5)))
        # 音量用 sqrt 拉開低音量段：耳語的 rms 常在 0.01~0.05，線性畫幾乎看不到
        r_disp = r ** 0.5
        self.meters.coords(self._rms_id, self._rms_x0, y,
                           self._rms_x0 + METER_W * r_disp, y + METER_H)
        self.meters.coords(self._prob_id, self._prob_x0, y,
                           self._prob_x0 + METER_W * p, y + METER_H)
        # 過了門檻才變綠：這就是使用者要的「過得了/過不了」的視覺答案
        self.meters.itemconfig(self._prob_id,
                               fill="#3fb950" if p >= t else "#8a5a2b")
        self.meters.itemconfig(self._rms_id,
                               fill="#4b9fff" if speaking else "#5c6470")
        tx = self._prob_x0 + int(METER_W * t)
        self.meters.coords(self._tick_id, tx, y - 2, tx + 1, y + METER_H + 2)


# ----------------------------------------------------------------------------- 結果面板
class ResultPanel(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.detached = False
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", app.cfg["panel_alpha"])
        self.configure(bg="#14171c")
        self.withdraw()

        fam = app.cfg["font_family"]
        self.body = tk.Frame(self, bg="#14171c", padx=12, pady=8)
        self.body.pack(fill="both", expand=True)
        self.lbl_status = tk.Label(self.body, text="", font=(fam, 9), fg="#8b95a1",
                                   bg="#14171c", anchor="w", justify="left")
        self.lbl_zh = tk.Label(self.body, text="", font=(fam, app.cfg["font_size"]), fg="#f4f6f8",
                               bg="#14171c", anchor="w", justify="left")
        self.lbl_src = tk.Label(self.body, text="", font=(fam, max(8, app.cfg["font_size"] - 4)),
                                fg="#9aa3ad", bg="#14171c", anchor="w", justify="left")
        # 聆聽狀態帶：只有字幕模式開著才 pack（OCR 模式不需要，也不該佔高度）
        self.listen_bar = ListenBar(self.body, fam)
        self.listen_shown = False

        self.lbl_status.pack(fill="x")
        self.lbl_zh.pack(fill="x", pady=(2, 0))
        self.lbl_src.pack(fill="x", pady=(4, 0))

        for w in (self, self.body, self.lbl_status, self.lbl_zh, self.lbl_src,
                  self.listen_bar, self.listen_bar.lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", self._popup)
            w.bind("<Double-Button-1>", lambda e: self.app.copy_result())

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="複製譯文", command=self.app.copy_result)
        self.menu.add_command(label="貼回透鏡下方", command=self.reattach)
        self.menu.add_command(label="隱藏面板", command=self.withdraw)

    # 拖曳面板超過 12px → 脫離自動跟隨（避免點兩下複製時誤觸）
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()
        self._press = (e.x_root, e.y_root)

    def _drag_move(self, e):
        if not self.detached and abs(e.x_root - self._press[0]) + abs(e.y_root - self._press[1]) < 12:
            return
        self.detached = True
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _popup(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    def reattach(self):
        self.detached = False
        self.follow()

    def set_font_size(self, size):
        fam = self.app.cfg["font_family"]
        self.lbl_zh.configure(font=(fam, size))
        self.lbl_src.configure(font=(fam, max(8, size - 4)))
        self.follow()

    def set_listen_bar(self, visible):
        """顯示／隱藏聆聽狀態帶。只在真的要變的時候動 pack，避免無謂重排。"""
        visible = bool(visible)
        if visible == self.listen_shown:
            return
        self.listen_shown = visible
        if visible:
            # before=lbl_status：狀態帶固定在面板最上面那一行
            self.listen_bar.pack(fill="x", before=self.lbl_status)
        else:
            self.listen_bar.pack_forget()
        self.follow()

    def showing_source(self, text):
        """目前面板上顯示的原文是不是這一句（撤回幻聽時要先確認）。"""
        return bool(text) and text in self.lbl_src.cget("text")

    def show(self, status="", zh="", src="", error=False):
        wrap = max(240, self.app.root.winfo_width() - 24)
        self.lbl_status.configure(text=status, fg="#ff7b72" if error else "#8b95a1")
        self.lbl_zh.configure(text=zh, wraplength=wrap)
        self.lbl_src.configure(text=src, wraplength=wrap)
        if src:
            self.lbl_src.pack(fill="x", pady=(4, 0))
        else:
            self.lbl_src.pack_forget()
        self.deiconify()
        self.follow()

    def follow(self):
        """貼在透鏡正下方；下方放不下就翻到上方。"""
        if self.detached or not self.winfo_viewable():
            return
        r = self.app.root
        self.update_idletasks()
        w = max(240, r.winfo_width())
        h = self.body.winfo_reqheight()
        x, y = r.winfo_x(), r.winfo_y() + r.winfo_height() + 6
        if y + h > r.winfo_screenheight() and r.winfo_y() - h - 6 >= 0:
            y = r.winfo_y() - h - 6
        self.geometry(f"{w}x{h}+{x}+{y}")

    def overlaps(self, box):
        if not self.winfo_viewable():
            return False
        x1, y1 = self.winfo_x(), self.winfo_y()
        x2, y2 = x1 + self.winfo_width(), y1 + self.winfo_height()
        return not (x2 <= box[0] or x1 >= box[2] or y2 <= box[1] or y1 >= box[3])


# ----------------------------------------------------------------------------- 透鏡主視窗
class LensApp:
    def __init__(self, cfg, smoke=False, autostart_subtitle=False):
        self.cfg = cfg
        self.smoke = smoke
        self.busy = False
        self.last_result = None
        self.last_sig = None
        self.auto_job = None
        self.events = queue.Queue()
        self.audio_worker = None      # AudioSubtitleWorker，勾選「🎧字幕」時才建立
        self.vad_note = ""            # 狀態列顯示的 VAD 名稱（Silero / 能量式）
        self._filtered = 0            # 這一輪擋掉幾條幻聽
        self._drops = 0               # 這一輪總共丟掉幾段（含 VAD 擋掉的）
        self._drop_summary = ""       # 「已丟棄 N（幻聽 a、太短 b…）」
        self.subtitle_job = None      # subtitle_hold_sec 到期後清空面板的 after id

        self.root = tk.Tk()
        self.root.title("TransLens")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        g = cfg["geometry"]
        self.root.geometry(f"{max(MIN_W, g['w'])}x{max(MIN_H, g['h'])}+{g['x']}+{g['y']}")

        self._build_frame()
        self.panel = ResultPanel(self)
        self._start_hotkeys()
        self.root.after(100, self._poll_events)
        self.root.bind("<Configure>", lambda e: self.panel.follow())
        if smoke:
            self.root.after(2500, self.quit)
        if autostart_subtitle:
            # run-ja.bat / run-en.bat：UI 就緒後自動勾「🎧字幕」，等同使用者手動點一下
            self.root.after(600, self._autostart_subtitle)

    # --- UI 組件
    def _build_frame(self):
        c = self.cfg["border_color"]
        fam = self.cfg["font_family"]
        r = self.root
        self.bar = tk.Frame(r, bg=c, height=TOP_H)
        self.bar.place(x=0, y=0, relwidth=1, height=TOP_H)
        self.left = tk.Frame(r, bg=c, width=BORDER)
        self.left.place(x=0, y=TOP_H, width=BORDER, relheight=1)
        self.right = tk.Frame(r, bg=c, width=BORDER, cursor="sb_h_double_arrow")
        self.right.place(relx=1, x=-BORDER, y=TOP_H, width=BORDER, relheight=1)
        self.bottom = tk.Frame(r, bg=c, height=BORDER, cursor="sb_v_double_arrow")
        self.bottom.place(x=0, rely=1, y=-BORDER, relwidth=1, height=BORDER)
        self.grip = tk.Frame(r, bg="#0a6f66", width=GRIP, height=GRIP, cursor="size_nw_se")
        self.grip.place(relx=1, rely=1, x=-GRIP, y=-GRIP, width=GRIP, height=GRIP)

        # 工具列
        self.title = tk.Label(self.bar, text="◎ TransLens", bg=c, fg="white",
                              font=(fam, 10, "bold"), cursor="fleur", padx=8)
        self.title.pack(side="left", fill="y")
        btn_kw = dict(bg="#ffffff", fg="#0e3b37", activebackground="#e6fffb", relief="flat",
                      font=(fam, 9, "bold"), padx=8, pady=1, cursor="hand2")
        self.btn_translate = tk.Button(self.bar, text=f"翻譯 ({self.cfg['hotkey_translate'].upper()})",
                                       command=self.translate, **btn_kw)
        self.btn_translate.pack(side="left", padx=(2, 4), pady=4)

        from engines import engine_labels
        self.engine_labels = dict(engine_labels())
        self.engine_var = tk.StringVar(value=self.engine_labels.get(self.cfg["engine"], "?"))
        om = tk.OptionMenu(self.bar, self.engine_var, *self.engine_labels.values(), command=self._on_engine)
        om.configure(bg=c, fg="white", activebackground=c, activeforeground="white", relief="flat",
                     highlightthickness=0, indicatoron=0, font=(fam, 9), cursor="hand2")
        om["menu"].configure(font=(fam, 10))
        om.pack(side="left", pady=4)

        self.auto_var = tk.BooleanVar(value=False)
        self.chk_auto = tk.Checkbutton(self.bar, text="自動", variable=self.auto_var, command=self._on_auto,
                                       bg=c, fg="white", selectcolor="#0a6f66", activebackground=c,
                                       activeforeground="white", font=(fam, 9), cursor="hand2")
        self.chk_auto.pack(side="left", padx=(4, 0))

        # 音訊字幕：擷取系統聲音 → 區網 whisper-server → 翻譯（與 OCR「自動」互斥）
        self.audio_var = tk.BooleanVar(value=False)
        self.chk_audio = tk.Checkbutton(self.bar, text="🎧字幕", variable=self.audio_var,
                                        command=self._on_audio, bg=c, fg="white",
                                        selectcolor="#0a6f66", activebackground=c,
                                        activeforeground="white", font=(fam, 9), cursor="hand2")
        self.chk_audio.pack(side="left", padx=(4, 0))

        self.audio_lang_labels = dict(AUDIO_LANGS)
        cur_code = self.cfg.get("audio_lang", "auto")
        cur_label = next((lab for lab, code in AUDIO_LANGS if code == cur_code), "自動")
        self.audio_lang_var = tk.StringVar(value=cur_label)
        om_lang = tk.OptionMenu(self.bar, self.audio_lang_var,
                                *[lab for lab, _ in AUDIO_LANGS], command=self._on_audio_lang)
        om_lang.configure(bg=c, fg="white", activebackground=c, activeforeground="white",
                          relief="flat", highlightthickness=0, indicatoron=0,
                          font=(fam, 9), cursor="hand2")
        om_lang["menu"].configure(font=(fam, 10))
        om_lang.pack(side="left", pady=4)

        tk.Button(self.bar, text="✕", command=self.quit, bg=c, fg="white", relief="flat",
                  activebackground="#c0392b", font=(fam, 10, "bold"), padx=8, cursor="hand2").pack(side="right", fill="y")
        tk.Button(self.bar, text="⚙", command=self._settings_menu, bg=c, fg="white", relief="flat",
                  activebackground="#0a6f66", font=(fam, 11), padx=6, cursor="hand2").pack(side="right", fill="y")
        self.lbl_state = tk.Label(self.bar, text="", bg=c, fg="#e8fffb", font=(fam, 9))
        self.lbl_state.pack(side="right", padx=6)

        # 拖曳與縮放
        for w in (self.bar, self.title, self.lbl_state):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        for w, mode in ((self.grip, "both"), (self.right, "w"), (self.bottom, "h")):
            w.bind("<ButtonPress-1>", self._resize_start)
            w.bind("<B1-Motion>", lambda e, m=mode: self._resize_move(e, m))

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _resize_start(self, e):
        self._rx, self._ry = e.x_root, e.y_root
        self._rw, self._rh = self.root.winfo_width(), self.root.winfo_height()

    def _resize_move(self, e, mode):
        w = max(MIN_W, self._rw + (e.x_root - self._rx)) if mode in ("both", "w") else self._rw
        h = max(MIN_H, self._rh + (e.y_root - self._ry)) if mode in ("both", "h") else self._rh
        self.root.geometry(f"{w}x{h}")

    # --- 設定選單
    def _settings_menu(self):
        fam = self.cfg["font_family"]
        m = tk.Menu(self.root, tearoff=0, font=(fam, 10))
        lang_menu = tk.Menu(m, tearoff=0, font=(fam, 10))
        self.ocr_var = tk.StringVar(value=self.cfg["ocr_lang"])
        lang_menu.add_radiobutton(label="自動（依系統語言）", value="auto", variable=self.ocr_var,
                                  command=self._on_ocr_lang)
        try:
            from engines.ocr_windows import available_languages
            for tag, name in available_languages():
                lang_menu.add_radiobutton(label=f"{name} ({tag})", value=tag, variable=self.ocr_var,
                                          command=self._on_ocr_lang)
        except Exception as e:  # noqa: BLE001
            lang_menu.add_command(label=f"（無法列出：{e}）", state="disabled")
        lang_menu.add_separator()
        lang_menu.add_command(label="安裝日文＋英文 OCR 語言包（需管理員）…", command=self._install_ocr_langs)
        lang_menu.add_command(label="如何安裝更多 OCR 語言…", command=self._show_ocr_help)
        m.add_cascade(label="OCR 語言（OCR+Google 引擎）", menu=lang_menu)

        # 翻譯來源：本地 LLM（文字留在區網）或 Google（免金鑰但會出外網）
        tr_menu = tk.Menu(m, tearoff=0, font=(fam, 10))
        self.translator_var = tk.StringVar(
            value=self.cfg.get("translator", DEFAULT_CONFIG["translator"]))
        for label, value in TRANSLATORS:
            tr_menu.add_radiobutton(label=label, value=value, variable=self.translator_var,
                                    command=self._on_translator)
        tr_menu.add_separator()
        tr_menu.add_command(label=f"本地 LLM：{self.cfg.get('local_llm_url', '')}"
                                  f" / {self.cfg.get('local_llm_model', '')}", state="disabled")
        m.add_cascade(label="翻譯來源", menu=tr_menu)

        # 詞彙表與自訂 prompt：全域那份在程式目錄，這裡只管「額外指定一份」
        g_menu = tk.Menu(m, tearoff=0, font=(fam, 10))
        g_menu.add_command(label=f"編輯全域詞彙表（{glossary_mod.GLOSSARY_NAME}）…",
                           command=lambda: self._open_text_file(glossary_mod.GLOSSARY_NAME))
        g_menu.add_command(label=f"編輯全域 prompt（{glossary_mod.PROMPT_NAME}）…",
                           command=lambda: self._open_text_file(glossary_mod.PROMPT_NAME))
        g_menu.add_separator()
        extra = self.cfg.get("glossary_file") or ""
        g_menu.add_command(
            label=f"額外詞彙表：{os.path.basename(extra) if extra else '（未指定）'}…",
            command=self._pick_glossary)
        if extra:
            g_menu.add_command(label="取消額外詞彙表", command=lambda: self._set_glossary(""))
        extra_p = self.cfg.get("prompt_file") or ""
        g_menu.add_command(
            label=f"額外 prompt：{os.path.basename(extra_p) if extra_p else '（未指定）'}…",
            command=self._pick_prompt)
        if extra_p:
            g_menu.add_command(label="取消額外 prompt", command=lambda: self._set_prompt_file(""))
        g_menu.add_separator()
        self.glossary_on_var = tk.BooleanVar(value=bool(self.cfg.get("glossary", True)))
        g_menu.add_checkbutton(label="啟用詞彙表／自訂 prompt", variable=self.glossary_on_var,
                               command=self._on_glossary_toggle)
        g_menu.add_command(label=self._glossary_summary(), state="disabled")
        m.add_cascade(label="詞彙表／自訂 prompt", menu=g_menu)

        # VAD 靈敏度：耳語漏掉調「靈敏」，BGM 吵幻聽多調「嚴格」
        from engines import vad as vad_mod
        v_menu = tk.Menu(m, tearoff=0, font=(fam, 10))
        self.vad_sens_var = tk.StringVar(
            value=self.cfg.get("vad_sensitivity", vad_mod.DEFAULT_SENSITIVITY))
        for label, value in vad_mod.SENSITIVITY_LABELS:
            p = vad_mod.SENSITIVITY[value]
            v_menu.add_radiobutton(
                label=f"{label}（門檻 {p['vad_threshold']:.1f}、"
                      f"語音含量 {int(p['vad_min_voiced_ms'])}ms）",
                value=value, variable=self.vad_sens_var,
                command=self._on_vad_sensitivity)
        v_menu.add_separator()
        v_menu.add_command(label="耳語聽不到 → 靈敏；BGM 吵、幻聽多 → 嚴格",
                           state="disabled")
        m.add_cascade(label="VAD 靈敏度（🎧字幕）", menu=v_menu)

        self.listen_bar_var = tk.BooleanVar(
            value=bool(self.cfg.get("show_listen_bar", True)))
        m.add_checkbutton(label="顯示聆聽狀態帶", variable=self.listen_bar_var,
                          command=self._on_listen_bar_toggle)

        self.show_src_var = tk.BooleanVar(value=self.cfg["show_original"])
        m.add_checkbutton(label="顯示原文", variable=self.show_src_var, command=self._on_show_src)
        m.add_command(label="字級 ＋", command=lambda: self._font_delta(+2))
        m.add_command(label="字級 －", command=lambda: self._font_delta(-2))
        m.add_separator()
        m.add_command(label="複製譯文", command=self.copy_result)
        m.add_command(label="顯示結果面板", command=self.panel.reattach)
        m.add_separator()
        m.add_command(label=f"快捷鍵：{self.cfg['hotkey_translate']} 翻譯 / "
                            f"{self.cfg['hotkey_toggle']} 隱藏顯示", state="disabled")
        m.add_command(label=f"設定檔：{CONFIG_PATH}", state="disabled")
        m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    # --- 詞彙表／自訂 prompt
    def _glossary_summary(self):
        """選單底部那行「目前有幾個詞、幾條規則」。讀不到就說沒有。"""
        try:
            g, p = glossary_mod.load_from_cfg(self.cfg)
        except Exception as e:  # noqa: BLE001
            return f"（讀取失敗：{e}）"
        if not g and not p:
            return "（目前沒有任何詞彙表／自訂 prompt）"
        return (f"目前：{len(g.terms)} 個對照詞、{len(g.rules)} 條取代規則"
                f"{'、有自訂 prompt' if p else ''}")

    def _open_text_file(self, name):
        """用系統預設編輯器打開全域檔；不存在就先建一個帶說明的空檔。"""
        path = os.path.join(APP_DIR, name)
        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# TransLens {name}\n# 用記事本編輯即可，存成 UTF-8。\n")
            except OSError as e:
                messagebox.showerror("TransLens", f"建立 {name} 失敗：{e}")
                return
        try:
            os.startfile(path)  # noqa: S606 — 開使用者自己的設定檔
        except OSError as e:
            messagebox.showerror("TransLens", f"開啟 {name} 失敗：{e}")

    def _pick_glossary(self):
        path = filedialog.askopenfilename(
            title="選一份額外的詞彙表", filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")])
        if path:
            self._set_glossary(path)

    def _pick_prompt(self):
        path = filedialog.askopenfilename(
            title="選一份額外的 prompt", filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")])
        if path:
            self._set_prompt_file(path)

    def _set_glossary(self, path):
        self.cfg["glossary_file"] = path
        save_config(self.cfg)
        self._reload_glossary()

    def _set_prompt_file(self, path):
        self.cfg["prompt_file"] = path
        save_config(self.cfg)
        self._reload_glossary()

    def _on_glossary_toggle(self):
        self.cfg["glossary"] = self.glossary_on_var.get()
        save_config(self.cfg)
        self._reload_glossary()

    def _reload_glossary(self):
        """讓改動立刻生效：字幕模式在跑就叫 worker 重讀一次。"""
        w = self.audio_worker
        if w is not None and w.running:
            from engines import translator
            w.glossary, w.custom_prompt = translator.load_glossary(self.cfg)

    def _install_ocr_langs(self):
        bat = os.path.join(APP_DIR, "install_ocr_lang.bat")
        if messagebox.askokcancel(
                "安裝 OCR 語言包",
                "將以系統管理員身分執行 install_ocr_lang.bat，透過 Windows Update 安裝\n"
                "日文（ja-JP）與英文（en-US）的 OCR 語言包，約 1~3 分鐘。\n\n"
                "安裝完請重新啟動 TransLens。要繼續嗎？", parent=self.root):
            os.startfile(bat)

    def _show_ocr_help(self):
        messagebox.showinfo(
            "安裝 Windows OCR 語言",
            "OCR+Google 引擎使用 Windows 內建 OCR，只能辨識「已安裝 OCR 功能」的語言。\n\n"
            "方法一（圖形介面）：設定 → 時間與語言 → 語言 → 新增語言（例如 English (United States)、日本語）"
            "→ 該語言的「選項」→ 安裝「光學字元辨識」。\n\n"
            "方法二（系統管理員 PowerShell）：\n"
            "  Add-WindowsCapability -Online -Name Language.OCR~~~en-US~0.0.1.0\n"
            "  Add-WindowsCapability -Online -Name Language.OCR~~~ja-JP~0.0.1.0\n\n"
            "或者直接切換到 Gemini / Claude 引擎，AI 視覺模型不需要語言包。",
            parent=self.root)

    def _on_engine(self, label):
        for key, lab in self.engine_labels.items():
            if lab == label:
                self.cfg["engine"] = key
        save_config(self.cfg)

    def _on_ocr_lang(self):
        self.cfg["ocr_lang"] = self.ocr_var.get()
        save_config(self.cfg)

    def _on_translator(self):
        """切換翻譯來源：存檔即生效（下一句字幕／下一次翻譯就會用新的）。"""
        self.cfg["translator"] = self.translator_var.get()
        save_config(self.cfg)
        if self.audio_var.get():
            self.panel.show(status=self._audio_status("聆聽中…"), zh="", src="")

    def _on_show_src(self):
        self.cfg["show_original"] = self.show_src_var.get()
        save_config(self.cfg)
        if self.last_result:
            self._render(self.last_result)

    def _font_delta(self, d):
        self.cfg["font_size"] = min(40, max(9, self.cfg["font_size"] + d))
        self.panel.set_font_size(self.cfg["font_size"])
        save_config(self.cfg)

    def _on_auto(self):
        if self.auto_var.get():
            if self.audio_var.get():          # 與音訊字幕互斥
                self.audio_var.set(False)
                self._stop_audio()
            self.lbl_state.configure(text="自動模式")
            self._auto_tick()
        else:
            if self.auto_job:
                self.root.after_cancel(self.auto_job)
                self.auto_job = None
            self.lbl_state.configure(text="")

    # --- 音訊字幕模式
    def _on_audio_lang(self, label):
        lang = self.audio_lang_labels.get(label, "auto")
        self.cfg["audio_lang"] = lang
        save_config(self.cfg)
        if self.audio_worker and self.audio_worker.running:
            # 手動選語言立即生效並停止投票 —— 不必重開 worker（重開會重新
            # 下載/載入模型並丟掉手上那段音訊）。
            self.audio_worker.set_language(lang)
            self.panel.show(status=self._audio_status("聆聽中…"), zh="", src="")

    def _autostart_subtitle(self):
        if not self.audio_var.get():
            self.audio_var.set(True)
            self._on_audio()

    def _on_audio(self):
        if self.audio_var.get():
            # 與 OCR 自動模式互斥：同時開會互搶結果面板
            if self.auto_var.get():
                self.auto_var.set(False)
                self._on_auto()
            self._start_audio()
        else:
            self._stop_audio()

    def _start_audio(self):
        from engines.audio_subtitle import AudioSubtitleWorker
        self.vad_note = ""
        self._filtered = 0
        self._drops = 0
        self._drop_summary = ""
        self.audio_worker = AudioSubtitleWorker(
            self.cfg, lambda kind, payload: self.events.put((f"audio_{kind}", payload)))
        self.audio_worker.start()
        self.lbl_state.configure(text="🎧字幕")
        self.panel.set_listen_bar(self.cfg.get("show_listen_bar", True))
        self.panel.listen_bar.set_state("idle", "聆聽中")
        self.panel.show(status=self._audio_status("啟動中…"), zh="", src="")

    def _stop_audio(self):
        if self.audio_worker:
            self.audio_worker.stop()
            self.audio_worker = None
        if self.subtitle_job:
            self.root.after_cancel(self.subtitle_job)
            self.subtitle_job = None
        self.panel.set_listen_bar(False)
        self.lbl_state.configure(text="自動模式" if self.auto_var.get() else "")

    def _audio_status(self, tail="", translator_note="", lang_status="", filtered=0):
        """狀態列：字幕 · whisper@<host> · <語言> · VAD · 翻譯: <來源 耗時> · <耗時>"""
        url = self.cfg.get("whisper_server_url", "")
        host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0] or "?"
        # 語言：投票鎖定後 worker 會回報「語言：ja（已鎖定）」，還沒有就顯示設定值
        parts = ["字幕", f"whisper@{host}",
                 lang_status or self._lang_status() or self.cfg.get("audio_lang", "auto")]
        if self.vad_note:
            parts.append(self.vad_note)
        if translator_note:
            parts.append(f"翻譯: {translator_note}")
        else:
            # 還沒翻過任何一句時，先顯示設定裡選的來源
            from engines.translator import BACKEND_LABELS
            choice = self.cfg.get("translator", DEFAULT_CONFIG["translator"])
            parts.append(f"翻譯: {BACKEND_LABELS.get(choice, choice)}")
        # 丟棄統計：原本只報幻聽，但被 VAD 擋掉的耳語（「語音含量不足」）
        # 才是使用者最常遇到卻看不見的那種，所以整包一起報。
        if self._drop_summary:
            parts.append(self._drop_summary)
        else:
            filtered = filtered or self._filtered
            if filtered:
                parts.append(f"已濾 {filtered} 條幻聽")
        if tail:
            parts.append(tail)
        return " · ".join(parts)

    def _lang_status(self):
        """worker 目前的語言狀態（沒開 worker 就回空字串）。"""
        w = self.audio_worker
        if w is not None and getattr(w, "lang_lock", None) is not None:
            return w.lang_lock.status()
        return ""

    def _on_subtitle(self, data):
        """收到一段字幕：上面顯示譯文、下面顯示原文，subtitle_hold_sec 後清空。"""
        if not self.audio_var.get():
            return                       # 已取消勾選，忽略在路上的殘留字幕
        self._filtered = data.get("filtered", self._filtered)
        self._drops = data.get("drops", self._drops)
        self._drop_summary = data.get("drop_summary", self._drop_summary)
        self._listen_state("idle", "聆聽中")
        self.panel.show(status=self._audio_status(f"{data['total_sec']:.1f}s",
                                                  data.get("translator", ""),
                                                  data.get("lang_status", "")),
                        zh=f"中文: {data['zh']}",
                        src=(f"原文: {data['src']}" if self.cfg["show_original"] else ""),
                        error=bool(data.get("fell_back")))
        if self.subtitle_job:
            self.root.after_cancel(self.subtitle_job)
        hold_ms = int(float(self.cfg.get("subtitle_hold_sec", 8)) * 1000)
        self.subtitle_job = self.root.after(hold_ms, self._clear_subtitle)

    def _on_retract(self, data):
        """同一句在 90 秒內第二次出現 = 幻聽：把先前顯示的那條從面板收回。

        只有面板還在顯示那句時才清 —— 使用者可能早就看到別的字幕了，
        把不相干的內容清掉反而更擾人。
        """
        if not self.audio_var.get():
            return
        self._filtered = data.get("filtered", self._filtered)
        if self.panel.showing_source(data.get("src", "")):
            if self.subtitle_job:
                self.root.after_cancel(self.subtitle_job)
                self.subtitle_job = None
            self.panel.show(status=self._audio_status("聆聽中…"), zh="", src="")

    def _on_audio_status(self, msg):
        """worker 的狀態訊息。裡面帶 VAD 名稱時記下來，之後每條字幕都顯示。"""
        if not self.audio_var.get():
            return
        text = str(msg or "")
        if "VAD:" in text:
            # 「擷取中：<裝置> · VAD: Silero」→ 把 VAD 那段留著給狀態列用
            self.vad_note = text.split("·")[-1].strip()
        self.panel.show(status=self._audio_status(text), zh="", src="")

    def _on_vad_sensitivity(self):
        """切換 VAD 靈敏度。worker 在跑就地生效，不重開（不重載模型、不丟音訊）。"""
        value = self.vad_sens_var.get()
        self.cfg["vad_sensitivity"] = value
        # 舊 config 可能把某一檔的預設值寫死在 vad_threshold 等鍵上，那會把
        # 靈敏度釘住。resolve_params 會忽略「原樣等於預設」的值，但存檔時
        # 順手清掉，config.json 看起來才不會自相矛盾。
        from engines import vad as vad_mod
        for key in vad_mod.PARAM_KEYS:
            if vad_mod.is_preset_value(key, self.cfg.get(key)):
                self.cfg[key] = None
        save_config(self.cfg)
        w = self.audio_worker
        if w is not None and w.running:
            w.set_sensitivity(value)

    def _on_listen_bar_toggle(self):
        self.cfg["show_listen_bar"] = self.listen_bar_var.get()
        save_config(self.cfg)
        self.panel.set_listen_bar(self.cfg["show_listen_bar"]
                                  and self.audio_var.get())

    # --- 聆聽狀態帶
    def _listen_state(self, state, text):
        """更新狀態帶的圓點與說明。狀態帶關掉時什麼都不做。"""
        if not self.audio_var.get() or not self.panel.listen_shown:
            return
        self.panel.listen_bar.set_state(state, text)

    def _on_level(self, data):
        """音量／VAD 機率量表。這是最高頻的事件，只改 Canvas 座標不重排版面。"""
        if not self.audio_var.get() or not self.panel.listen_shown:
            return
        self.panel.listen_bar.set_level(data.get("rms", 0.0), data.get("prob", 0.0),
                                        data.get("threshold", 0.5),
                                        data.get("speaking", False))

    def _on_asr_done(self, data):
        """辨識回來了：顯示耗時與原文前 20 字，讓使用者知道「聽到的是這句」。"""
        text = (data.get("text") or "").strip()
        head = text[:20] + ("…" if len(text) > 20 else "")
        tail = f" → {head}" if head else ""
        self._listen_state("working", f"辨識中 {data.get('sec', 0.0):.1f}s{tail}")

    def _on_dropped(self, data):
        """這段沒能變成字幕。橘點 + 原因，2 秒後自己回灰（ListenBar 管）。"""
        from engines.audio_subtitle import drop_label
        self._drops = data.get("drops", self._drops)
        self._drop_summary = data.get("drop_summary", self._drop_summary)
        seq = data.get("id")
        label = drop_label(data.get("reason"))
        sec = data.get("sec") or 0.0
        who = f" #{seq}" if seq else ""
        detail = f" {sec:.1f}s" if sec else ""
        self._listen_state("dropped", f"已丟棄{who}：{label}{detail}")
        # 狀態列的累計數也要跟著動（使用者可能沒在看狀態帶那一瞬間）
        if self.audio_var.get():
            self.panel.show(status=self._audio_status("聆聽中…"),
                            zh=self.panel.lbl_zh.cget("text"),
                            src=self.panel.lbl_src.cget("text"))

    def _clear_subtitle(self):
        self.subtitle_job = None
        if self.audio_var.get():
            self.panel.show(status=self._audio_status("聆聽中…"), zh="", src="")

    def _audio_failed(self, msg):
        """worker 回報錯誤：關掉勾選並把原因顯示在面板上。"""
        self.audio_var.set(False)
        self._stop_audio()
        self.panel.show(status=f"⚠ 音訊字幕：{msg}", zh="", src="", error=True)

    # --- 快捷鍵
    def _start_hotkeys(self):
        bindings = {}
        for hk_id, key in ((1, "hotkey_translate"), (2, "hotkey_toggle")):
            try:
                bindings[hk_id] = parse_hotkey(self.cfg[key])
            except ValueError as e:
                logging.warning("%s", e)
        HotkeyListener(bindings, self.events).start()
        self.hidden = False

    def toggle_visibility(self):
        if self.hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            if self.last_result:
                self.panel.deiconify()
        else:
            self.root.withdraw()
            self.panel.withdraw()
        self.hidden = not self.hidden

    # --- 事件迴圈
    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "hotkey":
                    if payload == 1:
                        self.translate()
                    elif payload == 2:
                        self.toggle_visibility()
                elif kind == "result":
                    self._finish(payload)
                elif kind == "error":
                    self._fail(payload)
                elif kind == "audio_subtitle":
                    self._on_subtitle(payload)
                elif kind == "audio_retract":
                    self._on_retract(payload)
                elif kind == "audio_status":
                    self._on_audio_status(payload)
                elif kind == "audio_error":
                    self._audio_failed(payload)
                elif kind == "audio_level":
                    self._on_level(payload)
                elif kind == "audio_speech_start":
                    self._listen_state("speech", "偵測到語音…")
                elif kind == "audio_speech_progress":
                    self._listen_state("speech", f"偵測到語音 {payload['sec']:.1f}s…")
                elif kind == "audio_segment_sent":
                    self._listen_state("working",
                                       f"送出辨識 #{payload['id']}（{payload['sec']:.1f}s）")
                elif kind == "audio_asr_done":
                    self._on_asr_done(payload)
                elif kind == "audio_dropped":
                    self._on_dropped(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_events)

    # --- 截圖與翻譯
    def capture_box(self):
        r = self.root
        x = r.winfo_rootx() + BORDER
        y = r.winfo_rooty() + TOP_H
        w = r.winfo_width() - 2 * BORDER
        h = r.winfo_height() - TOP_H - BORDER
        return (x, y, x + w, y + h)

    def grab(self):
        box = self.capture_box()
        if box[2] - box[0] < 20 or box[3] - box[1] < 20:
            raise RuntimeError("透鏡範圍太小")
        hide_panel = self.panel.overlaps(box)
        if hide_panel:
            self.panel.withdraw()
            self.root.update()
        try:
            return ImageGrab.grab(bbox=box, all_screens=True)
        finally:
            if hide_panel:
                self.panel.deiconify()

    def translate(self, img=None):
        if self.busy or self.hidden:
            return
        try:
            img = img or self.grab()
        except Exception as e:  # noqa: BLE001
            self._fail(str(e))
            return
        self.busy = True
        self.last_sig = image_signature(img)
        self.btn_translate.configure(state="disabled", text="辨識中…")
        self.panel.show(status="辨識中…", zh=self.panel.lbl_zh.cget("text"),
                        src=self.panel.lbl_src.cget("text") if self.cfg["show_original"] else "")
        engine_key = self.cfg["engine"]
        threading.Thread(target=self._worker, args=(engine_key, img), daemon=True).start()

    def _worker(self, engine_key, img):
        try:
            from engines import get_engine
            result = get_engine(engine_key, self.cfg).translate_image(img)
            self.events.put(("result", result))
        except Exception as e:  # noqa: BLE001
            logging.exception("翻譯失敗")
            self.events.put(("error", f"{type(e).__name__}: {e}"))

    def _finish(self, result):
        self.busy = False
        self.last_result = result
        self.btn_translate.configure(state="normal", text=f"翻譯 ({self.cfg['hotkey_translate'].upper()})")
        self._render(result)
        self._schedule_auto()

    def _fail(self, msg):
        self.busy = False
        self.btn_translate.configure(state="normal", text=f"翻譯 ({self.cfg['hotkey_translate'].upper()})")
        self.panel.show(status=f"⚠ {msg}", zh=self.panel.lbl_zh.cget("text"), error=True)
        self._schedule_auto()

    def _render(self, result):
        status = " · ".join([self.engine_labels.get(self.cfg["engine"], ""),
                             *(result.notes or []),
                             *([f"來源: {result.source_lang}"] if result.source_lang else [])])
        src = f"原文: {result.original}" if (self.cfg["show_original"] and result.original) else ""
        self.panel.show(status=status, zh=f"中文: {result.translation}", src=src)

    def copy_result(self):
        if self.last_result:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.last_result.translation)
            self.lbl_state.configure(text="已複製")
            self.root.after(1500, lambda: self.lbl_state.configure(
                text="自動模式" if self.auto_var.get() else ""))

    # --- 自動模式：畫面有變才重翻
    def _schedule_auto(self):
        if self.auto_var.get() and not self.auto_job:
            self.auto_job = self.root.after(int(self.cfg["auto_interval_sec"] * 1000), self._auto_tick)

    def _auto_tick(self):
        self.auto_job = None
        if not self.auto_var.get() or self.hidden:
            return
        if self.busy:
            self._schedule_auto()
            return
        try:
            img = self.grab()
        except Exception as e:  # noqa: BLE001
            self._fail(str(e))
            return
        if image_signature(img) != self.last_sig:
            self.translate(img)
        else:
            self._schedule_auto()

    # --- 收尾
    def quit(self):
        try:
            self._stop_audio()
        except Exception:  # noqa: BLE001
            pass
        try:
            r = self.root
            self.cfg["geometry"] = {"x": r.winfo_x(), "y": r.winfo_y(), "w": r.winfo_width(), "h": r.winfo_height()}
            save_config(self.cfg)
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


# ----------------------------------------------------------------------------- 入口
def run_test_image(path, engine_key, cfg):
    """不開 UI，直接對一張圖片跑引擎（除錯用）。"""
    from engines import get_engine
    img = Image.open(path)
    res = get_engine(engine_key or cfg["engine"], cfg).translate_image(img)
    print("中文:", res.translation)
    if res.original:
        print("原文:", res.original)
    print("備註:", res.notes, res.source_lang)


def main():
    ap = argparse.ArgumentParser(description="TransLens 桌面透鏡翻譯框")
    ap.add_argument("--test-image", help="對指定圖片跑翻譯引擎後離開（不開 UI）")
    ap.add_argument("--engine", help="覆寫引擎 key：ocr_google / gemini_api / gemini_cli / claude_api")
    ap.add_argument("--smoke", action="store_true", help="開啟 UI 2.5 秒後自動關閉（自檢）")
    ap.add_argument("--audio-lang", choices=["auto", "ja", "en"], help="音訊字幕語言（覆寫設定檔）")
    ap.add_argument("--vad-sensitivity", choices=["sensitive", "normal", "strict"],
                    help="VAD 靈敏度（覆寫設定檔）")
    ap.add_argument("--subtitle", action="store_true", help="啟動後直接開啟「🎧字幕」")
    args = ap.parse_args()

    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))
    cfg = load_config()
    if args.engine:
        cfg["engine"] = args.engine
    if args.audio_lang:
        cfg["audio_lang"] = args.audio_lang
    if args.vad_sensitivity:
        cfg["vad_sensitivity"] = args.vad_sensitivity
    if args.test_image:
        run_test_image(args.test_image, args.engine, cfg)
        return
    enable_dpi_awareness()
    if not args.smoke and not acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(
            None, "TransLens 已經在執行中（看看螢幕上是否已有透鏡框，或用 Ctrl+Alt+H 顯示）。",
            "TransLens", 0x40)
        return
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)
    LensApp(cfg, smoke=args.smoke, autostart_subtitle=args.subtitle).run()


if __name__ == "__main__":
    main()

"""TransLens — 桌面透鏡翻譯框

一個永遠置頂、可拖曳、可縮放的「透鏡框」：把框套在螢幕任何位置（遊戲對話、說明、選單），
按「翻譯」或 Ctrl+Alt+T，框內文字就會被辨識並翻成繁體中文，結果顯示在框的下方：
    中文: ……

引擎（工具列可切換）：
  ocr_google  Windows 內建 OCR + Google 翻譯（免金鑰、離線 OCR）— 預設
  gemini_api  Gemini 視覺模型一步 OCR+翻譯（需 GEMINI_API_KEY）
  gemini_cli  本機 gemini CLI 無頭模式
  claude_api  Claude 視覺模型（需 anthropic 套件 + ANTHROPIC_API_KEY）
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
from tkinter import messagebox

from PIL import Image, ImageGrab

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
    "geometry": {"x": 200, "y": 200, "w": 640, "h": 220},
}

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
        self.lbl_status.pack(fill="x")
        self.lbl_zh.pack(fill="x", pady=(2, 0))
        self.lbl_src.pack(fill="x", pady=(4, 0))

        for w in (self, self.body, self.lbl_status, self.lbl_zh, self.lbl_src):
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
    def __init__(self, cfg, smoke=False):
        self.cfg = cfg
        self.smoke = smoke
        self.busy = False
        self.last_result = None
        self.last_sig = None
        self.auto_job = None
        self.events = queue.Queue()

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
            self.lbl_state.configure(text="自動模式")
            self._auto_tick()
        else:
            if self.auto_job:
                self.root.after_cancel(self.auto_job)
                self.auto_job = None
            self.lbl_state.configure(text="")

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
    args = ap.parse_args()

    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))
    cfg = load_config()
    if args.engine:
        cfg["engine"] = args.engine
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
    LensApp(cfg, smoke=args.smoke).run()


if __name__ == "__main__":
    main()

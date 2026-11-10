"""A small, standard-library Tkinter interface for local transcription and summaries."""
from __future__ import annotations

import json
import queue
import threading
import time
import sys
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD

from transcribe_paraformer import DEFAULT_MODEL, DEFAULT_VAD, TranscriptionCancelled, timestamp, transcribe


FROZEN = getattr(sys, "frozen", False)
BUNDLE_DIR = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else BUNDLE_DIR
SETTINGS_FILE = APP_DIR / "settings.json"
PARAFORMER_MIRROR = "https://hf-mirror.com/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main/"
PARAFORMER_FILES = ("model.int8.onnx", "tokens.txt")
VAD_URL = "https://hf-mirror.com/csukuangfj/vad/resolve/main/silero_vad.onnx"
DEFAULT_PROMPT = "请根据提供的转写内容，整理一份准确、清晰的中文总结。提炼重点、结论和待办事项；不要补充原文没有的信息。"


class TranscribeWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("本地音频转文字")
        self.root.geometry("820x600")
        self.root.minsize(720, 520)
        self.root.configure(bg="#f5f7fb")
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.transcription_cancel_event = None
        self.audio_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.summary_source = tk.StringVar()
        self.progress_text = tk.StringVar(value="选择音频或视频后开始转写")
        self.model_status = tk.StringVar()
        self.ai_status = tk.StringVar(value="设置 DeepSeek Key 后即可总结")
        self.api_key = tk.StringVar()
        self.prompt = DEFAULT_PROMPT
        self._load_settings()
        self._set_icon()
        self._build_ui()
        self._refresh_model_status()
        self.root.after(100, self._process_events)

    def _load_settings(self):
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            self.api_key.set(settings.get("deepseek_api_key", ""))
            self.prompt = settings.get("summary_prompt", DEFAULT_PROMPT)
        except (OSError, ValueError, TypeError):
            pass

    def _save_settings(self):
        data = {
            "deepseek_api_key": self.api_key.get().strip(),
            "summary_prompt": self.prompt_text.get("1.0", "end-1c").strip(),
        }
        temporary = SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS_FILE)
        self.prompt = data["summary_prompt"] or DEFAULT_PROMPT

    def _set_icon(self):
        icon = tk.PhotoImage(width=32, height=32)
        icon.put("#2563eb", to=(0, 0, 32, 32))
        for x, height in ((7, 8), (11, 14), (15, 21), (19, 13), (23, 18), (27, 9)):
            top = (32 - height) // 2
            icon.put("#ffffff", to=(x, top, x + 2, top + height))
        self.root.iconphoto(True, icon)
        icon_path = BUNDLE_DIR / "assets" / "audio_to_txt.ico"
        if icon_path.is_file():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self._icon = icon

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f5f7fb")
        style.configure("Page.TFrame", background="#f5f7fb")
        style.configure("Header.TFrame", background="#eaf0fa")
        style.configure("Title.TLabel", background="#eaf0fa", foreground="#172033", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Hint.TLabel", background="#f5f7fb", foreground="#65718a", font=("Microsoft YaHei UI", 9))
        style.configure("Header.Hint.TLabel", background="#eaf0fa", foreground="#65718a", font=("Microsoft YaHei UI", 9))
        style.configure("TLabel", background="#f5f7fb", foreground="#263247", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 6), background="#e0e8f3", foreground="#263247", bordercolor="#aebed3", borderwidth=1, focusthickness=0, relief="flat")
        style.map("TButton", background=[("pressed", "#bdcce0"), ("active", "#cbd9eb"), ("disabled", "#e5e9ef")], foreground=[("disabled", "#7c8796")], bordercolor=[("active", "#879fbd"), ("disabled", "#c9d1dc")])
        style.configure("Start.TButton", font=("Microsoft YaHei UI", 9, "bold"), padding=(13, 6), background="#2563c9", foreground="#ffffff", bordercolor="#174fae", borderwidth=1, focusthickness=0, relief="flat")
        style.map("Start.TButton", background=[("pressed", "#194995"), ("active", "#1f56ae"), ("disabled", "#9cb3d2")], bordercolor=[("active", "#174995"), ("disabled", "#9cb3d2")])
        style.configure("Stop.TButton", font=("Microsoft YaHei UI", 9, "bold"), padding=(11, 6), background="#fde4e6", foreground="#981b2c", bordercolor="#df8c97", borderwidth=1, focusthickness=0, relief="flat")
        style.map("Stop.TButton", background=[("pressed", "#bd233d"), ("active", "#cf2e48"), ("disabled", "#eee2e3")], foreground=[("pressed", "#ffffff"), ("active", "#ffffff"), ("disabled", "#887a7d")], bordercolor=[("active", "#b8213b"), ("disabled", "#d4c5c7")])
        style.configure("TLabelframe", background="#f5f7fb", bordercolor="#d4deef", relief="solid", padding=8)
        style.configure("TLabelframe.Label", background="#f5f7fb", foreground="#315e9f", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#263247", bordercolor="#cbd6e8", insertcolor="#263247", padding=4)
        style.map("TEntry", bordercolor=[("focus", "#6b95d6")])
        style.configure("Horizontal.TProgressbar", troughcolor="#d5deeb", background="#2563c9", bordercolor="#aebed3", lightcolor="#2563c9", darkcolor="#2563c9", thickness=14)

        outer = ttk.Frame(self.root, padding=(12, 8))
        self._outer = outer
        outer.pack(fill="both", expand=True)
        self._build_menu()

        self.home_page = ttk.Frame(outer, style="Page.TFrame")
        self.settings_page = ttk.Frame(outer, style="Page.TFrame")
        self.about_page = ttk.Frame(outer, style="Page.TFrame")
        self._build_home()
        self._build_settings()
        self._build_about_page()
        self._show_page("home")

    def _build_menu(self):
        menu_bar = tk.Frame(self._outer, bg="#e9eef7", height=32)
        menu_bar.pack(fill="x", pady=(0, 7))
        menu_bar.pack_propagate(False)
        self.home_menu_button = self._menu_button(menu_bar, "首页", lambda: self._show_page("home"))
        self.settings_menu_button = self._menu_button(menu_bar, "设置", lambda: self._show_page("settings"))
        self.about_menu_button = self._menu_button(menu_bar, "关于", self._show_about)
        self._menu = menu_bar

    @staticmethod
    def _menu_button(parent, label, command):
        button = tk.Button(
            parent, text=label, command=command, relief="flat", bd=0,
            bg="#e9eef7", activebackground="#dbe6f5", fg="#263247",
            activeforeground="#172033", font=("Microsoft YaHei UI", 9, "bold"),
            padx=13, pady=3, cursor="hand2", takefocus=False,
        )
        button.base_bg = "#e9eef7"
        button.pack(side="left", fill="y")
        button.bind("<Enter>", lambda _event: button.configure(bg="#dbe6f5"))
        button.bind("<Leave>", lambda _event: button.configure(bg=button.base_bg))
        return button

    def _show_about(self):
        self._show_page("about")

    def _build_about_page(self):
        page = self.about_page
        self._build_page_header(page, "关于音频小助手", "软件介绍与使用方法")
        intro = ttk.LabelFrame(page, text="软件介绍")
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text="使用 Paraformer 和 Silero VAD 在本机完成音频或视频音轨转写，并可调用 DeepSeek 生成内容总结。",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", fill="x")

        usage = ttk.LabelFrame(page, text="使用方法")
        usage.pack(fill="x", pady=(0, 8))
        steps = (
            "1. 首次使用：进入“设置”，下载 Paraformer 和 Silero VAD 模型，并填写 DeepSeek API Key。",
            "2. 转写音频或视频：回到“首页”选择文件；视频会自动读取其中的音轨。确认输出位置后点击“开始”。",
            "3. 生成总结：使用当前转写结果，或选择已有的 TXT / Markdown 文件，再点击“总结”。",
        )
        for step in steps:
            ttk.Label(usage, text=step, wraplength=760, justify="left").pack(anchor="w", fill="x", pady=2)
        ttk.Label(
            page,
            text="音频和视频音轨在本机处理。生成 AI 总结时，所选文本会发送给 DeepSeek。",
            style="Hint.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(2, 0))

    def _build_home(self):
        page = self.home_page
        self._build_page_header(page, "音频小助手", "本地转写 · AI 总结")

        columns = ttk.Frame(page, style="Page.TFrame")
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=11)
        columns.columnconfigure(1, weight=9)
        columns.rowconfigure(0, weight=1)
        left = ttk.Frame(columns, style="Page.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        right = ttk.Frame(columns, style="Page.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        transcribe_box = ttk.LabelFrame(left, text="🎙 音频转写")
        transcribe_box.pack(fill="x", pady=(0, 7))
        self._path_row(transcribe_box, "输入文件", self.audio_path, self._choose_audio, "选择")
        self.drop_frame = tk.Frame(
            transcribe_box, bg="#fafdff", highlightbackground="#9bb8e2",
            highlightcolor="#3975d5", highlightthickness=1, bd=0, cursor="hand2",
        )
        self.drop_frame.pack(fill="x", padx=4, pady=(2, 3))
        self.drop_title = tk.Label(
            self.drop_frame, text="拖放音频或视频到这里",
            bg="#fafdff", fg="#315e9f", font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.drop_title.pack(pady=(4, 0))
        self.drop_hint = tk.Label(
            self.drop_frame, text="也可以点击上方“选择”",
            bg="#fafdff", fg="#65718a", font=("Microsoft YaHei UI", 8),
        )
        self.drop_hint.pack(pady=(0, 4))
        for target in (self.drop_frame, self.drop_title, self.drop_hint):
            target.drop_target_register(DND_FILES)
            target.dnd_bind("<<Drop>>", self._on_audio_drop)
        self._path_row(transcribe_box, "输出文本", self.output_path, self._choose_output, "位置")
        action_row = ttk.Frame(transcribe_box)
        action_row.pack(fill="x", pady=(4, 0))
        self.progress = ttk.Progressbar(action_row, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.transcribe_button = ttk.Button(action_row, text="开始转写", style="Start.TButton", command=self._start_transcription)
        self.transcribe_button.pack(side="right", padx=(8, 0))
        self.stop_button = ttk.Button(
            action_row, text="停止转写", style="Stop.TButton",
            command=self._stop_transcription, state="disabled",
        )
        self.stop_button.pack_forget()
        ttk.Label(transcribe_box, textvariable=self.progress_text, style="Hint.TLabel").pack(anchor="w", pady=(2, 0))

        result_box = ttk.LabelFrame(left, text="📜 转写结果")
        result_box.pack(fill="both", expand=True)
        self.result_text = self._text_area(result_box, height=6)

        summary_box = ttk.LabelFrame(right, text="✨ AI 总结")
        summary_box.pack(fill="both", expand=True)
        self.summary_button = self._path_row(
            summary_box, "总结来源", self.summary_source, self._choose_summary_source, "选择",
            action=self._start_summary, action_text="总结",
        )
        self.summary_text = self._text_area(summary_box, height=10)
        ttk.Label(summary_box, textvariable=self.ai_status, style="Hint.TLabel").pack(anchor="w", pady=(3, 0))

    def _build_settings(self):
        page = self.settings_page
        self._build_page_header(page, "小助手设置", "模型与总结偏好")

        models_box = ttk.LabelFrame(page, text="📦 模型下载")
        models_box.pack(fill="x", pady=(0, 6))
        model_buttons = ttk.Frame(models_box)
        model_buttons.pack(fill="x")
        self.paraformer_button = ttk.Button(model_buttons, text="下载 Paraformer（约 80 MB）", command=self._download_paraformer)
        self.paraformer_button.pack(side="left")
        self.vad_button = ttk.Button(model_buttons, text="下载 Silero VAD", command=self._download_vad)
        self.vad_button.pack(side="left", padx=(6, 0))
        ttk.Label(models_box, textvariable=self.model_status, style="Hint.TLabel", wraplength=800).pack(anchor="w", pady=(4, 2))
        self.download_progress = ttk.Progressbar(models_box, mode="determinate", maximum=100)
        self.download_progress.pack(fill="x", pady=(1, 0))

        ai_box = ttk.LabelFrame(page, text="🔑 DeepSeek 设置")
        ai_box.pack(fill="both", expand=True)
        key_row = ttk.Frame(ai_box)
        key_row.pack(fill="x", pady=(0, 5))
        ttk.Label(key_row, text="API Key").pack(side="left", padx=(0, 8))
        self.key_entry = ttk.Entry(key_row, textvariable=self.api_key, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.save_button = ttk.Button(key_row, text="保存", style="Start.TButton", command=self._save_settings_clicked)
        self.save_button.pack(side="left", padx=(6, 0))
        ttk.Label(ai_box, text="总结提示词").pack(anchor="w", pady=(1, 2))
        self.prompt_text = self._text_area(ai_box, height=6)
        self.prompt_text.insert("1.0", self.prompt)
        ttk.Label(ai_box, text="Key 和提示词保存在本机。", style="Hint.TLabel").pack(anchor="w", pady=(3, 0))

    def _build_page_header(self, page, title, subtitle):
        header = ttk.Frame(page, style="Header.TFrame", padding=(8, 4))
        header.pack(fill="x", pady=(0, 7))
        self._draw_mascot(header)
        labels = ttk.Frame(header, style="Header.TFrame")
        labels.pack(side="left", fill="y", padx=(7, 0))
        ttk.Label(labels, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(labels, text=subtitle, style="Header.Hint.TLabel").pack(anchor="w", pady=(1, 0))

    @staticmethod
    def _draw_mascot(parent):
        mascot = tk.Canvas(parent, width=50, height=44, bg="#eaf0fa", highlightthickness=0)
        mascot.pack(side="left")
        # A tiny smiling headphone character, drawn with Tk's built-in canvas.
        mascot.create_arc(7, 2, 43, 39, start=0, extent=180, style="arc", outline="#3975d5", width=3)
        mascot.create_oval(10, 15, 40, 42, fill="#b9d2f6", outline="#3975d5", width=2)
        mascot.create_rectangle(7, 18, 12, 29, fill="#78bcb0", outline="#3975d5", width=1)
        mascot.create_rectangle(38, 18, 43, 29, fill="#78bcb0", outline="#3975d5", width=1)
        mascot.create_oval(18, 25, 21, 29, fill="#263247", outline="")
        mascot.create_oval(29, 25, 32, 29, fill="#263247", outline="")
        mascot.create_arc(20, 27, 31, 36, start=205, extent=130, style="arc", outline="#315e9f", width=2)
        mascot.create_oval(14, 30, 18, 33, fill="#e6a2a4", outline="")
        mascot.create_oval(33, 30, 37, 33, fill="#e6a2a4", outline="")
        mascot.create_text(44, 7, text="♪", fill="#3975d5", font=("Segoe UI Symbol", 11, "bold"))

    @staticmethod
    def _text_area(parent, height=8):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, height=height, wrap="word", font=("Microsoft YaHei UI", 9), relief="flat", bd=0, padx=8, pady=7, undo=True, bg="#ffffff", fg="#263247", insertbackground="#3975d5", highlightthickness=1, highlightbackground="#d4deef", highlightcolor="#6b95d6")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return text

    @staticmethod
    def _path_row(parent, label, variable, command, button_text, action=None, action_text=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=8).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(row, text=button_text, command=command).pack(side="left", padx=(5, 0))
        action_button = None
        if action is not None:
            action_button = ttk.Button(row, text=action_text, style="Start.TButton", command=action)
            action_button.pack(side="left", padx=(5, 0))
        return action_button

    def _show_page(self, page_name):
        if page_name == "settings" or (getattr(self, "current_page", None) == "settings" and page_name != "settings"):
            try:
                self._save_settings()
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=self.root)
        for page in (self.home_page, self.settings_page, self.about_page):
            page.pack_forget()
        pages = {"home": self.home_page, "settings": self.settings_page, "about": self.about_page}
        self.current_page = page_name
        pages[page_name].pack(fill="both", expand=True)
        selected = {"home": self.home_menu_button, "settings": self.settings_menu_button, "about": self.about_menu_button}[page_name]
        for button in (self.home_menu_button, self.settings_menu_button, self.about_menu_button):
            button.base_bg = "#f5f7fb" if button is selected else "#e9eef7"
            button.configure(bg=button.base_bg)

    def _save_settings_clicked(self):
        try:
            self._save_settings()
        except OSError as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return
        self.ai_status.set("设置已保存")
        messagebox.showinfo("设置已保存", "DeepSeek Key 和提示词已保存在本机。", parent=self.root)

    def _refresh_model_status(self):
        asr_ok = (DEFAULT_MODEL / "model.int8.onnx").is_file() and (DEFAULT_MODEL / "tokens.txt").is_file()
        vad_ok = DEFAULT_VAD.is_file()
        self.model_status.set(f"Paraformer：{'已就绪' if asr_ok else '未下载'}    Silero VAD：{'已就绪' if vad_ok else '未下载'}")

    def _choose_audio(self):
        path = filedialog.askopenfilename(
            title="选择音频或视频文件",
            filetypes=(
                ("音频和视频文件", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.m4v *.mov *.mkv *.avi *.wmv *.webm *.mpeg *.mpg *.ts"),
                ("所有文件", "*.*"),
            ),
        )
        if path:
            self._set_audio_path(Path(path))

    def _on_audio_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            messagebox.showwarning("无法读取拖入文件", "请重新拖入一个音频或视频文件。", parent=self.root)
            return "break"

        if len(paths) != 1:
            messagebox.showinfo("一次拖入一个文件", "当前支持单文件转写，请一次拖入一个音频或视频文件。", parent=self.root)
            return "break"

        path = Path(paths[0]).expanduser()
        if not path.is_file():
            messagebox.showwarning("文件无效", "请拖入一个实际存在的音频或视频文件。", parent=self.root)
            return "break"

        supported = {
            ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
            ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".mpeg", ".mpg", ".ts",
        }
        if path.suffix.lower() not in supported:
            messagebox.showwarning("暂不支持此格式", "请拖入常见音频或视频文件。", parent=self.root)
            return "break"

        self._set_audio_path(path)
        return "copy"

    def _set_audio_path(self, path: Path):
        path = path.expanduser().resolve()
        output_path = path.with_suffix(".txt")
        self.audio_path.set(str(path))
        self.output_path.set(str(output_path))
        self.summary_source.set(str(output_path))
        self.progress_text.set(f"已载入：{path.name}，可以开始转写")

    def _choose_output(self):
        initial = self.output_path.get().strip()
        initial_path = Path(initial) if initial else None
        path = filedialog.asksaveasfilename(
            title="选择转写结果保存位置",
            defaultextension=".txt",
            initialdir=str(initial_path.parent) if initial_path else None,
            initialfile=initial_path.name if initial_path else "转写结果.txt",
            filetypes=(("文本文件", "*.txt"), ("Markdown 文件", "*.md"), ("所有文件", "*.*")),
        )
        if path:
            self.output_path.set(path)

    def _choose_summary_source(self):
        path = filedialog.askopenfilename(title="选择总结来源", filetypes=(("文本和 Markdown", "*.txt *.md"), ("所有文件", "*.*")))
        if path:
            self.summary_source.set(path)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.summary_button, self.paraformer_button, self.vad_button, self.save_button):
            widget.configure(state=state)
        can_stop = busy and self.transcription_cancel_event is not None
        if can_stop:
            self.transcribe_button.pack_forget()
            self.stop_button.configure(state="normal")
            self.stop_button.pack(side="right")
        else:
            self.stop_button.pack_forget()
            self.transcribe_button.configure(state=state)
            self.transcribe_button.pack(side="right", padx=(8, 0))

    def _stop_transcription(self):
        cancel_event = self.transcription_cancel_event
        if not self.busy or cancel_event is None or cancel_event.is_set():
            return
        cancel_event.set()
        self.transcription_phase = "停止中"
        self.stop_button.configure(state="disabled")
        self.progress_text.set("正在停止转写 · 当前识别片段结束后停止")

    def _start_transcription(self):
        if self.busy:
            return
        audio = Path(self.audio_path.get().strip()).expanduser()
        output = Path(self.output_path.get().strip()).expanduser()
        if not audio.is_file():
            messagebox.showwarning("需要选择文件", "请先选择一个有效的音频或视频文件。", parent=self.root)
            return
        if not output.name:
            messagebox.showwarning("需要选择输出位置", "请指定转写结果的保存位置。", parent=self.root)
            return
        if not (DEFAULT_MODEL / "model.int8.onnx").is_file() or not (DEFAULT_MODEL / "tokens.txt").is_file() or not DEFAULT_VAD.is_file():
            messagebox.showerror("模型尚未准备好", "请先到“设置”页面下载 Paraformer 和 Silero VAD 模型。", parent=self.root)
            return
        self.transcription_cancel_event = threading.Event()
        self._set_busy(True)
        self.progress.configure(value=0, mode="indeterminate")
        self.progress.start(12)
        self.transcription_started = time.perf_counter()
        self.transcription_phase = "读取音频"
        self.vad_percent = 0
        self.recognition_done = 0
        self.recognition_total = 0
        self.progress_text.set("读取音频 · 已用 00:00 · 总时长计算中")
        threading.Thread(
            target=self._transcription_worker,
            args=(audio.resolve(), output.resolve(), self.transcription_cancel_event),
            daemon=True,
        ).start()

    def _transcription_worker(self, audio_path, output_path, cancel_event):
        started = time.perf_counter()
        try:
            rows = transcribe(
                audio_path, DEFAULT_MODEL, DEFAULT_VAD, 4,
                lambda *event: self.events.put(event), cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise TranscriptionCancelled("转写已停止")
            if not rows:
                self.events.put(("transcribe_empty",))
                return
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("\n\n".join(f"{timestamp(start)}\n{text}" for start, text in rows) + "\n", encoding="utf-8")
            self.events.put(("transcribe_done", str(output_path), len(rows), time.perf_counter() - started))
        except TranscriptionCancelled:
            self.events.put(("transcribe_cancelled",))
        except Exception as error:
            self.events.put(("task_error", "转写失败", str(error)))

    def _start_summary(self):
        if self.busy:
            return
        try:
            self._save_settings()
        except OSError as error:
            messagebox.showerror("设置保存失败", str(error), parent=self.root)
            return
        key = self.api_key.get().strip()
        if not key:
            messagebox.showwarning("需要 API Key", "请先到“设置”页面填写 DeepSeek API Key。", parent=self.root)
            return
        source = self.summary_source.get().strip()
        if source:
            source_path = Path(source).expanduser()
            if not source_path.is_file():
                messagebox.showwarning("找不到总结来源", "请选择已有的 TXT 或 Markdown 文件。", parent=self.root)
                return
            try:
                content = source_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as error:
                messagebox.showerror("读取失败", str(error), parent=self.root)
                return
        else:
            content = self.result_text.get("1.0", "end-1c").strip()
        if not content.strip():
            messagebox.showwarning("没有可总结的内容", "请先转写音频或视频，或选择一个 TXT/Markdown 文件。", parent=self.root)
            return
        self._set_busy(True)
        self.ai_status.set("正在请求 DeepSeek…")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", "正在生成总结，请稍候…")
        threading.Thread(target=self._summary_worker, args=(key, self.prompt_text.get("1.0", "end-1c").strip() or DEFAULT_PROMPT, content), daemon=True).start()

    def _summary_worker(self, key, prompt, content):
        try:
            body = json.dumps({
                "model": "deepseek-flash",
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}],
                "stream": False,
            }).encode("utf-8")
            request = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
            summary = result["choices"][0]["message"]["content"]
            self.events.put(("summary_done", summary))
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")
            except OSError:
                detail = str(error)
            self.events.put(("task_error", "DeepSeek 请求失败", detail))
        except Exception as error:
            self.events.put(("task_error", "AI 总结失败", str(error)))

    def _download_paraformer(self):
        self._start_download("Paraformer", PARAFORMER_MIRROR, DEFAULT_MODEL, "paraformer")

    def _download_vad(self):
        self._start_download("Silero VAD", VAD_URL, DEFAULT_VAD, "file")

    def _start_download(self, name, url, destination, kind):
        if self.busy:
            return
        self._set_busy(True)
        self.download_progress.configure(value=0)
        self.model_status.set(f"正在直连下载 {name}…")
        threading.Thread(target=self._download_worker, args=(name, url, destination, kind), daemon=True).start()

    def _download_worker(self, name, url, destination, kind):
        try:
            root_models = APP_DIR / "models"
            root_models.mkdir(parents=True, exist_ok=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if kind == "paraformer":
                destination.mkdir(parents=True, exist_ok=True)
                for index, filename in enumerate(PARAFORMER_FILES, 1):
                    target = destination / filename
                    if target.is_file() and target.stat().st_size:
                        continue
                    temp_path = root_models / f"{filename}.download"
                    self._download_file(name, f"{url}{filename}", temp_path, f"文件 {index}/2：{filename}")
                    temp_path.replace(target)
            else:
                temp_path = root_models / "silero_vad.download"
                if not (destination.is_file() and destination.stat().st_size):
                    self._download_file(name, url, temp_path, "silero_vad.onnx")
                    temp_path.replace(destination)
            self.events.put(("download_done", name))
        except Exception as error:
            self.events.put(("task_error", f"{name} 下载失败", str(error)))

    def _download_file(self, name, url, destination, label):
        request = urllib.request.Request(url, headers={"User-Agent": "Audio-To-Txt/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            self.events.put(("download_progress", name, label, downloaded, total))
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                self.events.put(("download_progress", name, label, downloaded, total))

    def _process_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind in ("读取音频", "语音检测", "识别语音"):
                self._show_transcribe_progress(event)
            elif kind == "transcribe_done":
                _, output, count, seconds = event
                self._task_finished()
                self.progress.configure(value=100)
                duration = self._format_clock(seconds)
                self.progress_text.set(f"完成 {count} 段 · 已用 {duration} / 总计 {duration}")
                self.output_path.set(output)
                self.summary_source.set(output)
                self.result_text.delete("1.0", "end")
                self.result_text.insert("1.0", Path(output).read_text(encoding="utf-8"))
            elif kind == "transcribe_empty":
                self._task_finished()
                self.progress_text.set("没有检测到语音 · 可以换一个音频或视频文件再试试")
                messagebox.showinfo("转写完成", "没有检测到可识别的语音。", parent=self.root)
            elif kind == "transcribe_cancelled":
                self._task_finished()
                self.progress.configure(value=0)
                self.progress_text.set("转写已停止 · 本次没有保存结果")
            elif kind == "summary_done":
                self._task_finished()
                self.summary_text.delete("1.0", "end")
                self.summary_text.insert("1.0", event[1])
                self.ai_status.set("总结完成")
            elif kind == "download_progress":
                _, name, label, done, total = event
                if total:
                    percent = done / total * 100
                    self.download_progress.configure(value=percent)
                    self.model_status.set(f"正在下载 {name}（{label}）：{percent:.1f}%（{self._format_size(done)} / {self._format_size(total)}）")
                else:
                    self.model_status.set(f"正在下载 {name}（{label}）：{self._format_size(done)}")
            elif kind == "download_done":
                self._task_finished()
                self.download_progress.configure(value=100)
                self._refresh_model_status()
                messagebox.showinfo("模型已就绪", f"{event[1]} 下载完成。", parent=self.root)
            elif kind == "task_error":
                self._task_finished()
                _, title, detail = event
                if title.endswith("下载失败"):
                    self._refresh_model_status()
                elif title == "DeepSeek 请求失败":
                    self.ai_status.set("总结失败")
                else:
                    self.progress_text.set(title)
                messagebox.showerror(title, detail, parent=self.root)
        self._refresh_transcription_clock()
        self.root.after(100, self._process_events)

    def _show_transcribe_progress(self, event):
        phase, done, total = event
        if self.transcription_cancel_event is not None and self.transcription_cancel_event.is_set():
            self.transcription_phase = "停止中"
            self.progress_text.set("正在停止转写 · 当前识别片段结束后停止")
            return
        self.transcription_phase = phase
        elapsed = time.perf_counter() - self.transcription_started
        if phase == "读取音频":
            self.progress_text.set(f"读取音频 · 已用 {self._format_clock(elapsed)} · 总时长计算中")
            return
        if phase == "语音检测":
            if self.progress["mode"] == "indeterminate":
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
            percent = done / total * 100 if total else 0
            self.vad_percent = percent
            self.progress.configure(value=percent * 0.3)
            self.progress_text.set(f"语音检测 {percent:.0f}% · 已用 {self._format_clock(elapsed)} · 总时长计算中")
            return
        if phase == "识别语音" and total:
            if done == 0:
                self.recognition_started = time.perf_counter()
            self.recognition_done = done
            self.recognition_total = total
            self.progress.configure(value=30 + done / total * 70)
            remaining = None
            if done:
                recognition_elapsed = time.perf_counter() - self.recognition_started
                remaining = recognition_elapsed / done * (total - done)
            percent = done / total * 100
            if remaining is None:
                self.progress_text.set(f"转写 {done}/{total}（{percent:.0f}%）· 已用 {self._format_clock(elapsed)} · 总时长计算中")
            else:
                expected = elapsed + remaining
                self.progress_text.set(
                    f"转写 {done}/{total}（{percent:.0f}%）· 已用 {self._format_clock(elapsed)} "
                    f"/ 预计 {self._format_clock(expected)} · 剩余 {self._format_clock(remaining)}"
                )

    def _refresh_transcription_clock(self):
        phase = getattr(self, "transcription_phase", None)
        if not self.busy or phase is None:
            return
        elapsed = time.perf_counter() - self.transcription_started
        if phase == "读取音频":
            self.progress_text.set(f"读取音频 · 已用 {self._format_clock(elapsed)} · 总时长计算中")
        elif phase == "语音检测":
            self.progress_text.set(f"语音检测 {self.vad_percent:.0f}% · 已用 {self._format_clock(elapsed)} · 总时长计算中")
        elif phase == "识别语音" and self.recognition_total:
            done = self.recognition_done
            total = self.recognition_total
            percent = done / total * 100
            if done and getattr(self, "recognition_started", None):
                recognition_elapsed = time.perf_counter() - self.recognition_started
                remaining = recognition_elapsed / done * (total - done)
                self.progress_text.set(
                    f"转写 {done}/{total}（{percent:.0f}%）· 已用 {self._format_clock(elapsed)} "
                    f"/ 预计 {self._format_clock(elapsed + remaining)} · 剩余 {self._format_clock(remaining)}"
                )

    def _task_finished(self):
        self.transcription_cancel_event = None
        self._set_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.transcription_phase = None

    @staticmethod
    def _format_clock(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_size(size: int) -> str:
        return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def main():
    root = TkinterDnD.Tk()
    TranscribeWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()

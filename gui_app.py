#!/usr/bin/env python3
"""Native Windows desktop app (Tkinter, stdlib only) for browsing and
deleting Claude Code session history.

Left: project/session tree, with a sort mode (last modified / name / size).
Right: selected session, one row per turn, response on the left / prompt
on the right. Delete removes the .jsonl file from disk directly (with
confirmation) -- no server involved, this process owns the filesystem
call itself.

Image content blocks are previewed if Pillow is installed; otherwise
they fall back to a placeholder label (Pillow is optional, not required
to run the app).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, font as tkfont

from server import (
    PROJECTS_DIR,
    read_jsonl,
    project_display_name,
    session_cwd,
    session_cwd_counts,
    normalize_cwd,
    session_title,
    group_into_turns,
    collapse_blank_lines,
    tokenize_inline,
)

MONOKAI = {
    "bg": "#272822",
    "bg_alt": "#1e1f1c",
    "fg": "#f8f8f2",
    "prompt_bg": "#3e3d32",
    "resp_bg": "#2d2e27",
    "border": "#49483e",
    "blue": "#66d9ef",
    "green": "#a6e22e",
    "orange": "#fd971f",
    "pink": "#f92672",
    "yellow": "#e6db74",
    "muted": "#75715e",
}
M = MONOKAI

TOOL_TEXT_CAP = 3000
SORT_MODES = (("mtime", "Last modified"), ("name", "Name"), ("size", "Size"))


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable frame (canvas + inner frame + scrollbar)."""

    def __init__(self, parent, on_width_change=None, on_near_bottom=None, on_ctrl_wheel=None, **kw):
        super().__init__(parent, **kw)
        self.on_width_change = on_width_change
        self.on_near_bottom = on_near_bottom
        self.on_ctrl_wheel = on_ctrl_wheel
        self.canvas = tk.Canvas(self, bg=M["bg"], highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=M["bg"])
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        # bound globally (not just on the canvas) so wheel/paging scrolls
        # the conversation regardless of which widget in the window --
        # tree, a bubble, an entry box -- currently has focus/is under
        # the mouse; <Control-MouseWheel> is more specific than the plain
        # <MouseWheel> binding below so it wins whenever Ctrl is held
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.canvas.bind_all("<Prior>", self._on_page_up)
        self.canvas.bind_all("<Next>", self._on_page_down)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        if self.on_width_change is not None:
            self.on_width_change(event.width)

    def _on_yscroll(self, first, last):
        self.vsb.set(first, last)
        if self.on_near_bottom is not None and float(last) > 0.85:
            self.on_near_bottom()

    def _check_near_bottom(self):
        if self.on_near_bottom is not None and float(self.vsb.get()[1]) > 0.85:
            self.on_near_bottom()

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._check_near_bottom()

    def _on_ctrl_wheel(self, event):
        if self.on_ctrl_wheel is not None:
            self.on_ctrl_wheel(1 if event.delta > 0 else -1)
        return "break"

    def _on_page_up(self, event):
        self.canvas.yview_scroll(-1, "pages")
        return "break"

    def _on_page_down(self, event):
        self.canvas.yview_scroll(1, "pages")
        self._check_near_bottom()
        return "break"

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)

    def scroll_to(self, widget, margin=40):
        """Scroll so `widget` (anywhere inside self.inner, possibly nested
        in sub-frames) is visible near the top of the viewport."""
        self.canvas.update_idletasks()
        y = widget.winfo_rooty() - self.inner.winfo_rooty()
        bbox = self.canvas.bbox("all")
        total = (bbox[3] - bbox[1]) if bbox else 1
        frac = max(0, y - margin) / max(total, 1)
        self.canvas.yview_moveto(frac)


def make_readonly_text(parent, bg, fg, wrap="word"):
    t = tk.Text(
        parent,
        bg=bg,
        fg=fg,
        insertbackground=fg,
        relief="flat",
        wrap=wrap,
        padx=10,
        pady=8,
        highlightthickness=1,
        highlightbackground=M["border"],
        highlightcolor=M["border"],
        borderwidth=0,
        font=_md_fonts()["body"],
    )
    return t


# --- lightweight markdown rendering (headers, lists, code fences, quotes,
# plus inline bold/italic/code/strike) using Tk text tags -------------------
#
# All fonts below are shared named tkfont.Font objects (not literal
# ("family", size) tuples) so that Zoom In/Out (see App.set_zoom) can
# rescale every bubble on screen just by reconfiguring these fonts' size
# in place -- every widget referencing them redraws automatically.

_MD_FONTS = {}
_MD_FONT_BASE_SIZES = {}
_ZOOM_SCALE = [1.0]


def _md_fonts():
    if not _MD_FONTS:
        specs = {
            "body": dict(family="Segoe UI", size=10),
            "bold": dict(family="Segoe UI", size=10, weight="bold"),
            "italic": dict(family="Segoe UI", size=10, slant="italic"),
            "mono": dict(family="Consolas", size=9),
            "mono_bold": dict(family="Consolas", size=9, weight="bold"),
            "code": dict(family="Consolas", size=9),
        }
        for lvl, size in ((1, 15), (2, 14), (3, 13), (4, 12), (5, 11), (6, 10)):
            specs[f"h{lvl}"] = dict(family="Segoe UI", size=size, weight="bold")
        for key, spec in specs.items():
            _MD_FONT_BASE_SIZES[key] = spec["size"]
            _MD_FONTS[key] = tkfont.Font(**spec)
    return _MD_FONTS


def set_zoom_scale(scale):
    """Rescale every shared conversation font in place. Existing widgets
    that were built with these font objects re-render immediately; only
    their wrapped line count (and thus widget height) needs recomputing
    afterwards -- see App._apply_zoom."""
    _ZOOM_SCALE[0] = scale
    fonts = _md_fonts()
    for key, font in fonts.items():
        base = _MD_FONT_BASE_SIZES[key]
        font.configure(size=max(6, round(base * scale)))


def _configure_markdown_tags(t):
    fonts = _md_fonts()
    t.tag_configure("md_bold", font=fonts["bold"])
    t.tag_configure("md_italic", font=fonts["italic"])
    t.tag_configure("md_code", font=fonts["code"], background=M["bg_alt"], foreground=M["yellow"])
    t.tag_configure("md_strike", overstrike=1)
    t.tag_configure("md_link", foreground=M["blue"], underline=1)
    for lvl in range(1, 7):
        t.tag_configure(f"md_h{lvl}", font=fonts[f"h{lvl}"], spacing3=4)
    t.tag_configure("md_bullet", lmargin1=14, lmargin2=28)
    t.tag_configure("md_quote", foreground=M["muted"], lmargin1=14, lmargin2=14)


_FENCE_LINE_RE = re.compile(r"^\s*```")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUM_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_BLOCK_START_RE = re.compile(r"^\s*(#{1,6}\s|```|>|[-*+]\s|\d+[.)]\s)")


def _insert_inline(t, line, extra_tags=()):
    for kind, content in tokenize_inline(line):
        if kind is None:
            t.insert("end", content, extra_tags)
        elif kind == "code":
            t.insert("end", content, extra_tags + ("md_code",))
        elif kind == "bold":
            t.insert("end", content, extra_tags + ("md_bold",))
        elif kind == "italic":
            t.insert("end", content, extra_tags + ("md_italic",))
        elif kind == "strike":
            t.insert("end", content, extra_tags + ("md_strike",))
        elif kind == "link":
            label, _url = content
            t.insert("end", label, extra_tags + ("md_link",))


def insert_markdown(t, raw_text, base_tag=None):
    """Insert markdown-formatted text (headers, lists, code fences, quotes,
    inline bold/italic/code/strike) into a Tk Text widget using tags."""
    _configure_markdown_tags(t)
    base = (base_tag,) if base_tag else ()
    lines = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i, n = 0, len(lines)
    first_block = True

    def new_block():
        nonlocal first_block
        if not first_block:
            t.insert("end", "\n")
        first_block = False

    while i < n:
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        if _FENCE_LINE_RE.match(line):
            new_block()
            i += 1
            code_lines = []
            while i < n and not _FENCE_LINE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1
            t.insert("end", "\n".join(code_lines), base + ("md_code",))
            continue
        h = _HEADER_RE.match(line)
        if h:
            new_block()
            level = len(h.group(1))
            t.insert("end", h.group(2), base + (f"md_h{level}",))
            i += 1
            continue
        if _QUOTE_RE.match(line):
            new_block()
            quote_lines = []
            while i < n and _QUOTE_RE.match(lines[i]):
                quote_lines.append(_QUOTE_RE.sub("", lines[i], count=1))
                i += 1
            for qi, ql in enumerate(quote_lines):
                if qi:
                    t.insert("end", "\n")
                _insert_inline(t, ql, base + ("md_quote",))
            continue
        bullet = _BULLET_RE.match(line)
        num = _NUM_RE.match(line)
        if bullet or num:
            new_block()
            prefix = "• " if bullet else f"{num.group(1)}. "
            content = bullet.group(1) if bullet else num.group(2)
            t.insert("end", prefix, base + ("md_bullet",))
            _insert_inline(t, content, base + ("md_bullet",))
            i += 1
            continue
        new_block()
        para = [line]
        i += 1
        while i < n and lines[i].strip() != "" and not _BLOCK_START_RE.match(lines[i]):
            para.append(lines[i])
            i += 1
        for pi, pl in enumerate(para):
            if pi:
                t.insert("end", "\n")
            _insert_inline(t, pl, base)


def fit_height(text_widget, min_lines=1, max_lines=200):
    """Size a Text widget to fit its content. `height` is in units of the
    widget's *default* font line height, so a widget mixing font sizes
    (e.g. markdown headers, which are taller than body text) needs its
    pixel height converted back to those units -- counting displaylines
    alone undercounts and clips the taller line's extra height.

    Does NOT call update_idletasks() itself -- that flushes the WHOLE
    application's pending geometry work, not just this widget, so calling
    it once per widget while sizing dozens/hundreds of bubbles (rendering
    a session, a resize, a zoom) dominates render time (measured ~2.4x
    slower on a 190-turn session). Callers must call update_idletasks()
    themselves ONCE before fitting a batch of widgets."""
    try:
        ypixels = int(text_widget.count("1.0", "end", "ypixels")[0])
        linespace = tkfont.Font(font=text_widget.cget("font")).metrics("linespace")
        lines = -(-ypixels // linespace)  # ceil division
    except Exception:
        try:
            lines = int(text_widget.count("1.0", "end", "displaylines")[0])
        except Exception:
            lines = int(text_widget.index("end-1c").split(".")[0])
    lines = max(min_lines, min(lines, max_lines))
    text_widget.configure(height=lines)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Code History")
        self.geometry("1400x860")
        self.configure(bg=M["bg"])

        self._setup_style()

        self.item_path = {}   # tree item id -> (folder_name, Path)
        self.item_kind = {}   # tree item id -> "project" | "session"
        self.current_path = None
        self.current_folder = None
        self.current_cwd = None
        # session file path (str) -> user-picked replacement working dir,
        # for sessions whose recorded cwd has since been moved/renamed.
        # Remembered for this app run only.
        self._cwd_overrides = {}

        # avg pixel width of the body font, used to convert the desired
        # pixel width (80% of the pane) into a Text widget's char-based width
        self._char_px = tkfont.Font(family="Segoe UI", size=10).measure("0")
        self.bubble_chars = 90  # replaced by the first canvas <Configure> event
        self._resize_after_id = None

        # lazy-loading state for the currently open session
        self._turns = []           # all turns parsed for current session
        self._rendered_count = 0   # how many turns are actually built as widgets
        self._bubble_texts = []    # every tk.Text bubble currently on screen
        self._image_refs = []      # PhotoImage refs for the current session (avoid GC)
        self._load_more_pending = False
        self._rendering_batch = False
        self._render_generation = 0
        self.PAGE_SIZE = 8

        self.zoom_scale = 1.0  # 1.0 == 100%; persists across sessions until changed

        self._build_layout()
        self.populate_tree()

    # ------------------------------------------------------------------ zoom
    ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 2.2, 0.1

    def zoom_in(self, event=None):
        self._set_zoom(self.zoom_scale + self.ZOOM_STEP)
        return "break"

    def zoom_out(self, event=None):
        self._set_zoom(self.zoom_scale - self.ZOOM_STEP)
        return "break"

    def zoom_reset(self, event=None):
        self._set_zoom(1.0)
        return "break"

    def _set_zoom(self, scale):
        scale = round(min(self.ZOOM_MAX, max(self.ZOOM_MIN, scale)), 2)
        if scale == self.zoom_scale:
            return
        self.zoom_scale = scale
        set_zoom_scale(scale)
        self.zoom_label_var.set(f"{round(scale * 100)}%")
        # font sizes changed in place, so every on-screen bubble needs its
        # wrapped line count (and thus widget height) recomputed
        self._refit_bubbles_idle(0)

    # ------------------------------------------------------------- resizing
    def _on_canvas_width_change(self, width_px):
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(250, lambda: self._apply_bubble_width(width_px))

    def _apply_bubble_width(self, width_px):
        self._resize_after_id = None
        chars = max(20, int(width_px * 0.8 / self._char_px))
        if chars == self.bubble_chars or not self._bubble_texts:
            self.bubble_chars = chars
            return
        self.bubble_chars = chars
        # cheap path: reconfigure existing bubbles in place instead of
        # re-reading the file and rebuilding every widget from scratch
        for t in self._bubble_texts:
            try:
                t.configure(width=chars)
            except tk.TclError:
                pass
        self._refit_bubbles_idle(0)

    def _refit_bubbles_idle(self, i):
        # re-wrap in small batches, one real timer tick apart (not
        # after_idle -- see _continue_background_render for why that starves
        # Windows' repaint handling under real interaction), so a long
        # session doesn't freeze the UI for the whole duration of a resize.
        # One update_idletasks() flush per chunk instead of one per widget
        # (fit_height no longer does this itself -- see its docstring).
        CHUNK = 25
        end = min(i + CHUNK, len(self._bubble_texts))
        self.update_idletasks()
        for t in self._bubble_texts[i:end]:
            try:
                fit_height(t, max_lines=40 if str(t.cget("wrap")) == "none" else 200)
            except tk.TclError:
                pass
        if end < len(self._bubble_texts):
            self.after(1, lambda: self._refit_bubbles_idle(end))

    # ---------------------------------------------------------------- style
    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=M["bg_alt"],
            fieldbackground=M["bg_alt"],
            foreground=M["fg"],
            borderwidth=0,
            rowheight=26,
        )
        style.map("Treeview", background=[("selected", M["prompt_bg"])])
        style.configure(
            "Vertical.TScrollbar", background=M["bg_alt"], troughcolor=M["bg"]
        )
        style.configure("TFrame", background=M["bg"])
        style.configure("TButton", padding=6)
        style.configure(
            "Danger.TButton", foreground="#272822", background=M["pink"]
        )
        style.map("Danger.TButton", background=[("active", "#ff5c93")])
        style.configure("TEntry", fieldbackground=M["bg_alt"], foreground=M["fg"])

    # --------------------------------------------------------------- layout
    def _build_layout(self):
        # plain tk.PanedWindow (not ttk) so the sash is a visible, easily
        # grabbable drag handle for resizing the left panel
        paned = tk.PanedWindow(
            self,
            orient="horizontal",
            bg=M["border"],
            sashwidth=6,
            sashrelief="flat",
            bd=0,
            opaqueresize=True,
        )
        paned.pack(fill="both", expand=True)

        # --- left: search + tree ---
        left = tk.Frame(paned, bg=M["bg"])
        paned.add(left, width=380, minsize=220, stretch="never")

        search_row = ttk.Frame(left)
        search_row.pack(fill="x", padx=6, pady=6)
        self.search_var = tk.StringVar()
        entry = tk.Entry(
            search_row,
            textvariable=self.search_var,
            bg=M["bg_alt"],
            fg=M["fg"],
            insertbackground=M["fg"],
            relief="flat",
        )
        entry.pack(fill="x", ipady=4)

        sort_row = ttk.Frame(left)
        sort_row.pack(fill="x", padx=6, pady=(0, 6))
        tk.Label(
            sort_row, text="Sort:", bg=M["bg"], fg=M["muted"], font=("Segoe UI", 9)
        ).pack(side="left")
        self._sort_labels = [label for _key, label in SORT_MODES]
        self._sort_label_to_key = {label: key for key, label in SORT_MODES}
        self.sort_var = tk.StringVar(value=self._sort_labels[0])
        sort_combo = ttk.Combobox(
            sort_row,
            textvariable=self.sort_var,
            state="readonly",
            width=16,
            values=self._sort_labels,
        )
        sort_combo.pack(side="left", padx=(6, 0))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self.populate_tree())

        refresh_btn = ttk.Button(
            sort_row, text="⟳ Refresh", command=self.refresh_all, width=10
        )
        refresh_btn.pack(side="right")

        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # placeholder text is set directly (not via the variable) so it
        # can't trigger a filter/populate before the tree above exists
        self._add_placeholder(entry, "Filter sessions…")
        self.search_var.trace_add("write", lambda *a: self.populate_tree())

        # --- right: toolbar + scrollable conversation ---
        right = tk.Frame(paned, bg=M["bg"])
        paned.add(right, minsize=500, stretch="always")

        toolbar = tk.Frame(right, bg=M["bg"])
        toolbar.pack(fill="x", padx=14, pady=(12, 4))
        self.title_var = tk.StringVar(value="Select a session")
        title_lbl = tk.Label(
            toolbar,
            textvariable=self.title_var,
            bg=M["bg"],
            fg=M["fg"],
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        )
        title_lbl.pack(side="left", fill="x", expand=True)
        self.delete_btn = ttk.Button(
            toolbar,
            text="🗑 Delete session",
            style="Danger.TButton",
            command=self.delete_current,
            state="disabled",
        )
        self.delete_btn.pack(side="right")
        ttk.Button(
            toolbar, text="🔎 Find", command=self.open_find
        ).pack(side="right", padx=(0, 6))
        self.claude_btn = ttk.Button(
            toolbar,
            text="▶ Resume in CLI",
            command=self.open_claude_cli,
            state="disabled",
        )
        self.claude_btn.pack(side="right", padx=(0, 6))
        self.relocate_btn = ttk.Button(
            toolbar,
            text="⇄ Relocate…",
            command=self.relocate_project,
            state="disabled",
        )
        self.relocate_btn.pack(side="right", padx=(0, 6))

        zoom_frame = tk.Frame(toolbar, bg=M["bg"])
        zoom_frame.pack(side="right", padx=(0, 12))
        ttk.Button(zoom_frame, text="－", width=3, command=self.zoom_out).pack(side="left")
        self.zoom_label_var = tk.StringVar(value="100%")
        zoom_lbl = tk.Label(
            zoom_frame,
            textvariable=self.zoom_label_var,
            bg=M["bg"],
            fg=M["muted"],
            font=("Segoe UI", 9),
            width=5,
            anchor="center",
            cursor="hand2",
        )
        zoom_lbl.pack(side="left", padx=2)
        zoom_lbl.bind("<Button-1>", self.zoom_reset)
        ttk.Button(zoom_frame, text="＋", width=3, command=self.zoom_in).pack(side="left")

        self._toolbar = toolbar

        self.meta_var = tk.StringVar(value="")
        tk.Label(
            right,
            textvariable=self.meta_var,
            bg=M["bg"],
            fg=M["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", padx=14)

        self._build_find_bar(right)

        self.scroll = ScrollableFrame(
            right,
            on_width_change=self._on_canvas_width_change,
            on_near_bottom=self._maybe_load_more,
            on_ctrl_wheel=lambda direction: self.zoom_in() if direction > 0 else self.zoom_out(),
        )
        self.scroll.pack(fill="both", expand=True, padx=6, pady=6)
        self.scroll.inner.columnconfigure(0, weight=1)

        self.bind_all("<Control-f>", self.open_find)
        self.bind_all("<Control-equal>", self.zoom_in)   # Ctrl+= (no shift needed)
        self.bind_all("<Control-plus>", self.zoom_in)    # Ctrl+Shift+= on some layouts
        self.bind_all("<Control-KP_Add>", self.zoom_in)  # numpad +
        self.bind_all("<Control-minus>", self.zoom_out)
        self.bind_all("<Control-KP_Subtract>", self.zoom_out)
        self.bind_all("<Control-0>", self.zoom_reset)
        self.bind_all("<Control-KP_0>", self.zoom_reset)

    def _build_find_bar(self, right):
        """A find-in-session bar, hidden until Ctrl+F / the Find button is
        used. Matches every Text widget currently on screen; opening it
        forces the rest of a lazily-loaded session to render first so
        search covers the whole conversation, not just what's scrolled
        into view."""
        self.find_frame = tk.Frame(right, bg=M["bg_alt"])
        inner = tk.Frame(self.find_frame, bg=M["bg_alt"])
        inner.pack(fill="x", padx=14, pady=6)

        tk.Label(inner, text="Find:", bg=M["bg_alt"], fg=M["fg"]).pack(side="left")
        self.find_var = tk.StringVar()
        self.find_entry = tk.Entry(
            inner,
            textvariable=self.find_var,
            bg=M["bg"],
            fg=M["fg"],
            insertbackground=M["fg"],
            relief="flat",
        )
        self.find_entry.pack(side="left", fill="x", expand=True, ipady=3, padx=(6, 6))

        self.find_status_var = tk.StringVar(value="")
        tk.Label(
            inner, textvariable=self.find_status_var, bg=M["bg_alt"], fg=M["muted"],
            font=("Segoe UI", 9), width=8,
        ).pack(side="left")

        ttk.Button(inner, text="▲", width=3, command=self.find_prev).pack(side="left")
        ttk.Button(inner, text="▼", width=3, command=self.find_next).pack(
            side="left", padx=(2, 6)
        )
        ttk.Button(inner, text="✕", width=3, command=self.close_find).pack(side="left")

        self.find_var.trace_add("write", lambda *a: self._schedule_find())
        self.find_entry.bind("<Return>", lambda e: self.find_next())
        self.find_entry.bind("<Shift-Return>", lambda e: self.find_prev())
        self.find_entry.bind("<Escape>", lambda e: self.close_find())

        self._find_matches = []   # list of (widget, start_index, end_index)
        self._find_current = -1
        self._find_after_id = None

    def _add_placeholder(self, entry, text):
        entry.insert(0, text)
        entry.config(fg=M["muted"])

        def on_focus_in(_):
            if entry.get() == text:
                entry.delete(0, "end")
                entry.config(fg=M["fg"])

        def on_focus_out(_):
            if not entry.get():
                entry.insert(0, text)
                entry.config(fg=M["muted"])

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    # ----------------------------------------------------------------- tree
    def populate_tree(self):
        query = self.search_var.get().strip().lower()
        if query == "filter sessions…":
            query = ""
        self.tree.delete(*self.tree.get_children())
        self.item_path.clear()
        self.item_kind.clear()

        if not PROJECTS_DIR.exists():
            return
        sort_mode = self._sort_label_to_key.get(self.sort_var.get(), "mtime")

        folder_entries = []
        for folder in PROJECTS_DIR.iterdir():
            if not folder.is_dir():
                continue
            files = list(folder.glob("*.jsonl"))
            if not files:
                continue
            newest = max(files, key=lambda f: f.stat().st_mtime)
            display_name = project_display_name(folder.name, read_jsonl(newest))
            folder_entries.append((folder, files, display_name))

        def folder_key(entry):
            folder, files, display_name = entry
            if sort_mode == "name":
                return display_name.lower()
            if sort_mode == "size":
                return -sum(f.stat().st_size for f in files)
            return -max(f.stat().st_mtime for f in files)

        folder_entries.sort(key=folder_key)

        def session_key(entry):
            f, title = entry
            if sort_mode == "name":
                return title.lower()
            if sort_mode == "size":
                return -f.stat().st_size
            return -f.stat().st_mtime

        for folder, files, display_name in folder_entries:
            session_rows = []
            for f in files:
                records = read_jsonl(f)
                title = session_title(records)
                if not any(r.get("type") in ("user", "assistant") for r in records):
                    continue
                if query and query not in title.lower() and query not in display_name.lower():
                    continue
                session_rows.append((f, title))

            if not session_rows:
                continue
            session_rows.sort(key=session_key)
            proj_id = self.tree.insert(
                "", "end", text=f"📁 {display_name}  ({len(session_rows)})", open=bool(query)
            )
            self.item_kind[proj_id] = "project"
            for f, title in session_rows:
                sid = self.tree.insert(proj_id, "end", text=f"  {title}")
                self.item_kind[sid] = "session"
                self.item_path[sid] = (folder.name, f)

    # --------------------------------------------------------------- select
    def on_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        if self.item_kind.get(item) != "session":
            self.delete_btn.configure(state="disabled")
            self.claude_btn.configure(state="disabled")
            self.relocate_btn.configure(state="disabled")
            return
        folder_name, path = self.item_path[item]
        self.current_folder = folder_name
        self.current_path = path
        self.render_session(path)
        self.delete_btn.configure(state="normal")
        self.relocate_btn.configure(state="normal")

    # ------------------------------------------------------------ claude cli
    @staticmethod
    def _ensure_chrome_running():
        """`claude --chrome` attaches to an already-running Chrome; it never
        launches one. Start a single Chrome window if none is open, so the
        integration has something to connect to. No-op when Chrome is
        already running (avoids stacking a new window on every launch)."""
        try:
            out = subprocess.run(
                ["tasklist", "/fi", "imagename eq chrome.exe", "/nh"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            if "chrome.exe" not in out:
                subprocess.Popen(["cmd", "/c", "start", "", "chrome"])
        except Exception:
            pass

    def open_claude_cli(self):
        """Open a terminal that resumes this session with
        `claude --resume <session-id>`, run in the session's own working
        directory (that's where Claude Code looks up the conversation). The
        session id is the .jsonl file's name. Prefers Windows Terminal
        (`wt`); falls back to a bare console window if it isn't installed.
        Also makes sure one Chrome window is open for `--chrome` to use."""
        cwd = self.current_cwd
        if not cwd:
            messagebox.showwarning(
                "Resume in CLI",
                "This session didn't record a working directory.",
            )
            return
        if not os.path.isdir(cwd):
            if not messagebox.askyesno(
                "Resume in CLI",
                f"This session's recorded folder no longer exists:\n\n{cwd}\n\n"
                "Pick its new location?",
            ):
                return
            picked = filedialog.askdirectory(
                title="Select this session's working directory", mustexist=True
            )
            if not picked:
                return
            cwd = normalize_cwd(os.path.normpath(picked))
            self._cwd_overrides[str(self.current_path)] = cwd
            self.current_cwd = cwd
        session_id = self.current_path.stem
        self._ensure_chrome_running()
        # `cmd /k` keeps the window open after claude exits so any final
        # output (or a "no conversation found" error) stays readable.
        # --chrome: enable the Claude in Chrome integration for the session.
        # --remote-control: make the resumed session controllable from other
        # devices/sessions.
        cli = ["claude", "--resume", session_id, "--chrome", "--remote-control"]
        try:
            subprocess.Popen(
                ["wt", "-d", cwd, "cmd", "/k", *cli],
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return
        except FileNotFoundError:
            pass
        except Exception as e:
            messagebox.showerror("Resume in CLI", str(e))
            return
        try:
            subprocess.Popen(
                ["cmd", "/k", *cli],
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as e:
            messagebox.showerror("Resume in CLI", f"Couldn't launch a terminal:\n{e}")

    # -------------------------------------------------------------- relocate
    def relocate_project(self):
        """Re-point every session in this project's history folder at a new
        directory on disk by rewriting the `cwd` field wherever it still
        names an old path. The history folder itself is left as-is -- its
        name is just a label (these get renamed by hand over a project's
        life and can't be derived from `cwd` reliably), and Claude Code
        reads a folder's sessions regardless of the per-record cwd. A full
        copy of the folder is saved under ~/.claude/history_viewer_backups/
        first; tool outputs and file paths inside the sessions are left
        untouched."""
        if not self.current_path:
            return
        folder_path = PROJECTS_DIR / self.current_folder
        jsonl_files = sorted(folder_path.glob("*.jsonl"))

        recorded = {}
        for f in jsonl_files:
            for cwd, n in session_cwd_counts(read_jsonl(f)).items():
                recorded[cwd] = recorded.get(cwd, 0) + n
        if not recorded:
            messagebox.showwarning(
                "Relocate project",
                "No session in this folder recorded a working directory, so "
                "there's nothing to re-point.",
            )
            return

        picked = filedialog.askdirectory(
            title="Select this project's current location", mustexist=True
        )
        if not picked:
            return
        new_cwd = normalize_cwd(os.path.normpath(picked))
        known = set(recorded)  # every cwd this project has ever recorded
        stale = {c: n for c, n in recorded.items() if c != new_cwd}
        if not stale:
            messagebox.showinfo(
                "Relocate project", "These sessions already point there."
            )
            return

        listing = "\n".join(f"  {c}  ({n})" for c, n in sorted(stale.items()))
        if not messagebox.askyesno(
            "Relocate project?",
            f"In {len(jsonl_files)} session file(s), rewrite these recorded "
            f"working directories:\n\n{listing}\n\nto:\n  {new_cwd}\n\n"
            "The history folder name and all tool outputs / file paths inside "
            "the sessions are left unchanged. A full backup is saved first.\n\n"
            "Continue?",
            icon="warning",
        ):
            return

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = (
            Path.home() / ".claude" / "history_viewer_backups"
            / f"{self.current_folder}-{ts}"
        )
        try:
            backup_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(folder_path, backup_dir)
        except Exception as e:
            messagebox.showerror(
                "Relocate project", f"Backup failed -- nothing was changed:\n{e}"
            )
            return

        hits = 0
        try:
            for f in jsonl_files:
                out = []
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        s = line.strip()
                        if not s:
                            continue
                        try:
                            rec = json.loads(s)
                        except json.JSONDecodeError:
                            out.append(s)
                            continue
                        rc = rec.get("cwd")
                        if rc and rc != new_cwd and normalize_cwd(rc) in known:
                            rec["cwd"] = new_cwd
                            hits += 1
                        out.append(
                            json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
                        )
                fd, tmp = tempfile.mkstemp(dir=folder_path, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("\n".join(out) + ("\n" if out else ""))
                os.replace(tmp, f)
        except Exception as e:
            messagebox.showerror(
                "Relocate project",
                f"Rewrite failed partway through:\n{e}\n\n"
                f"Your original data is backed up at:\n{backup_dir}",
            )
            return

        self._cwd_overrides.pop(str(self.current_path), None)
        reselect = self.current_path
        self.populate_tree()
        for item, (_folder, path) in self.item_path.items():
            if path == reselect:
                parent = self.tree.parent(item)
                if parent:
                    self.tree.item(parent, open=True)
                self.tree.selection_set(item)
                self.tree.see(item)
                break
        messagebox.showinfo(
            "Relocate project",
            f"Done. {hits} record(s) updated across {len(jsonl_files)} file(s).\n\n"
            f"Backup: {backup_dir}",
        )

    # --------------------------------------------------------------- render
    def render_session(self, path):
        self.scroll.clear()
        self._bubble_texts = []
        self._image_refs = []
        self._load_more_pending = False
        self._rendering_batch = False
        self._find_matches = []
        self._find_current = -1
        self.find_status_var.set("")
        self._render_generation += 1
        records = read_jsonl(path)
        title = session_title(records)
        self.title_var.set(title)

        self.current_cwd = self._cwd_overrides.get(str(path)) or session_cwd(records)
        # enabled whenever the session recorded *a* working dir -- if it no
        # longer exists on disk, open_claude_cli offers to relocate it
        self.claude_btn.configure(state="normal" if self.current_cwd else "disabled")

        self._turns = group_into_turns(records)
        self._rendered_count = 0
        self._session_meta_base = f"{self.current_folder} · {path.name} · {len(self._turns)} turns"
        self._update_load_status()
        self._render_next_batch()
        self._update_load_status()
        self._continue_background_render(self._render_generation)
        if self.find_frame.winfo_ismapped() and self.find_var.get():
            self._ensure_fully_rendered()
            self._run_find()

    def _render_next_batch(self):
        # reentrancy guard: _fit_new_bubbles() below calls update_idletasks(),
        # which flushes the *whole app's* pending idle queue -- including a
        # scrollregion-triggered "near bottom" check that can fire mid-call,
        # before self._rendered_count has been updated for the batch still
        # being rendered here. Without this guard that reentrant call reuses
        # the stale (pre-update) start/end range and re-renders the same
        # turns on top of themselves, producing duplicate, overlapping
        # widgets in the same grid cells.
        if self._rendering_batch:
            return
        self._rendering_batch = True
        try:
            start = self._rendered_count
            end = min(start + self.PAGE_SIZE, len(self._turns))
            bubbles_before = len(self._bubble_texts)
            for i in range(start, end):
                self._render_turn(i + 1, self._turns[i])
            self._fit_new_bubbles(bubbles_before)
            self._rendered_count = end
        finally:
            self._rendering_batch = False
            self._load_more_pending = False

    def _fit_new_bubbles(self, start_index):
        """Size every bubble text widget added since start_index with ONE
        upfront geometry flush instead of one update_idletasks() call per
        widget -- see fit_height's docstring. This is what turns rendering
        a large (190-turn) session from ~12s into ~5s."""
        self.update_idletasks()
        for t in self._bubble_texts[start_index:]:
            try:
                fit_height(t, max_lines=40 if str(t.cget("wrap")) == "none" else 200)
            except tk.TclError:
                pass

    def _continue_background_render(self, generation):
        """Keep rendering the rest of the session a batch at a time,
        independent of scroll position. Waiting for the user to scroll near
        the bottom of whatever happens to be on screen (see
        _maybe_load_more below) works fine for ordinary turns, but a
        tool-heavy turn can render hundreds of widgets -- scrolling through
        just one such batch can take dozens of page-downs before the next
        batch would ever load, which looks and feels like the session is
        permanently stuck partway through. `generation` guards against a
        stale chain from a since-replaced session still trickling in
        widgets after the user has switched away.

        Uses after(1, ...) rather than after_idle: after_idle drains the
        *entire* pending idle queue back-to-back with nothing forcing a
        return to the OS message pump in between. Under real interaction
        (actual mouse/scroll/paint events competing for the same queue),
        that starves Windows' repaint handling and can leave stale bitmap
        regions on screen -- text that LOOKS like it's overlapping even
        though the widget tree underneath is fully correct. An explicit
        update_idletasks() before rescheduling flushes geometry/scrollregion
        and lets pending paint messages actually get processed."""
        if generation != self._render_generation or self._rendered_count >= len(self._turns):
            return
        self._render_next_batch()
        self._update_load_status()
        self.update_idletasks()
        self.after(1, lambda: self._continue_background_render(generation))

    def _update_load_status(self):
        # a big tool-heavy session can take several seconds to fully
        # render even with batched geometry flushing -- without this,
        # that looks indistinguishable from the app being stuck
        total = len(self._turns)
        if self._rendered_count < total:
            self.meta_var.set(
                f"{self._session_meta_base}  ·  loading… {self._rendered_count}/{total}"
            )
        else:
            self.meta_var.set(self._session_meta_base)

    def _maybe_load_more(self):
        if self._load_more_pending or self._rendered_count >= len(self._turns):
            return
        self._load_more_pending = True
        self.after_idle(self._render_next_batch)

    def _ensure_fully_rendered(self):
        if self._rendered_count >= len(self._turns):
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            while self._rendered_count < len(self._turns):
                self._render_next_batch()
                self._update_load_status()
                self.update_idletasks()
        finally:
            self.configure(cursor="")

    # ----------------------------------------------------------------- find
    def open_find(self, event=None):
        if not self.current_path:
            return "break"
        if not self.find_frame.winfo_ismapped():
            self.find_frame.pack(fill="x", after=self._toolbar)
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")
        self._ensure_fully_rendered()
        self._run_find()
        return "break"

    def close_find(self, event=None):
        self._clear_find_highlights()
        self.find_frame.pack_forget()
        self.find_status_var.set("")
        self._find_matches = []
        self._find_current = -1
        self.scroll.canvas.focus_set()

    def _schedule_find(self):
        if self._find_after_id is not None:
            self.after_cancel(self._find_after_id)
        self._find_after_id = self.after(200, self._run_find)

    def _run_find(self):
        self._find_after_id = None
        self._clear_find_highlights()
        query = self.find_var.get()
        self._find_matches = []
        self._find_current = -1
        if not query:
            self.find_status_var.set("")
            return
        for widget in self._bubble_texts:
            widget.tag_configure("find_hl", background=M["yellow"], foreground="#272822")
            widget.tag_configure("find_hl_cur", background=M["orange"], foreground="#272822")
            start = "1.0"
            while True:
                idx = widget.search(query, start, stopindex="end", nocase=True)
                if not idx:
                    break
                end = f"{idx}+{len(query)}c"
                widget.tag_add("find_hl", idx, end)
                self._find_matches.append((widget, idx, end))
                start = end
        if self._find_matches:
            self.find_next()
        else:
            self.find_status_var.set("0/0")

    def _clear_find_highlights(self):
        for widget in self._bubble_texts:
            try:
                widget.tag_remove("find_hl", "1.0", "end")
                widget.tag_remove("find_hl_cur", "1.0", "end")
            except tk.TclError:
                pass

    def _unmark_current(self):
        widget, start, end = self._find_matches[self._find_current]
        try:
            widget.tag_remove("find_hl_cur", start, end)
        except tk.TclError:
            pass

    def _mark_current(self):
        widget, start, end = self._find_matches[self._find_current]
        widget.tag_add("find_hl_cur", start, end)
        widget.see(start)
        self.scroll.scroll_to(widget)
        self.find_status_var.set(f"{self._find_current + 1}/{len(self._find_matches)}")

    def find_next(self, event=None):
        if not self._find_matches:
            return "break"
        if self._find_current >= 0:
            self._unmark_current()
        self._find_current = (self._find_current + 1) % len(self._find_matches)
        self._mark_current()
        return "break"

    def find_prev(self, event=None):
        if not self._find_matches:
            return "break"
        if self._find_current >= 0:
            self._unmark_current()
        self._find_current = (self._find_current - 1) % len(self._find_matches)
        self._mark_current()
        return "break"

    def _render_turn(self, idx, turn):
        row = (idx - 1) * 4  # 4 rows per turn: label, prompt, response, separator
        num_lbl = tk.Label(
            self.scroll.inner,
            text=f"TURN {idx}",
            bg=M["bg"],
            fg=M["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        num_lbl.grid(row=row, column=0, sticky="w", padx=4, pady=(10, 2))

        # prompt: right-aligned bubble, 80% of the pane width
        prompt_txt = make_readonly_text(self.scroll.inner, M["prompt_bg"], M["fg"])
        prompt_txt.configure(width=self.bubble_chars)
        prompt_txt.grid(row=row + 1, column=0, sticky="e", padx=(4, 4), pady=2)
        prompt_content = turn["prompt"] or "(no prompt — tool-only turn)"
        insert_markdown(prompt_txt, prompt_content)
        prompt_txt.configure(state="disabled")
        # height fitting is deferred to the caller (_render_next_batch),
        # which flushes geometry once for the whole batch instead of once
        # per widget -- see fit_height's docstring
        self._bubble_texts.append(prompt_txt)

        # response: left-aligned stack of bubbles
        resp_frame = tk.Frame(self.scroll.inner, bg=M["bg"])
        resp_frame.grid(row=row + 2, column=0, sticky="w", padx=(4, 4), pady=2)
        self._render_response(resp_frame, turn["items"])

        sep = tk.Frame(self.scroll.inner, bg=M["border"], height=1)
        sep.grid(row=row + 3, column=0, sticky="ew", pady=(10, 0))

    def _render_response(self, parent, items):
        r = 0
        any_content = False
        for rec in items:
            msg = rec.get("message") or {}
            content = msg.get("content")
            blocks = content if isinstance(content, list) else (
                [{"type": "text", "text": content}] if isinstance(content, str) else []
            )
            for block in blocks:
                widget = self._block_widget(parent, block)
                if widget is not None:
                    if isinstance(widget, tk.Text):
                        widget.configure(width=self.bubble_chars)
                    widget.grid(row=r, column=0, sticky="w", pady=(0, 6))
                    if isinstance(widget, tk.Text):
                        # height fitting deferred -- see _render_turn
                        self._bubble_texts.append(widget)
                    r += 1
                    any_content = True
        if not any_content:
            lbl = tk.Label(parent, text="(no content)", bg=M["bg"], fg=M["muted"])
            lbl.grid(row=0, column=0, sticky="w")

    def _block_widget(self, parent, block):
        btype = block.get("type") if isinstance(block, dict) else None
        if btype == "text":
            text = (block.get("text") or "").strip()
            if not text:
                return None
            t = make_readonly_text(parent, M["resp_bg"], M["fg"])
            insert_markdown(t, text)
            t.configure(state="disabled")
            return t
        if btype == "thinking":
            text = (block.get("thinking") or "").strip()
            if not text:
                return None
            t = make_readonly_text(parent, M["resp_bg"], M["muted"])
            t.tag_configure("italic", font=_md_fonts()["italic"])
            t.insert("1.0", "\U0001F4AD ", "italic")
            insert_markdown(t, text, base_tag="italic")
            t.configure(state="disabled")
            return t
        if btype == "tool_use":
            import json as _json

            name = block.get("name", "tool")
            try:
                inp = _json.dumps(block.get("input", {}), indent=2, ensure_ascii=False)
            except Exception:
                inp = str(block.get("input"))
            inp = collapse_blank_lines(inp)
            if len(inp) > TOOL_TEXT_CAP:
                inp = inp[:TOOL_TEXT_CAP] + "\n... (truncated)"
            t = make_readonly_text(parent, M["bg_alt"], M["fg"], wrap="none")
            t.tag_configure("hdr", foreground=M["orange"], font=_md_fonts()["mono_bold"])
            t.tag_configure("body", foreground=M["fg"], font=_md_fonts()["mono"])
            t.insert("1.0", f"\U0001F527 {name}\n", "hdr")
            t.insert("end", inp, "body")
            t.configure(state="disabled")
            return t
        if btype == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                sub_blocks = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                sub_blocks = [b for b in content if isinstance(b, dict)]
            else:
                sub_blocks = []
            text = collapse_blank_lines(
                "\n".join(b.get("text", "") for b in sub_blocks if b.get("type") == "text")
            )
            image_blocks = [b for b in sub_blocks if b.get("type") == "image"]
            if not text.strip() and not image_blocks and sub_blocks:
                import json as _json

                try:
                    text = _json.dumps(content, indent=2, ensure_ascii=False)
                except Exception:
                    text = str(content)
                text = collapse_blank_lines(text)
            if len(text) > TOOL_TEXT_CAP:
                text = text[:TOOL_TEXT_CAP] + "\n... (truncated)"

            if not image_blocks:
                if not text.strip():
                    return None
                t = make_readonly_text(parent, M["bg_alt"], M["fg"], wrap="none")
                t.tag_configure("hdr", foreground=M["blue"], font=_md_fonts()["mono_bold"])
                t.tag_configure("body", foreground=M["yellow"], font=_md_fonts()["mono"])
                t.insert("1.0", "↳ tool result\n", "hdr")
                t.insert("end", text, "body")
                t.configure(state="disabled")
                return t

            # tool result mixes text and image(s): a small container holds
            # both so they're returned to _render_response as one widget
            container = tk.Frame(parent, bg=M["bg"])
            r = 0
            if text.strip():
                t = make_readonly_text(parent=container, bg=M["bg_alt"], fg=M["yellow"], wrap="none")
                t.configure(width=self.bubble_chars)
                t.tag_configure("hdr", foreground=M["blue"], font=_md_fonts()["mono_bold"])
                t.insert("1.0", "↳ tool result\n", "hdr")
                t.insert("end", text)
                t.configure(state="disabled")
                # height fitting deferred -- see _render_turn
                self._bubble_texts.append(t)
                t.grid(row=r, column=0, sticky="w", pady=(0, 4))
                r += 1
            for ib in image_blocks:
                img_widget = self._make_image_widget(container, ib)
                img_widget.grid(row=r, column=0, sticky="w", pady=(0, 4))
                r += 1
            return container
        if btype == "image":
            return self._make_image_widget(parent, block)
        return None

    def _make_image_widget(self, parent, block, max_width=560, max_height=480):
        """Decode a base64 image content block into a preview Label, if
        Pillow is available; otherwise fall back to a placeholder."""
        source = block.get("source") if isinstance(block, dict) else None
        if isinstance(source, dict) and source.get("type") == "base64" and source.get("data"):
            try:
                import base64
                import io

                from PIL import Image, ImageTk

                raw = base64.b64decode(source["data"])
                img = Image.open(io.BytesIO(raw))
                img.load()
                w, h = img.size
                scale = min(max_width / w, max_height / h, 1.0)
                if scale < 1.0:
                    img = img.resize(
                        (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
                    )
                photo = ImageTk.PhotoImage(img)
                self._image_refs.append(photo)  # Tk drops the image if not referenced
                return tk.Label(parent, image=photo, bg=M["bg_alt"], bd=1, relief="solid")
            except Exception:
                pass
        return tk.Label(
            parent,
            text="\U0001F5BC image (preview unavailable)",
            bg=M["resp_bg"],
            fg=M["muted"],
        )

    # --------------------------------------------------------------- delete
    def delete_current(self):
        if not self.current_path or not self.current_path.is_file():
            return
        title = self.title_var.get()
        ok = messagebox.askyesno(
            "Delete session?",
            f'Permanently delete "{title}"?\n\n{self.current_path.name}\n\n'
            "This removes the session file from disk and cannot be undone.",
            icon="warning",
        )
        if not ok:
            return
        try:
            self.current_path.unlink()
        except Exception as e:
            messagebox.showerror("Delete failed", str(e))
            return
        sel = self.tree.selection()
        if sel:
            parent = self.tree.parent(sel[0])
            self.tree.delete(sel[0])
            if parent and not self.tree.get_children(parent):
                self.tree.delete(parent)
        self._clear_session_view()

    def _clear_session_view(self):
        self.scroll.clear()
        self._bubble_texts = []
        self._image_refs = []
        self._turns = []
        self._rendered_count = 0
        self.title_var.set("Select a session")
        self.meta_var.set("")
        self.current_path = None
        self.current_folder = None
        self.current_cwd = None
        self.delete_btn.configure(state="disabled")
        self.claude_btn.configure(state="disabled")
        self.relocate_btn.configure(state="disabled")

    # -------------------------------------------------------------- refresh
    def refresh_all(self):
        """Reload the project/session tree from disk, and re-render the
        currently open session (if any) so newly appended turns or new
        sessions show up without restarting the app."""
        prev_path = self.current_path
        self.populate_tree()
        if prev_path is None:
            return
        if not prev_path.is_file():
            self._clear_session_view()
            return
        for item, (_folder, path) in self.item_path.items():
            if path == prev_path:
                parent = self.tree.parent(item)
                if parent:
                    self.tree.item(parent, open=True)
                self.tree.selection_set(item)
                self.tree.see(item)
                return
        # session still exists but is filtered out of the rebuilt tree
        # (e.g. an active search query) -- keep showing it anyway
        self.render_session(prev_path)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

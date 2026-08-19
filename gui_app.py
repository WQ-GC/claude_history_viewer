#!/usr/bin/env python3
"""Native Windows desktop app (Tkinter, stdlib only) for browsing and
deleting Claude Code session history.

Left: project/session tree. Right: selected session, one row per turn,
prompt on the left / everything generated in reply on the right.
Delete removes the .jsonl file from disk directly (with confirmation) --
no server involved, this process owns the filesystem call itself.
"""
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont

from server import (
    PROJECTS_DIR,
    read_jsonl,
    project_display_name,
    session_title,
    group_into_turns,
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


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable frame (canvas + inner frame + scrollbar)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=M["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=M["bg"])
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width)
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


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
        font=("Segoe UI", 10),
    )
    return t


def fit_height(text_widget, min_lines=1, max_lines=200):
    text_widget.update_idletasks()
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

        self._build_layout()
        self.populate_tree()

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
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # --- left: search + tree ---
        left = ttk.Frame(paned, width=380)
        left.pack_propagate(False)
        paned.add(left, weight=0)

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

        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # placeholder text is set directly (not via the variable) so it
        # can't trigger a filter/populate before the tree above exists
        self._add_placeholder(entry, "Filter sessions…")
        self.search_var.trace_add("write", lambda *a: self.populate_tree())

        # --- right: toolbar + scrollable conversation ---
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

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

        self.meta_var = tk.StringVar(value="")
        tk.Label(
            right,
            textvariable=self.meta_var,
            bg=M["bg"],
            fg=M["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", padx=14)

        self.scroll = ScrollableFrame(right)
        self.scroll.pack(fill="both", expand=True, padx=6, pady=6)
        self.scroll.inner.columnconfigure(0, weight=1)
        self.scroll.inner.columnconfigure(1, weight=1)

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
        folders = sorted(
            [p for p in PROJECTS_DIR.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for folder in folders:
            files = sorted(
                folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
            )
            if not files:
                continue
            first_records = read_jsonl(files[0])[:5]
            display_name = project_display_name(folder.name, first_records)

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
            return
        folder_name, path = self.item_path[item]
        self.current_folder = folder_name
        self.current_path = path
        self.render_session(path)
        self.delete_btn.configure(state="normal")

    # --------------------------------------------------------------- render
    def render_session(self, path):
        self.scroll.clear()
        records = read_jsonl(path)
        title = session_title(records)
        self.title_var.set(title)
        self.meta_var.set(f"{self.current_folder} · {path.name}")

        turns = group_into_turns(records)
        for i, turn in enumerate(turns, 1):
            self._render_turn(i, turn)

    def _render_turn(self, idx, turn):
        row = idx * 2 - 1  # leave room for a separator row after each turn
        num_lbl = tk.Label(
            self.scroll.inner,
            text=f"TURN {idx}",
            bg=M["bg"],
            fg=M["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        num_lbl.grid(row=row - 1, column=0, columnspan=2, sticky="w", padx=4, pady=(10, 2))

        prompt_txt = make_readonly_text(self.scroll.inner, M["prompt_bg"], M["fg"])
        prompt_txt.grid(row=row, column=0, sticky="new", padx=(4, 6), pady=2)
        prompt_content = turn["prompt"] or "(no prompt — tool-only turn)"
        prompt_txt.insert("1.0", prompt_content)
        prompt_txt.configure(state="disabled")
        fit_height(prompt_txt)

        resp_frame = tk.Frame(self.scroll.inner, bg=M["bg"])
        resp_frame.grid(row=row, column=1, sticky="new", padx=(6, 4), pady=2)
        resp_frame.columnconfigure(0, weight=1)
        self._render_response(resp_frame, turn["items"])

        sep = tk.Frame(self.scroll.inner, bg=M["border"], height=1)
        sep.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

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
                    widget.grid(row=r, column=0, sticky="new", pady=(0, 6))
                    if isinstance(widget, tk.Text):
                        fit_height(
                            widget,
                            max_lines=40 if str(widget.cget("wrap")) == "none" else 200,
                        )
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
            t.insert("1.0", text)
            t.configure(state="disabled")
            return t
        if btype == "thinking":
            text = (block.get("thinking") or "").strip()
            if not text:
                return None
            t = make_readonly_text(parent, M["resp_bg"], M["muted"])
            t.tag_configure("italic", font=("Segoe UI", 9, "italic"))
            t.insert("1.0", "\U0001F4AD " + text, "italic")
            t.configure(state="disabled")
            return t
        if btype == "tool_use":
            import json as _json

            name = block.get("name", "tool")
            try:
                inp = _json.dumps(block.get("input", {}), indent=2, ensure_ascii=False)
            except Exception:
                inp = str(block.get("input"))
            if len(inp) > TOOL_TEXT_CAP:
                inp = inp[:TOOL_TEXT_CAP] + "\n... (truncated)"
            t = make_readonly_text(parent, M["bg_alt"], M["fg"], wrap="none")
            t.tag_configure("hdr", foreground=M["orange"], font=("Consolas", 9, "bold"))
            t.tag_configure("body", foreground=M["fg"], font=("Consolas", 9))
            t.insert("1.0", f"\U0001F527 {name}\n", "hdr")
            t.insert("end", inp, "body")
            t.configure(state="disabled")
            return t
        if btype == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                text = content
            else:
                text = ""
                if isinstance(content, list):
                    parts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    text = "\n".join(parts)
                if not text:
                    import json as _json

                    try:
                        text = _json.dumps(content, indent=2, ensure_ascii=False)
                    except Exception:
                        text = str(content)
            text = text or ""
            if len(text) > TOOL_TEXT_CAP:
                text = text[:TOOL_TEXT_CAP] + "\n... (truncated)"
            t = make_readonly_text(parent, M["bg_alt"], M["fg"], wrap="none")
            t.tag_configure("hdr", foreground=M["blue"], font=("Consolas", 9, "bold"))
            t.tag_configure("body", foreground=M["yellow"], font=("Consolas", 9))
            t.insert("1.0", "↳ tool result\n", "hdr")
            t.insert("end", text, "body")
            t.configure(state="disabled")
            return t
        if btype == "image":
            lbl = tk.Label(parent, text="\U0001F5BC image", bg=M["resp_bg"], fg=M["muted"])
            return lbl
        return None

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
        self.scroll.clear()
        self.title_var.set("Select a session")
        self.meta_var.set("")
        self.current_path = None
        self.delete_btn.configure(state="disabled")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

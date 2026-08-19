#!/usr/bin/env python3
"""Local read-only GUI for browsing Claude Code session history.

Reads JSONL session logs from ~/.claude/projects and serves a small
web UI on 127.0.0.1. No data leaves the machine.
"""
import html
import json
import os
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECTS_DIR = Path.home() / ".claude" / "projects"
PORT = 8756

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:dark}}
body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  max-width:1400px;margin:0 auto;padding:24px 16px 80px;
  background:#272822;color:#f8f8f2}}
.narrow{{max-width:900px;margin:0 auto}}
h1{{font-size:20px;margin-bottom:4px;color:#f8f8f2}}
h2{{font-size:16px;color:#f8f8f2}}
a{{color:#66d9ef;text-decoration:none}}
a:hover{{text-decoration:underline}}
.meta{{color:#75715e;font-size:13px}}
.card{{display:block;border:1px solid #49483e;border-radius:10px;padding:12px 16px;
  margin:10px 0;background:#2d2e27}}
.card:hover{{border-color:#75715e}}
.preview{{margin-top:4px;font-size:14px;color:#cfcfc2;
  white-space:pre-wrap;overflow:hidden;text-overflow:ellipsis;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}}
.bubble{{border-radius:10px;padding:10px 14px;margin:10px 0;white-space:pre-wrap;
  word-wrap:break-word;color:#f8f8f2}}
.bubble.user{{background:#3e3d32;margin-left:40px}}
.bubble.assistant{{background:#2d2e27;border:1px solid #49483e;margin-right:40px}}
.role{{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  color:#a6e22e;margin-bottom:4px}}
.tool{{background:#1e1f1c;border:1px solid #49483e;border-radius:8px;
  margin:8px 40px 8px 0;padding:8px 12px;font-size:13px;color:#f8f8f2}}
.tool summary{{cursor:pointer;font-weight:600;color:#fd971f}}
pre{{background:#1e1f1c;color:#e6db74;padding:8px;border-radius:6px;
  overflow-x:auto;font-size:12.5px}}
code{{background:#1e1f1c;color:#e6db74;padding:1px 4px;border-radius:4px;font-size:.92em}}
.thinking{{color:#75715e;font-size:13px;font-style:italic;margin:6px 0}}
.backlink{{margin-bottom:16px;display:inline-block}}
.turn{{display:grid;grid-template-columns:1fr 1fr;gap:20px;
  margin:0 0 20px;padding-bottom:20px;border-bottom:1px solid #49483e}}
.turn-num{{grid-column:1/-1;color:#75715e;font-size:12px;font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;margin-top:6px}}
.prompt-col{{background:#3e3d32;border-radius:10px;padding:12px 14px;
  white-space:pre-wrap;word-wrap:break-word;align-self:start;font-size:14.5px;
  color:#f8f8f2}}
.response-col{{display:flex;flex-direction:column;gap:8px;min-width:0}}
.response-col .bubble{{margin:0;background:#2d2e27;border:1px solid #49483e}}
.response-col .tool{{margin:0}}
.response-col .thinking{{margin:0}}
@media (max-width:760px){{.turn{{grid-template-columns:1fr}}}}
.card-row{{display:flex;align-items:stretch;gap:8px;margin:10px 0}}
.card-row .card{{flex:1;margin:0}}
.del-btn{{align-self:center;color:#f92672;padding:8px 10px;border-radius:8px;
  text-decoration:none;font-size:17px;border:1px solid transparent}}
.del-btn:hover{{background:#3e3d32;border-color:#f92672;text-decoration:none}}
.confirm-box{{background:#2d2e27;border:1px solid #f92672;border-radius:10px;
  padding:18px 20px;margin:16px 0}}
.btn-danger{{background:#f92672;color:#272822;border:none;border-radius:8px;
  padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer}}
.btn-danger:hover{{background:#ff5c93}}
.btn-cancel{{margin-left:10px;color:#66d9ef}}
</style></head><body>
"""
PAGE_TAIL = "</body></html>"


def read_jsonl(path):
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return lines


def fmt_time(ts):
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except Exception:
        return ts


def project_display_name(folder, records):
    for r in records[:5]:
        cwd = r.get("cwd")
        if cwd:
            return cwd
    return folder


def session_title(records, max_len=70):
    """Prefer the session's own custom/AI-generated title; fall back to the
    first human prompt."""
    custom_title = None
    ai_title = None
    for r in records:
        rtype = r.get("type")
        if rtype == "custom-title":
            custom_title = r.get("customTitle") or custom_title
        elif rtype == "ai-title":
            ai_title = r.get("aiTitle") or ai_title
    if custom_title:
        return custom_title.strip()
    if ai_title:
        return ai_title.strip()
    for r in records:
        if r.get("type") != "user":
            continue
        msg = r.get("message") or {}
        text = text_of_content(msg.get("content"))
        text = text.strip()
        if not text:
            continue
        first_line = text.splitlines()[0].strip()
        if len(first_line) > max_len:
            first_line = first_line[: max_len - 1].rstrip() + "…"
        return first_line
    return "(untitled session)"


def collapse_blank_lines(text, max_consecutive=2):
    """Normalize CRLF and squash long runs of blank/whitespace-only lines,
    so command output padded with blank lines (progress bars, trailing
    whitespace) doesn't render as a huge mostly-empty box."""
    if not text:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= max_consecutive:
                out.append(line)
        else:
            blank_run = 0
            out.append(line)
    return "\n".join(out)


def text_of_content(content):
    """Flatten a message content field (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def render_block(block):
    """Render a single content block to HTML."""
    if not isinstance(block, dict):
        return ""
    btype = block.get("type")
    if btype == "text":
        return html.escape(block.get("text", "")).replace("\n", "<br>")
    if btype == "thinking":
        t = block.get("thinking") or ""
        if not t.strip():
            return ""
        return f'<div class="thinking">💭 {html.escape(t)}</div>'
    if btype == "tool_use":
        name = html.escape(block.get("name", "tool"))
        try:
            inp = json.dumps(block.get("input", {}), indent=2, ensure_ascii=False)
        except Exception:
            inp = str(block.get("input"))
        inp = collapse_blank_lines(inp)
        inp = inp if len(inp) < 4000 else inp[:4000] + "\n... (truncated)"
        return (
            f'<details class="tool"><summary>🔧 {name}</summary>'
            f"<pre>{html.escape(inp)}</pre></details>"
        )
    if btype == "tool_result":
        content = block.get("content")
        text = text_of_content(content) if not isinstance(content, str) else content
        if isinstance(content, list) and not text:
            try:
                text = json.dumps(content, indent=2, ensure_ascii=False)
            except Exception:
                text = str(content)
        text = collapse_blank_lines(text or "")
        text = text if len(text) < 4000 else text[:4000] + "\n... (truncated)"
        return (
            f'<details class="tool"><summary>↳ tool result</summary>'
            f"<pre>{html.escape(text)}</pre></details>"
        )
    if btype == "image":
        return '<div class="tool">🖼️ (image)</div>'
    return ""


def render_message(record):
    msg = record.get("message") or {}
    role = msg.get("role") or record.get("type")
    if role not in ("user", "assistant"):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        body = f'<div class="bubble {role}"><div class="role">{role}</div>{html.escape(content)}</div>'
        return body
    if isinstance(content, list):
        # tool_result-only user turns are rendered as plain tool blocks, not a bubble
        text_parts = []
        other_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(render_block(block))
            else:
                other_parts.append(render_block(block))
        out = ""
        if text_parts:
            out += (
                f'<div class="bubble {role}"><div class="role">{role}</div>'
                + "<br>".join(text_parts)
                + "</div>"
            )
        out += "".join(other_parts)
        return out
    return ""


def group_into_turns(records):
    """Group records into turns: each human text message starts a new turn;
    everything generated in reply (assistant text/thinking/tool calls, and
    tool results fed back) belongs to its "items" list (raw records), which
    callers render however they like (HTML for the web UI, Tk tags for the
    desktop app)."""
    turns = []
    current = None
    for r in records:
        msg = r.get("message") or {}
        role = msg.get("role") or r.get("type")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if role == "user":
            block_types = set()
            if isinstance(content, list):
                block_types = {
                    b.get("type") for b in content if isinstance(b, dict)
                }
            is_tool_result_only = bool(block_types) and block_types <= {
                "tool_result",
                "image",
            }
            text = text_of_content(content) if not isinstance(content, str) else content
            if text.strip() and not is_tool_result_only:
                current = {"prompt": text.strip(), "items": []}
                turns.append(current)
                continue
            # tool-result-only user message: feeds back into current turn's response
            if current is None:
                current = {"prompt": "", "items": []}
                turns.append(current)
            current["items"].append(r)
            continue
        # assistant
        if current is None:
            current = {"prompt": "", "items": []}
            turns.append(current)
        current["items"].append(r)
    return turns


def render_turns_html(records):
    out = []
    for i, turn in enumerate(group_into_turns(records), 1):
        prompt_html = html.escape(turn["prompt"]) if turn["prompt"] else "<em>(no prompt — tool-only turn)</em>"
        resp_html = "".join(render_message(r) for r in turn["items"])
        out.append(
            f'<div class="turn"><div class="turn-num">Turn {i}</div>'
            f'<div class="prompt-col">{prompt_html}</div>'
            f'<div class="response-col">{resp_html}</div>'
            f"</div>"
        )
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, body, status=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p != ""]
        try:
            if not parts:
                self.list_projects()
            elif parts[0] == "project" and len(parts) == 2:
                self.list_sessions(parts[1])
            elif parts[0] == "session" and len(parts) == 3:
                self.show_session(parts[1], parts[2])
            elif parts[0] == "delete" and len(parts) == 3:
                self.confirm_delete(parts[1], parts[2])
            else:
                self._send("<h1>404 Not Found</h1>", 404)
        except Exception as e:
            self._send(f"<h1>Error</h1><pre>{html.escape(str(e))}</pre>", 500)

    def list_projects(self):
        out = [PAGE_HEAD.format(title="Claude Code History"), '<div class="narrow">']
        out.append("<h1>Claude Code — Conversation History</h1>")
        out.append(f'<p class="meta">{PROJECTS_DIR}</p>')
        if not PROJECTS_DIR.exists():
            out.append("<p>No projects directory found.</p>")
        else:
            folders = sorted(
                [p for p in PROJECTS_DIR.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for folder in folders:
                files = list(folder.glob("*.jsonl"))
                if not files:
                    continue
                newest = max(files, key=lambda f: f.stat().st_mtime)
                first_records = read_jsonl(newest)[:5]
                name = project_display_name(folder.name, first_records)
                last_mod = fmt_time(
                    datetime.fromtimestamp(newest.stat().st_mtime).isoformat()
                )
                out.append(
                    f'<a class="card" href="/project/{html.escape(folder.name)}">'
                    f"<h2>{html.escape(name)}</h2>"
                    f'<div class="meta">{len(files)} session(s) · last activity {last_mod}</div>'
                    f"</a>"
                )
        out.append("</div>")
        out.append(PAGE_TAIL)
        self._send("".join(out))

    def list_sessions(self, folder_name):
        folder = PROJECTS_DIR / folder_name
        if not folder.is_dir():
            self._send("<h1>404</h1>", 404)
            return
        out = [PAGE_HEAD.format(title=folder_name), '<div class="narrow">']
        out.append('<a class="backlink" href="/">← All projects</a>')
        out.append(f"<h1>{html.escape(folder_name)}</h1>")
        files = sorted(
            folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        for f in files:
            records = read_jsonl(f)
            preview = ""
            msg_count = 0
            first_ts = ""
            for r in records:
                if r.get("type") in ("user", "assistant"):
                    msg_count += 1
                if r.get("type") == "user" and not preview:
                    m = r.get("message") or {}
                    t = text_of_content(m.get("content"))
                    if t.strip():
                        preview = t.strip()
                        first_ts = r.get("timestamp", "")
            if not preview:
                continue
            mtime = fmt_time(
                datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            )
            title = session_title(records)
            out.append(
                '<div class="card-row">'
                f'<a class="card" href="/session/{html.escape(folder_name)}/{html.escape(f.name)}">'
                f"<h2>{html.escape(title)}</h2>"
                f'<div class="meta">{fmt_time(first_ts) or mtime} · {msg_count} messages</div>'
                f'<div class="preview">{html.escape(preview[:400])}</div>'
                f"</a>"
                f'<a class="del-btn" href="/delete/{html.escape(folder_name)}/{html.escape(f.name)}" title="Delete this session">🗑️</a>'
                f"</div>"
            )
        out.append("</div>")
        out.append(PAGE_TAIL)
        self._send("".join(out))

    def show_session(self, folder_name, filename):
        path = PROJECTS_DIR / folder_name / filename
        if not path.is_file():
            self._send("<h1>404</h1>", 404)
            return
        records = read_jsonl(path)
        title = session_title(records)
        out = [PAGE_HEAD.format(title=title)]
        out.append(
            f'<a class="backlink" href="/project/{html.escape(folder_name)}">← {html.escape(folder_name)}</a>'
        )
        out.append(f"<h1>{html.escape(title)}</h1>")
        out.append(
            f'<p class="meta">{html.escape(filename)} · '
            f'<a class="del-btn" style="padding:0;font-size:13px" '
            f'href="/delete/{html.escape(folder_name)}/{html.escape(filename)}">🗑️ Delete this session</a></p>'
        )
        out.append(render_turns_html(records))
        out.append(PAGE_TAIL)
        self._send("".join(out))

    def confirm_delete(self, folder_name, filename):
        path = PROJECTS_DIR / folder_name / filename
        if not path.is_file():
            self._send("<h1>404</h1>", 404)
            return
        title = session_title(read_jsonl(path))
        out = [PAGE_HEAD.format(title="Delete session?"), '<div class="narrow">']
        out.append(
            f'<a class="backlink" href="/project/{html.escape(folder_name)}">← Cancel, go back</a>'
        )
        out.append(
            '<div class="confirm-box">'
            f"<h2>Delete “{html.escape(title)}”?</h2>"
            f'<p class="meta">{html.escape(filename)}</p>'
            "<p>This permanently deletes the session file from disk. This cannot be undone.</p>"
            f'<form method="post" action="/delete/{html.escape(folder_name)}/{html.escape(filename)}">'
            '<button type="submit" class="btn-danger">Delete permanently</button>'
            f'<a class="btn-cancel" href="/project/{html.escape(folder_name)}">Cancel</a>'
            "</form></div>"
        )
        out.append("</div>")
        out.append(PAGE_TAIL)
        self._send("".join(out))

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p != ""]
        try:
            if parts and parts[0] == "delete" and len(parts) == 3:
                self.do_delete(parts[1], parts[2])
            else:
                self._send("<h1>404 Not Found</h1>", 404)
        except Exception as e:
            self._send(f"<h1>Error</h1><pre>{html.escape(str(e))}</pre>", 500)

    def do_delete(self, folder_name, filename):
        path = PROJECTS_DIR / folder_name / filename
        if not path.is_file() or path.suffix != ".jsonl":
            self._send("<h1>404</h1>", 404)
            return
        # keep the deletion confined to the Claude projects tree
        try:
            path.resolve().relative_to(PROJECTS_DIR.resolve())
        except ValueError:
            self._send("<h1>403 Forbidden</h1>", 403)
            return
        path.unlink()
        out = [PAGE_HEAD.format(title="Deleted"), '<div class="narrow">']
        out.append(
            f'<a class="backlink" href="/project/{html.escape(folder_name)}">← Back to sessions</a>'
        )
        out.append(f"<h2>Deleted {html.escape(filename)}</h2>")
        out.append("</div>")
        out.append(PAGE_TAIL)
        self._send("".join(out))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Claude Code history viewer running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Local read-only GUI for browsing Claude Code session history.

Reads JSONL session logs from ~/.claude/projects and serves a small
web UI on 127.0.0.1. No data leaves the machine.
"""
import base64
import html
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

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
.bubble{{border-radius:10px;padding:10px 14px;margin:10px 0;
  word-wrap:break-word;color:#f8f8f2}}
.bubble.user{{background:#3e3d32;margin-right:40px}}
.bubble.assistant{{background:#2d2e27;border:1px solid #49483e;margin-left:40px}}
.bubble p{{margin:6px 0}}
.bubble p:first-child{{margin-top:0}}
.bubble p:last-child{{margin-bottom:0}}
.bubble ul,.bubble ol{{margin:6px 0;padding-left:22px}}
.bubble li{{margin:2px 0}}
.bubble blockquote{{margin:6px 0;padding:2px 10px;border-left:3px solid #75715e;color:#cfcfc2}}
.bubble hr{{border:none;border-top:1px solid #49483e;margin:10px 0}}
.bubble h1,.bubble h2,.bubble h3,.bubble h4,.bubble h5,.bubble h6{{margin:10px 0 4px;color:#f8f8f2;line-height:1.3}}
.bubble h1:first-child,.bubble h2:first-child,.bubble h3:first-child,
.bubble h4:first-child,.bubble h5:first-child,.bubble h6:first-child{{margin-top:0}}
.bubble h1{{font-size:18px}}
.bubble h2{{font-size:16.5px}}
.bubble h3{{font-size:15.5px}}
.bubble h4,.bubble h5,.bubble h6{{font-size:14.5px}}
.bubble strong{{font-weight:700}}
.bubble del{{opacity:.7}}
pre code{{background:none;padding:0}}
.role{{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  color:#a6e22e;margin-bottom:4px}}
.tool{{background:#1e1f1c;border:1px solid #49483e;border-radius:8px;
  margin:8px 0 8px 40px;padding:8px 12px;font-size:13px;color:#f8f8f2}}
.tool summary{{cursor:pointer;font-weight:600;color:#fd971f}}
pre{{background:#1e1f1c;color:#e6db74;padding:8px;border-radius:6px;
  overflow-x:auto;font-size:12.5px}}
code{{background:#1e1f1c;color:#e6db74;padding:1px 4px;border-radius:4px;font-size:.92em}}
.thinking{{color:#75715e;font-size:13px;font-style:italic;margin:6px 0}}
.backlink{{margin-bottom:16px;display:inline-block}}
.sort-bar{{margin:6px 0 16px;font-size:13px;color:#75715e;display:flex;
  flex-wrap:wrap;align-items:center;gap:10px}}
.sort-links{{display:inline-block}}
.sort-link{{display:inline-block;margin-right:6px;padding:4px 10px;border-radius:6px;
  border:1px solid #49483e;color:#cfcfc2}}
.sort-link:hover{{border-color:#75715e;text-decoration:none}}
.sort-link.active{{background:#3e3d32;color:#a6e22e;border-color:#a6e22e}}
.search-form{{display:inline-flex;gap:6px;margin-left:auto}}
.search-input{{background:#2d2e27;color:#f8f8f2;border:1px solid #49483e;
  border-radius:6px;padding:5px 10px;font-size:13px;min-width:200px}}
.search-input:focus{{outline:none;border-color:#66d9ef}}
.search-btn{{background:#3e3d32;color:#f8f8f2;border:1px solid #49483e;
  border-radius:6px;padding:5px 10px;font-size:13px;cursor:pointer}}
.search-btn:hover{{border-color:#75715e}}
.refresh-link{{white-space:nowrap}}
.image-block{{margin:8px 0 8px 40px}}
.image-block img{{max-width:100%;max-height:480px;border-radius:8px;
  border:1px solid #49483e;cursor:zoom-in;display:block}}
.turn{{display:grid;grid-template-columns:1fr 1fr;gap:20px;
  margin:0 0 20px;padding-bottom:20px;border-bottom:1px solid #49483e}}
.turn-num{{grid-column:1/-1;color:#75715e;font-size:12px;font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;margin-top:6px}}
.prompt-col{{background:#3e3d32;border-radius:10px;padding:12px 14px;
  word-wrap:break-word;align-self:start;font-size:14.5px;
  color:#f8f8f2}}
.prompt-col p{{margin:6px 0}}
.prompt-col p:first-child{{margin-top:0}}
.prompt-col p:last-child{{margin-bottom:0}}
.response-col{{display:flex;flex-direction:column;gap:8px;min-width:0}}
.response-col .bubble{{margin:0;background:#2d2e27;border:1px solid #49483e}}
.response-col .tool{{margin:0}}
.response-col .thinking{{margin:0}}
.response-col .image-block{{margin:0}}
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


# Small cache so opening a session page (which reads the file once) doesn't
# get re-parsed from scratch for every single /blob/ image request that
# follows -- a big session can be tens of MB, and re-reading it per image
# is the dominant cost of serving each one (see serve_blob).
_JSONL_CACHE = {}
_JSONL_CACHE_ORDER = []
_JSONL_CACHE_MAX = 4


def read_jsonl_cached(path):
    path = Path(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return read_jsonl(path)
    key = (str(path), mtime)
    cached = _JSONL_CACHE.get(key)
    if cached is not None:
        return cached
    records = read_jsonl(path)
    _JSONL_CACHE[key] = records
    _JSONL_CACHE_ORDER.append(key)
    if len(_JSONL_CACHE_ORDER) > _JSONL_CACHE_MAX:
        _JSONL_CACHE.pop(_JSONL_CACHE_ORDER.pop(0), None)
    return records


def fmt_time(ts):
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except Exception:
        return ts


def human_size(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


SORT_OPTIONS = (("mtime", "Last modified"), ("name", "Name"), ("size", "Size"))


def parse_sort(query_string):
    sort = parse_qs(query_string).get("sort", ["mtime"])[0]
    return sort if sort in dict(SORT_OPTIONS) else "mtime"


def parse_query(query_string):
    return parse_qs(query_string).get("q", [""])[0].strip()


def matches_query(query, *texts):
    """Case-insensitive substring match against any of the given texts."""
    if not query:
        return True
    q = query.lower()
    return any(q in (t or "").lower() for t in texts)


def render_controls(base_path, sort, q):
    """Sort links + search box + refresh link, all preserving each other's
    current state (e.g. re-sorting keeps the active search, searching keeps
    the active sort)."""
    q_suffix = f"&q={quote(q)}" if q else ""
    links = " ".join(
        f'<a class="sort-link{" active" if key == sort else ""}" '
        f'href="{base_path}?sort={key}{q_suffix}">{label}</a>'
        for key, label in SORT_OPTIONS
    )
    refresh_href = f"{base_path}?sort={sort}{q_suffix}"
    return (
        '<div class="sort-bar">'
        f'<span class="sort-links">Sort: {links}</span>'
        f'<form class="search-form" method="get" action="{base_path}">'
        f'<input type="hidden" name="sort" value="{html.escape(sort)}">'
        f'<input type="text" name="q" value="{html.escape(q)}" '
        f'class="search-input" placeholder="Search…">'
        '<button type="submit" class="search-btn">Search</button>'
        "</form>"
        f'<a class="sort-link refresh-link" href="{refresh_href}" '
        'title="Reload from disk">⟳ Refresh</a>'
        "</div>"
    )


def normalize_cwd(cwd):
    """Upper-case a Windows drive letter (`c:\\x` -> `C:\\x`). The same
    directory gets recorded with either case depending on how the shell was
    sitting when `claude` launched; normalizing on read makes those compare
    and display as one."""
    if cwd and len(cwd) >= 2 and cwd[1] == ":" and cwd[0].isalpha():
        return cwd[0].upper() + cwd[1:]
    return cwd


def session_cwd(records):
    """The filesystem directory this session ran in, taken from the first
    record that carries a `cwd` (used to launch a terminal there)."""
    for r in records:
        cwd = r.get("cwd")
        if cwd:
            return normalize_cwd(cwd)
    return None


def session_cwd_counts(records):
    """Every distinct `cwd` recorded across these records, with how many
    records name it. A project folder that's been renamed on disk over its
    life accumulates several. Drive-letter case is normalized so `c:\\x`
    and `C:\\x` count as one."""
    counts = {}
    for r in records:
        cwd = r.get("cwd")
        if cwd:
            cwd = normalize_cwd(cwd)
            counts[cwd] = counts.get(cwd, 0) + 1
    return counts


def project_display_name(folder, records):
    # scan all records, not just the head -- newer sessions open with a
    # run of metadata records (mode, permission-mode, bridge-session,
    # file-history-snapshot, ...) that carry no `cwd`, so a head-only look
    # falls back to the ugly encoded folder name for no reason
    return session_cwd(records) or folder


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


# --- lightweight markdown rendering (no third-party deps) --------------

_INLINE_RE = re.compile(
    r"(?P<code>`[^`\n]+?`)"
    r"|(?P<bold>\*\*[^\n]+?\*\*|__[^\n]+?__)"
    r"|(?P<strike>~~[^\n]+?~~)"
    r"|(?P<italic>\*[^\n*]+?\*|(?<!\w)_[^\n_]+?_(?!\w))"
    r"|(?P<link>\[[^\]]+\]\([^)\s]+\))"
)


def tokenize_inline(text):
    """Yield (kind, content) pairs for a single line of text. kind is None
    for plain text, or one of 'bold','italic','code','strike','link' (whose
    content is a (label, url) tuple). Shared by the web UI (HTML output)
    and the desktop GUI (Tk tag output)."""
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            yield (None, text[pos:m.start()])
        kind = m.lastgroup
        raw = m.group()
        if kind == "code":
            yield ("code", raw[1:-1])
        elif kind == "bold":
            yield ("bold", raw[2:-2])
        elif kind == "strike":
            yield ("strike", raw[2:-2])
        elif kind == "italic":
            yield ("italic", raw[1:-1])
        elif kind == "link":
            label_end = raw.index("]")
            yield ("link", (raw[1:label_end], raw[label_end + 2:-1]))
        pos = m.end()
    if pos < len(text):
        yield (None, text[pos:])


def inline_markdown_html(text):
    """Render inline markdown (bold/italic/code/strike/links) to HTML,
    escaping everything else. Newlines become <br>."""
    out_lines = []
    for line in text.split("\n"):
        parts = []
        for kind, content in tokenize_inline(line):
            if kind is None:
                parts.append(html.escape(content))
            elif kind == "code":
                parts.append(f"<code>{html.escape(content)}</code>")
            elif kind == "bold":
                parts.append(f"<strong>{html.escape(content)}</strong>")
            elif kind == "italic":
                parts.append(f"<em>{html.escape(content)}</em>")
            elif kind == "strike":
                parts.append(f"<del>{html.escape(content)}</del>")
            elif kind == "link":
                label, url = content
                if url.startswith(("http://", "https://")):
                    parts.append(
                        f'<a href="{html.escape(url)}" target="_blank" '
                        f'rel="noopener noreferrer">{html.escape(label)}</a>'
                    )
                else:
                    parts.append(html.escape(f"[{label}]({url})"))
        out_lines.append("".join(parts))
    return "<br>".join(out_lines)


_FENCE_LINE_RE = re.compile(r"^\s*```")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUM_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_BLOCK_START_RE = re.compile(r"^\s*(#{1,6}\s|```|>|[-*+]\s|\d+[.)]\s)")


def markdown_to_html(text):
    """Render a chat message (headers, lists, code fences, blockquotes,
    paragraphs, plus inline formatting) to HTML."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    list_stack = []

    def close_lists():
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if _FENCE_LINE_RE.match(line):
            close_lists()
            lang = line.strip()[3:].strip()
            i += 1
            code_lines = []
            while i < n and not _FENCE_LINE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            lang_attr = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(
                f"<pre><code{lang_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>"
            )
            continue
        if line.strip() == "":
            close_lists()
            i += 1
            continue
        h = _HEADER_RE.match(line)
        if h:
            close_lists()
            level = len(h.group(1))
            out.append(f"<h{level}>{inline_markdown_html(h.group(2))}</h{level}>")
            i += 1
            continue
        if _HR_RE.match(line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue
        if _QUOTE_RE.match(line):
            close_lists()
            quote_lines = []
            while i < n and _QUOTE_RE.match(lines[i]):
                quote_lines.append(_QUOTE_RE.sub("", lines[i], count=1))
                i += 1
            out.append(
                f"<blockquote>{inline_markdown_html(chr(10).join(quote_lines))}</blockquote>"
            )
            continue
        bullet = _BULLET_RE.match(line)
        num = _NUM_RE.match(line)
        if bullet or num:
            kind = "ul" if bullet else "ol"
            content = (bullet or num).group(bullet and 1 or 2)
            if not list_stack or list_stack[-1] != kind:
                close_lists()
                list_stack.append(kind)
                out.append(f"<{kind}>")
            out.append(f"<li>{inline_markdown_html(content)}</li>")
            i += 1
            continue
        close_lists()
        para = [line]
        i += 1
        while i < n and lines[i].strip() != "" and not _BLOCK_START_RE.match(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{inline_markdown_html(chr(10).join(para))}</p>")
    close_lists()
    return "".join(out)


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


def iter_image_blocks(records):
    """Yield every image content block in a session, in the exact order
    render_block()/render_message() encounter and number them in -- top
    level message content, or nested inside a tool_result's content list.
    Used both to assign each image a stable index when rendering (see
    render_block's "image" case) and to look one up by that index when
    serving it from /blob/... ."""
    for r in records:
        msg = r.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image":
                yield block
            elif block.get("type") == "tool_result":
                sub = block.get("content")
                if isinstance(sub, list):
                    for b in sub:
                        if isinstance(b, dict) and b.get("type") == "image":
                            yield b


def render_block(block, image_ctx=None):
    """Render a single content block to HTML. image_ctx, if given, is a
    {"folder": ..., "filename": ..., "next": [0]} dict used to number
    images and link them to the /blob/ endpoint instead of inlining their
    base64 data (see iter_image_blocks) -- keeps large sessions with many
    screenshots from bloating the page and lets the browser lazy-load them.
    Without it (e.g. in tests), images fall back to an inline data: URI."""
    if not isinstance(block, dict):
        return ""
    btype = block.get("type")
    if btype == "text":
        return markdown_to_html(block.get("text", ""))
    if btype == "thinking":
        t = block.get("thinking") or ""
        if not t.strip():
            return ""
        return f'<div class="thinking">💭 {inline_markdown_html(t)}</div>'
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
        if isinstance(content, str):
            sub_blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            sub_blocks = [b for b in content if isinstance(b, dict)]
        else:
            sub_blocks = []
        text = collapse_blank_lines(
            "\n".join(b.get("text", "") for b in sub_blocks if b.get("type") == "text")
        )
        image_html = "".join(
            render_block(b, image_ctx) for b in sub_blocks if b.get("type") == "image"
        )
        if not text.strip() and not image_html and sub_blocks:
            # unrecognized block shape(s) -- fall back to a raw dump rather
            # than silently dropping the content
            try:
                text = json.dumps(content, indent=2, ensure_ascii=False)
            except Exception:
                text = str(content)
            text = collapse_blank_lines(text)
        text_html = ""
        if text.strip():
            shown = text if len(text) < 4000 else text[:4000] + "\n... (truncated)"
            text_html = (
                f'<details class="tool"><summary>↳ tool result</summary>'
                f"<pre>{html.escape(shown)}</pre></details>"
            )
        return text_html + image_html
    if btype == "image":
        source = block.get("source")
        data = source.get("data") if isinstance(source, dict) else None
        if isinstance(source, dict) and source.get("type") == "base64" and data:
            if image_ctx is not None:
                idx = image_ctx["next"][0]
                image_ctx["next"][0] += 1
                src = (
                    f'/blob/{quote(image_ctx["folder"], safe="")}'
                    f'/{quote(image_ctx["filename"], safe="")}/{idx}'
                )
                # a plain URL, so linking to it again for "open full size"
                # costs nothing extra (unlike the data: URI fallback below)
                return (
                    f'<div class="image-block"><a href="{src}" target="_blank" '
                    f'rel="noopener noreferrer"><img src="{src}" alt="image" loading="lazy"></a></div>'
                )
            media_type = html.escape(source.get("media_type") or "image/png", quote=True)
            src = f"data:{media_type};base64,{data}"
            return f'<div class="image-block"><img src="{src}" alt="image"></div>'
        return '<div class="tool">🖼️ (image)</div>'
    return ""


def render_message(record, image_ctx=None):
    msg = record.get("message") or {}
    role = msg.get("role") or record.get("type")
    if role not in ("user", "assistant"):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        body = f'<div class="bubble {role}"><div class="role">{role}</div>{markdown_to_html(content)}</div>'
        return body
    if isinstance(content, list):
        # tool_result-only user turns are rendered as plain tool blocks, not a bubble
        text_parts = []
        other_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(render_block(block, image_ctx))
            else:
                other_parts.append(render_block(block, image_ctx))
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


def render_turns_html(records, image_ctx=None):
    out = []
    for i, turn in enumerate(group_into_turns(records), 1):
        prompt_html = markdown_to_html(turn["prompt"]) if turn["prompt"] else "<em>(no prompt — tool-only turn)</em>"
        resp_html = "".join(render_message(r, image_ctx) for r in turn["items"])
        out.append(
            f'<div class="turn"><div class="turn-num">Turn {i}</div>'
            f'<div class="response-col">{resp_html}</div>'
            f'<div class="prompt-col">{prompt_html}</div>'
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
            elif parts[0] == "blob" and len(parts) == 4:
                self.serve_blob(parts[1], parts[2], parts[3])
            elif parts[0] == "delete" and len(parts) == 3:
                self.confirm_delete(parts[1], parts[2])
            else:
                self._send("<h1>404 Not Found</h1>", 404)
        except Exception as e:
            self._send(f"<h1>Error</h1><pre>{html.escape(str(e))}</pre>", 500)

    def list_projects(self):
        query_string = urlparse(self.path).query
        sort = parse_sort(query_string)
        q = parse_query(query_string)
        out = [PAGE_HEAD.format(title="Claude Code History"), '<div class="narrow">']
        out.append("<h1>Claude Code — Conversation History</h1>")
        out.append(f'<p class="meta">{PROJECTS_DIR}</p>')
        if not PROJECTS_DIR.exists():
            out.append("<p>No projects directory found.</p>")
        else:
            out.append(render_controls("/", sort, q))
            entries = []
            for folder in PROJECTS_DIR.iterdir():
                if not folder.is_dir():
                    continue
                files = list(folder.glob("*.jsonl"))
                if not files:
                    continue
                newest = max(files, key=lambda f: f.stat().st_mtime)
                name = project_display_name(folder.name, read_jsonl(newest))
                if not matches_query(q, name, folder.name):
                    continue
                entries.append((folder, files, newest, name))

            def sort_key(entry):
                folder, files, newest, name = entry
                if sort == "name":
                    return name.lower()
                if sort == "size":
                    return -sum(f.stat().st_size for f in files)
                return -max(f.stat().st_mtime for f in files)

            entries.sort(key=sort_key)
            if not entries:
                out.append('<p class="meta">No projects match your search.</p>')
            for folder, files, newest, name in entries:
                last_mod = fmt_time(
                    datetime.fromtimestamp(newest.stat().st_mtime).isoformat()
                )
                total_size = human_size(sum(f.stat().st_size for f in files))
                out.append(
                    f'<a class="card" href="/project/{html.escape(folder.name)}">'
                    f"<h2>{html.escape(name)}</h2>"
                    f'<div class="meta">{len(files)} session(s) · {total_size} · '
                    f"last activity {last_mod}</div>"
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
        query_string = urlparse(self.path).query
        sort = parse_sort(query_string)
        q = parse_query(query_string)
        out = [PAGE_HEAD.format(title=folder_name), '<div class="narrow">']
        out.append('<a class="backlink" href="/">← All projects</a>')
        out.append(f"<h1>{html.escape(folder_name)}</h1>")
        out.append(render_controls(f"/project/{html.escape(folder_name)}", sort, q))
        entries = []
        for f in folder.glob("*.jsonl"):
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
            title = session_title(records)
            if not matches_query(q, title, preview):
                continue
            entries.append((f, title, preview, msg_count, first_ts))

        def sort_key(entry):
            f, title, preview, msg_count, first_ts = entry
            if sort == "name":
                return title.lower()
            if sort == "size":
                return -f.stat().st_size
            return -f.stat().st_mtime

        entries.sort(key=sort_key)
        if not entries:
            out.append('<p class="meta">No sessions match your search.</p>')
        for f, title, preview, msg_count, first_ts in entries:
            mtime = fmt_time(
                datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            )
            size = human_size(f.stat().st_size)
            out.append(
                '<div class="card-row">'
                f'<a class="card" href="/session/{html.escape(folder_name)}/{html.escape(f.name)}">'
                f"<h2>{html.escape(title)}</h2>"
                f'<div class="meta">{fmt_time(first_ts) or mtime} · {msg_count} messages · {size}</div>'
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
        records = read_jsonl_cached(path)
        title = session_title(records)
        out = [PAGE_HEAD.format(title=title)]
        out.append(
            f'<a class="backlink" href="/project/{html.escape(folder_name)}">← {html.escape(folder_name)}</a>'
        )
        out.append(f"<h1>{html.escape(title)}</h1>")
        out.append(
            f'<p class="meta">{html.escape(filename)} · '
            f'<a href="/session/{html.escape(folder_name)}/{html.escape(filename)}" '
            'title="Reload from disk">⟳ Refresh</a> · '
            f'<a class="del-btn" style="padding:0;font-size:13px" '
            f'href="/delete/{html.escape(folder_name)}/{html.escape(filename)}">🗑️ Delete this session</a></p>'
        )
        image_ctx = {"folder": folder_name, "filename": filename, "next": [0]}
        out.append(render_turns_html(records, image_ctx))
        out.append(PAGE_TAIL)
        self._send("".join(out))

    def serve_blob(self, folder_name, filename, index_str):
        path = PROJECTS_DIR / folder_name / filename
        if not path.is_file() or not index_str.isdigit():
            self._send("<h1>404</h1>", 404)
            return
        index = int(index_str)
        records = read_jsonl_cached(path)
        for i, block in enumerate(iter_image_blocks(records)):
            if i != index:
                continue
            source = block.get("source") or {}
            data = source.get("data")
            if source.get("type") != "base64" or not data:
                break
            try:
                raw = base64.b64decode(data)
            except Exception:
                break
            self.send_response(200)
            self.send_header("Content-Type", source.get("media_type") or "image/png")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(raw)
            return
        self._send("<h1>404</h1>", 404)

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

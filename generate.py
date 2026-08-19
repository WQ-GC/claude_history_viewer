#!/usr/bin/env python3
"""Generate a static, server-free HTML viewer for Claude Code session history.

Writes plain .html files (linked with relative <a href> tags) into ./static/.
Open static/index.html directly in a browser -- no server, no Node.js needed.
Re-run any time to refresh with new sessions.
"""
import re
import webbrowser
from pathlib import Path

from server import (
    PROJECTS_DIR,
    PAGE_HEAD,
    PAGE_TAIL,
    read_jsonl,
    fmt_time,
    project_display_name,
    text_of_content,
    session_title,
    render_turns_html,
)
from datetime import datetime
import html as htmlmod

OUT_DIR = Path(__file__).parent / "static"


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def project_file(folder_name):
    return f"project__{safe_name(folder_name)}.html"


def session_file(folder_name, filename):
    return f"session__{safe_name(folder_name)}__{safe_name(filename)}.html"


def write(path, content):
    path.write_text(content, encoding="utf-8")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    index_parts = [PAGE_HEAD.format(title="Claude Code History"), '<div class="narrow">']
    index_parts.append("<h1>Claude Code — Conversation History</h1>")
    index_parts.append(f'<p class="meta">{PROJECTS_DIR}</p>')

    if not PROJECTS_DIR.exists():
        index_parts.append("<p>No projects directory found.</p>")
    else:
        folders = sorted(
            [p for p in PROJECTS_DIR.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        total_sessions = 0
        for folder in folders:
            files = sorted(
                folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
            )
            if not files:
                continue

            first_records = read_jsonl(files[0])[:5]
            display_name = project_display_name(folder.name, first_records)
            newest_mtime = fmt_time(
                datetime.fromtimestamp(files[0].stat().st_mtime).isoformat()
            )

            # --- per-project session list page ---
            proj_parts = [PAGE_HEAD.format(title=folder.name), '<div class="narrow">']
            proj_parts.append('<a class="backlink" href="index.html">← All projects</a>')
            proj_parts.append(f"<h1>{htmlmod.escape(display_name)}</h1>")

            session_count = 0
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
                session_count += 1
                total_sessions += 1

                # --- session detail page ---
                title = session_title(records)
                sess_parts = [PAGE_HEAD.format(title=title)]
                sess_parts.append(
                    f'<a class="backlink" href="{project_file(folder.name)}">← {htmlmod.escape(display_name)}</a>'
                )
                sess_parts.append(f"<h1>{htmlmod.escape(title)}</h1>")
                sess_parts.append(f'<p class="meta">{htmlmod.escape(f.name)}</p>')
                sess_parts.append(render_turns_html(records))
                sess_parts.append(PAGE_TAIL)
                write(OUT_DIR / session_file(folder.name, f.name), "".join(sess_parts))

                f_mtime = fmt_time(
                    datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                )
                proj_parts.append(
                    f'<a class="card" href="{session_file(folder.name, f.name)}">'
                    f"<h2>{htmlmod.escape(title)}</h2>"
                    f'<div class="meta">{fmt_time(first_ts) or f_mtime} · {msg_count} messages</div>'
                    f'<div class="preview">{htmlmod.escape(preview[:400])}</div>'
                    f"</a>"
                )

            proj_parts.append("</div>")
            proj_parts.append(PAGE_TAIL)
            write(OUT_DIR / project_file(folder.name), "".join(proj_parts))

            if session_count == 0:
                continue
            index_parts.append(
                f'<a class="card" href="{project_file(folder.name)}">'
                f"<h2>{htmlmod.escape(display_name)}</h2>"
                f'<div class="meta">{session_count} session(s) · last activity {newest_mtime}</div>'
                f"</a>"
            )

        index_parts.append(f'<p class="meta">Generated: {total_sessions} sessions total.</p>')

    index_parts.append("</div>")
    index_parts.append(PAGE_TAIL)
    index_path = OUT_DIR / "index.html"
    write(index_path, "".join(index_parts))
    print(f"Wrote static site to {OUT_DIR}")
    print(f"Open: {index_path}")
    try:
        webbrowser.open(index_path.as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()

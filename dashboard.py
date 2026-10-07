"""Build one self-contained local HTML dashboard of jobs, resume variants, runs and trial logs.

The page embeds personal data (job files, resume PDFs, run notes). Open it from
disk only; never publish or share it. Standard library only, no network access.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import os
import re
import shlex
import sys
import textwrap
from collections import Counter
from datetime import datetime
from pathlib import Path

from assistant import GuardError, atomic_text, require_python


MAX_EMBED_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_CHARS = 4000
MAX_EVENTS = 3000
MAX_FILES_PER_DIR = 200
MAX_MD_DEPTH = 10
WARNING = "contains personal data; do not publish"
MANIFEST = Path("private/resume_variants/build/manifest.json")
SETUP_FILES = (
    "private/targets.yaml", "private/career_facts.md", "private/answers.md",
    "private/resume_variants/variants.json",
)
FILE_ORDER = (
    "jd.md", "jd.txt", "source.json", "fit.md", "form.json", "resume-plan.md",
    "resume-diff.md", "fact-check.md", "resume.pdf", "pre-submit.json", "application.json",
)
ARTIFACTS = (
    ("JD", "jd.txt"), ("匹配", "fit.md"), ("简历", "resume.pdf"),
    ("预提交", "pre-submit.json"), ("申请", "application.json"),
)
IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
}
BINARY_TYPES = {
    ".pdf": "application/pdf", ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
TRIAL_SUFFIXES = {".md", ".txt", ".log", ".json"}
TONES = {
    "active_verified": "ok", "closed": "none", "blocked": "bad", "unknown": "warn",
    "discovered": "none", "submitted_confirmed": "ok", "submission_unknown": "bad",
    "approved": "ok", "draft": "warn", "failed": "bad", "met": "ok", "unmet": "bad",
    "pass": "ok", "fail": "bad", "dry-run": "warn",
}
STAGE_LABELS = {"ok": "完成", "warn": "部分", "bad": "有问题", "none": "未开始"}
EXEC_TYPES = {
    "thread.started", "turn.started", "turn.completed", "turn.failed",
    "item.started", "item.updated", "item.completed",
}
ROLLOUT_TYPES = {"session_meta", "response_item", "event_msg", "turn_context"}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def chip(text: object, tone: str | None = None) -> str:
    tone = tone or TONES.get(str(text), "none")
    return f'<span class="chip {tone}">{esc(text)}</span>'


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit * 2 // 3], text[-(limit // 3):]
    return f"{head}\n… [省略 {len(text) - len(head) - len(tail):,} 字符] …\n{tail}"


def pre(text: str, cls: str = "") -> str:
    return f'<pre class="{cls}">{esc(text)}</pre>' if cls else f"<pre>{esc(text)}</pre>"


# ---------------------------------------------------------------------------
# Markdown: a small subset; every character of the source is HTML-escaped.

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^`\s]*)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_LIST = re.compile(r"^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_TABLE_SEP = re.compile(r"^ *\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *$")
_TASK = re.compile(r"\[([ xX])\][ \t]+(.*)$", re.S)
_CODE_SPAN = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)", re.S)
_LINK = re.compile(r"(!?)\[([^\]\n]{0,500})\]\(\s*<?([^)\s<>]+)>?(?:\s+\"[^\"\n]*\")?\s*\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_BARE_URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]*[A-Za-z0-9\-_~/#=&%+@]")
_SLOT = re.compile("\x00(\\d+)\x00")


def md_to_html(text: str) -> str:
    """Convert Markdown to HTML. Raw HTML in the source is shown as text."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").expandtabs(4).split("\n")
    return "\n".join(_blocks(lines, 0))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_table(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1]
        and bool(_TABLE_SEP.match(lines[i + 1]))
    )


def _block_start(line: str) -> bool:
    return any(pattern.match(line) for pattern in (_FENCE, _HEADING, _HR, _QUOTE, _LIST))


def _blocks(lines: list[str], depth: int) -> list[str]:
    if depth > MAX_MD_DEPTH:
        return [pre("\n".join(lines))]
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif fence := _FENCE.match(line):
            i = _fenced(lines, i, fence, out)
        elif heading := _HEADING.match(line):
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
        elif _HR.match(line):
            out.append("<hr>")
            i += 1
        elif _is_table(lines, i):
            i = _table(lines, i, out)
        elif _QUOTE.match(line):
            i = _quote(lines, i, out, depth)
        elif _LIST.match(line):
            i = _list(lines, i, out, depth)
        else:
            i = _paragraph(lines, i, out)
    return out


def _fenced(lines: list[str], i: int, fence: re.Match, out: list[str]) -> int:
    marker, lang = fence.group(1), fence.group(2)
    close = re.compile(rf"^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
    body: list[str] = []
    i += 1
    while i < len(lines) and not close.match(lines[i]):
        body.append(lines[i])
        i += 1
    cls = f' class="lang-{esc(lang)}"' if lang else ""
    out.append(f'<pre class="code"><code{cls}>{esc(chr(10).join(body))}</code></pre>')
    return i + 1


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def _table(lines: list[str], i: int, out: list[str]) -> int:
    header = _cells(lines[i])
    aligns = []
    for cell in _cells(lines[i + 1]):
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("center" if left and right else "right" if right else "left" if left else "")
    i += 2
    rows: list[list[str]] = []
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        rows.append(_cells(lines[i]))
        i += 1

    def cell(tag: str, text: str, k: int) -> str:
        style = f' style="text-align:{aligns[k]}"' if k < len(aligns) and aligns[k] else ""
        return f"<{tag}{style}>{_inline(text)}</{tag}>"

    head = "".join(cell("th", text, k) for k, text in enumerate(header))
    body = "".join(
        "<tr>" + "".join(cell("td", row[k] if k < len(row) else "", k) for k in range(len(header))) + "</tr>"
        for row in rows
    )
    out.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
    return i


def _quote(lines: list[str], i: int, out: list[str], depth: int) -> int:
    inner: list[str] = []
    while i < len(lines) and (match := _QUOTE.match(lines[i])):
        inner.append(match.group(1))
        i += 1
    out.append("<blockquote>" + "\n".join(_blocks(inner, depth + 1)) + "</blockquote>")
    return i


def _list(lines: list[str], i: int, out: list[str], depth: int) -> int:
    base = _indent(lines[i])
    block = [lines[i]]
    i += 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and (_indent(lines[j]) > base or (_LIST.match(lines[j]) and _indent(lines[j]) == base)):
                block.extend(lines[i:j])
                i = j
                continue
            break
        if _indent(line) > base or (_LIST.match(line) and _indent(line) >= base):
            block.append(line)
        elif _block_start(line) or _is_table(lines, i):
            break
        else:
            block.append(line)  # lazy continuation of the previous item
        i += 1
    out.extend(_render_list(block, base, depth))
    return i


def _render_list(block: list[str], base: int, depth: int) -> list[str]:
    items: list[tuple[bool, str, list[str]]] = []
    for line in block:
        match = _LIST.match(line)
        if match and _indent(line) - base < 2:
            items.append((match.group(2)[0].isdigit(), match.group(2), [match.group(3)]))
        else:
            items[-1][2].append(line)
    parts: list[str] = []
    kind: bool | None = None
    for ordered, marker, body in items:
        if kind is None or kind != ordered:
            if kind is not None:
                parts.append("</ol>" if kind else "</ul>")
            start = int(marker[:-1]) if ordered else 1
            parts.append(("<ol>" if start == 1 else f'<ol start="{start}">') if ordered else "<ul>")
            kind = ordered
        parts.append(f"<li>{_list_item(body, depth)}</li>")
    parts.append("</ol>" if kind else "</ul>")
    return parts


def _list_item(body: list[str], depth: int) -> str:
    first, rest = body[0], body[1:]
    prefix = ""
    task = _TASK.match(first)
    if task:
        prefix = '<span class="task">' + ("☑" if task.group(1) in "xX" else "☐") + "</span> "
        first = task.group(2)
    text = [first]
    k = 0
    while k < len(rest) and rest[k].strip() and not _block_start(rest[k].lstrip()):
        text.append(rest[k].strip())
        k += 1
    result = prefix + _inline("\n".join(text))
    remainder = rest[k:]
    if any(line.strip() for line in remainder):
        result += "\n".join(_blocks(textwrap.dedent("\n".join(remainder)).split("\n"), depth + 1))
    return result


def _paragraph(lines: list[str], i: int, out: list[str]) -> int:
    buffer = [lines[i].lstrip()]
    i += 1
    while i < len(lines) and lines[i].strip() and not (_block_start(lines[i]) or _is_table(lines, i)):
        buffer.append(lines[i].lstrip())
        i += 1
    out.append(f"<p>{_inline(chr(10).join(buffer))}</p>")
    return i


def safe_href(url: str) -> str | None:
    """Return a link target that cannot run script, or None."""
    cleaned = re.sub(r"[\x00-\x20\x7f]", "", html.unescape(url))
    if re.match(r"(?i)(?:https?:|mailto:)", cleaned) or cleaned.startswith("#"):
        return cleaned
    if re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:", cleaned) or cleaned.startswith(("//", "\\", "/\\")):
        return None
    return cleaned


def link_html(href: str, label_html: str) -> str:
    external = ' target="_blank"' if href.lower().startswith("http") else ""
    return f'<a href="{esc(href)}" rel="noopener noreferrer"{external}>{label_html}</a>'


def _emphasis(text: str) -> str:
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)", r"<em>\1</em>", text)
    return re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text)


def _inline(text: str) -> str:
    slots: list[str] = []

    def keep(fragment: str) -> str:
        slots.append(fragment)
        return f"\x00{len(slots) - 1}\x00"

    def link(match: re.Match) -> str:
        image, label, url = match.groups()
        label_html = esc(f"[图片: {label}]") if image else _emphasis(esc(label))
        href = safe_href(url)
        return keep(link_html(href, label_html) if href else label_html)

    def bare(match: re.Match) -> str:
        url = match.group(1) if match.re is _AUTOLINK else match.group(0)
        return keep(link_html(url, esc(url)))

    text = _CODE_SPAN.sub(lambda m: keep(f"<code>{esc(m.group(2).strip())}</code>"), text.replace("\x00", ""))
    text = _LINK.sub(link, text)
    text = _AUTOLINK.sub(bare, text)
    text = _BARE_URL.sub(bare, text)
    text = _emphasis(esc(text))
    text = re.sub(r"(?: {2,}|\\)\n", "<br>\n", text)
    for _ in range(5):
        if "\x00" not in text:
            break
        text = _SLOT.sub(lambda m: slots[int(m.group(1))], text)
    return text


# ---------------------------------------------------------------------------
# Page assembly helpers: lazy <template> views and deduplicated binary blobs.

class Page:
    def __init__(self) -> None:
        self.templates: list[str] = []
        self.blobs: dict[str, tuple[str, str, str]] = {}

    def template(self, body: str) -> str:
        tid = f"tpl-{len(self.templates)}"
        self.templates.append(f'<template id="{tid}">{body}</template>')
        return tid

    def blob(self, data: bytes, mime: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self.blobs:
            bid = f"blob-{len(self.blobs)}"
            self.blobs[digest] = (bid, mime, base64.b64encode(data).decode("ascii"))
        return self.blobs[digest][0]

    def blob_buttons(self, data: bytes, mime: str, name: str) -> str:
        bid = self.blob(data, mime)
        opener = "打开 PDF" if mime == "application/pdf" else "打开"
        return (
            f'<div class="actions"><button type="button" class="btn" data-open-blob="{bid}">{opener}</button>'
            f'<button type="button" class="btn ghost" data-download-blob="{bid}" data-name="{esc(name)}">'
            "下载</button></div>"
        )

    def filebox(self, items: list[tuple[str, str]]) -> str:
        if not items:
            return '<p class="muted">没有文件。</p>'
        tabs = "".join(
            f'<button type="button" class="tab" role="tab" aria-selected="false" data-tpl="{self.template(view)}">'
            f"{esc(label)}</button>"
            for label, view in items
        )
        return (
            f'<div class="filebox"><div class="tabs" role="tablist">{tabs}</div>'
            '<div class="tab-body" role="tabpanel"></div></div>'
        )

    def tail(self) -> str:
        blobs = "".join(
            f'<script type="text/plain" id="{bid}" data-mime="{esc(mime)}">{data}</script>'
            for bid, mime, data in self.blobs.values()
        )
        return "".join(self.templates) + blobs


def list_files(folder: Path, skip: set[Path]) -> list[Path]:
    found: list[Path] = []
    for current, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            path = Path(current) / name
            if name.startswith(".") or (name.startswith("dashboard") and name.endswith(".html")):
                continue
            if path.resolve() in skip or any(parent in skip for parent in path.resolve().parents):
                continue
            found.append(path)
    return found


def ordered_files(folder: Path, skip: set[Path]) -> list[tuple[str, Path]]:
    pairs = [(path.relative_to(folder).as_posix(), path) for path in list_files(folder, skip)]

    def key(pair: tuple[str, Path]) -> tuple[int, int, str]:
        rel = pair[0]
        rank = FILE_ORDER.index(rel) if rel in FILE_ORDER else len(FILE_ORDER)
        return rank, rel.count("/"), rel

    return sorted(pairs, key=key)


def decode_text(data: bytes) -> str | None:
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8").lstrip("﻿")
    except UnicodeDecodeError:
        return None


def load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def form_table(questions: list) -> str:
    rows = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        fields = [field for field in question.get("fields") or [] if isinstance(field, dict)]
        names = ", ".join(f"{field.get('name', '')}: {field.get('type', '')}" for field in fields)
        options: list[str] = []
        for source in [question, *fields]:
            for value in source.get("values") or source.get("options") or source.get("option_labels") or []:
                label = value.get("label", value.get("value", "")) if isinstance(value, dict) else value
                options.append(str(label))
        required = question.get("required")
        flag = chip("必填", "warn") if required else "否" if required is False else ""
        rows.append(
            f"<tr><td>{esc(question.get('label', ''))}</td><td>{flag}</td>"
            f"<td><code>{esc(names)}</code></td><td>{esc(' / '.join(options[:30]))}</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<div class="table-wrap"><table class="data"><thead><tr><th>问题</th><th>必填</th><th>字段</th>'
        f'<th>选项</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def file_view(page: Page, path: Path, rel: str) -> str:
    """Render one file: Markdown, JSON, text, PDF, image, Codex log or download."""
    stat = path.stat()
    when = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    if stat.st_size > MAX_EMBED_BYTES:
        meta = f'<div class="filemeta"><code>{esc(rel)}</code> · {human_size(stat.st_size)} · 修改于 {when}</div>'
        return meta + (
            f'<p class="note warn">文件超过 {MAX_EMBED_BYTES // 1024 // 1024} MB，未嵌入。请在本机打开：'
            f"<code>{esc(path)}</code></p>"
        )
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    meta = (
        f'<div class="filemeta"><code>{esc(rel)}</code> · {human_size(len(data))} · 修改于 {when}'
        f' · sha256 <code title="{digest}">{digest[:12]}</code></div>'
    )
    return meta + file_body(page, path, data)


def file_body(page: Page, path: Path, data: bytes) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        bid = page.blob(data, "application/pdf")
        frame = f'<iframe class="pdf-frame" data-blob-src="{bid}" title="{esc(path.name)}"></iframe>'
        return page.blob_buttons(data, "application/pdf", path.name) + frame
    if suffix in IMAGE_TYPES:
        encoded = base64.b64encode(data).decode("ascii")
        return f'<img class="shot" alt="{esc(path.name)}" src="data:{IMAGE_TYPES[suffix]};base64,{encoded}">'
    text = decode_text(data)
    if text is None:
        mime = BINARY_TYPES.get(suffix, "application/octet-stream")
        return '<p class="muted">二进制文件，无法直接预览。</p>' + page.blob_buttons(data, mime, path.name)
    if suffix in {".md", ".markdown"}:
        return f'<div class="md">{md_to_html(text)}</div>'
    if suffix == ".json":
        try:
            value = json.loads(text)
        except ValueError:
            return '<p class="note warn">JSON 无法解析，显示原文。</p>' + pre(text)
        questions = value.get("questions") if isinstance(value, dict) else None
        table = form_table(questions) if isinstance(questions, list) else ""
        return table + pre(json.dumps(value, ensure_ascii=False, indent=2), "json")
    if suffix == ".jsonl":
        log = parse_codex_log(path)
        if log is not None:
            return render_log(log)
    return pre(text)


# ---------------------------------------------------------------------------
# Codex logs: `codex exec --json` events and interactive TUI rollouts.

def read_jsonl(path: Path) -> tuple[list[dict], int]:
    records: list[dict] = []
    bad = 0
    with path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                bad += 1
    return records, bad


def log_kind(records: list[dict]) -> str | None:
    for record in records[:200]:
        kind = record.get("type")
        if kind in EXEC_TYPES:
            return "exec"
        if kind in ROLLOUT_TYPES and isinstance(record.get("payload"), dict):
            return "rollout"
    return None


def parse_codex_log(path: Path) -> dict | None:
    """Return a timeline for a Codex JSONL log, or None for other JSONL files."""
    records, bad = read_jsonl(path)
    kind = log_kind(records)
    if kind is None:
        return None
    log = {
        "kind": kind, "name": path.name, "path": str(path), "meta": {}, "events": [], "usage": {},
        "reasoning": 0, "bad_lines": bad,
    }
    if kind == "exec":
        parse_exec(records, log)
    else:
        parse_rollout(records, log)
    return log


def event(log: dict, cat: str, label: str, title: str, **fields: object) -> dict:
    item = {"cat": cat, "label": label, "title": title, "time": "", "duration": "", "status": "",
            "exit_code": None, "markdown": "", "items": [], "detail": "", "output": ""}
    item.update(fields)
    log["events"].append(item)
    return item


def is_failed(item: dict) -> bool:
    status = str(item.get("status") or "").lower()
    code = item.get("exit_code")
    return item["cat"] == "error" or (isinstance(code, int) and code != 0) or any(
        word in status for word in ("fail", "declin", "error")
    )


_SHELL = re.compile(r"^(?:\S*/)?(?:bash|zsh|sh)\s+-l?c\s+(.+)$", re.S)


def shell_text(command: object) -> str:
    if isinstance(command, list):
        parts = [str(part) for part in command]
        if len(parts) == 3 and re.fullmatch(r"(?:\S*/)?(?:bash|zsh|sh)", parts[0]) and parts[1] in {"-lc", "-c"}:
            return parts[2]
        return shlex.join(parts)
    text = str(command or "")
    match = _SHELL.match(text.strip())
    if match:
        try:
            inner = shlex.split(match.group(1))
        except ValueError:
            return text
        if len(inner) == 1:
            return inner[0]
    return text


def content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content if isinstance(content, list) else []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif isinstance(item, dict) and "image" in str(item.get("type", "")).lower():
            parts.append("[图片]")
    return "\n".join(parts)


def output_text(output: object) -> tuple[str, int | None]:
    code = None
    if isinstance(output, str):
        text = output
        try:
            parsed = json.loads(output)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and "output" in parsed:
            text = str(parsed.get("output") or "")
            metadata = parsed.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("exit_code"), int):
                code = metadata["exit_code"]
    elif isinstance(output, dict):
        text = content_text(output.get("content")) or json.dumps(output, ensure_ascii=False, indent=2)
    else:
        text = content_text(output)
    if code is None:
        match = re.search(r"(?:Exit code|Process exited with code|exit_code)[:=]?\s*(-?\d+)", text)
        code = int(match.group(1)) if match else None
    return text, code


def pretty(value: object) -> str:
    if value in (None, "", {}, []):
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def add_usage(total: dict, usage: object) -> None:
    for key, value in (usage.items() if isinstance(usage, dict) else []):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def usage_text(usage: dict) -> str:
    def get(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    if not usage:
        return ""
    total = get("total_tokens") or get("input_tokens") + get("output_tokens")
    parts = [f"输入 {get('input_tokens'):,}"]
    if get("cached_input_tokens"):
        parts[0] += f"（缓存 {get('cached_input_tokens'):,}）"
    parts.append(f"输出 {get('output_tokens'):,}")
    if get("reasoning_output_tokens"):
        parts.append(f"推理 {get('reasoning_output_tokens'):,}")
    parts.append(f"合计 {total:,}")
    return " · ".join(parts)


def parse_exec(records: list[dict], log: dict) -> None:
    started: dict[str, dict] = {}
    turns = 0
    for record in records:
        kind = record.get("type")
        if kind == "thread.started":
            log["meta"]["thread_id"] = record.get("thread_id", "")
        elif kind == "turn.started":
            turns += 1
            event(log, "turn", "回合", f"回合 {turns} 开始")
        elif kind == "turn.completed":
            add_usage(log["usage"], record.get("usage"))
            event(log, "turn", "用量", f"回合 {turns} 结束 · {usage_text(record.get('usage') or {})}")
        elif kind == "turn.failed":
            error = record.get("error")
            event(log, "error", "错误", "回合失败", output=pretty(error.get("message") if isinstance(error, dict) else error))
        elif kind == "error":
            event(log, "error", "错误", str(record.get("message") or "error")[:200], output=pretty(record.get("message")))
        elif kind in {"item.started", "item.updated"} and isinstance(record.get("item"), dict):
            started[str(record["item"].get("id"))] = record["item"]
        elif kind == "item.completed" and isinstance(record.get("item"), dict):
            started.pop(str(record["item"].get("id")), None)
            exec_item(log, record["item"])
    for item in started.values():
        exec_item(log, dict(item, status=item.get("status") or "in_progress"))
    log["meta"]["回合数"] = turns


def exec_item(log: dict, item: dict) -> None:
    kind = item.get("type")
    status = str(item.get("status") or "")
    if kind == "command_execution":
        event(log, "command", "命令", shell_text(item.get("command")), status=status,
              exit_code=item.get("exit_code"), output=str(item.get("aggregated_output") or ""))
    elif kind == "agent_message":
        event(log, "message", "Agent", "", markdown=str(item.get("text") or ""))
    elif kind == "reasoning":
        log["reasoning"] += 1
    elif kind == "file_change":
        changes = [c for c in item.get("changes") or [] if isinstance(c, dict)]
        event(log, "file", "文件", f"{len(changes)} 个文件", status=status,
              items=[f"{c.get('kind', '')} {c.get('path', '')}".strip() for c in changes])
    elif kind == "mcp_tool_call":
        event(log, "tool", "MCP", f"{item.get('server', '')}.{item.get('tool', '')}", status=status,
              detail=pretty(item.get("arguments")), output=pretty(item.get("error") or item.get("result")))
    elif kind == "web_search":
        event(log, "tool", "搜索", str(item.get("query") or ""), status=status)
    elif kind == "todo_list":
        todos = [t for t in item.get("items") or [] if isinstance(t, dict)]
        event(log, "message", "计划", f"{len(todos)} 项",
              items=[("☑ " if t.get("completed") else "☐ ") + str(t.get("text", "")) for t in todos])
    elif kind == "error":
        event(log, "error", "错误", str(item.get("message") or "error")[:200], output=pretty(item.get("message")))
    else:
        event(log, "tool", "其他", str(kind), status=status, detail=pretty(item))


def clock(timestamp: object) -> str:
    text = str(timestamp or "")
    return text[11:19] if len(text) >= 19 and text[10] == "T" else ""


def duration_text(value: object) -> str:
    if isinstance(value, dict) and isinstance(value.get("secs"), (int, float)):
        return f"{value['secs'] + (value.get('nanos') or 0) / 1e9:.1f}s"
    if isinstance(value, (int, float)):
        return f"{value:.1f}s"
    return ""


# Raw tool calls that item_completed records already show (as CommandExecution or FileChange).
ITEM_COVERED_CALLS = {"exec", "shell", "exec_command", "write_stdin", "local_shell_call", "apply_patch"}


def completed_items(records: list[dict]) -> list[dict]:
    return [
        record["payload"]["item"] for record in records
        if record.get("type") == "event_msg" and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") == "item_completed" and isinstance(record["payload"].get("item"), dict)
    ]


def raw_calls(records: list[dict]) -> dict[str, dict]:
    """call_id -> the response_item payload of that tool call, for items that only carry an id."""
    return {
        str(record["payload"]["call_id"]): record["payload"] for record in records
        if record.get("type") == "response_item" and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") in {"function_call", "custom_tool_call"} and record["payload"].get("call_id")
    }


def parse_rollout(records: list[dict], log: dict) -> None:
    # Newer Codex versions also log event_msg/item_completed items (with exit
    # codes); when present they replace the raw response_item calls they cover.
    # Other raw calls (waits, user-input requests, agent tools) are still shown.
    items = completed_items(records)
    has_items = bool(items)
    covered = {str(item.get("id")) for item in items}
    by_call = raw_calls(records)
    outputs = {
        str(record["payload"].get("call_id")): record["payload"].get("output") for record in records
        if record.get("type") == "response_item" and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") in {"function_call_output", "custom_tool_call_output"}
    }
    calls: dict[str, dict] = {}
    turns = 0
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind, sub, when = record.get("type"), payload.get("type"), clock(record.get("timestamp"))
        if kind == "session_meta":
            for key in ("id", "cli_version", "originator", "source", "cwd", "model_provider"):
                if payload.get(key):
                    log["meta"][key] = payload[key]
        elif kind == "turn_context":
            turns += 1
            sandbox = payload.get("sandbox_policy")
            sandbox = sandbox.get("type") if isinstance(sandbox, dict) else sandbox
            context = [f"approval={payload['approval_policy']}"] if payload.get("approval_policy") else []
            context += [f"sandbox={sandbox}"] if sandbox else []
            context += [f"model={payload['model']}"] if payload.get("model") else []
            log["meta"]["approval / sandbox"] = " · ".join(context)
            event(log, "turn", "回合", " · ".join([f"回合 {turns}", *context]), time=when)
        elif kind == "event_msg" and sub == "token_count":
            info = payload.get("info")
            if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                log["usage"] = dict(info["total_token_usage"])
        elif kind == "event_msg" and sub in {"error", "stream_error"}:
            message = payload.get("message")
            event(log, "error", "错误", str(message or sub)[:200], time=when, output=pretty(message))
        elif kind == "event_msg" and sub == "turn_aborted":
            event(log, "error", "中断", f"回合中断：{payload.get('reason', '')}", time=when)
        elif kind == "event_msg" and sub == "item_completed" and isinstance(payload.get("item"), dict):
            item = payload["item"]
            call = by_call.get(str(item.get("id")))
            rollout_item(log, item, when, call, outputs.get(str(item.get("id"))))
        elif kind == "response_item" and not has_items:
            response_item(log, payload, when, calls)
        elif kind == "response_item" and uncovered(payload, covered, by_call):
            response_item(log, payload, when, calls)
    log["meta"]["回合数"] = turns


def uncovered(payload: dict, covered: set[str], by_call: dict[str, dict]) -> bool:
    """A raw tool call (or the output of one) that no item_completed record shows."""
    kind, call_id = payload.get("type"), str(payload.get("call_id") or "")
    if kind in {"function_call_output", "custom_tool_call_output"}:
        call = by_call.get(call_id)
        return call is not None and uncovered(call, covered, by_call)
    if kind not in {"function_call", "custom_tool_call"}:
        return False
    return call_id not in covered and str(payload.get("name") or "") not in ITEM_COVERED_CALLS


def call_arguments(call: dict | None) -> str:
    if not call:
        return ""
    raw = call.get("arguments") if call.get("type") == "function_call" else call.get("input")
    try:
        return pretty(json.loads(raw)) if isinstance(raw, str) else pretty(raw)
    except ValueError:
        return str(raw)


def questions_text(questions: object) -> list[str]:
    lines = []
    for question in questions if isinstance(questions, list) else []:
        if isinstance(question, dict):
            options = [str(option) for option in question.get("options") or []]
            lines.append(str(question.get("title") or "") + (f"（选项：{' / '.join(options)}）" if options else ""))
    return lines


def rollout_item(log: dict, item: dict, when: str, call: dict | None = None, output: object = None) -> None:
    kind = item.get("type")
    status = str(item.get("status") or "")
    if kind == "CommandExecution":
        event(log, "command", "命令", shell_text(item.get("command")), time=when, status=status,
              exit_code=item.get("exit_code"), duration=duration_text(item.get("duration")),
              output=str(item.get("aggregated_output") or ""))
    elif kind == "AgentMessage":
        label = "Agent" + (f" · {item['phase']}" if item.get("phase") else "")
        asked = questions_text(item.get("questions"))
        event(log, "message", "Agent · 向用户提问" if asked else label, "", time=when,
              markdown=content_text(item.get("content")), items=asked,
              output=output_text(output)[0] if output is not None else "")
    elif kind == "UserMessage":
        user_event(log, content_text(item.get("content")), when)
    elif kind == "Reasoning":
        log["reasoning"] += 1
    elif kind == "FileChange":
        changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
        kinds = {path: c.get("type", "") if isinstance(c, dict) else "" for path, c in changes.items()}
        diffs = [str(c.get("unified_diff") or "") for c in changes.values() if isinstance(c, dict)]
        event(log, "file", "文件", f"{len(changes)} 个文件", time=when, status=status,
              items=[f"{kind} {path}".strip() for path, kind in kinds.items()], detail="\n".join(d for d in diffs if d))
    elif kind == "McpToolCall":
        event(log, "tool", "MCP", f"{item.get('server', '')}.{item.get('tool', '')}", time=when, status=status,
              duration=duration_text(item.get("duration")), detail=pretty(item.get("arguments")),
              output=pretty(item.get("result")))
    elif kind in {"Extension", "WebSearch"}:
        label = "搜索" if kind == "WebSearch" or "search" in str(item.get("kind", "")).lower() else "扩展"
        event(log, "tool", label, str(item.get("query") or item.get("kind") or kind), time=when, status=status,
              output=pretty(item.get("results")))
    elif kind == "ImageView":
        event(log, "tool", "看图", str(item.get("path") or ""), time=when)
    elif kind == "SubAgentActivity":
        name = f"{call.get('namespace')}.{call.get('name')}" if call and call.get("namespace") else (call or {}).get("name")
        title = " · ".join(str(part) for part in (item.get("kind"), item.get("agent_path"), name) if part)
        event(log, "tool", "子代理", title, time=when, detail=call_arguments(call),
              output=output_text(output)[0] if output is not None else "")
    elif kind == "CollabAgentToolCall":
        receivers = ", ".join(str(agent) for agent in item.get("receiver_agents") or [])
        event(log, "tool", "子代理", " · ".join(part for part in (str(item.get("tool") or ""), receivers) if part),
              time=when, status=status, detail=pretty(item.get("agents_states")))
    elif kind == "ContextCompaction":
        event(log, "turn", "压缩", "上下文已压缩", time=when)
    else:
        event(log, "tool", "其他", str(kind), time=when, status=status, detail=pretty(item))


def user_event(log: dict, text: str, when: str) -> None:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    event(log, "message", "用户", first[:160], time=when, output=text)


def response_item(log: dict, payload: dict, when: str, calls: dict[str, dict]) -> None:
    kind = payload.get("type")
    if kind == "message":
        text = content_text(payload.get("content"))
        if payload.get("role") == "assistant":
            event(log, "message", "Agent", "", time=when, markdown=text)
        elif payload.get("role") == "user":
            user_event(log, text, when)
    elif kind == "reasoning":
        log["reasoning"] += 1
    elif kind in {"function_call", "custom_tool_call", "local_shell_call"}:
        item = call_event(log, payload, when)
        if payload.get("call_id"):
            calls[str(payload["call_id"])] = item
    elif kind in {"function_call_output", "custom_tool_call_output"}:
        text, code = output_text(payload.get("output"))
        item = calls.get(str(payload.get("call_id")))
        if item is None:
            event(log, "tool", "输出", "未匹配的工具输出", time=when, output=text)
            return
        item["output"] = text
        if code is not None:
            item["exit_code"] = code
        first = text.split("\n", 1)[0]
        if first.startswith("Script ") and not item["status"]:
            item["status"] = first[7:40]
    elif kind == "web_search_call":
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        status = str(payload.get("status") or "")
        event(log, "tool", "搜索", str(action.get("query") or "web_search"), time=when, status=status)


def call_event(log: dict, payload: dict, when: str) -> dict:
    kind, name = payload.get("type"), str(payload.get("name") or payload.get("type"))
    if kind == "local_shell_call":
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        status = str(payload.get("status") or "")
        return event(log, "command", "命令", shell_text(action.get("command")), time=when, status=status)
    if kind == "custom_tool_call":
        raw = str(payload.get("input") or "")
        if name == "apply_patch":
            paths = re.findall(r"^\*\*\* (Add|Update|Delete) File: (.+)$", raw, re.M)
            return event(log, "file", "文件", f"{len(paths)} 个文件", time=when,
                         items=[f"{verb.lower()} {path}" for verb, path in paths], detail=raw)
        if name == "exec":
            return event(log, "command", "脚本", raw, time=when)
        return event(log, "tool", "工具", name, time=when, detail=raw)
    try:
        args = json.loads(str(payload.get("arguments") or "{}"))
    except ValueError:
        args = payload.get("arguments")
    if isinstance(args, dict) and (args.get("command") or args.get("cmd")):
        return event(log, "command", "命令", shell_text(args.get("command") or args.get("cmd")), time=when)
    title = f"{payload['namespace']}.{name}" if payload.get("namespace") else name
    return event(log, "tool", "工具", title, time=when, detail=pretty(args))


def log_counts(log: dict) -> Counter:
    counts: Counter = Counter()
    for item in log["events"]:
        counts[item["cat"]] += 1
        counts["failed"] += item["cat"] == "command" and is_failed(item)
        counts["paths"] += len(item["items"]) if item["cat"] == "file" else 0
    return counts


def render_event(number: int, item: dict) -> str:
    failed = is_failed(item)
    title = item["title"]
    head = [f'<span class="ev-n">{number}</span>', f'<span class="badge b-{item["cat"]}">{esc(item["label"])}</span>']
    if title:
        first = title.split("\n", 1)[0]
        short = first if len(first) <= 200 else first[:200] + "…"
        tag = "code" if item["cat"] == "command" else "span"
        head.append(f'<{tag} class="ev-title">{esc(short)}</{tag}>')
    code = item["exit_code"]
    if isinstance(code, int):
        head.append(chip(f"exit {code}", "ok" if code == 0 else "bad"))
    elif item["status"]:
        head.append(chip(item["status"], "bad" if failed else "warn" if "progress" in item["status"] else "none"))
    stamp = " · ".join(part for part in (item["time"], item["duration"]) if part)
    if stamp:
        head.append(f'<span class="ev-time">{esc(stamp)}</span>')
    body = []
    if item["markdown"]:
        body.append(f'<div class="md">{md_to_html(item["markdown"])}</div>')
    if item["items"]:
        lines = "".join(f"<li><code>{esc(line)}</code></li>" for line in item["items"][:200])
        body.append(f'<ul class="paths">{lines}</ul>')
    if item["cat"] == "command" and ("\n" in title or len(title) > 200):
        body.append(f"<details><summary>完整命令（{len(title):,} 字符）</summary>{pre(clip(title))}</details>")
    for key, label in (("detail", "详情"), ("output", "全文" if item["label"] == "用户" else "输出")):
        text = item[key]
        if text:
            body.append(f"<details><summary>{label}（{len(text):,} 字符）</summary>{pre(clip(text))}</details>")
    classes = f"ev ev-{item['cat']}" + (" failed" if failed else "")
    return (
        f'<li class="{classes}" data-kind="{item["cat"]}" data-failed="{int(failed)}">'
        f'<div class="ev-head">{"".join(head)}</div>{"".join(body)}</li>'
    )


def render_log(log: dict) -> str:
    counts = log_counts(log)
    kind = "codex exec --json 事件流" if log["kind"] == "exec" else "Codex 交互会话 rollout"
    meta = [f"<dt>类型</dt><dd>{esc(kind)}</dd>"]
    meta += [f"<dt>{esc(key)}</dt><dd><code>{esc(value)}</code></dd>" for key, value in log["meta"].items()]
    if log["usage"]:
        meta.append(f"<dt>Token</dt><dd>{esc(usage_text(log['usage']))}</dd>")
    if log["bad_lines"]:
        meta.append(f"<dt>无法解析</dt><dd>{log['bad_lines']} 行</dd>")
    filters = [("all", "全部", len(log["events"])), ("command", "命令", counts["command"]),
               ("failed", "失败", sum(is_failed(item) for item in log["events"])),
               ("message", "消息", counts["message"]), ("file", "文件改动", counts["file"]),
               ("tool", "工具", counts["tool"]), ("error", "错误", counts["error"])]
    buttons = "".join(
        f'<button type="button" class="tl-btn" data-filter="{key}" aria-pressed="{str(key == "all").lower()}">'
        f"{label} {count}</button>"
        for key, label, count in filters
    )
    shown = log["events"][:MAX_EVENTS]
    note = ""
    if len(log["events"]) > MAX_EVENTS:
        note = f'<p class="note warn">共 {len(log["events"]):,} 条，只显示前 {MAX_EVENTS:,} 条。</p>'
    events = "".join(render_event(n, item) for n, item in enumerate(shown, 1))
    return (
        f'<div class="timeline"><dl class="kv">{"".join(meta)}</dl>'
        f'<div class="tl-filters">{buttons}</div>{note}<ol class="events">{events}</ol></div>'
    )


# ---------------------------------------------------------------------------
# Data collection.

def read_jobs_csv(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    if not path.is_file():
        return [], [], ""
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            rows = [{key: value or "" for key, value in row.items() if key is not None} for row in reader]
            return list(reader.fieldnames or []), rows, ""
    except (OSError, UnicodeError, csv.Error) as error:
        return [], [], f"jobs.csv 无法读取：{error}"


def load_manifest(root: Path) -> tuple[dict | None, str]:
    path = root / MANIFEST
    if not path.is_file():
        return None, ""
    value = load_json_file(path)
    if not isinstance(value, dict) or not isinstance(value.get("variants"), list):
        return None, f"{MANIFEST.as_posix()} 无法解析或结构不符合预期"
    return value, ""


def inside(root: Path, rel: object) -> Path | None:
    if not isinstance(rel, str) or not rel:
        return None
    try:
        path = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    return path if path.is_file() and path.is_relative_to(root) else None


def summarize_test_log(text: str) -> tuple[str, str]:
    ran = [int(n) for n in re.findall(r"^Ran (\d+) tests? in", text, re.M)]
    verdicts = re.findall(r"^(OK|FAILED)\b(.*)$", text, re.M)
    pytest = re.findall(r"^=+ (.*?\b(?:passed|failed|error|errors)\b.*?) in [\d.]+s", text, re.M)
    if verdicts:
        status = "fail" if any(word == "FAILED" for word, _ in verdicts) else "pass"
        extra = "；".join((word + rest).strip() for word, rest in verdicts)
        return status, f"共运行 {sum(ran)} 个测试 · {extra}"
    if pytest:
        status = "fail" if re.search(r"\b(failed|error|errors)\b", pytest[-1]) else "pass"
        return status, pytest[-1]
    return "unknown", "未识别到测试结论"


def job_meta(folder: Path | None) -> dict:
    meta: dict = {"files": {}, "source": None, "pre": None}
    if folder is None:
        return meta
    for name in ("jd.txt", "fit.md", "resume.pdf", "pre-submit.json", "application.json", "source.json"):
        meta["files"][name] = (folder / name).is_file()
    source = load_json_file(folder / "source.json") if meta["files"]["source.json"] else None
    meta["source"] = source if isinstance(source, dict) else None
    pre_submit = load_json_file(folder / "pre-submit.json") if meta["files"]["pre-submit.json"] else None
    meta["pre"] = pre_submit if isinstance(pre_submit, dict) else None
    jd = folder / "jd.txt"
    meta["jd_sha256"] = hashlib.sha256(jd.read_bytes()).hexdigest() if jd.is_file() else ""
    return meta


def recency_chip(source: dict | None) -> str:
    if not source:
        return ""
    recency = source.get("recency") if isinstance(source.get("recency"), dict) else {}
    status = recency.get("status")
    if not status:
        return ""
    age = recency.get("age_days")
    text = f"recency {status}" + (f" · {age} 天" if age is not None else "")
    return chip(text, TONES.get(str(status), "none"))


def url_link(value: str) -> str:
    if value.startswith("https://") and safe_href(value):
        return link_html(value, esc(value))
    return esc(value)


# ---------------------------------------------------------------------------
# Sections.

def stage(name: str, tone: str, value: str, detail: str) -> str:
    return (
        f'<div class="card stage"><div class="stage-top"><span class="stage-name">{esc(name)}</span>'
        f'{chip(STAGE_LABELS[tone], tone)}</div><div class="stage-value">{esc(value)}</div>'
        f'<div class="muted small">{esc(detail)}</div></div>'
    )


def progress_tone(done: int, total: int) -> str:
    return "ok" if total and done == total else "warn" if done else "none"


def counts_line(title: str, counter: Counter) -> str:
    if not counter:
        return ""
    chips = " ".join(chip(f"{key or '(空)'} {n}", TONES.get(key, "none")) for key, n in counter.most_common())
    return f'<div class="count-line"><span class="muted">{esc(title)}</span> {chips}</div>'


def joined(counter: Counter) -> str:
    return " · ".join(f"{key} {value}" for key, value in counter.items())


def overview_section(ctx: dict) -> str:
    root, rows, metas = ctx["root"], ctx["rows"], ctx["metas"]
    setup = [name for name in SETUP_FILES if (root / name).is_file()]
    missing = "、".join(Path(name).name for name in SETUP_FILES if name not in setup)
    variants = ctx["manifest"]["variants"] if ctx["manifest"] else []
    variant_status = Counter(str(v.get("status", "")) for v in variants if isinstance(v, dict))
    source_status = Counter(row.get("source_status", "") for row in rows)
    app_status = Counter(row.get("application_status", "") for row in rows)
    n_jobs = len(ctx["entries"])  # the same set the jobs table lists: jobs.csv rows + folders not in it
    orphans = n_jobs - len(rows)

    def has(name: str) -> int:
        return sum(1 for meta in metas.values() if meta["files"].get(name))

    dry = sum(1 for meta in metas.values() if meta["pre"] and meta["pre"].get("dry_run") is True)
    logs, tests = ctx["logs"], ctx["tests"]
    totals: Counter = Counter()
    for log in logs:
        totals.update(log_counts(log))
    test_status = {status for _, status, _, _ in tests}
    if ctx["manifest_error"]:
        variant_tone = "bad"
    else:
        variant_tone = "ok" if variant_status["approved"] else "warn" if variants else "none"
    cards = [
        stage("配置", progress_tone(len(setup), len(SETUP_FILES)), f"{len(setup)}/{len(SETUP_FILES)}",
              f"缺少：{missing}" if missing else "私有配置齐全"),
        stage("简历版本", variant_tone, f"{variant_status['approved']}/{len(variants)} 已批准",
              ctx["manifest_error"] or joined(variant_status) or "未找到 manifest.json"),
        stage("找岗", "ok" if rows else "warn" if n_jobs else "none", f"{n_jobs} 个岗位",
              " · ".join(part for part in (
                  f"jobs.csv {len(rows)} 行" + (f" + {orphans} 个不在 jobs.csv 的文件夹" if orphans else ""),
                  joined(source_status)) if part) if n_jobs else "jobs.csv 为空或不存在"),
        stage("匹配", progress_tone(has("fit.md"), n_jobs), f"{has('fit.md')}/{n_jobs}", "有 fit.md 的岗位"),
        stage("逐岗简历", progress_tone(has("resume.pdf"), n_jobs), f"{has('resume.pdf')}/{n_jobs}",
              "有 resume.pdf 的岗位"),
        stage("申请", "ok" if app_status["submitted_confirmed"] else "warn" if has("pre-submit.json") else "none",
              f"{has('pre-submit.json')} 份预提交",
              f"dry-run {dry} · 已确认提交 {app_status['submitted_confirmed']} · "
              f"结果不明 {app_status['submission_unknown']}"),
        stage("试跑日志", "none" if not logs else "warn" if totals["failed"] or totals["error"] else "ok",
              f"{len(logs)} 份日志",
              f"命令 {totals['command']} · 失败 {totals['failed']} · 错误 {totals['error']}"
              if logs else "未提供 --trial-dir 或无 Codex 日志"),
        stage("测试", "bad" if "fail" in test_status else "ok" if test_status == {"pass"} else "warn" if tests else "none",
              f"{sum(status == 'pass' for _, status, _, _ in tests)}/{len(tests)} 通过",
              "；".join(detail for _, _, detail, _ in tests) or "未提供 --tests-log"),
    ]
    issues = list(ctx["issues"])
    if source_status["blocked"]:
        issues.append(f"{source_status['blocked']} 个岗位来源为 blocked。")
    if variant_status["failed"]:
        issues.append(f"{variant_status['failed']} 个简历版本检查失败，不能批准。")
    if totals["failed"]:
        issues.append(f"试跑中有 {totals['failed']} 条命令以非零退出码或失败状态结束（见试跑时间线 → 失败）。")
    attention = "".join(f"<li>{esc(issue)}</li>" for issue in issues)
    runs = ", ".join(path.name for path in ctx["runs"]) or "无"
    counts = (
        counts_line("来源状态", source_status) + counts_line("申请状态", app_status)
        + counts_line("简历版本", variant_status)
        + f'<div class="count-line"><span class="muted">运行记录</span> {esc(runs)}</div>'
    )
    return (
        f'<section id="overview" class="block"><h2>总览</h2><div class="grid stages">{"".join(cards)}</div>'
        f'<div class="card counts">{counts}</div>'
        + (f'<div class="card attention"><h3>需要注意</h3><ul>{attention}</ul></div>' if issues else "")
        + "</section>"
    )


def status_chip(row: dict, key: str) -> str:
    if row.get(key):
        return chip(row[key])
    return chip("不在 jobs.csv", "warn") if key == "source_status" else ""


def job_row(index: int, job_id: str, row: dict, meta: dict) -> str:
    source = meta["source"] or {}

    def value(key: str) -> str:
        return esc(row.get(key) or source.get(key) or "")

    marks = "".join(
        f'<span class="mark {"on" if meta["files"].get(name) else "off"}" title="{esc(name)}">{esc(label)}</span>'
        for label, name in ARTIFACTS
    )
    posted = esc(str(source.get("posted_at") or "")[:10])
    return (
        f'<tr class="job" data-job="{index}" data-job-id="{esc(job_id)}" '
        f'data-source="{esc(row.get("source_status", ""))}" data-app="{esc(row.get("application_status", ""))}">'
        f'<td><button type="button" class="linkish" data-job="{index}">{esc(job_id)}</button></td>'
        f'<td>{value("company")}</td><td>{value("title")}</td><td class="hide-sm">{value("location")}</td>'
        f'<td>{status_chip(row, "source_status")}</td><td>{status_chip(row, "application_status")}</td>'
        f'<td class="hide-sm">{posted} {recency_chip(meta["source"])}</td><td class="marks">{marks}</td></tr>'
    )


def job_entries(rows: list[dict[str, str]], dirs: dict[str, Path]) -> list[tuple[str, dict, Path | None]]:
    """jobs.csv rows first, then job folders that jobs.csv does not list."""
    entries: list[tuple[str, dict, Path | None]] = [
        (row.get("job_id", ""), row, dirs.get(row.get("job_id", ""))) for row in rows
    ]
    listed = {job_id for job_id, _, _ in entries}
    return entries + [(name, {}, folder) for name, folder in dirs.items() if name not in listed]


def jobs_section(page: Page, ctx: dict) -> str:
    rows, entries = ctx["rows"], ctx["entries"]
    body = []
    for index, (job_id, row, folder) in enumerate(entries):
        meta = ctx["metas"].get(job_id) or job_meta(folder)
        body.append(job_row(index, job_id, row, meta))
        panel = job_panel(page, ctx, job_id, row, folder, meta)
        page.templates.append(f'<template id="job-{index}">{panel}</template>')

    def options(key: str) -> str:
        values = sorted({row.get(key, "") for row in rows} - {""})
        return "".join(f'<option value="{esc(value)}">{esc(value)}</option>' for value in values)

    error = f'<p class="note bad">{esc(ctx["csv_error"])}</p>' if ctx["csv_error"] else ""
    if body:
        table = (
            '<div class="table-wrap"><table class="data jobs"><thead><tr><th>job_id</th><th>公司</th>'
            '<th>职位</th><th class="hide-sm">地点</th><th>来源</th><th>申请</th><th class="hide-sm">发布</th>'
            f'<th>产物</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>'
        )
    else:
        table = '<p class="muted">没有岗位：jobs.csv 和 jobs/ 都为空。</p>'
    return (
        f'<section id="jobs" class="block"><h2>岗位 <span class="muted small">共 {len(entries)} 个 · '
        f'点击一行查看全部文件</span></h2>{error}<div class="filters">'
        '<input id="job-filter" type="search" placeholder="搜索 job_id / 公司 / 职位 / 地点" aria-label="搜索岗位">'
        '<select id="job-source" aria-label="来源状态"><option value="">全部来源状态</option>'
        f'{options("source_status")}</select><select id="job-app" aria-label="申请状态">'
        f'<option value="">全部申请状态</option>{options("application_status")}</select>'
        f'<span class="muted small" id="job-count">{len(entries)}</span></div>{table}</section>'
    )


def job_panel(page: Page, ctx: dict, job_id: str, row: dict, folder: Path | None, meta: dict) -> str:
    chips = [status_chip(row, "source_status"), status_chip(row, "application_status"), recency_chip(meta["source"])]
    if meta["pre"] and meta["pre"].get("dry_run") is True:
        chips.append(chip("dry-run"))
    if row.get("jd_sha256") and meta.get("jd_sha256"):
        same = row["jd_sha256"] == meta["jd_sha256"]
        chips.append(chip(f"jd.txt 哈希与 jobs.csv {'一致' if same else '不一致'}", "ok" if same else "bad"))
    facts = []
    for key in ctx["fields"]:
        value = row.get(key, "")
        if key.endswith("_url"):
            shown = url_link(value)
        else:
            shown = f"<code>{esc(value)}</code>" if key.endswith("sha256") else esc(value)
        facts.append(f"<dt>{esc(key)}</dt><dd>{shown}</dd>")
    source = meta["source"] or {}
    for key in ("posted_at", "posted_at_meaning", "employment_type", "fetched_at"):
        if source.get(key):
            facts.append(f"<dt>source.{esc(key)}</dt><dd>{esc(source[key])}</dd>")
    files = ordered_files(folder, ctx["skip"]) if folder else []
    note = ""
    if len(files) > MAX_FILES_PER_DIR:
        note = f'<p class="note warn">共 {len(files)} 个文件，只显示前 {MAX_FILES_PER_DIR} 个。</p>'
        files = files[:MAX_FILES_PER_DIR]
    if files:
        box = page.filebox([(rel, file_view(page, path, rel)) for rel, path in files])
    else:
        box = '<p class="muted">jobs/ 下没有该岗位的目录。</p>'
    parts = (row.get("company") or source.get("company"), row.get("title") or source.get("title"))
    title = " — ".join(str(part) for part in parts if part) or job_id
    return (
        f'<div class="panel-head"><h3>{esc(title)}</h3><div class="chips">{" ".join(c for c in chips if c)}</div></div>'
        f'<dl class="kv">{"".join(facts)}</dl>{note}{box}'
    )


def variants_section(page: Page, ctx: dict) -> str:
    manifest = ctx["manifest"]
    if ctx["manifest_error"]:
        content = f'<p class="note bad">{esc(ctx["manifest_error"])}</p>'
    elif not manifest:
        content = (
            f'<p class="muted">未找到 <code>{esc(MANIFEST.as_posix())}</code>。'
            "请在普通终端运行 <code>python3 render_resume.py build</code>。</p>"
        )
    else:
        cards = "".join(variant_card(page, ctx["root"], v) for v in manifest["variants"] if isinstance(v, dict))
        content = (
            f'<p class="muted small">master <code>{esc(manifest.get("master", ""))}</code> · 上传文件名 '
            f'<code>{esc(manifest.get("upload_filename", ""))}</code> · 生成于 {esc(manifest.get("generated_at", ""))}</p>'
            f'<div class="variants">{cards or "<p class=muted>manifest 中没有版本。</p>"}</div>'
        )
    return f'<section id="variants" class="block"><h2>简历版本</h2>{content}</section>'


def checks_html(checks: dict) -> str:
    if not checks:
        return '<p class="muted small">没有检查记录。</p>'
    rows, failed = [], 0
    for name, check in checks.items():
        ok = isinstance(check, dict) and check.get("ok") is True
        failed += not ok
        detail = check.get("detail", "") if isinstance(check, dict) else check
        verdict = chip("通过", "ok") if ok else chip("失败", "bad")
        rows.append(f"<tr><td>{esc(name)}</td><td>{verdict}</td><td>{esc(detail)}</td></tr>")
    return (
        f'<details{" open" if failed else ""}><summary>检查 {len(checks) - failed}/{len(checks)} 通过</summary>'
        f'<div class="table-wrap"><table class="data"><tbody>{"".join(rows)}</tbody></table></div></details>'
    )


def fact_list(value: object) -> str:
    return ", ".join(map(str, value)) if isinstance(value, list) else str(value or "")


def edits_html(edits: list[dict]) -> str:
    if not edits:
        return '<p class="muted small">无改动（与 master 相同）。</p>'
    rows = "".join(
        f"<tr><td>{esc(e.get('paragraph_contains', ''))}</td>"
        f"<td><del>{esc(e.get('old', ''))}</del><br>{esc(e.get('new', ''))}</td>"
        f"<td>{esc(fact_list(e.get('fact_ids')))}</td><td>{esc(e.get('reason', ''))}</td></tr>"
        for e in edits
    )
    return (
        f'<details><summary>改动 {len(edits)} 处</summary><div class="table-wrap"><table class="data"><thead><tr>'
        f"<th>段落定位</th><th>原文 → 新文</th><th>事实 ID</th><th>理由</th></tr></thead><tbody>{rows}</tbody>"
        "</table></div></details>"
    )


def variant_card(page: Page, root: Path, variant: dict) -> str:
    malformed = [key for key, kind in (("previews", list), ("edits", list), ("checks", dict))
                 if variant.get(key) is not None and not isinstance(variant.get(key), kind)]
    variant = {key: value for key, value in variant.items() if key not in malformed}
    note = (f'<p class="note bad">manifest 字段格式不对，已忽略：{esc(", ".join(malformed))}</p>'
            if malformed else "")
    pdf = inside(root, variant.get("pdf"))
    if pdf and pdf.stat().st_size <= MAX_EMBED_BYTES:
        actions = page.blob_buttons(pdf.read_bytes(), "application/pdf", pdf.name)
    else:
        actions = f'<p class="note warn">PDF 不存在或过大：<code>{esc(variant.get("pdf", ""))}</code></p>'
    previews = []
    for rel in variant.get("previews") or []:
        image = inside(root, rel)
        rel = str(rel)
        if image and image.suffix.lower() in IMAGE_TYPES and image.stat().st_size <= MAX_EMBED_BYTES:
            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            source = f"data:{IMAGE_TYPES[image.suffix.lower()]};base64,{encoded}"
            previews.append(f'<img alt="{esc(image.name)}" loading="lazy" src="{source}">')
        else:
            previews.append(f'<p class="muted small">预览缺失：<code>{esc(rel)}</code></p>')
    checks = variant.get("checks") if isinstance(variant.get("checks"), dict) else {}
    edits = [e for e in variant.get("edits") or [] if isinstance(e, dict)]
    if variant.get("approved_by"):
        approval = f"批准：{variant.get('approved_by')} · {variant.get('approved_at')}"
    else:
        approval = "尚未批准"
    sha = str(variant.get("pdf_sha256") or "")
    return (
        f'<article class="card variant"><div class="panel-head"><h3>{esc(variant.get("id", "?"))}</h3>'
        f'{chip(variant.get("status", "unknown"))}</div>'
        f'<p class="muted small">{esc(variant.get("role_family", ""))} · {esc(variant.get("description", ""))}</p>'
        f'<p class="small">{esc(approval)} · 页数 {esc(variant.get("pages", "?"))} · pdf_sha256 '
        f'<code title="{esc(sha)}">{esc(sha[:12])}</code></p>{note}{actions}{checks_html(checks)}'
        f'<div class="previews">{"".join(previews)}</div>{edits_html(edits)}</article>'
    )


def runs_section(page: Page, ctx: dict) -> str:
    blocks = []
    for index, folder in enumerate(ctx["runs"]):
        files = sorted(ordered_files(folder, ctx["skip"]), key=lambda pair: (pair[0] != "digest.md", pair[0]))
        views = [(rel, file_view(page, path, rel)) for rel, path in files[:MAX_FILES_PER_DIR]]
        blocks.append(
            f'<details class="card run"{" open" if index == 0 else ""}><summary><strong>{esc(folder.name)}</strong> '
            f'<span class="muted small">{len(files)} 个文件</span></summary>{page.filebox(views)}</details>'
        )
    content = "".join(blocks) or '<p class="muted">runs/ 下没有运行记录。</p>'
    return f'<section id="runs" class="block"><h2>运行记录</h2>{content}</section>'


def log_summary_row(log: dict) -> str:
    counts = log_counts(log)
    cells = [
        f"<code>{esc(log['name'])}</code>", log["kind"], log["meta"].get("回合数", 0), counts["command"],
        chip(counts["failed"], "bad" if counts["failed"] else "ok"), counts["message"], counts["paths"],
        counts["tool"], counts["error"],
    ]
    usage = f'<td class="hide-sm">{esc(usage_text(log["usage"]))}</td>'
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + usage + "</tr>"


def trial_section(page: Page, ctx: dict) -> str:
    trial_dir, logs = ctx["trial_dir"], ctx["logs"]
    if trial_dir is None:
        return ""
    if logs:
        summary = (
            '<div class="table-wrap"><table class="data"><thead><tr><th>日志</th><th>类型</th><th>回合</th>'
            "<th>命令</th><th>失败命令</th><th>消息</th><th>改动文件</th><th>工具</th><th>错误</th>"
            '<th class="hide-sm">Token</th></tr></thead>'
            f'<tbody>{"".join(map(log_summary_row, logs))}</tbody></table></div>'
        )
    else:
        summary = '<p class="muted">该目录没有 Codex JSONL 日志。</p>'
    details = "".join(
        f'<details class="card log"{" open" if index == 0 else ""}><summary><strong>{esc(log["name"])}</strong>'
        f"</summary>{render_log(log)}</details>"
        for index, log in enumerate(logs)
    )
    parsed = {log["path"] for log in logs}
    files = [
        (rel, path) for rel, path in ordered_files(trial_dir, set())
        if path.suffix.lower() in TRIAL_SUFFIXES or (path.suffix.lower() == ".jsonl" and str(path) not in parsed)
    ][:MAX_FILES_PER_DIR]
    others = f"<h3>试跑目录中的其他文件</h3>{page.filebox([(rel, file_view(page, p, rel)) for rel, p in files])}"
    return (
        f'<section id="trial" class="block"><h2>试跑时间线 <span class="muted small">{esc(trial_dir)}</span></h2>'
        f'{summary}{details}{others if files else ""}</section>'
    )


def tests_section(ctx: dict) -> str:
    if not ctx["tests"]:
        return ""
    blocks = "".join(
        f'<details class="card" open><summary><code>{esc(path)}</code> {chip(status)} '
        f'<span class="muted small">{esc(detail)}</span></summary>{pre(text)}</details>'
        for path, status, detail, text in ctx["tests"]
    )
    return f'<section id="tests" class="block"><h2>测试</h2>{blocks}</section>'


def report_section(ctx: dict) -> str:
    if ctx["report"] is None:
        return ""
    text = ctx["report"].read_text(encoding="utf-8", errors="replace")
    return (
        f'<section id="report" class="block"><h2>报告与决策 <span class="muted small">{esc(ctx["report"].name)}'
        f'</span></h2><div class="card md">{md_to_html(text)}</div></section>'
    )


# ---------------------------------------------------------------------------
# Build.

def subdirs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_dir() and not path.name.startswith("."))


def collect(root: Path, out: Path, report: Path | None, trial_dir: Path | None, tests_logs: list[Path]) -> dict:
    fields, rows, csv_error = read_jobs_csv(root / "jobs.csv")
    dirs = {path.name: path for path in subdirs(root / "jobs")}
    metas = {name: job_meta(folder) for name, folder in dirs.items()}
    manifest, manifest_error = load_manifest(root)
    skip = {out.resolve(), *(path.resolve() for path in tests_logs)} | ({trial_dir.resolve()} if trial_dir else set())
    logs = []
    for _, path in ordered_files(trial_dir, set()) if trial_dir else []:
        log = parse_codex_log(path) if path.suffix.lower() == ".jsonl" else None
        if log is not None:
            logs.append(log)
    tests = []
    for path in tests_logs:
        text = path.read_text(encoding="utf-8", errors="replace")
        tests.append((str(path), *summarize_test_log(text), text))
    issues = [csv_error] if csv_error else []
    for row in rows:
        meta = metas.get(row.get("job_id", ""))
        if meta and meta["jd_sha256"] and row.get("jd_sha256") and meta["jd_sha256"] != row["jd_sha256"]:
            issues.append(f"{row['job_id']}: jd.txt 的 sha256 与 jobs.csv 不一致。")
    return {
        "root": root, "fields": fields, "rows": rows, "csv_error": csv_error, "dirs": dirs, "metas": metas,
        "entries": job_entries(rows, dirs),
        "manifest": manifest, "manifest_error": manifest_error, "skip": skip,
        "runs": list(reversed(subdirs(root / "runs"))), "logs": logs, "tests": tests,
        "trial_dir": trial_dir, "report": report, "issues": issues,
    }


def build_dashboard(
    root: Path, out: Path, *, title: str = "求职 Agent 试跑看板", report: Path | None = None,
    trial_dir: Path | None = None, tests_logs: list[Path] | None = None,
) -> Path:
    root = root.resolve()
    if not root.is_dir():
        raise GuardError(f"Repository root not found: {root}")
    for path in [report, *(tests_logs or [])]:
        if path is not None and not path.is_file():
            raise GuardError(f"File not found: {path}")
    if trial_dir is not None and not trial_dir.is_dir():
        raise GuardError(f"Trial directory not found: {trial_dir}")
    ctx = collect(root, out, report, trial_dir, list(tests_logs or []))
    page = Page()
    sections = [
        ("report", "报告", report_section(ctx)),
        ("overview", "总览", overview_section(ctx)),
        ("jobs", "岗位", jobs_section(page, ctx)),
        ("variants", "简历版本", variants_section(page, ctx)),
        ("runs", "运行记录", runs_section(page, ctx)),
        ("trial", "试跑时间线", trial_section(page, ctx)),
        ("tests", "测试", tests_section(ctx)),
    ]
    nav = "".join(f'<a href="#{key}">{label}</a>' for key, label, body in sections if body)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    document = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">'
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
        f'<div class="wrap"><header class="top"><h1>{esc(title)}</h1>'
        f'<p class="muted small">生成于 {esc(generated)} · 仓库 <code>{esc(root)}</code></p>'
        '<p class="banner">本页包含个人数据（简历、岗位材料、运行记录），只在本机打开；不要发布、上传或分享。</p></header>'
        f'<nav class="sections">{nav}</nav>{"".join(body for _, _, body in sections)}</div>'
        '<div id="drawer" class="drawer" hidden><div class="drawer-backdrop" data-close></div>'
        '<section class="drawer-panel" role="dialog" aria-modal="true" aria-label="岗位详情">'
        '<div class="drawer-bar"><button type="button" class="btn ghost" data-close>关闭 ✕</button></div>'
        '<div id="drawer-body"></div></section></div>'
        f"{page.tail()}<script>{JS}</script></body></html>\n"
    )
    atomic_text(out, document)
    return out


CSP = (
    "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "frame-src blob:; object-src blob:; base-uri 'none'; form-action 'none'"
)

CSS = """
:root{--bg:#f6f6f3;--panel:#fff;--panel-2:#f1f1ee;--text:#1f2328;--muted:#656d76;--border:#d9dcd6;--accent:#0b57d0;--accent-text:#fff;
--ok:#116329;--ok-bg:#dafbe1;--warn:#7d4e00;--warn-bg:#fff4c2;--bad:#a40e26;--bad-bg:#ffebe9;--none:#57606a;--none-bg:#eaeef2;--code-bg:#f3f3f0;
--shadow:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#171a21;--panel-2:#1e222b;--text:#e6e8eb;--muted:#9aa4b2;--border:#2d333d;
--accent:#7aa7ff;--accent-text:#0f1115;--ok:#6fdd8b;--ok-bg:#133220;--warn:#e3b341;--warn-bg:#3a2e0b;--bad:#ff8182;--bad-bg:#4a1418;
--none:#a1abb8;--none-bg:#262b34;--code-bg:#12151b;--shadow:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;scroll-padding-top:56px}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
body.noscroll{overflow:hidden}a{color:var(--accent)}code{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px 64px}
.top h1{margin:20px 0 4px;font-size:24px}.banner{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:8px 12px;font-weight:600;margin:8px 0}
.muted{color:var(--muted)}.small{font-size:13px;font-weight:400}
nav.sections{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;gap:6px;overflow-x:auto;padding:10px 0;border-bottom:1px solid var(--border)}
nav.sections a{white-space:nowrap;padding:4px 12px;border-radius:999px;color:var(--text);text-decoration:none;border:1px solid var(--border);background:var(--panel);font-size:14px}
.block{margin-top:28px}.block>h2{font-size:19px;margin:0 0 12px}h3{font-size:16px;margin:0}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(min(100%,150px),1fr))}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow);min-width:0;margin-bottom:10px}
.grid .card{margin:0}.stage-top{display:flex;justify-content:space-between;align-items:center;gap:8px}.stage-name{font-weight:600}
.stage-value{font-size:22px;font-weight:700;margin:4px 0}.counts{margin-top:10px}.count-line{margin:4px 0;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.attention ul{margin:6px 0 0;padding-left:20px}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;background:var(--none-bg);color:var(--none);white-space:nowrap;vertical-align:middle}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.warn{background:var(--warn-bg);color:var(--warn)}.chip.bad{background:var(--bad-bg);color:var(--bad)}
.note{border-radius:8px;padding:8px 12px;background:var(--none-bg)}.note.warn{background:var(--warn-bg);color:var(--warn)}.note.bad{background:var(--bad-bg);color:var(--bad)}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.filters input,.filters select{font:inherit;padding:6px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text);min-width:0}
.filters input{flex:1 1 220px}
.table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--panel);margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{background:var(--panel-2);font-weight:600;white-space:nowrap}tbody tr:last-child td{border-bottom:0}
tr.job{cursor:pointer}tr.job:hover{background:var(--panel-2)}tr.job[hidden]{display:none}
.linkish{font:inherit;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;color:var(--accent);background:none;border:0;padding:0;cursor:pointer;text-align:left;overflow-wrap:break-word}
td code{overflow-wrap:normal}
.marks{white-space:nowrap}.mark{display:inline-block;font-size:11px;padding:0 5px;margin:1px;border-radius:4px;border:1px solid var(--border)}
.mark.on{background:var(--ok-bg);color:var(--ok);border-color:transparent}.mark.off{color:var(--muted);opacity:.55}
.btn{font:inherit;font-size:13px;padding:5px 12px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:var(--accent-text);cursor:pointer}
.btn.ghost{background:var(--panel);color:var(--text);border-color:var(--border)}.actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.drawer{position:fixed;inset:0;z-index:20}.drawer[hidden]{display:none}.drawer-backdrop{position:absolute;inset:0;background:rgba(0,0,0,.4)}
.drawer-panel{position:absolute;top:0;right:0;bottom:0;width:min(1000px,100%);background:var(--bg);overflow-y:auto;padding:0 16px 40px;box-shadow:-4px 0 24px rgba(0,0,0,.2)}
.drawer-bar{position:sticky;top:0;background:var(--bg);padding:10px 0;display:flex;justify-content:flex-end;z-index:2}
.panel-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;margin-bottom:8px}.chips{display:flex;flex-wrap:wrap;gap:6px}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;font-size:13px;margin:8px 0}dl.kv dt{color:var(--muted)}dl.kv dd{margin:0;overflow-wrap:anywhere;min-width:0}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 8px}
.tab{font:inherit;font-size:13px;padding:4px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text);cursor:pointer;max-width:100%;overflow-wrap:anywhere;text-align:left}
.tab[aria-selected=true]{background:var(--accent);border-color:var(--accent);color:var(--accent-text)}
.filemeta{font-size:12px;color:var(--muted);margin:4px 0 8px;overflow-wrap:anywhere}
pre{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;padding:10px 12px;overflow:auto;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:70vh;margin:6px 0}
.md{overflow-wrap:anywhere}.md h1{font-size:21px}.md h2{font-size:18px}.md h3{font-size:16px}.md h4,.md h5,.md h6{font-size:15px}
.md h1,.md h2,.md h3,.md h4{margin:18px 0 8px}.md>:first-child{margin-top:0}.md p{margin:8px 0}.md ul,.md ol{padding-left:22px;margin:6px 0}
.md blockquote{margin:8px 0;padding:2px 12px;border-left:3px solid var(--border);color:var(--muted)}.md :not(pre)>code{background:var(--code-bg);padding:1px 4px;border-radius:4px}
.md table{font-size:13.5px}.md hr{border:0;border-top:1px solid var(--border);margin:16px 0}
.pdf-frame{width:100%;height:78vh;border:1px solid var(--border);border-radius:8px;background:#fff}
img.shot{max-width:100%;border:1px solid var(--border);border-radius:8px}
.variants{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}.variants .card{margin:0}
.previews{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.previews img{max-width:calc(50% - 4px);height:auto;border:1px solid var(--border);border-radius:6px;background:#fff}
details>summary{cursor:pointer;padding:4px 0}details.card>summary{font-size:15px}
.tl-filters{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.tl-btn{font:inherit;font-size:12.5px;padding:3px 10px;border-radius:999px;border:1px solid var(--border);background:var(--panel);color:var(--text);cursor:pointer}
.tl-btn[aria-pressed=true]{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
ol.events{list-style:none;margin:0;padding:0}.ev{border-left:3px solid var(--border);padding:6px 10px;margin:6px 0;background:var(--panel);border-radius:0 8px 8px 0}
.ev[hidden]{display:none}.ev-command{border-left-color:var(--accent)}.ev-message{border-left-color:var(--ok)}.ev-file{border-left-color:var(--warn)}
.ev-turn{background:transparent;border-left-color:transparent;padding:2px 10px;font-size:13px;color:var(--muted)}.ev.failed{border-left-color:var(--bad);background:var(--bad-bg)}
.ev-head{display:flex;flex-wrap:wrap;gap:6px;align-items:baseline}.ev-n{color:var(--muted);font-size:12px;min-width:2.2em}
.ev-title{overflow-wrap:anywhere;min-width:0;flex:1 1 240px}.ev-time{color:var(--muted);font-size:12px}
.badge{font-size:11.5px;font-weight:700;padding:0 6px;border-radius:4px;background:var(--none-bg);color:var(--none);white-space:nowrap}
.b-command{background:var(--accent);color:var(--accent-text)}.b-message{background:var(--ok-bg);color:var(--ok)}.b-file{background:var(--warn-bg);color:var(--warn)}.b-error{background:var(--bad-bg);color:var(--bad)}
ul.paths{margin:4px 0;padding-left:20px;font-size:13px}.task{font-weight:700}
@media (max-width:640px){body{font-size:14px}.hide-sm{display:none}.top h1{font-size:20px}.drawer-panel{padding:0 12px 32px}
.variants{grid-template-columns:1fr}.previews img{max-width:100%}dl.kv{grid-template-columns:minmax(0,8.5em) minmax(0,1fr);gap:3px 8px}dl.kv dt{overflow-wrap:anywhere}.pdf-frame{height:60vh}}
"""

JS = """
(function(){
var cache={};
function blobUrl(id){if(cache[id])return cache[id];var el=document.getElementById(id);if(!el)return null;
var bin=atob(el.textContent.trim()),bytes=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
return cache[id]=URL.createObjectURL(new Blob([bytes],{type:el.getAttribute('data-mime')||'application/octet-stream'}));}
function activate(tab){var box=tab.closest('.filebox');box.querySelectorAll(':scope>.tabs>.tab').forEach(function(t){t.setAttribute('aria-selected',t===tab?'true':'false');});
var body=box.querySelector(':scope>.tab-body'),tpl=document.getElementById(tab.getAttribute('data-tpl'));body.replaceChildren(tpl.content.cloneNode(true));box.setAttribute('data-init','1');hydrate(body);}
function initBoxes(root){root.querySelectorAll('.filebox:not([data-init])').forEach(function(box){if(box.closest('details:not([open])'))return;var t=box.querySelector(':scope>.tabs>.tab');if(t)activate(t);});}
function hydrate(root){root.querySelectorAll('iframe[data-blob-src]').forEach(function(f){if(!f.getAttribute('src')){var u=blobUrl(f.getAttribute('data-blob-src'));if(u)f.setAttribute('src',u);}});initBoxes(root);}
var drawer=document.getElementById('drawer'),drawerBody=document.getElementById('drawer-body');
function openJob(i){var tpl=document.getElementById('job-'+i);if(!tpl)return;drawerBody.replaceChildren(tpl.content.cloneNode(true));drawer.hidden=false;document.body.classList.add('noscroll');
hydrate(drawerBody);var row=document.querySelector('tr.job[data-job="'+i+'"]');if(row)setHash('#job='+encodeURIComponent(row.getAttribute('data-job-id')));
var close=drawer.querySelector('.drawer-bar [data-close]');if(close)close.focus();}
function closeJob(){if(drawer.hidden)return;drawer.hidden=true;drawerBody.replaceChildren();document.body.classList.remove('noscroll');setHash('');}
function setHash(h){try{history.replaceState(null,'',h||(location.pathname+location.search));}catch(err){}}
document.addEventListener('click',function(e){var t=e.target;
var tab=t.closest('.tab');if(tab){activate(tab);return;}
var open=t.closest('[data-open-blob]');if(open){var u=blobUrl(open.getAttribute('data-open-blob'));if(u)window.open(u,'_blank');return;}
var dl=t.closest('[data-download-blob]');if(dl){var a=document.createElement('a');a.href=blobUrl(dl.getAttribute('data-download-blob'));a.download=dl.getAttribute('data-name')||'file';document.body.appendChild(a);a.click();a.remove();return;}
if(t.closest('[data-close]')){closeJob();return;}
var f=t.closest('.tl-btn');if(f){var tl=f.closest('.timeline'),k=f.getAttribute('data-filter');tl.querySelectorAll('.tl-btn').forEach(function(b){b.setAttribute('aria-pressed',b===f?'true':'false');});
tl.querySelectorAll('.ev').forEach(function(ev){ev.hidden=!(k==='all'||ev.getAttribute('data-kind')===k||(k==='failed'&&ev.getAttribute('data-failed')==='1'));});return;}
var row=t.closest('[data-job]');if(row&&!t.closest('a')){openJob(row.getAttribute('data-job'));}});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeJob();});
document.addEventListener('toggle',function(e){if(e.target.open)initBoxes(e.target);},true);
var rows=[].slice.call(document.querySelectorAll('tr.job')),q=document.getElementById('job-filter'),s=document.getElementById('job-source'),a=document.getElementById('job-app'),n=document.getElementById('job-count');
rows.forEach(function(r){r.setAttribute('data-text',r.textContent.toLowerCase());});
function filter(){var text=(q.value||'').trim().toLowerCase(),sv=s.value,av=a.value,shown=0;rows.forEach(function(r){var ok=(!text||r.getAttribute('data-text').indexOf(text)>=0)&&(!sv||r.getAttribute('data-source')===sv)&&(!av||r.getAttribute('data-app')===av);r.hidden=!ok;if(ok)shown++;});if(n)n.textContent=shown+' / '+rows.length;}
if(q){q.addEventListener('input',filter);s.addEventListener('change',filter);a.addEventListener('change',filter);filter();}
initBoxes(document);
function route(){if(location.hash.indexOf('#job=')!==0)return;var id=decodeURIComponent(location.hash.slice(5));rows.forEach(function(r){if(r.getAttribute('data-job-id')===id)openJob(r.getAttribute('data-job'));});}
window.addEventListener('hashchange',route);route();
})();
"""


def latest_run_item(root: Path, name: str, want_dir: bool) -> Path | None:
    """runs/<today>/<name> if present, else the newest runs/<date>/<name>."""
    today = root / "runs" / datetime.now().date().isoformat() / name
    for path in [today, *(folder / name for folder in reversed(subdirs(root / "runs")))]:
        if path.is_dir() if want_dir else path.is_file():
            return path
    return None


def main(argv: list[str] | None = None) -> int:
    require_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent,
                        help="repository root (default: the folder of this script)")
    parser.add_argument("--out", type=Path, help="output HTML (default: <root>/runs/<today>/dashboard.html)")
    parser.add_argument("--title", default="求职 Agent 试跑看板")
    parser.add_argument("--report", type=Path, help="Markdown report shown first")
    parser.add_argument("--trial-dir", type=Path,
                        help="folder with Codex JSONL logs and trial notes (default: newest runs/*/trial)")
    parser.add_argument("--tests-log", type=Path, nargs="+", action="extend", default=[],
                        help="unittest/pytest output files (default: newest runs/*/tests.log)")
    args = parser.parse_args(argv)
    out = args.out or args.root / "runs" / datetime.now().date().isoformat() / "dashboard.html"
    if not any((args.root / name).exists() for name in ("jobs.csv", "jobs", "private")):
        print(f"Warning: {args.root.resolve()} has no jobs.csv, jobs/ or private/; is --root the repository?",
              file=sys.stderr)
    trial_dir = args.trial_dir or latest_run_item(args.root, "trial", want_dir=True)
    tests_logs = args.tests_log or [path for path in [latest_run_item(args.root, "tests.log", want_dir=False)] if path]
    for label, path in (("trial dir", trial_dir if not args.trial_dir else None),
                        *(("tests log", path) for path in (tests_logs if not args.tests_log else []))):
        if path:
            print(f"Using {label}: {path}")
    try:
        path = build_dashboard(
            args.root, out, title=args.title, report=args.report,
            trial_dir=trial_dir, tests_logs=tests_logs,
        )
    except GuardError as error:
        print(f"Blocked: {error}", file=sys.stderr)
        return 2
    resolved = path.resolve()
    print(f"Dashboard: {resolved} ({human_size(resolved.stat().st_size)})")
    print(f"Open: {resolved.as_uri()}")
    print(f"WARNING: {WARNING} (local file only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

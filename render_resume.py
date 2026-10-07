"""Build approved resume PDF variants from the user's DOCX master in a normal terminal.

Agents never render or re-typeset a resume; they only select an approved PDF
recorded in the manifest. LibreOffice cannot run inside the Codex sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape

from assistant import GuardError, atomic_text, load_json, now_utc, require_python, sha256_file


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path("private/resume_variants/variants.json")
DEFAULT_OUTPUT = "private/resume_variants/build"
VARIANT_ID = re.compile(r"[a-z0-9-]{1,40}\Z")
STATUSES = {"draft", "approved", "failed"}
POPPLER = ("pdfinfo", "pdffonts", "pdfimages", "pdftotext", "pdftoppm")
RENDER_HINT = (
    "LibreOffice could not render. It cannot run inside the Codex sandbox; "
    "run this build in a normal terminal."
)
SUBSTITUTION = "/org.openoffice.Office.Common/Font/Substitution"

# One scanner for paragraph boundaries and text runs keeps offsets consistent.
TOKEN = re.compile(
    r"(?P<open><w:p(?=[\s>/])[^>]*?(?P<self>/?)>)"
    r"|(?P<close></w:p>)"
    r"|(?P<t><w:t(?P<attrs>(?:\s[^>]*?)?)(?<!/)>(?P<text>[^<]*)</w:t>)"
)
PLAIN = re.compile(
    r"<w:t(?:\s[^>]*?)?(?<!/)>(?P<text>[^<]*)</w:t>"
    r"|(?P<gap><w:(?:tab|br|cr)(?=[\s/>])[^>]*>)"
    r"|(?P<para><w:p(?=[\s>/])[^>]*>|</w:p>)"
)
PRESERVE = re.compile(r"""\sxml:space=(["'])preserve\1""")
SPACE_ATTR = re.compile(r"""\sxml:space=(["'])[^"']*\1""")
WORD = re.compile(r"[^\W_]+")
CJK = "぀-ヿ㐀-鿿가-힯豈-﫿"


class Paragraph(NamedTuple):
    start: int
    end: int
    depth: int
    runs: tuple[re.Match, ...]
    nested: bool = False  # contains another <w:p>, e.g. the anchor of a text box


# ---- configuration ---------------------------------------------------------

def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardError(message)


def is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_edit(edit: object, where: str) -> dict:
    require(isinstance(edit, dict), f"{where} must be an object")
    for key in ("paragraph_contains", "old", "reason"):
        require(is_text(edit.get(key)), f"{where}.{key} must be a non-empty string")
    require(isinstance(edit.get("new"), str), f"{where}.new must be a string")
    facts = edit.get("fact_ids")
    require(isinstance(facts, list) and bool(facts) and all(is_text(f) for f in facts),
            f"{where}.fact_ids must list at least one fact ID")
    return {key: edit[key] for key in ("paragraph_contains", "old", "new", "fact_ids", "reason")}


def valid_variant(variant: object, index: int) -> dict:
    where = f"variants[{index}]"
    require(isinstance(variant, dict), f"{where} must be an object")
    require(isinstance(variant.get("id"), str) and bool(VARIANT_ID.fullmatch(variant["id"])),
            f"{where}.id must match [a-z0-9-]{{1,40}}")
    for key in ("role_family", "description"):
        require(isinstance(variant.get(key), str), f"{where}.{key} must be a string")
    edits = variant.get("edits", [])
    require(isinstance(edits, list), f"{where}.edits must be a list")
    return {
        "id": variant["id"], "role_family": variant["role_family"],
        "description": variant["description"],
        "edits": [valid_edit(edit, f"{where}.edits[{n}]") for n, edit in enumerate(edits)],
    }


def load_config(path: Path) -> dict:
    raw = load_json(path)
    require(raw.get("schema_version") == 1, "Config schema_version must be 1")
    require(is_text(raw.get("master")), "Config master must name the DOCX master")
    upload = raw.get("upload_filename")
    require(isinstance(upload, str) and Path(upload).name == upload and not upload.startswith(".")
            and upload.lower().endswith(".pdf") and len(upload) <= 120,
            "upload_filename must be a plain file name ending in .pdf")
    output_dir = raw.get("output_dir", DEFAULT_OUTPUT)
    require(is_text(output_dir), "output_dir must be a path")

    renderer = raw.get("renderer", {})
    require(isinstance(renderer, dict), "renderer must be an object")
    fonts = renderer.get("font_replacements", {})
    require(isinstance(fonts, dict) and all(is_text(k) and is_text(v) for k, v in fonts.items()),
            "renderer.font_replacements must map font names to installed font names")
    timeout = renderer.get("timeout_seconds", 180)
    require(type(timeout) is int and timeout > 0, "renderer.timeout_seconds must be a positive integer")
    soffice = renderer.get("soffice", "soffice")
    require(is_text(soffice), "renderer.soffice must be a command")

    metadata = raw.get("metadata", {})
    require(isinstance(metadata, dict) and set(metadata) <= {"title", "author"}
            and all(isinstance(v, str) for v in metadata.values()),
            "metadata may only contain string title and author")

    checks = raw.get("checks", {})
    require(isinstance(checks, dict), "checks must be an object")
    pages = checks.get("expected_pages")
    require(pages is None or (type(pages) is int and pages > 0), "checks.expected_pages must be null or a positive integer")
    images = checks.get("min_images", 0)
    require(type(images) is int and images >= 0, "checks.min_images must be a non-negative integer")
    coverage = checks.get("min_word_coverage", 0.995)
    require(type(coverage) in (int, float) and 0 <= coverage <= 1, "checks.min_word_coverage must be between 0 and 1")
    phrases = checks.get("required_phrases", [])
    require(isinstance(phrases, list) and all(is_text(p) for p in phrases),
            "checks.required_phrases must be a list of strings")

    variants = raw.get("variants")
    require(isinstance(variants, list) and bool(variants), "Config needs at least one variant")
    clean = [valid_variant(variant, n) for n, variant in enumerate(variants)]
    ids = [variant["id"] for variant in clean]
    require(len(ids) == len(set(ids)), "Variant ids must be unique")
    return {
        "master": raw["master"], "upload_filename": upload, "output_dir": output_dir,
        "renderer": {"soffice": soffice, "font_replacements": dict(fonts), "timeout_seconds": timeout},
        "metadata": dict(metadata),
        "checks": {"expected_pages": pages, "min_images": images,
                   "min_word_coverage": float(coverage), "required_phrases": list(phrases)},
        "variants": clean,
    }


def resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def relative(root: Path, path: Path) -> str:
    """Repository-relative path; a file outside the repository is recorded by name only."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name  # never write a local absolute path (user name, folders) into the manifest


def output_root(root: Path, config: dict) -> Path:
    folder = resolve(root, config["output_dir"])
    require(folder.resolve().is_relative_to(root.resolve()),
            "output_dir must be inside the repository (keep the default under private/, which Git ignores)")
    return folder


# ---- edit engine: string surgery on word/document.xml -----------------------

def scan_paragraphs(xml: str) -> list[Paragraph]:
    """All <w:p> elements in document order; runs are a paragraph's own <w:t>, not a text box's."""
    stack: list[list] = []  # [start, runs, contains a nested paragraph]
    found: list[Paragraph] = []
    for match in TOKEN.finditer(xml):
        if match.group("t"):
            if stack:
                stack[-1][1].append(match)
        elif match.group("close"):
            require(bool(stack), "Unbalanced </w:p> in word/document.xml")
            start, runs, nested = stack.pop()
            found.append(Paragraph(start, match.end(), len(stack), tuple(runs), nested))
            if stack:
                stack[-1][2] = True
        elif match.group("self"):
            found.append(Paragraph(match.start(), match.end(), len(stack), ()))
            if stack:
                stack[-1][2] = True
        else:
            stack.append([match.start(), [], False])
    require(not stack, "Unbalanced <w:p> in word/document.xml")
    return sorted(found, key=lambda paragraph: paragraph.start)


def run_text(run: re.Match) -> str:
    return html.unescape(run.group("text"))


def paragraph_text(paragraph: Paragraph) -> str:
    return "".join(run_text(run) for run in paragraph.runs)


def occurrences(text: str, part: str) -> int:
    return sum(1 for index in range(len(text)) if text.startswith(part, index))


def short(text: str) -> str:
    return text if len(text) <= 60 else text[:57] + "..."


def t_element(attrs: str, text: str) -> str:
    if (text != text.strip() or "  " in text) and not PRESERVE.search(attrs):
        attrs = SPACE_ATTR.sub("", attrs) + ' xml:space="preserve"'
    return f"<w:t{attrs}>{escape(text)}</w:t>"


def apply_edit(xml: str, edit: dict) -> tuple[str, int]:
    paragraphs = scan_paragraphs(xml)
    texts = [paragraph_text(paragraph) for paragraph in paragraphs]
    anchor, old, new = edit["paragraph_contains"], edit["old"], edit["new"]
    hits = [index for index, text in enumerate(texts) if anchor in text]
    require(len(hits) == 1,
            f"paragraph_contains {short(anchor)!r} must match exactly one paragraph; matched {len(hits)}")
    index = hits[0]
    paragraph = paragraphs[index]
    require(paragraph.depth == 0,
            f"paragraph_contains {short(anchor)!r} is inside a text box (nested w:p); edit refused")
    require(not paragraph.nested,
            f"paragraph_contains {short(anchor)!r} contains a text box or frame (nested w:p); edit refused")
    count = occurrences(texts[index], old)
    require(count == 1, f"old {short(old)!r} must occur exactly once in its paragraph; found {count}")
    begin = texts[index].find(old)
    finish = begin + len(old)
    changes = []
    offset = 0
    for run in paragraph.runs:
        text = run_text(run)
        low, high = offset, offset + len(text)
        offset = high
        if low == high or high <= begin or low >= finish:
            continue
        if low <= begin:  # first affected run keeps its formatting and takes the new text
            replaced = text[:begin - low] + new + (text[finish - low:] if finish <= high else "")
        else:  # later runs lose the consumed part
            replaced = text[finish - low:]
        changes.append((run.start(), run.end(), t_element(run.group("attrs"), replaced)))
    for start, end, element in reversed(changes):
        xml = xml[:start] + element + xml[end:]
    return xml, index


def apply_edits(xml: str, edits: list[dict]) -> tuple[str, list[int]]:
    targets = []
    for edit in edits:
        xml, index = apply_edit(xml, edit)
        targets.append(index)
    return xml, targets


def frame(xml: str, paragraphs: list[Paragraph], edited: set[int]) -> list[str]:
    """Every byte except the <w:t> elements of edited paragraphs."""
    pieces, last = [], 0
    runs = sorted((run for index in edited for run in paragraphs[index].runs), key=lambda run: run.start())
    for run in runs:
        pieces.append(xml[last:run.start()])
        last = run.end()
    pieces.append(xml[last:])
    return pieces


def check_invariant(master_xml: str, result_xml: str, edits: list[dict], targets: list[int]) -> None:
    before, after = scan_paragraphs(master_xml), scan_paragraphs(result_xml)
    require(len(before) == len(after), "Edit invariant failed: paragraph count changed")
    expected = [paragraph_text(paragraph) for paragraph in before]
    for edit, index in zip(edits, targets, strict=True):
        require(occurrences(expected[index], edit["old"]) == 1,
                f"Edit invariant failed: {short(edit['old'])!r} is not unique in the master paragraph")
        expected[index] = expected[index].replace(edit["old"], edit["new"], 1)
    actual = [paragraph_text(paragraph) for paragraph in after]
    changed = [index for index, (want, got) in enumerate(zip(expected, actual)) if want != got]
    require(not changed, f"Edit invariant failed: unexpected text in paragraph(s) {changed[:5]}")
    edited = set(targets)
    require(frame(master_xml, before, edited) == frame(result_xml, after, edited),
            "Edit invariant failed: bytes outside the edited text runs changed")


def set_core_metadata(xml: str, metadata: dict) -> str:
    require('xmlns:dc="http://purl.org/dc/elements/1.1/"' in xml, "docProps/core.xml lacks the dc namespace")
    for key, tag in (("title", "dc:title"), ("author", "dc:creator")):
        if key not in metadata:
            continue
        element = f"<{tag}>{escape(metadata[key])}</{tag}>"
        pattern = re.compile(rf"<{tag}(?:\s[^>]*?)?/>|<{tag}(?:\s[^>]*?)?>[^<]*</{tag}>")
        if pattern.search(xml):
            xml = pattern.sub(lambda _: element, xml, count=1)
        else:
            require("</cp:coreProperties>" in xml, "docProps/core.xml has no cp:coreProperties element")
            xml = xml.replace("</cp:coreProperties>", element + "</cp:coreProperties>", 1)
    return xml


def write_docx(source: zipfile.ZipFile, target: Path, replaced: dict[str, bytes]) -> None:
    """Copy every entry in order with its name and compression; swap only replaced contents."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w") as output:
            for info in source.infolist():
                clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                clone.create_system = info.create_system
                data = replaced[info.filename] if info.filename in replaced else source.read(info)
                output.writestr(clone, data)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def docx_plain_text(xml: str) -> str:
    parts = []
    for match in PLAIN.finditer(xml):
        if match.group("text") is not None:
            parts.append(html.unescape(match.group("text")))
        else:
            parts.append(" " if match.group("gap") else "\n")
    return "".join(parts)


def make_variant_docx(master: Path, target: Path, edits: list[dict], metadata: dict) -> str:
    """Write the variant DOCX and return its document text for coverage checks."""
    try:
        source = zipfile.ZipFile(master)
    except (OSError, zipfile.BadZipFile) as error:
        raise GuardError(f"Master is not a readable DOCX: {master}") from error
    with source:
        names = set(source.namelist())
        require("word/document.xml" in names, "Master DOCX has no word/document.xml")
        try:
            master_xml = source.read("word/document.xml").decode("utf-8")
        except UnicodeDecodeError as error:
            raise GuardError("word/document.xml is not UTF-8") from error
        xml, targets = apply_edits(master_xml, edits)
        check_invariant(master_xml, xml, edits, targets)
        replaced = {"word/document.xml": xml.encode("utf-8")} if edits else {}
        if metadata:
            require("docProps/core.xml" in names, "Master DOCX has no docProps/core.xml for metadata")
            core = source.read("docProps/core.xml").decode("utf-8")
            replaced["docProps/core.xml"] = set_core_metadata(core, metadata).encode("utf-8")
        write_docx(source, target, replaced)
    return docx_plain_text(xml)


# ---- rendering ----------------------------------------------------------------

def font_profile_xcu(replacements: dict[str, str]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        f'<item oor:path="{SUBSTITUTION}"><prop oor:name="Replacement" oor:op="fuse">'
        f'<value>{"true" if replacements else "false"}</value></prop></item>',
    ]
    for index, (font, substitute) in enumerate(replacements.items()):
        props = {"Always": "true", "OnScreenOnly": "false",
                 "ReplaceFont": font, "SubstituteFont": substitute}
        body = "".join(
            f'<prop oor:name="{name}" oor:op="fuse"><value>{escape(value)}</value></prop>'
            for name, value in props.items()
        )
        lines.append(f'<item oor:path="{SUBSTITUTION}/FontPairs">'
                     f'<node oor:name="_{index}" oor:op="replace">{body}</node></item>')
    lines.append("</oor:items>")
    return "\n".join(lines) + "\n"


def tail(data: bytes | str) -> str:
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return " ".join(text.split())[-400:]


def soffice_version(soffice: str) -> str:
    try:
        completed = subprocess.run([soffice, "--version"], capture_output=True, text=True, timeout=60)
    except FileNotFoundError as error:
        raise GuardError(f"LibreOffice not found ({soffice}); install it with "
                         "`brew install --cask libreoffice` or set renderer.soffice") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GuardError(f"{RENDER_HINT} ({error})") from error
    version = completed.stdout.strip()
    require(completed.returncode == 0 and bool(version), f"{RENDER_HINT} (soffice --version failed)")
    return version


def render_pdf(docx: Path, renderer: dict, work: Path) -> Path:
    profile = work / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(
        font_profile_xcu(renderer["font_replacements"]), encoding="utf-8")
    outdir = work / "out"
    outdir.mkdir()
    command = [
        renderer["soffice"], f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(outdir), str(docx),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=renderer["timeout_seconds"])
    except FileNotFoundError as error:
        raise GuardError(f"LibreOffice not found ({renderer['soffice']}); "
                         "install it with `brew install --cask libreoffice`") from error
    except subprocess.TimeoutExpired as error:
        raise GuardError(f"{RENDER_HINT} (timed out after {renderer['timeout_seconds']}s)") from error
    except OSError as error:
        raise GuardError(f"{RENDER_HINT} ({error})") from error
    pdf = outdir / f"{docx.stem}.pdf"
    require(completed.returncode == 0 and pdf.is_file(),
            f"{RENDER_HINT} (soffice exit {completed.returncode}: {tail(completed.stderr)})")
    return pdf


# ---- validation -----------------------------------------------------------------

def require_poppler() -> None:
    missing = [tool for tool in POPPLER if not shutil.which(tool)]
    require(not missing, f"Poppler tools missing ({', '.join(missing)}); install them with `brew install poppler`")


def run_tool(*args: str) -> str:
    try:
        completed = subprocess.run(list(args), capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GuardError(f"{args[0]} failed: {error}") from error
    require(completed.returncode == 0, f"{args[0]} failed: {tail(completed.stderr)}")
    return completed.stdout.decode("utf-8", "replace")


def table_rows(output: str) -> list[list[str]]:
    lines = output.splitlines()
    if len(lines) < 2 or not lines[1].startswith("-"):
        return []
    return [line.split() for line in lines[2:] if line.strip()]


def font_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def font_check(pdffonts_output: str, replacements: dict[str, str] | None = None) -> dict:
    """All fonts embedded; the detail names them so a silent fallback is visible."""
    rows = table_rows(pdffonts_output)
    require(all(len(row) >= 6 for row in rows), "Unexpected pdffonts output")
    missing = [row[0] for row in rows if row[-5] != "yes"]
    type3 = [row[0] for row in rows if "Type 3" in " ".join(row[1:-5])]
    names = sorted({re.sub(r"^[A-Z]{6}\+", "", row[0]) for row in rows})
    detail = f"{len(rows)} font(s): {', '.join(names) or 'none'}; not embedded: {', '.join(missing) or 'none'}"
    detail += f"; Type 3: {', '.join(type3) or 'none'}"
    embedded = [font_key(name) for name in names]
    unseen = sorted({target for target in (replacements or {}).values()
                     if not any(name.startswith(font_key(target)) for name in embedded)})
    if unseen:
        detail += (f"; WARNING replacement font(s) not in the PDF: {', '.join(unseen)} "
                   "(not installed, or the replaced font is unused)")
    return {"ok": bool(rows) and not missing, "detail": detail if rows else "no fonts found"}


def image_check(pdfimages_output: str, minimum: int) -> dict:
    count = sum(1 for row in table_rows(pdfimages_output) if len(row) > 2 and row[2] != "smask")
    return {"ok": count >= minimum, "detail": f"{count} image(s); minimum {minimum}"}


def word_tokens(text: str) -> set[str]:
    words = WORD.findall(unicodedata.normalize("NFC", text))
    return {word.casefold() for word in words if len(word) >= 2}


def join_wrapped(text: str) -> str:
    """Undo line-end hyphenation and CJK line wraps added by layout."""
    text = re.sub(r"(?<=[^\W\d_])-\n(?=[^\W\d_])", "", text)
    return re.sub(rf"(?<=[{CJK}])[ \t]*\n[ \t]*(?=[{CJK}])", "", text)


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def text_checks(docx_text: str, pdf_text: str, checks: dict) -> dict:
    ligatures = sorted({f"U+{ord(char):04X}" for char in pdf_text if "ﬀ" <= char <= "ﬆ"})
    cids = pdf_text.count("(cid:")
    results = {"ligatures": {
        "ok": not ligatures and not cids,
        "detail": f"ligature characters: {', '.join(ligatures) or 'none'}; (cid: sequences: {cids}",
    }}
    expected = word_tokens(docx_text)
    joined = join_wrapped(pdf_text)
    missing = sorted(expected - (word_tokens(pdf_text) | word_tokens(joined)))
    minimum = checks["min_word_coverage"]
    coverage = (len(expected) - len(missing)) / len(expected) if expected else 0.0
    detail = (f"{coverage:.2%} of {len(expected)} DOCX words found in PDF text; "
              f"minimum {minimum:.2%}")
    if missing:
        detail += f"; missing ({len(missing)}): {', '.join(missing[:20])}"
    results["word_coverage"] = {"ok": bool(expected) and coverage >= minimum, "detail": detail}
    haystacks = (normalized(pdf_text), normalized(joined))
    phrases = checks["required_phrases"]
    absent = [p for p in phrases if not any(normalized(p) in hay for hay in haystacks)]
    results["required_phrases"] = {
        "ok": not absent,
        "detail": (f"{len(phrases) - len(absent)}/{len(phrases)} present"
                   + (f"; missing: {', '.join(absent)}" if absent else "")),
    }
    return results


def parse_pdfinfo(output: str) -> dict[str, str]:
    info = {}
    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            info[key.strip()] = value.strip()
    return info


def pdf_checks(pdf: Path, docx_text: str, checks: dict, replacements: dict[str, str]) -> tuple[dict, int]:
    info = parse_pdfinfo(run_tool("pdfinfo", str(pdf)))
    pages = int(info.get("Pages", "0") or 0)
    expected = checks["expected_pages"]
    encrypted = info.get("Encrypted", "unknown")
    results = {
        "pages": {"ok": pages > 0 and expected in (None, pages),
                  "detail": f"{pages} page(s)" + (f"; expected {expected}" if expected else "")},
        "not_encrypted": {"ok": encrypted.startswith("no"), "detail": f"Encrypted: {encrypted}"},
        "fonts_embedded": font_check(run_tool("pdffonts", str(pdf)), replacements),
        "images": image_check(run_tool("pdfimages", "-list", str(pdf)), checks["min_images"]),
    }
    pdf_text = run_tool("pdftotext", "-enc", "UTF-8", str(pdf), "-")
    results.update(text_checks(docx_text, pdf_text, checks))
    return results, pages


def make_previews(pdf: Path, folder: Path) -> list[Path]:
    for old in folder.glob("preview-*.png"):
        old.unlink()
    run_tool("pdftoppm", "-r", "80", "-png", str(pdf), str(folder / "preview"))

    def page(path: Path) -> int:
        return int(path.stem.rsplit("-", 1)[1])

    return sorted(folder.glob("preview-*.png"), key=page)


# ---- manifest and commands ------------------------------------------------------

def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def input_sha256(master_bytes: bytes, variant: dict, config: dict, version: str) -> str:
    digest = hashlib.sha256(master_bytes)
    for part in (variant["edits"], config["renderer"], config["metadata"], config["checks"]):
        digest.update(b"\0" + canonical(part))
    digest.update(b"\0" + version.encode("utf-8"))
    return digest.hexdigest()


def load_manifest(path: Path) -> dict:
    manifest = load_json(path)
    variants = manifest.get("variants")
    require(manifest.get("schema_version") == 1 and isinstance(variants, list)
            and all(isinstance(v, dict) and isinstance(v.get("id"), str)
                    and v.get("status") in STATUSES and isinstance(v.get("checks", {}), dict)
                    for v in variants),
            f"Malformed manifest: {path}")
    return manifest


def save_manifest(path: Path, manifest: dict) -> None:
    atomic_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def settle_status(entry: dict, previous: dict | None) -> dict:
    """Failed checks block approval; a new PDF hash always needs a new approval."""
    if any(not check.get("ok") for check in entry["checks"].values()):
        entry.update(status="failed", approved_by=None, approved_at=None)
    elif (previous and previous.get("status") == "approved"
          and previous.get("pdf_sha256") == entry["pdf_sha256"]):
        entry.update(status="approved", approved_by=previous.get("approved_by"),
                     approved_at=previous.get("approved_at"))
    else:
        entry.update(status="draft", approved_by=None, approved_at=None)
    return entry


def pdf_current(root: Path, entry: dict) -> bool:
    if not entry.get("pdf") or not entry.get("pdf_sha256"):
        return False
    pdf = resolve(root, entry["pdf"])
    return pdf.is_file() and sha256_file(pdf) == entry["pdf_sha256"]


def build_variant(root: Path, config: dict, variant: dict, master: Path,
                  digest: str, previous: dict | None) -> dict:
    folder = output_root(root, config) / variant["id"]
    upload = config["upload_filename"]
    docx = folder / f"{Path(upload).stem}.docx"
    pdf = folder / upload
    entry = {
        "id": variant["id"], "role_family": variant["role_family"],
        "description": variant["description"], "status": "failed",
        "input_sha256": digest, "docx": None, "pdf": None, "pdf_sha256": None,
        "pages": None, "checks": {}, "previews": [], "edits": variant["edits"],
        "approved_by": None, "approved_at": None,
    }
    try:
        docx_text = make_variant_docx(master, docx, variant["edits"], config["metadata"])
    except GuardError as error:
        entry["checks"]["edits"] = {"ok": False, "detail": str(error)}
        return entry
    entry["docx"] = relative(root, docx)
    entry["checks"]["edits"] = {
        "ok": True, "detail": f"{len(variant['edits'])} edit(s); all other paragraphs and bytes unchanged",
    }
    with tempfile.TemporaryDirectory(prefix="render-resume-") as work:
        rendered = render_pdf(docx, config["renderer"], Path(work))
        staged = pdf.with_name(pdf.name + ".tmp")
        shutil.copyfile(rendered, staged)
        staged.replace(pdf)
    checks, pages = pdf_checks(pdf, docx_text, config["checks"], config["renderer"]["font_replacements"])
    entry["checks"].update(checks)
    entry.update(pdf=relative(root, pdf), pdf_sha256=sha256_file(pdf), pages=pages,
                 previews=[relative(root, path) for path in make_previews(pdf, folder)])
    return settle_status(entry, previous)


def summary(entry: dict, skipped: bool) -> dict:
    return {"id": entry["id"], "status": entry["status"], "skipped": skipped,
            "pages": entry.get("pages"), "pdf": entry.get("pdf"), "checks": entry.get("checks", {})}


def build(config_path: Path, root: Path, only: str | None = None, force: bool = False) -> dict:
    config = load_config(config_path)
    selected = [v for v in config["variants"] if only in (None, v["id"])]
    require(bool(selected), f"Unknown variant id: {only}")
    output_dir = output_root(root, config)
    require_poppler()
    master = resolve(root, config["master"])
    require(master.is_file(), f"Master DOCX not found: {master}")
    master_bytes = master.read_bytes()
    version = soffice_version(config["renderer"]["soffice"])
    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path) if manifest_path.exists() else {"schema_version": 1, "variants": []}
    before = json.dumps(manifest, sort_keys=True)
    entries = {entry["id"]: entry for entry in manifest["variants"]}
    for variant in config["variants"]:  # display metadata follows the config; it never affects approval
        if variant["id"] in entries:
            entries[variant["id"]].update(role_family=variant["role_family"], description=variant["description"])
    order = [variant["id"] for variant in config["variants"]]
    manifest["variants"] = [entries[i] for i in order if i in entries]
    results = []
    for variant in selected:
        digest = input_sha256(master_bytes, variant, config, version)
        previous = entries.get(variant["id"])
        expected_pdf = relative(root, output_dir / variant["id"] / config["upload_filename"])
        if (not force and previous and previous.get("input_sha256") == digest
                and previous.get("pdf") == expected_pdf and pdf_current(root, previous)):
            results.append(summary(previous, skipped=True))
            continue
        entry = build_variant(root, config, variant, master, digest, previous)
        if previous and previous.get("status") == "approved" and entry["status"] != "approved":
            why = "checks failed" if entry["status"] == "failed" else (
                "the PDF changed; LibreOffice output is not byte-identical between builds")
            print(f"Approval cleared for {entry['id']} ({why}). Review and approve it again; job folders "
                  "holding the old PDF fail preflight until resume-tailor copies the new one.", file=sys.stderr)
        entries[entry["id"]] = entry
        manifest.update(
            schema_version=1, master=relative(root, master),
            master_sha256=hashlib.sha256(master_bytes).hexdigest(),
            upload_filename=config["upload_filename"], generated_at=now_utc(),
            variants=[entries[i] for i in order if i in entries],
        )
        save_manifest(manifest_path, manifest)
        results.append(summary(entry, skipped=False))
    if manifest_path.exists() and json.dumps(manifest, sort_keys=True) != before:
        save_manifest(manifest_path, manifest)
    return {"manifest": relative(root, manifest_path), "soffice": version, "variants": results}


def manifest_for(config_path: Path, root: Path) -> tuple[Path, dict]:
    config = load_config(config_path)
    path = output_root(root, config) / "manifest.json"
    require(path.is_file(), f"No manifest yet; run build first: {path}")
    return path, load_manifest(path)


def approve(config_path: Path, root: Path, variant_id: str, by: str, at: str | None = None) -> dict:
    require(is_text(by), "--by must name who reviewed the PDF")
    path, manifest = manifest_for(config_path, root)
    matches = [entry for entry in manifest["variants"] if entry["id"] == variant_id]
    require(len(matches) == 1, f"Unknown variant id in manifest: {variant_id}")
    entry = matches[0]
    require(entry["status"] != "failed" and all(c.get("ok") for c in entry.get("checks", {}).values()),
            "Variant failed checks and cannot be approved; fix the config and rebuild")
    require(pdf_current(root, entry), "PDF is missing or changed since build; rebuild before approval")
    entry.update(status="approved", approved_by=by.strip(), approved_at=at or now_utc())
    save_manifest(path, manifest)
    return {key: entry[key] for key in ("id", "status", "pdf", "pdf_sha256", "approved_by", "approved_at")}


def status(config_path: Path, root: Path) -> dict:
    config = load_config(config_path)
    path = output_root(root, config) / "manifest.json"
    entries = {e["id"]: e for e in load_manifest(path)["variants"]} if path.is_file() else {}
    rows = []
    for variant in config["variants"]:
        entry = entries.get(variant["id"])
        if entry is None:
            rows.append({"id": variant["id"], "role_family": variant["role_family"], "status": "not_built"})
            continue
        rows.append({
            "id": entry["id"], "role_family": entry.get("role_family"), "status": entry["status"],
            "pages": entry.get("pages"), "pdf": entry.get("pdf"),
            "pdf_matches_manifest": pdf_current(root, entry),
            "failed_checks": [name for name, c in entry.get("checks", {}).items() if not c.get("ok")],
            "approved_by": entry.get("approved_by"), "approved_at": entry.get("approved_at"),
        })
    return {"manifest": relative(root, path), "variants": rows}


def main(argv: list[str] | None = None) -> int:
    require_python()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build", help="Render and check resume variants (normal terminal only)")
    make.add_argument("--only", metavar="ID")
    make.add_argument("--force", action="store_true", help="Rebuild even when inputs are unchanged")
    accept = commands.add_parser("approve", help="Approve one checked variant PDF after reviewing it")
    accept.add_argument("variant_id", metavar="ID")
    accept.add_argument("--by", required=True, help="Who reviewed the PDF")
    show = commands.add_parser("status", help="Show variant status from the manifest")
    for command in (make, accept, show):
        command.add_argument("--config", type=Path, default=ROOT / DEFAULT_CONFIG)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.config, ROOT, args.only, args.force)
        elif args.command == "approve":
            result = approve(args.config, ROOT, args.variant_id, args.by)
        else:
            result = status(args.config, ROOT)
    except GuardError as error:
        print(f"Blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = [v["id"] for v in result.get("variants", []) if v.get("status") == "failed"]
    if args.command == "build" and failed:
        print(f"Blocked: variant(s) failed checks: {', '.join(failed)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

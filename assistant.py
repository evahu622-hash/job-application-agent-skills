"""Small deterministic state guard for the project Skills. No browser actions here;
the only network access is read-only HTTPS to public ATS job-board APIs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import http.client
import json
import re
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlparse


MIN_PYTHON = (3, 10)
FIELDNAMES = [
    "job_id", "company", "title", "location", "source_url", "apply_url",
    "first_seen_at", "last_seen_at", "jd_sha256", "source_status",
    "source_verified_at", "application_status",
]
BLOCKED_STATUSES = {"submitted_confirmed", "submission_unknown"}
SOURCE_STATUSES = {"active_verified", "closed", "blocked", "unknown"}
MAX_SOURCE_AGE = timedelta(hours=72)
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


class GuardError(ValueError):
    """A requested state change fails a known project invariant."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise GuardError(f"File not found: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_job_id(job_id: str) -> None:
    if not JOB_ID.fullmatch(job_id):
        raise GuardError("job_id must be 1–80 letters, digits, dots, dashes or underscores")


def valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def require_python() -> None:
    if sys.version_info < MIN_PYTHON:
        found = ".".join(map(str, sys.version_info[:2]))
        raise SystemExit(f"Python 3.10+ required (found {found}); see SETUP.md")


# --- One posting behind several URLs -------------------------------------------

TRACKING_PARAMS = re.compile(r"utm_\w*|gh_src|ref|src|source|trk|lever-source\S*|lever-origin", re.I)


def ats_posting_key(url: str) -> str | None:
    """ATS posting identity (Greenhouse and Lever/Ashby IDs are global), or None."""
    try:
        board, job = parse_ats_posting(url)
    except GuardError:
        jid = parse_qs(urlparse(url.strip()).query).get("gh_jid", [""])[0]
        return f"greenhouse:{jid}" if jid.isdigit() else None
    return f"personio:{board.name}:{job}" if board.ats == "personio" else f"{board.ats}:{job}"


def posting_key(url: str) -> str | None:
    """The same posting under URL variants (host alias, tracking query, trailing slash) gets one key."""
    strong = ats_posting_key(url)
    if strong or not url.strip():
        return strong
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    query = sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                   if not TRACKING_PARAMS.fullmatch(key))
    return f"url:{host}{parsed.path.rstrip('/')}" + (f"?{urlencode(query)}" if query else "")


def row_keys(row: dict[str, str]) -> set[str]:
    # Apply URLs count only with an ATS ID: a generic careers apply page may serve many jobs.
    return {posting_key(row["source_url"]), ats_posting_key(row["apply_url"])} - {None}


def parse_utc(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GuardError(f"Invalid verification time: {value}") from error
    if instant.tzinfo is None:
        raise GuardError("Verification time must include a timezone")
    return instant.astimezone(timezone.utc)


def read_jobs(store: Path) -> list[dict[str, str]]:
    if not store.exists():
        return []
    with store.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != FIELDNAMES:
            raise GuardError(f"Unexpected jobs.csv columns in {store}")
        rows = list(reader)
    if any(None in row for row in rows):
        raise GuardError(f"Malformed CSV row in {store}")
    return rows


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as target:
        temporary = Path(target.name)
        target.write(content)
    temporary.replace(path)


def write_jobs(store: Path, rows: list[dict[str, str]]) -> None:
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(store, buffer.getvalue())


def get_job(rows: list[dict[str, str]], job_id: str) -> dict[str, str]:
    matches = [row for row in rows if row["job_id"] == job_id]
    if len(matches) != 1:
        raise GuardError(f"Expected one job_id={job_id}; found {len(matches)}")
    return matches[0]


def upsert_job(
    store: Path, *, job_id: str, company: str, title: str, location: str,
    source_url: str, apply_url: str, jd_file: Path, observed_at: str | None = None,
    source_status: str = "unknown",
) -> dict[str, str]:
    valid_job_id(job_id)
    if not company.strip() or not title.strip():
        raise GuardError("Company and title are required")
    if not valid_url(source_url) or not valid_url(apply_url):
        raise GuardError("Source and apply URLs must be HTTPS URLs")
    if source_status not in SOURCE_STATUSES:
        raise GuardError(f"Invalid source status: {source_status}")
    jd_hash = sha256_file(jd_file)
    if jd_file.stat().st_size == 0:
        raise GuardError("JD file is empty")
    rows = read_jobs(store)
    keys = row_keys({"source_url": source_url, "apply_url": apply_url})
    for other in rows:
        if other["job_id"] != job_id and keys & row_keys(other):
            raise GuardError(f"Same posting already tracked under another job_id: {other['job_id']}")
    existing = [row for row in rows if row["job_id"] == job_id]
    if len(existing) > 1:
        raise GuardError(f"Duplicate job_id already present: {job_id}")
    observed_at = observed_at or now_utc()
    parse_utc(observed_at)
    source_verified_at = observed_at if source_status == "active_verified" else ""
    if existing:
        row = existing[0]
        if posting_key(row["source_url"]) != posting_key(source_url):
            raise GuardError("Existing job_id points to a different source URL")
        row.update(
            company=company.strip(), title=title.strip(), location=location.strip(),
            source_url=source_url, apply_url=apply_url, last_seen_at=observed_at, jd_sha256=jd_hash,
            source_status=source_status, source_verified_at=source_verified_at,
        )
    else:
        row = dict(
            job_id=job_id, company=company.strip(), title=title.strip(),
            location=location.strip(), source_url=source_url, apply_url=apply_url,
            first_seen_at=observed_at, last_seen_at=observed_at,
            jd_sha256=jd_hash, source_status=source_status,
            source_verified_at=source_verified_at, application_status="discovered",
        )
        rows.append(row)
    write_jobs(store, rows)
    return row


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise GuardError(f"File not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise GuardError(f"Invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise GuardError(f"Expected JSON object: {path}")
    return value


def application_path(store: Path, job_id: str) -> Path:
    valid_job_id(job_id)
    return store.parent / "jobs" / job_id / "application.json"


def default_variants_manifest(store: Path) -> Path:
    return store.parent / "private" / "resume_variants" / "build" / "manifest.json"


def variant_manifests(store: Path, variants: Path | None) -> list[Path]:
    """Manifests the resume must be approved in: --variants (only under private/) and the default one."""
    found = []
    if variants is not None:
        if not variants.resolve().is_relative_to((store.parent / "private").resolve()):
            raise GuardError(f"--variants must be a render_resume.py manifest under private/: {variants}")
        found.append(variants)
    default = default_variants_manifest(store)
    if default.exists() and (variants is None or default.resolve() != variants.resolve()):
        found.append(default)
    return found


def approved_variant_hashes(manifest: Path) -> set[str]:
    data = load_json(manifest)
    variants = data.get("variants")
    if data.get("schema_version") != 1 or not isinstance(variants, list):
        raise GuardError(f"Malformed resume variants manifest: {manifest}")
    approved = set()
    for variant in variants:
        if not isinstance(variant, dict) or not isinstance(variant.get("status"), str):
            raise GuardError(f"Malformed resume variants manifest: {manifest}")
        if variant["status"] == "approved":
            digest = variant.get("pdf_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise GuardError(f"Malformed resume variants manifest: {manifest}")
            approved.add(digest)
    return approved


def preflight(
    store: Path, job_id: str, resume: Path, pre_submit: Path,
    checked_at: datetime | None = None, variants: Path | None = None,
) -> dict[str, str]:
    valid_job_id(job_id)
    rows = read_jobs(store)
    row = get_job(rows, job_id)
    if row["application_status"] in BLOCKED_STATUSES:
        raise GuardError(f"Application status blocks submission: {row['application_status']}")
    for other in rows:
        if (other["job_id"] != job_id and other["application_status"] in BLOCKED_STATUSES
                and row_keys(row) & row_keys(other)):
            raise GuardError(f"Same posting is {other['application_status']} as job_id {other['job_id']}")
    previous = application_path(store, job_id)
    if previous.exists() and load_json(previous).get("status") in BLOCKED_STATUSES:
        raise GuardError("Existing application record blocks submission")
    if not valid_url(row["apply_url"]):
        raise GuardError("Missing valid application URL")
    if row["source_status"] != "active_verified":
        raise GuardError(f"Original source is not active_verified: {row['source_status']}")
    verified_at = parse_utc(row["source_verified_at"])
    checked_at = checked_at or datetime.now(timezone.utc)
    age = checked_at.astimezone(timezone.utc) - verified_at
    if age < timedelta(0) or age > MAX_SOURCE_AGE:
        raise GuardError("Original source verification is stale or in the future")
    if resume.suffix.lower() != ".pdf" or not resume.is_file():
        raise GuardError("Resume must be an existing PDF")
    with resume.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise GuardError("Resume does not have a PDF header")
    resume_sha256 = sha256_file(resume)
    for manifest in variant_manifests(store, variants):
        if resume_sha256 not in approved_variant_hashes(manifest):
            raise GuardError("Resume is not an approved resume variant")
    payload = load_json(pre_submit)
    if payload.get("dry_run") is True:
        raise GuardError("Dry-run cannot be ready for submission")
    expected = {
        "job_id": job_id,
        "jd_sha256": row["jd_sha256"],
        "resume_sha256": resume_sha256,
        "mode": "review",
        "fields_verified": True,
        "attachment_verified": True,
        "unknown_required_fields": [],
    }
    for key, value in expected.items():
        if type(payload.get(key)) is not type(value) or payload.get(key) != value:
            raise GuardError(f"Pre-submit check failed: {key}")
    return {
        "job_id": job_id, "apply_url": row["apply_url"],
        "jd_sha256": row["jd_sha256"],
        "resume_sha256": expected["resume_sha256"],
        "status": "ready_for_review",
    }


def record_outcome(
    store: Path, job_id: str, resume: Path, pre_submit: Path,
    outcome: str, evidence: str, observed_at: str | None = None,
    checked_at: datetime | None = None, variants: Path | None = None,
) -> dict[str, str]:
    if outcome not in {"confirmed", "unknown"}:
        raise GuardError("Outcome must be confirmed or unknown")
    if not evidence.strip():
        raise GuardError("Describe the observed receipt or uncertainty")
    rows = read_jobs(store)
    row = get_job(rows, job_id)
    previous_path = application_path(store, job_id)
    previous = load_json(previous_path) if previous_path.exists() else None
    previous_status = previous.get("status") if previous else row["application_status"]
    if previous_status == "submitted_confirmed":
        raise GuardError("Application already confirmed")
    if previous_status == "submission_unknown":
        if outcome != "confirmed":
            raise GuardError("Unknown outcome cannot be retried; investigate existing submission")
        # Resolution records a newly found receipt; it never clicks submit again.
        checked = previous
    else:
        checked = preflight(
            store, job_id, resume, pre_submit, checked_at=checked_at, variants=variants,
        )
    status = "submitted_confirmed" if outcome == "confirmed" else "submission_unknown"
    application = {
        "job_id": job_id, "status": status, "observed_at": observed_at or now_utc(),
        "evidence": evidence.strip(), "resume_sha256": checked["resume_sha256"],
        "jd_sha256": checked["jd_sha256"], "mode": "review",
    }
    # Write evidence first. If the CSV write is interrupted, preflight still
    # sees application.json and refuses another click.
    atomic_text(previous_path, json.dumps(application, ensure_ascii=False, indent=2) + "\n")
    row["application_status"] = status
    write_jobs(store, rows)
    return application


def write_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_text(path: Path) -> str:
    if not path.is_file():
        raise GuardError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


# --- Canonical JD text --------------------------------------------------------

SKIP_TAGS = {"script", "style"}
BLOCK_BREAKS = {  # tag -> line breaks before the next text (2 = blank line)
    "p": 2, "section": 2, **{f"h{level}": 2 for level in range(1, 7)},
    "div": 1, "li": 1, "ul": 1, "ol": 1, "tr": 1,
}
HTML_TAG = re.compile(r"<[A-Za-z!/]")


class _HtmlText(HTMLParser):
    """Text with breaks only at block tags; source whitespace collapses as in HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.pending = 0
        self.bullet = False
        self.skip = 0
        self.li_depth = 0

    def _block(self, tag: str) -> None:  # inside a list item, paragraphs stay compact
        self.pending = max(self.pending, 1 if self.li_depth else BLOCK_BREAKS[tag])

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "br":
            self.pending += 1
        elif tag in BLOCK_BREAKS:
            self._block(tag)
            if tag == "li":
                self.li_depth += 1
                self.bullet = True

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag not in SKIP_TAGS and tag != "li":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_BREAKS:
            self._block(tag)
            if tag == "li":
                self.li_depth = max(0, self.li_depth - 1)
                self.bullet = False

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            if self.parts and not self.pending:
                self.parts.append(" ")
            return
        if self.pending:
            if self.parts:
                self.parts.append("\n" * self.pending)
            self.pending = 0
            text = text.lstrip()
        if self.bullet:
            text = "- " + text.lstrip()
            self.bullet = False
        self.parts.append(text)


def tidy_lines(text: str) -> str:
    lines = [
        re.sub(r"[^\S\n]+", " ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    return unicodedata.normalize("NFC", "\n".join(kept)) + "\n"


def html_to_text(markup: str) -> str:
    """Deterministic JD text: the same API payload always gives the same bytes.

    Entities are decoded exactly once, as a browser does, so a JD that mentions
    "&lt;style&gt;" keeps that text instead of swallowing the rest of the JD."""
    if HTML_TAG.search(markup):
        parser = _HtmlText()
        parser.feed(markup)
        parser.close()
        text = "".join(parser.parts)
    else:
        text = html.unescape(markup)
    return tidy_lines(text)


def unescape_greenhouse(content: str) -> str:
    """Greenhouse sends its HTML escaped once; undo that once (never "until stable")."""
    return content if HTML_TAG.search(content) else html.unescape(content)


# --- Public ATS job-board APIs: read-only, no browser, no login ---------------

USER_AGENT = "job-application-agent-skills/2"
HTTP_TIMEOUT_SECONDS = 30
# boards-api.greenhouse.io also serves EU-hosted boards (checked live 2026-10-08);
# boards-api.eu.greenhouse.io does not exist in DNS.
API_HOSTS = {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"}
PERSONIO_HOST = re.compile(r"([a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?:de|com)\Z")
ATS_SITES = {  # human host -> (ats, canonical human host)
    "job-boards.greenhouse.io": ("greenhouse", "job-boards.greenhouse.io"),
    "boards.greenhouse.io": ("greenhouse", "job-boards.greenhouse.io"),
    "job-boards.eu.greenhouse.io": ("greenhouse", "job-boards.eu.greenhouse.io"),
    "boards.eu.greenhouse.io": ("greenhouse", "job-boards.eu.greenhouse.io"),
    "jobs.lever.co": ("lever", "jobs.lever.co"),
    "jobs.eu.lever.co": ("lever", "jobs.eu.lever.co"),
    "jobs.ashbyhq.com": ("ashby", "jobs.ashbyhq.com"),
}
SHORTHAND = re.compile(r"(greenhouse|lever|ashby|personio):([^:/?#\s]+)(?::([^:/?#\s]+))?\Z")
SHORTHAND_SITES = {
    "greenhouse": "job-boards.greenhouse.io", "lever": "jobs.lever.co", "ashby": "jobs.ashbyhq.com",
}
POSTING_PATHS = {
    "greenhouse": re.compile(r"jobs/(\d+)"),
    "lever": re.compile(r"([0-9A-Za-z-]+)(?:/apply)?"),
    "ashby": re.compile(r"([0-9A-Za-z-]+)(?:/application)?"),
    "personio": re.compile(r"job/(\d+)"),
}
POSTED_AT = {  # ats -> (API field, what that date means)
    "greenhouse": ("first_published", "first published"),
    "lever": ("createdAt", "created"),
    "ashby": ("publishedAt", "last published; may reflect a repost"),
    "personio": ("createdAt", "created"),
}
Fetch = Callable[[str], bytes]


class SourceClosed(Exception):
    """The ATS API answered 404/410 or its board no longer lists the posting."""


@dataclass(frozen=True)
class AtsBoard:
    ats: str
    name: str
    site: str  # canonical human-facing host

    @property
    def api_host(self) -> str:
        if self.ats == "greenhouse":
            return "boards-api.greenhouse.io"
        if self.ats == "lever":
            return "api.eu.lever.co" if ".eu." in self.site else "api.lever.co"
        return "api.ashbyhq.com" if self.ats == "ashby" else self.site

    def list_url(self) -> str:
        name = quote(self.name, safe="")
        return {
            "greenhouse": f"https://{self.api_host}/v1/boards/{name}/jobs",
            "lever": f"https://{self.api_host}/v0/postings/{name}?mode=json",
            "ashby": f"https://{self.api_host}/posting-api/job-board/{name}?includeCompensation=true",
            "personio": f"https://{self.site}/xml?language=en",
        }[self.ats]

    def default_language_url(self) -> str:
        """Personio feed in each posting's own language; German-only postings are empty in ?language=en."""
        return f"https://{self.site}/xml"

    def posting_api_url(self, ats_job_id: str, questions: bool = False) -> str:
        name, job = quote(self.name, safe=""), quote(ats_job_id, safe="")
        if self.ats == "greenhouse":
            suffix = "?questions=true" if questions else ""
            return f"https://{self.api_host}/v1/boards/{name}/jobs/{job}{suffix}"
        if self.ats == "lever":
            return f"https://{self.api_host}/v0/postings/{name}/{job}?mode=json"
        return self.list_url()  # Ashby and Personio publish whole boards only

    def page_url(self, ats_job_id: str) -> str:
        name, job = quote(self.name, safe=""), quote(ats_job_id, safe="")
        path = {"greenhouse": f"{name}/jobs/{job}", "personio": f"job/{job}"}
        return f"https://{self.site}/" + path.get(self.ats, f"{name}/{job}")


def _ats_location(url: str) -> tuple[AtsBoard, str]:
    """Split a supported ATS URL or shorthand into its board and the rest of the path."""
    text = url.strip()
    short = SHORTHAND.fullmatch(text)
    if short:
        ats, name, job = short.groups()
        name = name.lower()
        site = f"{name}.jobs.personio.de" if ats == "personio" else SHORTHAND_SITES[ats]
        if ats != "personio" or PERSONIO_HOST.fullmatch(site):
            prefix = {"greenhouse": "jobs/", "personio": "job/"}.get(ats, "")
            return AtsBoard(ats, name, site), f"{prefix}{job}" if job else ""
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if parsed.scheme == "https":
        personio = PERSONIO_HOST.fullmatch(host)
        if personio:  # .de and .com serve the same tenant; one canonical host keeps source_url stable
            name = personio.group(1)
            return AtsBoard("personio", name, f"{name}.jobs.personio.de"), "/".join(segments)
        if host in ATS_SITES and segments:
            ats, site = ATS_SITES[host]
            if ats == "greenhouse" and segments[0] == "embed":  # embed/job_board?for=B, embed/job_app?for=B&token=N
                query = parse_qs(parsed.query)
                board, token = query.get("for", [""])[0], query.get("token", [""])[0]
                if board:
                    return AtsBoard(ats, board.lower(), site), f"jobs/{token}" if token else ""
            return AtsBoard(ats, segments[0].lower(), site), "/".join(segments[1:])
    hint = ""
    jid = parse_qs(parsed.query).get("gh_jid", [""])[0]
    if jid:
        hint = f" (employer page of a Greenhouse job: use greenhouse:BOARD:{jid}, BOARD from the Apply link)"
    raise GuardError(f"Unsupported ATS URL: {url}{hint}")


def parse_ats_source(url: str) -> AtsBoard:
    board, rest = _ats_location(url)
    if rest:
        raise GuardError(f"Unsupported ATS URL (expected a job-board URL): {url}")
    return board


def parse_ats_posting(url: str) -> tuple[AtsBoard, str]:
    board, rest = _ats_location(url)
    match = POSTING_PATHS[board.ats].fullmatch(rest)
    if not match:
        raise GuardError(f"Unsupported ATS URL (expected one posting): {url}")
    return board, match.group(1).lower()


def _id_text(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "-", text.lower())


def make_job_id(ats: str, board: str, ats_job_id: str) -> str:
    """{ats}-{board}-{ats_job_id}; a board slug too long for 80 characters is cut and tagged with its hash."""
    job_id = _id_text(f"{ats}-{board}-{ats_job_id}")
    if len(job_id) > 80:
        tag = hashlib.sha256(board.encode("utf-8")).hexdigest()[:8]
        room = 80 - len(_id_text(f"{ats}--{tag}-{ats_job_id}"))
        cut = _id_text(board)[:room] if room > 0 else ""
        job_id = _id_text(f"{ats}-{cut}-{tag}-{ats_job_id}" if cut else f"{ats}-{tag}-{ats_job_id}")
    if not JOB_ID.fullmatch(job_id):
        raise GuardError(f"Cannot derive a valid job_id for {ats} posting {ats_job_id!r} on board {board!r}")
    return job_id


def check_api_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host in API_HOSTS or PERSONIO_HOST.fullmatch(host)):
        raise GuardError(f"Refusing a request outside the fixed ATS API hosts: {url}")


class _ApiRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # urllib's signature
        check_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str) -> bytes:
    """GET one public ATS API URL: 404/410 -> SourceClosed, other failures -> GuardError."""
    check_api_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_ApiRedirect)
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            raise SourceClosed(f"HTTP {error.code}") from error
        raise GuardError(f"ATS API answered HTTP {error.code}; source status unknown: {url}") from error
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        reason = getattr(error, "reason", error)
        raise GuardError(f"ATS API unreachable ({reason}); source status unknown: {url}") from error


def utc_z(value: object) -> str | None:
    """ISO-8601 text or epoch milliseconds -> 'YYYY-MM-DDTHH:MM:SSZ'; None if absent or invalid."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            instant = datetime.fromtimestamp(value / 1000, timezone.utc)
        else:
            text = str(value).strip().replace("Z", "+00:00")
            text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text)
            instant = datetime.fromisoformat(text)
    except (ValueError, OverflowError, OSError):
        return None
    if instant.tzinfo is None:
        return None
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def recency(posted_at: str | None, days: int, now: datetime) -> dict:
    if days < 0:
        raise GuardError("--recency-days must be 0 or more")
    if posted_at is None:
        return {"days": days, "age_days": None, "status": "unknown"}
    age = now.astimezone(timezone.utc) - parse_utc(posted_at)
    status = "met" if age <= timedelta(days=days) else "unmet"
    # Round up to 0.1 day so the shown age never looks inside the window when status is unmet.
    tenths = -(-(age // timedelta(microseconds=1)) // 8_640_000_000)
    return {"days": days, "age_days": tenths / 10, "status": status}


def _plain(value: object) -> str | None:
    if isinstance(value, list):
        value = ", ".join(str(item).strip() for item in value if item not in (None, ""))
    text = "" if value is None else str(value).strip()
    return text or None


def _join_locations(values: list) -> str | None:
    unique: list[str] = []
    for value in values:
        text = _plain(value)
        if text and text not in unique:
            unique.append(text)
    return "; ".join(unique) or None


def _greenhouse_metadata(job: dict, name: str) -> object:
    """Value of a board-defined custom field such as 'Employment Type', if the board has one."""
    for item in job.get("metadata") or []:
        if isinstance(item, dict) and str(item.get("name", "")).strip().casefold() == name:
            return item.get("value")
    return None


def _greenhouse_fields(job: dict) -> dict:
    return {
        "ats_job_id": job.get("id"), "title": job.get("title"), "company": job.get("company_name"),
        "location": (job.get("location") or {}).get("name"), "apply_url": job.get("absolute_url"),
        "posted_at": job.get("first_published"), "updated_at": job.get("updated_at"),
        "employment_type": _greenhouse_metadata(job, "employment type"),
        "workplace_type": _greenhouse_metadata(job, "workplace type"), "compensation": None,
        "html": unescape_greenhouse(job.get("content") or ""),
    }


def _lever_fields(posting: dict) -> dict:
    categories = posting.get("categories") or {}
    salary = posting.get("salaryRange") or {}
    amounts = "-".join(str(salary[key]) for key in ("min", "max") if salary.get(key) is not None)
    lists = "".join(
        f"<h3>{html.escape(item.get('text') or '')}</h3><ul>{item.get('content') or ''}</ul>"
        for item in posting.get("lists") or [] if isinstance(item, dict)
    )
    return {
        "ats_job_id": posting.get("id"), "title": posting.get("text"), "company": None,
        "location": _join_locations([categories.get("location"), *(categories.get("allLocations") or [])]),
        "apply_url": posting.get("applyUrl"), "posted_at": posting.get("createdAt"),
        "updated_at": None, "employment_type": categories.get("commitment"),
        "workplace_type": posting.get("workplaceType"),
        "compensation": _plain(" ".join(
            str(part) for part in (amounts, salary.get("currency"), salary.get("interval")) if part
        )),
        "html": (posting.get("description") or "") + lists + (posting.get("additional") or ""),
    }


def _ashby_fields(job: dict) -> dict:
    secondary = [item.get("location") for item in job.get("secondaryLocations") or [] if isinstance(item, dict)]
    compensation = job.get("compensation") or {}
    return {
        "ats_job_id": job.get("id"), "title": job.get("title"), "company": None,
        "location": _join_locations([job.get("location"), *secondary]),
        "apply_url": job.get("applyUrl"), "posted_at": job.get("publishedAt"), "updated_at": None,
        "employment_type": job.get("employmentType"), "workplace_type": job.get("workplaceType"),
        "compensation": compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary"),
        "html": job.get("descriptionHtml") or "",
    }


def _xml_text(element: ET.Element, path: str) -> str | None:
    return _plain(element.findtext(path))


def _personio_fields(position: ET.Element) -> dict:
    offices = [position.findtext("office")] + [item.text for item in position.findall("additionalOffices/office")]
    sections = "".join(
        f"<h3>{html.escape(_xml_text(block, 'name') or '')}</h3>{block.findtext('value') or ''}"
        for block in position.findall("jobDescriptions/jobDescription")
    )
    employment = [_xml_text(position, "employmentType"), _xml_text(position, "schedule")]
    return {
        "ats_job_id": _xml_text(position, "id"), "title": _xml_text(position, "name"),
        "company": _xml_text(position, "subcompany"), "location": _join_locations(offices),
        "apply_url": None, "posted_at": _xml_text(position, "createdAt"), "updated_at": None,
        "employment_type": " / ".join(part for part in employment if part) or None,
        "workplace_type": None, "compensation": None, "html": sections,
    }


def _decode_json(data: bytes, url: str) -> object:
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise GuardError(f"ATS API returned invalid JSON: {url}") from error


def _read_board(board: AtsBoard, fetch: Fetch, url: str | None = None) -> list[dict]:
    """Every posting on a board as normalized fields; raises SourceClosed on 404/410."""
    url = url or board.list_url()
    data = fetch(url)
    if board.ats == "personio":
        try:
            root = ET.fromstring(data)
        except ET.ParseError as error:
            raise GuardError(f"ATS API returned invalid XML: {url}") from error
        return [_personio_fields(position) for position in root.iter("position")]
    payload = _decode_json(data, url)
    jobs = payload if board.ats == "lever" else (payload.get("jobs") if isinstance(payload, dict) else None)
    if not isinstance(jobs, list):
        raise GuardError(f"Unexpected ATS API response: {url}")
    convert = {"greenhouse": _greenhouse_fields, "lever": _lever_fields, "ashby": _ashby_fields}[board.ats]
    return [convert(job) for job in jobs if isinstance(job, dict)]


def _summary(board: AtsBoard, fields: dict, recency_days: int | None, now: datetime) -> dict:
    ats_job_id = _plain(fields["ats_job_id"])
    if not ats_job_id:
        raise GuardError(f"ATS API returned a posting without an id: {board.list_url()}")
    posted_field, meaning = POSTED_AT[board.ats]
    posted_at = utc_z(fields["posted_at"])
    summary = {
        "ats": board.ats, "board": board.name, "ats_job_id": ats_job_id,
        "job_id": make_job_id(board.ats, board.name, ats_job_id),
        "title": _plain(fields["title"]), "location": _plain(fields["location"]),
        "posted_at": posted_at, "posted_at_field": posted_field, "posted_at_meaning": meaning,
        "updated_at": utc_z(fields["updated_at"]),
        "employment_type": _plain(fields["employment_type"]),
        "workplace_type": _plain(fields["workplace_type"]),
        "compensation": _plain(fields["compensation"]),
        "url": board.page_url(ats_job_id),
    }
    if recency_days is not None:
        summary["recency"] = recency(posted_at, recency_days, now)
    return summary


def list_ats(
    source_url: str, recency_days: int | None = None, fetch: Fetch | None = None,
    now: datetime | None = None,
) -> list[dict]:
    board = parse_ats_source(source_url)
    try:
        postings = _read_board(board, fetch or http_get)
    except SourceClosed as error:
        raise GuardError(f"ATS board not found ({error}): {board.list_url()}") from error
    now = now or datetime.now(timezone.utc)
    return [_summary(board, fields, recency_days, now) for fields in postings]


def _find_posting(postings: list[dict], ats_job_id: str) -> dict | None:
    return next((fields for fields in postings
                 if (_plain(fields["ats_job_id"]) or "").lower() == ats_job_id), None)


def _fetch_posting(board: AtsBoard, ats_job_id: str, api_url: str, fetch: Fetch) -> tuple[dict, dict, str]:
    """Normalized fields, raw payload and the API URL actually used; raises SourceClosed if gone."""
    if board.ats in ("ashby", "personio"):
        fields = _find_posting(_read_board(board, fetch), ats_job_id)
        if fields is None:
            raise SourceClosed("absent from the board listing")
        if board.ats == "personio" and not html_to_text(fields["html"]).strip():
            fallback = board.default_language_url()
            original = _find_posting(_read_board(board, fetch, fallback), ats_job_id)
            if original is not None and html_to_text(original["html"]).strip():
                return {**fields, "html": original["html"]}, {}, fallback
        return fields, {}, api_url
    payload = _decode_json(fetch(api_url), api_url)
    if not isinstance(payload, dict):
        raise GuardError(f"Unexpected ATS API response: {api_url}")
    convert = _greenhouse_fields if board.ats == "greenhouse" else _lever_fields
    return convert(payload), payload, api_url


def _form_question(section: str, item: dict) -> dict:
    description = item.get("description")
    return {
        "section": section, "label": _plain(item.get("label")),
        "required": item.get("required") is True,
        "description": html_to_text(unescape_greenhouse(description)).strip() or None if description else None,
        "fields": [
            {
                "name": field.get("name"), "type": field.get("type"),
                "options": [value.get("label") for value in field.get("values") or [] if isinstance(value, dict)],
            }
            for field in item.get("fields") or [] if isinstance(field, dict)
        ],
    }


def greenhouse_questions(payload: dict) -> list[dict]:
    questions = [
        _form_question(section, item)
        for section, key in (("application", "questions"), ("location", "location_questions"))
        for item in payload.get(key) or [] if isinstance(item, dict)
    ]
    for block in payload.get("compliance") or []:
        for item in block.get("questions") or [] if isinstance(block, dict) else []:
            if isinstance(item, dict):
                questions.append(_form_question("compliance", item))
    demographic = payload.get("demographic_questions")
    for item in demographic.get("questions") or [] if isinstance(demographic, dict) else []:
        if isinstance(item, dict):
            questions.append({
                "section": "demographic", "label": _plain(item.get("label")),
                "required": item.get("required") is True, "description": None,
                "fields": [{
                    "name": str(item.get("id")), "type": item.get("type"),
                    "options": [option.get("label") for option in item.get("answer_options") or []
                                if isinstance(option, dict)],
                }],
            })
    return questions


def fetch_ats(
    posting_url: str, job_dir: Path, questions: bool = False, recency_days: int | None = None,
    fetch: Fetch | None = None, now: datetime | None = None,
) -> dict:
    """Save one posting's canonical jd.txt and source.json from its public ATS API."""
    board, ats_job_id = parse_ats_posting(posting_url)
    job_id = make_job_id(board.ats, board.name, ats_job_id)
    if job_dir.name != job_id:  # check-quotes and the dashboard look in jobs/<job_id>/
        raise GuardError(f"--job-dir must be jobs/{job_id} for this posting, not {job_dir}")
    now = now or datetime.now(timezone.utc)
    fetched_at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    api_url = board.posting_api_url(ats_job_id, questions)
    source_path = job_dir / "source.json"
    previous = load_json(source_path) if source_path.exists() else {}
    if previous and previous.get("job_id") != job_id:
        raise GuardError(f"{source_path} belongs to job_id {previous.get('job_id')}, not {job_id}")
    if recency_days is None:  # a refresh without the flag keeps the window chosen while scouting
        window = previous.get("recency")
        recency_days = window.get("days") if isinstance(window, dict) and type(window.get("days")) is int else None
    identity = {
        "schema_version": 1, "ats": board.ats, "board": board.name,
        "ats_job_id": ats_job_id, "job_id": job_id, "source_url": board.page_url(ats_job_id),
    }
    try:
        fields, payload, api_url = _fetch_posting(board, ats_job_id, api_url, fetch or http_get)
    except SourceClosed as reason:
        kept = {k: v for k, v in previous.items() if k not in ("recency", "source_status", "closed_reason")}
        closed = {
            "source_status": "closed", "closed_reason": str(reason), **kept, **identity,
            "fetched_at": fetched_at, "api_url": api_url,
        }
        write_json(source_path, closed)
        return closed
    text = html_to_text(fields["html"])
    if not text.strip():
        raise GuardError(f"ATS API returned no JD text: {api_url}")
    jd_path = job_dir / "jd.txt"
    if not jd_path.is_file() or jd_path.read_bytes() != text.encode("utf-8"):
        atomic_text(jd_path, text)
    summary = _summary(board, fields, None, now)
    apply_url = _plain(fields["apply_url"]) or ""
    source = {
        **identity, "title": summary["title"],
        "company": _plain(fields["company"]) or board.name, "location": summary["location"],
        "apply_url": apply_url if valid_url(apply_url) else identity["source_url"],
        **{key: summary[key] for key in (
            "posted_at", "posted_at_field", "posted_at_meaning", "updated_at",
            "employment_type", "workplace_type", "compensation",
        )},
        "fetched_at": fetched_at, "api_url": api_url, "extraction": f"ats-api/{board.ats}/v1",
        "jd_sha256": sha256_file(jd_path), "source_status": "active_verified",
    }
    if recency_days is not None:
        source["recency"] = recency(source["posted_at"], recency_days, now)
    write_json(source_path, source)
    if questions and board.ats == "greenhouse":
        write_json(job_dir / "form.json", {
            "schema_version": 1, "job_id": job_id, "source_url": source["source_url"],
            "apply_url": source["apply_url"], "fetched_at": fetched_at, "api_url": api_url,
            "questions": greenhouse_questions(payload),
        })
    return source


# --- Upsert from source.json ---------------------------------------------------

UPSERT_FIELDS = ("job_id", "company", "title", "location", "source_url", "apply_url", "jd_file")


def upsert_fields_from_source(path: Path) -> dict:
    source = load_json(path)
    fields: dict = {}
    for key in ("job_id", "company", "title", "location", "source_url", "apply_url", "source_status", "fetched_at"):
        value = source.get(key)
        if key == "location" and value is None:
            value = ""
        if not isinstance(value, str):
            raise GuardError(f"source.json lacks {key}: {path}")
        fields["observed_at" if key == "fetched_at" else key] = value
    fields["jd_file"] = path.parent / "jd.txt"
    return fields


def upsert_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    fields = upsert_fields_from_source(args.source_json) if args.source_json else {}
    for key in (*UPSERT_FIELDS, "source_status", "observed_at"):
        if getattr(args, key) is not None:
            fields[key] = getattr(args, key)
    missing = [f"--{key.replace('_', '-')}" for key in UPSERT_FIELDS if key not in fields]
    if missing:
        parser.error("the following arguments are required: " + ", ".join(missing) + " (or --source-json)")
    fields.setdefault("source_status", "unknown")
    return fields


# --- Quotes in job notes must come from this job's own JD ----------------------

# Straight, curly and CJK corner quotes; a quote may wrap lines but not cross a blank line.
QUOTED = re.compile(r'"([^"]*)"|“([^”]*)”|「([^」]*)」|『([^』]*)』')
QUOTE_MARKS = '"“”「」『』'
PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
CJK_TEXT = re.compile(r"[぀-ヿ㐀-鿿가-힯豈-﫿]")
MIN_QUOTE_LENGTH = 12
UNIFY_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
})


def normalize_for_quotes(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(UNIFY_PUNCTUATION)
    text = re.sub(r"(?m)^[ \t]*[-•*][ \t]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_quotes(markdown: str) -> tuple[list[str], list[str]]:
    """Quoted passages of 12+ characters (code included), and paragraphs with an unpaired quote mark."""
    quotes, unpaired = [], []
    for paragraph in PARAGRAPH_BREAK.split(markdown):
        for match in QUOTED.finditer(paragraph):
            text = next(group for group in match.groups() if group is not None).strip()
            if len(normalize_for_quotes(text)) >= MIN_QUOTE_LENGTH:
                quotes.append(text)
        if any(mark in QUOTED.sub(" ", paragraph) for mark in QUOTE_MARKS):
            excerpt = " ".join(paragraph.split())
            unpaired.append(excerpt if len(excerpt) <= 80 else excerpt[:77] + "...")
    return quotes, unpaired


def quote_in_jd(quote: str, jd: str) -> bool:
    """Verbatim after normalization; an ellipsis may omit text, but each part needs 12+ characters, in order."""
    parts = [part.strip() for part in normalize_for_quotes(quote).split("...")]
    parts = [part for part in parts if part]
    if len(parts) > 1 and any(len(part) < MIN_QUOTE_LENGTH for part in parts):
        return False
    position = 0
    for part in parts:
        found = jd.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return bool(parts)


def check_quotes(store: Path, job_id: str, names: list[str] | None = None) -> dict:
    valid_job_id(job_id)
    job_dir = store.parent / "jobs" / job_id
    jd_text = read_text(job_dir / "jd.txt")
    jd = normalize_for_quotes(jd_text)
    cjk_jd = bool(CJK_TEXT.search(jd_text))
    checked, missing, skipped = 0, [], []
    for name in names or ["fit.md"]:
        if Path(name).name != name or name in (".", ".."):
            raise GuardError(f"--file must be a file name inside jobs/{job_id}/: {name}")
        quotes, unpaired = extract_quotes(read_text(job_dir / name))
        missing += [f"unpaired quote mark in: {excerpt}" for excerpt in unpaired]
        for quoted in quotes:
            if CJK_TEXT.search(quoted) and not cjk_jd:  # Chinese wording cannot be a verbatim quote here
                skipped.append(quoted)
                continue
            checked += 1
            if not quote_in_jd(quoted, jd):
                missing.append(quoted)
    return {"job_id": job_id, "checked": checked, "missing": missing, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    require_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path("jobs.csv"))
    commands = parser.add_subparsers(dest="command", required=True)

    upsert = commands.add_parser("upsert-job", help="Add or refresh one JD and its live source status")
    for name in ("job-id", "company", "title", "location", "source-url", "apply-url"):
        upsert.add_argument(f"--{name}")
    upsert.add_argument("--jd-file", type=Path)
    upsert.add_argument("--source-status", choices=sorted(SOURCE_STATUSES))
    upsert.add_argument("--source-json", type=Path,
                        help="fetch-ats source.json; supplies the flags above (explicit flags win)")
    upsert.add_argument("--observed-at", help="ISO-8601 time the source was observed")

    check = commands.add_parser("preflight", help="Check a form before user review")
    check.add_argument("job_id")
    check.add_argument("--resume", required=True, type=Path)
    check.add_argument("--pre-submit", required=True, type=Path)
    check.add_argument("--variants", type=Path, help="Resume variants manifest.json")

    record = commands.add_parser("record-outcome", help="Record observed result; never submits")
    record.add_argument("job_id")
    record.add_argument("--resume", required=True, type=Path)
    record.add_argument("--pre-submit", required=True, type=Path)
    record.add_argument("--outcome", choices=("confirmed", "unknown"), required=True)
    record.add_argument("--evidence", required=True)
    record.add_argument("--variants", type=Path, help="Resume variants manifest.json")

    ats_list = commands.add_parser("list-ats", help="List postings from a public ATS job-board API")
    ats_list.add_argument("source_url")
    ats_list.add_argument("--recency-days", type=int)

    ats_fetch = commands.add_parser("fetch-ats", help="Save one posting's jd.txt and source.json from its ATS API")
    ats_fetch.add_argument("posting_url")
    ats_fetch.add_argument("--job-dir", required=True, type=Path)
    ats_fetch.add_argument("--questions", action="store_true", help="Also write form.json (Greenhouse)")
    ats_fetch.add_argument("--recency-days", type=int)

    quotes = commands.add_parser("check-quotes", help="Check that quoted JD text exists in this job's jd.txt")
    quotes.add_argument("job_id")
    quotes.add_argument("--file", dest="files", action="extend", nargs="+", metavar="NAME")

    args = parser.parse_args(argv)
    try:
        if args.command == "upsert-job":
            result = upsert_job(args.store, **upsert_arguments(args, upsert))
        elif args.command == "preflight":
            result = preflight(
                args.store, args.job_id, args.resume, args.pre_submit, variants=args.variants,
            )
        elif args.command == "record-outcome":
            result = record_outcome(
                args.store, args.job_id, args.resume, args.pre_submit,
                args.outcome, args.evidence, variants=args.variants,
            )
        elif args.command == "list-ats":
            result = list_ats(args.source_url, recency_days=args.recency_days)
        elif args.command == "fetch-ats":
            result = fetch_ats(
                args.posting_url, args.job_dir, questions=args.questions,
                recency_days=args.recency_days,
            )
            if args.questions and result["ats"] != "greenhouse":
                print(f"Note: the {result['ats']} public API has no application questions; "
                      "form.json not written.", file=sys.stderr)
        else:
            result = check_quotes(args.store, args.job_id, args.files)
    except GuardError as error:
        print(f"Blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "check-quotes" and result["missing"]:
        print("Blocked: quotes not found in this job's jd.txt", file=sys.stderr)
        return 2
    if args.command == "check-quotes" and not result["checked"]:
        print('Note: no JD quote was checked; JD quotes must be in "..." or “...”.', file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

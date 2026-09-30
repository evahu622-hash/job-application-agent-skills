"""Small deterministic state guard for the project Skills. No web actions here."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


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
    if any(row["source_url"] == source_url and row["job_id"] != job_id for row in rows):
        raise GuardError("Source URL already belongs to another job_id")
    existing = [row for row in rows if row["job_id"] == job_id]
    if len(existing) > 1:
        raise GuardError(f"Duplicate job_id already present: {job_id}")
    observed_at = observed_at or now_utc()
    parse_utc(observed_at)
    source_verified_at = observed_at if source_status == "active_verified" else ""
    if existing:
        row = existing[0]
        if row["source_url"] != source_url:
            raise GuardError("Existing job_id points to a different source URL")
        row.update(
            company=company.strip(), title=title.strip(), location=location.strip(),
            apply_url=apply_url, last_seen_at=observed_at, jd_sha256=jd_hash,
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


def preflight(
    store: Path, job_id: str, resume: Path, pre_submit: Path,
    checked_at: datetime | None = None,
) -> dict[str, str]:
    valid_job_id(job_id)
    row = get_job(read_jobs(store), job_id)
    if row["application_status"] in BLOCKED_STATUSES:
        raise GuardError(f"Application status blocks submission: {row['application_status']}")
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
    payload = load_json(pre_submit)
    if payload.get("dry_run") is True:
        raise GuardError("Dry-run cannot be ready for submission")
    expected = {
        "job_id": job_id,
        "jd_sha256": row["jd_sha256"],
        "resume_sha256": sha256_file(resume),
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
    checked_at: datetime | None = None,
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
        checked = preflight(store, job_id, resume, pre_submit, checked_at=checked_at)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path("jobs.csv"))
    commands = parser.add_subparsers(dest="command", required=True)

    upsert = commands.add_parser("upsert-job", help="Add or refresh one JD and its live source status")
    for name in ("job-id", "company", "title", "location", "source-url", "apply-url"):
        upsert.add_argument(f"--{name}", required=True)
    upsert.add_argument("--jd-file", required=True, type=Path)
    upsert.add_argument("--source-status", choices=sorted(SOURCE_STATUSES), default="unknown")

    check = commands.add_parser("preflight", help="Check a form before user review")
    check.add_argument("job_id")
    check.add_argument("--resume", required=True, type=Path)
    check.add_argument("--pre-submit", required=True, type=Path)

    record = commands.add_parser("record-outcome", help="Record observed result; never submits")
    record.add_argument("job_id")
    record.add_argument("--resume", required=True, type=Path)
    record.add_argument("--pre-submit", required=True, type=Path)
    record.add_argument("--outcome", choices=("confirmed", "unknown"), required=True)
    record.add_argument("--evidence", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "upsert-job":
            result = upsert_job(
                args.store, job_id=args.job_id, company=args.company,
                title=args.title, location=args.location, source_url=args.source_url,
                apply_url=args.apply_url, jd_file=args.jd_file,
                source_status=args.source_status,
            )
        elif args.command == "preflight":
            result = preflight(args.store, args.job_id, args.resume, args.pre_submit)
        else:
            result = record_outcome(
                args.store, args.job_id, args.resume, args.pre_submit,
                args.outcome, args.evidence,
            )
    except GuardError as error:
        print(f"Blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

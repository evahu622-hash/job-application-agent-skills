"""Behavior tests for the local state guard; no browser or real application."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from assistant import (
    GuardError, SourceClosed, check_api_url, check_quotes, fetch_ats, html_to_text, http_get,
    list_ats, main, make_job_id, parse_ats_posting, parse_ats_source, preflight, read_jobs,
    recency, record_outcome, sha256_file, unescape_greenhouse, upsert_job, write_jobs,
)


class AssistantTest(unittest.TestCase):
    CHECKED_AT = datetime(2026, 9, 29, 2, tzinfo=timezone.utc)

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.store = self.root / "jobs.csv"
        self.jd = self.root / "jd.txt"
        self.jd.write_text("# Original JD\nProduct manager role", encoding="utf-8")
        self.resume = self.root / "resume.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nsmall test fixture")
        self.pre = self.root / "pre-submit.json"
        self.row = self.add_job()
        self.write_pre()

    def add_job(self, job_id="site-123", source_url="https://example.com/jobs/123", at="2026-09-29T01:00:00Z", source_status="active_verified"):
        return upsert_job(
            self.store, job_id=job_id, company="Example", title="Product Manager",
            location="Remote", source_url=source_url,
            apply_url="https://example.com/jobs/123/apply", jd_file=self.jd,
            observed_at=at, source_status=source_status,
        )

    def write_pre(self, **changes):
        payload = {
            "job_id": "site-123", "jd_sha256": self.row["jd_sha256"],
            "resume_sha256": sha256_file(self.resume), "mode": "review",
            "fields_verified": True, "attachment_verified": True,
            "unknown_required_fields": [],
        }
        payload.update(changes)
        self.pre.write_text(json.dumps(payload), encoding="utf-8")

    def check(self):
        return preflight(self.store, "site-123", self.resume, self.pre,
                         checked_at=self.CHECKED_AT)

    def test_repeat_discovery_preserves_first_seen_and_status(self):
        first = self.row["first_seen_at"]
        self.jd.write_text("# Original JD\nUpdated requirements", encoding="utf-8")
        updated = self.add_job(at="2026-09-30T01:00:00Z")
        self.assertEqual(1, len(read_jobs(self.store)))
        self.assertEqual(first, updated["first_seen_at"])
        self.assertEqual("2026-09-30T01:00:00Z", updated["last_seen_at"])
        self.assertNotEqual(self.row["jd_sha256"], updated["jd_sha256"])

    def test_identity_collision_is_blocked(self):
        with self.assertRaisesRegex(GuardError, "another job_id"):
            self.add_job(job_id="site-999")
        with self.assertRaisesRegex(GuardError, "different source URL"):
            self.add_job(source_url="https://example.com/jobs/999")
        self.assertEqual(1, len(read_jobs(self.store)))

    def test_preflight_requires_exact_resume_and_verified_fields(self):
        self.assertEqual("ready_for_review", self.check()["status"])
        self.write_pre(fields_verified="true")
        with self.assertRaisesRegex(GuardError, "fields_verified"):
            self.check()
        self.write_pre(unknown_required_fields=["work authorization"])
        with self.assertRaisesRegex(GuardError, "unknown_required_fields"):
            self.check()
        self.write_pre(resume_sha256="0" * 64)
        with self.assertRaisesRegex(GuardError, "resume_sha256"):
            self.check()
        self.write_pre()
        self.resume.write_bytes(b"not a pdf")
        with self.assertRaisesRegex(GuardError, "PDF header"):
            self.check()

    def test_closed_or_stale_original_source_blocks_preflight(self):
        self.add_job(source_status="closed")
        with self.assertRaisesRegex(GuardError, "not active_verified"):
            self.check()
        self.add_job(at="2026-09-25T01:00:00Z")
        with self.assertRaisesRegex(GuardError, "stale"):
            self.check()

    def test_dry_run_never_passes_preflight(self):
        self.write_pre(dry_run=True)
        with self.assertRaisesRegex(GuardError, "Dry-run"):
            self.check()

    def test_changed_jd_invalidates_previous_preflight(self):
        self.jd.write_text("# Original JD\nChanged after form review", encoding="utf-8")
        self.add_job(at="2026-09-29T01:30:00Z")
        with self.assertRaisesRegex(GuardError, "jd_sha256"):
            self.check()

    def test_unknown_outcome_blocks_retry_but_can_be_resolved_with_receipt(self):
        result = record_outcome(
            self.store, "site-123", self.resume, self.pre,
            "unknown", "Clicked once; page timed out", observed_at="2026-09-29T02:00:00Z",
            checked_at=self.CHECKED_AT,
        )
        self.assertEqual("submission_unknown", result["status"])
        with self.assertRaisesRegex(GuardError, "blocks submission"):
            self.check()
        with self.assertRaisesRegex(GuardError, "cannot be retried"):
            record_outcome(self.store, "site-123", self.resume, self.pre, "unknown", "Try again")
        confirmed = record_outcome(
            self.store, "site-123", self.resume, self.pre,
            "confirmed", "Found existing application receipt #ABC",
        )
        self.assertEqual("submitted_confirmed", confirmed["status"])
        with self.assertRaisesRegex(GuardError, "already confirmed"):
            record_outcome(self.store, "site-123", self.resume, self.pre, "confirmed", "second receipt")

    def test_confirmed_requires_evidence(self):
        with self.assertRaisesRegex(GuardError, "Describe"):
            record_outcome(self.store, "site-123", self.resume, self.pre, "confirmed", "  ")
        self.assertFalse((self.root / "jobs" / "site-123" / "application.json").exists())

    def write_manifest(self, status="approved", digest=None, path=None):
        path = path or self.root / "private" / "resume_variants" / "build" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        variant = {"id": "general", "status": status, "pdf_sha256": digest or sha256_file(self.resume)}
        path.write_text(json.dumps({"schema_version": 1, "variants": [variant]}), encoding="utf-8")
        return path

    def test_preflight_requires_approved_variant_when_manifest_exists(self):
        self.write_manifest()  # auto-detected next to the store
        self.assertEqual("ready_for_review", self.check()["status"])
        self.write_manifest(status="draft")
        with self.assertRaisesRegex(GuardError, "not an approved resume variant"):
            self.check()
        self.write_manifest(digest="0" * 64)
        with self.assertRaisesRegex(GuardError, "not an approved resume variant"):
            self.check()

    def test_preflight_explicit_variants_path_and_malformed_manifest(self):
        custom = self.write_manifest(path=self.root / "private" / "custom" / "manifest.json")
        result = preflight(self.store, "site-123", self.resume, self.pre,
                           checked_at=self.CHECKED_AT, variants=custom)
        self.assertEqual("ready_for_review", result["status"])
        with self.assertRaisesRegex(GuardError, "File not found"):
            preflight(self.store, "site-123", self.resume, self.pre,
                      checked_at=self.CHECKED_AT, variants=self.root / "private" / "missing.json")
        custom.write_text(json.dumps({"schema_version": 1, "variants": "general"}), encoding="utf-8")
        with self.assertRaisesRegex(GuardError, "Malformed"):
            preflight(self.store, "site-123", self.resume, self.pre,
                      checked_at=self.CHECKED_AT, variants=custom)

    def test_hand_written_manifest_cannot_replace_the_default(self):
        self.write_manifest(digest="0" * 64)  # the real manifest approves another PDF
        forged = self.write_manifest(path=self.root / "jobs" / "x" / "m.json")
        with self.assertRaisesRegex(GuardError, "under private/"):
            preflight(self.store, "site-123", self.resume, self.pre,
                      checked_at=self.CHECKED_AT, variants=forged)
        inside = self.write_manifest(path=self.root / "private" / "other" / "manifest.json")
        with self.assertRaisesRegex(GuardError, "not an approved resume variant"):
            preflight(self.store, "site-123", self.resume, self.pre,
                      checked_at=self.CHECKED_AT, variants=inside)

    def test_same_posting_under_another_url_is_not_a_new_job(self):
        self.jd.write_text("JD", encoding="utf-8")
        upsert_job(self.store, job_id="acme-csm", company="Acme", title="CSM", location="Berlin",
                   source_url="https://boards.greenhouse.io/acme/jobs/123?gh_src=abc",
                   apply_url="https://boards.greenhouse.io/acme/jobs/123", jd_file=self.jd,
                   observed_at="2026-09-29T01:00:00Z", source_status="active_verified")
        variants = {
            "https://job-boards.greenhouse.io/acme/jobs/123": "https://careers.acme.example/x",
            "https://careers.acme.example/positions/123": "https://careers.acme.example/positions/123?gh_jid=123",
        }
        for source_url, apply_url in variants.items():
            with self.assertRaisesRegex(GuardError, "already tracked under another job_id: acme-csm"):
                upsert_job(self.store, job_id="greenhouse-acme-123", company="Acme", title="CSM",
                           location="Berlin", source_url=source_url, apply_url=apply_url, jd_file=self.jd)
        with self.assertRaisesRegex(GuardError, "another job_id: site-123"):
            self.add_job(job_id="site-999", source_url="https://www.example.com/jobs/123/?utm_source=x")
        refreshed = self.add_job(source_url="https://example.com/jobs/123/")
        self.assertEqual("https://example.com/jobs/123/", refreshed["source_url"])
        self.add_job(job_id="site-456", source_url="https://example.com/jobs/123?page=2")  # a real query is kept

    def test_preflight_refuses_a_posting_already_submitted_under_another_id(self):
        rows = read_jobs(self.store)
        rows.append({**rows[0], "job_id": "old-v1-id", "source_url": "https://example.com/jobs/123?ref=mail",
                     "application_status": "submitted_confirmed"})
        write_jobs(self.store, rows)
        with self.assertRaisesRegex(GuardError, "submitted_confirmed as job_id old-v1-id"):
            self.check()


def fake_fetch(responses):
    """Offline stand-in for http_get: url -> bytes, or an exception to raise."""
    calls = []

    def fetch(url):
        calls.append(url)
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        return value

    fetch.calls = calls
    return fetch


def as_bytes(value):
    return json.dumps(value).encode("utf-8")


GH_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/exampleco/jobs"
GH_JOB_URL = GH_LIST_URL + "/101"
GH_POSTING = {
    "id": 101, "title": " Product Manager ", "company_name": "Example GmbH",
    "location": {"name": "Berlin"}, "absolute_url": "https://careers.example.com/jobs?gh_jid=101",
    "first_published": "2026-09-28T08:00:00-04:00", "updated_at": "2026-10-02T04:45:05-04:00",
    "metadata": [{"id": 1, "name": "Employment Type", "value": "Full-time", "value_type": "single_select"},
                 {"id": 2, "name": "Workplace Type", "value": ["Hybrid"], "value_type": "multi_select"}],
}
GH_CONTENT = ("&lt;p&gt;Build &amp;amp; ship.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Fluent German required"
              "&lt;/li&gt;&lt;li&gt;SQL is a plus&lt;/li&gt;&lt;/ul&gt;")
GH_JD = "Build & ship.\n\n- Fluent German required\n- SQL is a plus\n"
GH_QUESTIONS = {
    "questions": [
        {"label": "Email", "required": True, "description": None,
         "fields": [{"name": "email", "type": "input_text", "values": []}]},
        {"label": "Work permit?", "required": True, "description": "&lt;p&gt;EU only&lt;/p&gt;",
         "fields": [{"name": "question_1", "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
    ],
    "location_questions": [],
    "compliance": [{"type": "eeoc", "questions": [
        {"label": "Gender", "required": False,
         "fields": [{"name": "gender", "type": "multi_value_single_select",
                     "values": [{"label": "Decline to self-identify", "value": 3}]}]}]}],
    "demographic_questions": None,
}
LEVER_ID = "aaaaaaaa-1111-2222-3333-444444444444"
LEVER_POSTING = {
    "id": LEVER_ID, "text": "Data Analyst", "createdAt": 1790000000000, "workplaceType": "hybrid",
    "categories": {"commitment": "Full-time", "location": "Berlin", "allLocations": ["Berlin", "Remote - Germany"]},
    "salaryRange": {"min": 60000, "max": 80000, "currency": "EUR", "interval": "per-year-salary"},
    "description": "<div>Intro text here.</div>",
    "lists": [{"text": "Requirements", "content": "<li>SQL</li><li>German C1</li>"}],
    "additional": "<div>Benefits apply.</div>",
    "hostedUrl": f"https://jobs.lever.co/exampleco/{LEVER_ID}",
    "applyUrl": f"https://jobs.lever.co/exampleco/{LEVER_ID}/apply",
}
ASHBY_ID = "bbbbbbbb-1111-2222-3333-444444444444"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/exampleco?includeCompensation=true"
ASHBY_BOARD = {"apiVersion": "1", "jobs": [{
    "id": ASHBY_ID, "title": "Support Lead", "location": "Remote - EU",
    "secondaryLocations": [{"location": "Spain"}], "publishedAt": "2026-09-30T10:00:00.123+00:00",
    "employmentType": "FullTime", "workplaceType": "Remote",
    "applyUrl": f"https://jobs.ashbyhq.com/exampleco/{ASHBY_ID}/application",
    "descriptionHtml": "<p>Help customers.</p><ul><li><p>Zendesk</p></li><li><p>English</p></li></ul>",
    "compensation": {"compensationTierSummary": "€50K – €60K"},
}]}
PERSONIO_URL = "https://exampleco.jobs.personio.de/xml?language=en"
PERSONIO_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs><position><id>123</id><office>Munich</office><name>Ops Manager</name>
<jobDescriptions>
<jobDescription><name>Your tasks</name><value><![CDATA[<ul><li>Run P&amp;L reviews</li></ul>]]></value></jobDescription>
<jobDescription><name>Your profile</name><value><![CDATA[Fluent English<br>Some German]]></value></jobDescription>
</jobDescriptions><employmentType>permanent</employmentType><schedule>full-time</schedule>
<createdAt>2026-09-01T09:00:00+00:00</createdAt></position></workzag-jobs>"""


class AtsTest(unittest.TestCase):
    NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.store = self.root / "jobs.csv"
        self.gh_dir = self.root / "jobs" / "greenhouse-exampleco-101"

    def fetch_greenhouse(self, content=GH_CONTENT, now=None, **options):
        fetch = fake_fetch({
            GH_JOB_URL: as_bytes({**GH_POSTING, "content": content}),
            GH_JOB_URL + "?questions=true": as_bytes({**GH_POSTING, "content": content, **GH_QUESTIONS}),
        })
        return fetch_ats("https://job-boards.greenhouse.io/exampleco/jobs/101?gh_src=x", self.gh_dir,
                         fetch=fetch, now=now or self.NOW, **options)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--store", str(self.store), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_board_and_posting_urls_parse_for_every_supported_host(self):
        boards = {
            "https://job-boards.greenhouse.io/exampleco": GH_LIST_URL,
            "https://boards.greenhouse.io/ExampleCo/": GH_LIST_URL,
            "https://job-boards.eu.greenhouse.io/exampleco?gh_src=x": GH_LIST_URL,  # one API serves EU boards
            "https://boards.eu.greenhouse.io/exampleco": GH_LIST_URL,
            "https://boards.greenhouse.io/embed/job_board?for=ExampleCo": GH_LIST_URL,
            "greenhouse:exampleco": GH_LIST_URL,
            "https://jobs.lever.co/exampleco": "https://api.lever.co/v0/postings/exampleco?mode=json",
            "https://jobs.eu.lever.co/exampleco/?team=x": "https://api.eu.lever.co/v0/postings/exampleco?mode=json",
            "lever:exampleco": "https://api.lever.co/v0/postings/exampleco?mode=json",
            "https://jobs.ashbyhq.com/exampleco": ASHBY_URL,
            "ashby:exampleco": ASHBY_URL,
            "https://exampleco.jobs.personio.de": PERSONIO_URL,
            "https://exampleco.jobs.personio.com/?language=de": PERSONIO_URL,
            "personio:exampleco": PERSONIO_URL,
        }
        for url, api in boards.items():
            self.assertEqual(api, parse_ats_source(url).list_url(), url)
        postings = {
            "https://job-boards.greenhouse.io/exampleco/jobs/101?gh_jid=101": ("greenhouse", "101"),
            "https://boards.greenhouse.io/exampleco/jobs/101/": ("greenhouse", "101"),
            "https://job-boards.eu.greenhouse.io/exampleco/jobs/101": ("greenhouse", "101"),
            "greenhouse:exampleco:101": ("greenhouse", "101"),
            "https://boards.greenhouse.io/embed/job_app?for=exampleco&token=101": ("greenhouse", "101"),
            "https://boards.eu.greenhouse.io/exampleco/jobs/101?gh_jid=101": ("greenhouse", "101"),
            f"https://jobs.lever.co/exampleco/{LEVER_ID}/apply": ("lever", LEVER_ID),
            f"https://jobs.eu.lever.co/exampleco/{LEVER_ID}": ("lever", LEVER_ID),
            f"https://jobs.ashbyhq.com/exampleco/{ASHBY_ID}/application": ("ashby", ASHBY_ID),
            "https://exampleco.jobs.personio.de/job/123?language=en&display=en": ("personio", "123"),
        }
        for url, (ats, job) in postings.items():
            board, ats_job_id = parse_ats_posting(url)
            self.assertEqual((ats, "exampleco", job), (board.ats, board.name, ats_job_id), url)
        eu, _ = parse_ats_posting("https://job-boards.eu.greenhouse.io/exampleco/jobs/101")
        self.assertEqual("https://job-boards.eu.greenhouse.io/exampleco/jobs/101", eu.page_url("101"))
        de, _ = parse_ats_posting("personio:exampleco:123")
        com, _ = parse_ats_posting("https://exampleco.jobs.personio.com/job/123?language=en&display=en")
        self.assertEqual(de.page_url("123"), com.page_url("123"))
        self.assertEqual("https://exampleco.jobs.personio.de/job/123", com.page_url("123"))
        with self.assertRaisesRegex(GuardError, "greenhouse:BOARD:4242"):
            parse_ats_posting("https://careers.example.com/jobs?gh_jid=4242")
        for bad in ("https://example.com/careers", "http://jobs.lever.co/exampleco",
                    "https://jobs.lever.co/", "workday:exampleco"):
            with self.assertRaisesRegex(GuardError, "Unsupported ATS URL"):
                parse_ats_source(bad)
        with self.assertRaisesRegex(GuardError, "Unsupported ATS URL"):
            parse_ats_source(f"https://jobs.lever.co/exampleco/{LEVER_ID}")
        with self.assertRaisesRegex(GuardError, "Unsupported ATS URL"):
            parse_ats_posting("https://job-boards.greenhouse.io/exampleco")
        self.assertEqual("ashby-example-co-x1", make_job_id("ashby", "Example Co", "X1"))

    def test_long_board_slug_still_gets_a_stable_job_id(self):
        board = "a" * 60
        job_id = make_job_id("ashby", board, ASHBY_ID)
        self.assertLessEqual(len(job_id), 80)
        self.assertTrue(job_id.startswith("ashby-aaa") and job_id.endswith(ASHBY_ID), job_id)
        self.assertEqual(job_id, make_job_id("ashby", board, ASHBY_ID))
        self.assertNotEqual(job_id, make_job_id("ashby", "a" * 59 + "b", ASHBY_ID))
        payload = as_bytes({"jobs": [{**ASHBY_BOARD["jobs"][0]}]})
        [posting] = list_ats("ashby:" + board, fetch=lambda url: payload)
        self.assertEqual(job_id, posting["job_id"])
        with self.assertRaisesRegex(GuardError, "Cannot derive a valid job_id"):
            make_job_id("lever", "x", "9" * 90)

    def test_only_https_to_fixed_api_hosts(self):
        for url in ("http://api.lever.co/v0/postings/x", "https://evil.example/v1/boards/x/jobs",
                    "https://x.jobs.personio.de.evil.example/xml"):
            with self.assertRaisesRegex(GuardError, "fixed ATS API hosts"):
                http_get(url)
        check_api_url(PERSONIO_URL)
        check_api_url(ASHBY_URL)

    def test_list_ats_normalizes_each_ats(self):
        fetch = fake_fetch({
            GH_LIST_URL: as_bytes({"jobs": [GH_POSTING], "meta": {"total": 1}}),
            "https://api.lever.co/v0/postings/exampleco?mode=json": as_bytes([LEVER_POSTING]),
            ASHBY_URL: as_bytes(ASHBY_BOARD),
            PERSONIO_URL: PERSONIO_XML,
        })
        [gh] = list_ats("https://job-boards.greenhouse.io/exampleco", recency_days=7, fetch=fetch, now=self.NOW)
        self.assertEqual({
            "ats": "greenhouse", "board": "exampleco", "ats_job_id": "101",
            "job_id": "greenhouse-exampleco-101", "title": "Product Manager", "location": "Berlin",
            "posted_at": "2026-09-28T12:00:00Z", "posted_at_field": "first_published",
            "posted_at_meaning": "first published", "updated_at": "2026-10-02T08:45:05Z",
            "employment_type": "Full-time", "workplace_type": "Hybrid", "compensation": None,
            "url": "https://job-boards.greenhouse.io/exampleco/jobs/101",
            "recency": {"days": 7, "age_days": 7.0, "status": "met"},
        }, gh)
        [lever] = list_ats("lever:exampleco", fetch=fetch)
        self.assertEqual(f"lever-exampleco-{LEVER_ID}", lever["job_id"])
        self.assertEqual(("2026-09-21T14:13:20Z", "createdAt", "created"),
                         (lever["posted_at"], lever["posted_at_field"], lever["posted_at_meaning"]))
        self.assertEqual("Berlin; Remote - Germany", lever["location"])
        self.assertEqual(("Full-time", "hybrid", "60000-80000 EUR per-year-salary"),
                         (lever["employment_type"], lever["workplace_type"], lever["compensation"]))
        self.assertNotIn("recency", lever)
        [ashby] = list_ats("https://jobs.ashbyhq.com/exampleco", recency_days=3, fetch=fetch, now=self.NOW)
        self.assertEqual(("2026-09-30T10:00:00Z", "last published; may reflect a repost"),
                         (ashby["posted_at"], ashby["posted_at_meaning"]))
        self.assertEqual(("Remote - EU; Spain", "FullTime", "€50K – €60K"),
                         (ashby["location"], ashby["employment_type"], ashby["compensation"]))
        self.assertEqual("unmet", ashby["recency"]["status"])
        [personio] = list_ats("https://exampleco.jobs.personio.de/", fetch=fetch)
        self.assertEqual(("personio-exampleco-123", "Ops Manager", "Munich", "permanent / full-time"),
                         (personio["job_id"], personio["title"], personio["location"], personio["employment_type"]))
        self.assertEqual("https://exampleco.jobs.personio.de/job/123", personio["url"])
        missing = fake_fetch({GH_LIST_URL: SourceClosed("HTTP 404")})
        with self.assertRaisesRegex(GuardError, "board not found"):
            list_ats("greenhouse:exampleco", fetch=missing)

    def test_recency_boundary_and_unknown_date(self):
        posted = "2026-10-01T00:00:00Z"
        edge = datetime(2026, 10, 8, tzinfo=timezone.utc)
        self.assertEqual({"days": 7, "age_days": 7.0, "status": "met"}, recency(posted, 7, edge))
        self.assertEqual({"days": 7, "age_days": 7.1, "status": "unmet"},
                         recency(posted, 7, edge + timedelta(seconds=1)))  # shown age agrees with status
        self.assertEqual(7.1, recency("2026-10-01T11:00:00Z", 7, datetime(2026, 10, 8, 12, tzinfo=timezone.utc))["age_days"])
        self.assertEqual(6.5, recency(posted, 7, edge - timedelta(hours=12))["age_days"])
        self.assertEqual({"days": 7, "age_days": None, "status": "unknown"}, recency(None, 7, edge))
        with self.assertRaises(GuardError):
            recency(posted, -1, edge)

    def test_html_to_text_is_deterministic(self):
        markup = ("<p>Hello&nbsp;  world</p><script>x()</script><style>p{}</style>"
                  "<ul><li><p>First</p></li><li>Second<br/>line</li></ul><h2>Next</h2>Café")
        expected = "Hello world\n\n- First\n- Second\nline\n\nNext\n\nCafé\n"
        self.assertEqual(expected, html_to_text(markup))
        escaped = markup.replace("&", "&amp;").replace("<", "&lt;")  # how Greenhouse sends content
        self.assertEqual(expected, html_to_text(unescape_greenhouse(escaped)))
        self.assertEqual(html_to_text(markup).encode(), html_to_text(markup).encode())
        self.assertEqual(GH_JD, html_to_text(unescape_greenhouse(GH_CONTENT)))
        self.assertEqual("Line one\n\nLine two\n", html_to_text("Line one\n\n\n  Line \t two\t\n"))

    def test_escaped_markup_in_a_jd_stays_text(self):
        jd = html_to_text("<p>You will teach customers how the &lt;style&gt; tag works.</p><h3>Requirements</h3>"
                          "<ul><li>Fluent German</li><li>Work permit for the EU</li></ul>")
        self.assertEqual("You will teach customers how the <style> tag works.\n\nRequirements\n\n"
                         "- Fluent German\n- Work permit for the EU\n", jd)
        greenhouse = unescape_greenhouse("&lt;p&gt;Explain the &amp;lt;template&amp;gt; element&lt;/p&gt;")
        self.assertEqual("Explain the <template> element\n", html_to_text(greenhouse))
        self.assertEqual("R&D <b>\n", html_to_text("R&amp;D &lt;b&gt;"))
        lever = {**LEVER_POSTING, "lists": [{"text": "Requirements <required> & nice", "content": "<li>SQL</li>"}]}
        fetch = fake_fetch({f"https://api.lever.co/v0/postings/exampleco/{LEVER_ID}?mode=json": as_bytes(lever)})
        job_dir = self.root / "jobs" / f"lever-exampleco-{LEVER_ID}"
        fetch_ats(f"lever:exampleco:{LEVER_ID}", job_dir, fetch=fetch, now=self.NOW)
        self.assertIn("Requirements <required> & nice\n", (job_dir / "jd.txt").read_text(encoding="utf-8"))

    def test_fetch_greenhouse_writes_canonical_jd_source_and_form(self):
        source = self.fetch_greenhouse(questions=True, recency_days=7)
        jd = self.gh_dir / "jd.txt"
        self.assertEqual(GH_JD, jd.read_text(encoding="utf-8"))
        saved = json.loads((self.gh_dir / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source, saved)
        self.assertEqual({
            "schema_version": 1, "job_id": "greenhouse-exampleco-101", "title": "Product Manager",
            "company": "Example GmbH", "source_url": "https://job-boards.greenhouse.io/exampleco/jobs/101",
            "apply_url": "https://careers.example.com/jobs?gh_jid=101", "posted_at": "2026-09-28T12:00:00Z",
            "employment_type": "Full-time", "fetched_at": "2026-10-05T12:00:00Z",
            "api_url": GH_JOB_URL + "?questions=true", "extraction": "ats-api/greenhouse/v1",
            "jd_sha256": sha256_file(jd), "source_status": "active_verified",
            "recency": {"days": 7, "age_days": 7.0, "status": "met"},
        }, {key: saved[key] for key in (
            "schema_version", "job_id", "title", "company", "source_url", "apply_url", "posted_at",
            "employment_type", "fetched_at", "api_url", "extraction", "jd_sha256", "source_status", "recency",
        )})
        form = json.loads((self.gh_dir / "form.json").read_text(encoding="utf-8"))
        self.assertEqual(["Email", "Work permit?", "Gender"], [q["label"] for q in form["questions"]])
        self.assertEqual(["application", "application", "compliance"], [q["section"] for q in form["questions"]])
        permit = form["questions"][1]
        self.assertEqual((True, "EU only"), (permit["required"], permit["description"]))
        self.assertEqual([{"name": "question_1", "type": "multi_value_single_select", "options": ["Yes", "No"]}],
                         permit["fields"])

    def test_jd_rewritten_only_when_content_changes(self):
        self.fetch_greenhouse()
        jd = self.gh_dir / "jd.txt"
        os.utime(jd, (1_000_000, 1_000_000))
        later = self.fetch_greenhouse(now=self.NOW + timedelta(hours=1))
        self.assertEqual(1_000_000, jd.stat().st_mtime)
        self.assertEqual("2026-10-05T13:00:00Z", later["fetched_at"])
        changed = self.fetch_greenhouse(content=GH_CONTENT.replace("SQL", "Python"))
        self.assertNotEqual(1_000_000, jd.stat().st_mtime)
        self.assertNotEqual(later["jd_sha256"], changed["jd_sha256"])
        self.assertFalse((self.gh_dir / "form.json").exists())

    def test_fetch_lever_ashby_personio_compose_canonical_text(self):
        fetch = fake_fetch({
            f"https://api.lever.co/v0/postings/exampleco/{LEVER_ID}?mode=json": as_bytes(LEVER_POSTING),
            ASHBY_URL: as_bytes(ASHBY_BOARD),
            PERSONIO_URL: PERSONIO_XML,
        })
        cases = {
            f"https://jobs.lever.co/exampleco/{LEVER_ID}":
                "Intro text here.\n\nRequirements\n\n- SQL\n- German C1\nBenefits apply.\n",
            f"https://jobs.ashbyhq.com/exampleco/{ASHBY_ID}": "Help customers.\n\n- Zendesk\n- English\n",
            "https://exampleco.jobs.personio.de/job/123":
                "Your tasks\n\n- Run P&L reviews\n\nYour profile\n\nFluent English\nSome German\n",
        }
        for url, expected in cases.items():
            board, ats_job_id = parse_ats_posting(url)
            job_dir = self.root / "jobs" / make_job_id(board.ats, board.name, ats_job_id)
            source = fetch_ats(url, job_dir, fetch=fetch, now=self.NOW)
            self.assertEqual(expected, (job_dir / "jd.txt").read_text(encoding="utf-8"), url)
            self.assertEqual("active_verified", source["source_status"])
            self.assertTrue(source["apply_url"].startswith("https://"), url)
        self.assertEqual("https://exampleco.jobs.personio.de/job/123", source["apply_url"])
        self.assertEqual(("exampleco", "created"), (source["company"], source["posted_at_meaning"]))

    def test_personio_posting_without_english_text_uses_its_own_language(self):
        english = PERSONIO_XML.split(b"<jobDescriptions>")[0] + b"<jobDescriptions></jobDescriptions>" \
            + PERSONIO_XML.split(b"</jobDescriptions>")[1]
        german = PERSONIO_XML.replace(b"Your tasks", b"Deine Aufgaben")
        fallback = "https://exampleco.jobs.personio.de/xml"
        fetch = fake_fetch({PERSONIO_URL: english, fallback: german})
        job_dir = self.root / "jobs" / "personio-exampleco-123"
        source = fetch_ats("https://exampleco.jobs.personio.com/job/123", job_dir, fetch=fetch, now=self.NOW)
        self.assertEqual((fallback, "active_verified"), (source["api_url"], source["source_status"]))
        self.assertTrue((job_dir / "jd.txt").read_text(encoding="utf-8").startswith("Deine Aufgaben\n"))
        self.assertEqual("https://exampleco.jobs.personio.de/job/123", source["source_url"])
        empty = fake_fetch({PERSONIO_URL: english, fallback: english})
        with self.assertRaisesRegex(GuardError, "no JD text"):
            fetch_ats("personio:exampleco:123", job_dir, fetch=empty, now=self.NOW)

    def test_closed_posting_reports_closed_and_keeps_jd(self):
        self.fetch_greenhouse()
        jd_bytes = (self.gh_dir / "jd.txt").read_bytes()
        closed = fake_fetch({GH_JOB_URL: SourceClosed("HTTP 404")})
        with mock.patch("assistant.http_get", closed):
            code, out, _ = self.run_cli("fetch-ats", "greenhouse:exampleco:101",
                                        "--job-dir", str(self.gh_dir), "--recency-days", "7")
        self.assertEqual(0, code)
        printed = json.loads(out)
        self.assertEqual(("closed", "HTTP 404", "Product Manager"),
                         (printed["source_status"], printed["closed_reason"], printed["title"]))
        self.assertNotIn("recency", printed)
        self.assertEqual(jd_bytes, (self.gh_dir / "jd.txt").read_bytes())
        self.assertEqual(printed, json.loads((self.gh_dir / "source.json").read_text(encoding="utf-8")))
        gone = fake_fetch({PERSONIO_URL: PERSONIO_XML.replace(b"<id>123</id>", b"<id>999</id>")})
        personio_dir = self.root / "jobs" / "personio-exampleco-123"
        result = fetch_ats("personio:exampleco:123", personio_dir, fetch=gone, now=self.NOW)
        self.assertEqual(("closed", "absent from the board listing"),
                         (result["source_status"], result["closed_reason"]))
        self.assertFalse((personio_dir / "jd.txt").exists())

    def test_network_error_blocks_without_writing(self):
        failing = fake_fetch({GH_JOB_URL: GuardError("ATS API answered HTTP 503")})
        with mock.patch("assistant.http_get", failing):
            code, out, err = self.run_cli("fetch-ats", "greenhouse:exampleco:101", "--job-dir", str(self.gh_dir))
        self.assertEqual((2, ""), (code, out))
        self.assertIn("Blocked: ATS API answered HTTP 503", err)
        self.assertFalse(self.gh_dir.exists())

    def test_job_dir_must_be_named_after_the_job_id(self):
        self.fetch_greenhouse()
        with self.assertRaisesRegex(GuardError, "--job-dir must be jobs/greenhouse-exampleco-202"):
            fetch_ats("greenhouse:exampleco:202", self.gh_dir, fetch=fake_fetch({}), now=self.NOW)
        with self.assertRaisesRegex(GuardError, "--job-dir must be jobs/greenhouse-exampleco-101"):
            fetch_ats("greenhouse:exampleco:101", self.root / "jobs" / "anthropic-admin",
                      fetch=fake_fetch({}), now=self.NOW)

    def test_refresh_without_flag_keeps_the_recency_window(self):
        self.fetch_greenhouse(recency_days=7)
        later = self.fetch_greenhouse(questions=True, now=self.NOW + timedelta(days=1))
        self.assertEqual({"days": 7, "age_days": 8.0, "status": "unmet"}, later["recency"])

    def test_upsert_job_from_source_json(self):
        source = self.fetch_greenhouse()
        source_json = str(self.gh_dir / "source.json")
        code, out, _ = self.run_cli("upsert-job", "--source-json", source_json)
        self.assertEqual(0, code)
        [row] = read_jobs(self.store)
        self.assertEqual(json.loads(out), row)
        self.assertEqual(
            ("greenhouse-exampleco-101", "Example GmbH", "active_verified", source["fetched_at"], source["jd_sha256"]),
            (row["job_id"], row["company"], row["source_status"], row["source_verified_at"], row["jd_sha256"]),
        )
        code, _, _ = self.run_cli("upsert-job", "--source-json", source_json, "--source-status", "unknown",
                                  "--observed-at", "2026-10-06T00:00:00Z")
        [row] = read_jobs(self.store)
        self.assertEqual((0, "unknown", "2026-10-06T00:00:00Z"), (code, row["source_status"], row["last_seen_at"]))
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()):
            main(["--store", str(self.store), "upsert-job", "--job-id", "x"])
        self.assertEqual(2, stopped.exception.code)

    def test_check_quotes_requires_verbatim_jd_text(self):
        self.fetch_greenhouse()
        fit = self.gh_dir / "fit.md"
        fit.write_text(
            '- Language: "Fluent German required" (met)\n'
            "- Bonus: “SQL is a plus” and - \"fluent   GERMAN required\"\n"
            '- Omission: "Build & ship. ... SQL is a plus"\n'
            '- Short quotes are ignored: "SQL"\n'
            '- A quote may wrap: "Fluent German\n  required"\n\n'
            "- 结论：属于“行政支持类而非产品类的岗位方向判断”。\n",
            encoding="utf-8",
        )
        result = check_quotes(self.store, "greenhouse-exampleco-101")
        self.assertEqual({"job_id": "greenhouse-exampleco-101", "checked": 5, "missing": [],
                          "skipped": ["行政支持类而非产品类的岗位方向判断"]}, result)
        notes = self.gh_dir / "notes.md"
        notes.write_text('Copied from another job: "Five years of Kubernetes experience"\n', encoding="utf-8")
        code, out, err = self.run_cli("check-quotes", "greenhouse-exampleco-101", "--file", "fit.md", "notes.md")
        self.assertEqual(2, code)
        self.assertEqual(["Five years of Kubernetes experience"], json.loads(out)["missing"])
        self.assertIn("Blocked: quotes not found", err)
        with self.assertRaisesRegex(GuardError, "file name"):
            check_quotes(self.store, "greenhouse-exampleco-101", ["../other/fit.md"])

    def test_check_quotes_cannot_be_bypassed(self):
        self.fetch_greenhouse()
        bad = {
            '"SQL ... required"': "short ellipsis parts",
            '"SQL is a plus ... Fluent German required"': "parts out of order",
            '"Ten years of enterprise SaaS leadership\n   experience in a regulated industry"': "wrapped",
            '`"German is mandatory for this role"`': "inside code",
            "「Fluent German language skills (required)」": "corner brackets",
            '"Salesforce CPQ is mandatory" and a stray inch mark 5"': "unpaired",
        }
        for text, why in bad.items():
            with self.subTest(why):
                (self.gh_dir / "fit.md").write_text(f"- {text}\n", encoding="utf-8")
                self.assertTrue(check_quotes(self.store, "greenhouse-exampleco-101")["missing"], why)
        (self.gh_dir / "fit.md").write_text("No quotes at all.\n", encoding="utf-8")
        code, out, err = self.run_cli("check-quotes", "greenhouse-exampleco-101")
        self.assertEqual((0, 0), (code, json.loads(out)["checked"]))
        self.assertIn("no JD quote was checked", err)


if __name__ == "__main__":
    unittest.main()

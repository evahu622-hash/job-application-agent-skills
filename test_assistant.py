"""Behavior tests for the local state guard; no browser or real application."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from assistant import GuardError, preflight, read_jobs, record_outcome, sha256_file, upsert_job


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


if __name__ == "__main__":
    unittest.main()

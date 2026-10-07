"""Offline tests for the local HTML dashboard; fixtures are fictional."""

import io
import json
import re
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

import dashboard
from dashboard import build_dashboard, md_to_html, parse_codex_log, summarize_test_log


FIELDS = (
    "job_id,company,title,location,source_url,apply_url,first_seen_at,last_seen_at,"
    "jd_sha256,source_status,source_verified_at,application_status"
)
JD_TEXT = "Example role\n- Own the roadmap\n</template><script>alert(2)</script>\n"
PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


def jsonl(path: Path, records: list) -> None:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


EXEC_EVENTS = [
    {"type": "thread.started", "thread_id": "thread-123"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "thinking"}},
    {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
     "command": "/bin/zsh -lc 'python3 assistant.py list-ats greenhouse:example'",
     "aggregated_output": "[]\n", "exit_code": 0, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "i2", "type": "command_execution", "command": "/bin/zsh -lc 'open https://example.com'",
     "aggregated_output": "Permission denied <b>x</b>", "exit_code": 1, "status": "failed"}},
    {"type": "item.started", "item": {"id": "i9", "type": "command_execution", "command": "sleep 100",
     "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "i3", "type": "file_change",
     "changes": [{"path": "jobs/example-1/fit.md", "kind": "add"}], "status": "completed"}},
    {"type": "item.completed", "item": {"id": "i4", "type": "mcp_tool_call", "server": "browser", "tool": "navigate",
     "arguments": {"url": "https://example.com"}, "result": None, "error": {"message": "denied"}, "status": "failed"}},
    {"type": "item.completed", "item": {"id": "i5", "type": "web_search", "query": "example jobs"}},
    {"type": "item.completed", "item": {"id": "i6", "type": "todo_list",
     "items": [{"text": "scout", "completed": True}, {"text": "tailor", "completed": False}]}},
    {"type": "item.completed", "item": {"id": "i7", "type": "agent_message", "text": "Done. **2** jobs <script>alert(3)</script>"}},
    {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 200}},
    "not json",
]
TS = "2026-10-08T01:02:03.000Z"
ROLLOUT_EVENTS = [
    {"timestamp": TS, "type": "session_meta", "payload": {"id": "sess-1", "cli_version": "0.0.0", "originator": "codex_cli_rs", "cwd": "/tmp/example"}},
    {"timestamp": TS, "type": "turn_context", "payload": {"approval_policy": "on-request", "sandbox_policy": {"type": "workspace-write"}, "model": "model-x"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run $job-scout"}]}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "reasoning", "summary": []}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call", "name": "shell",
     "arguments": json.dumps({"command": ["bash", "-lc", "soffice --version"]}), "call_id": "c1"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
     "output": json.dumps({"output": "sandbox denied", "metadata": {"exit_code": 134}})}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2",
     "input": "*** Begin Patch\n*** Update File: jobs/example-1/fit.md\n@@\n-a\n+b\n*** End Patch"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c2",
     "output": [{"type": "input_text", "text": "Success. Updated the following files:\nM jobs/example-1/fit.md"}]}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "message", "role": "assistant",
     "content": [{"type": "output_text", "text": "LibreOffice **failed** in the sandbox."}]}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": 5000, "cached_input_tokens": 1000, "output_tokens": 300, "total_tokens": 5300}}}},
]
ITEM_EVENTS = [
    {"timestamp": TS, "type": "session_meta", "payload": {"id": "sess-2", "source": "cli"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input": "run()", "call_id": "x"}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "CommandExecution", "id": "1", "command": ["/bin/zsh", "-lc", "pdftotext x.pdf -"], "exit_code": 0,
        "status": "completed", "duration": {"secs": 1, "nanos": 500000000}, "aggregated_output": "text"}}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "AgentMessage", "id": "2", "content": [{"type": "Text", "text": "All good"}], "phase": "final_answer"}}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "FileChange", "id": "3", "changes": {"jobs/example-1/fit.md": {"type": "update", "unified_diff": "-a\n+b"}}}}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "x", "output": "ran"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call", "name": "request_user_input_async",
     "call_id": "q1", "arguments": json.dumps({"questions": [{"title": "Approve site permission for example.com?",
                                                              "options": ["Yes", "No"]}]})}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call_output", "call_id": "q1",
                                                           "output": "user declined"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call", "name": "request_user_input_async",
     "call_id": "q2", "arguments": "{}"}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "AgentMessage", "id": "q2", "content": [{"type": "Text", "text": "Need a choice"}],
        "questions": [{"title": "Use the master resume?", "options": ["Yes", "No"]}]}}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call_output", "call_id": "q2", "output": "Yes"}},
    {"timestamp": TS, "type": "response_item", "payload": {"type": "function_call", "namespace": "collaboration",
     "name": "send_message", "call_id": "s1", "arguments": json.dumps({"target": "scout", "message": "check the JD"})}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "SubAgentActivity", "id": "s1", "kind": "interacted", "agent_path": "/root/scout"}}},
    {"timestamp": TS, "type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "ContextCompaction", "id": "c"}}},
]


class MarkdownTest(unittest.TestCase):
    def test_raw_html_and_script_links_are_escaped(self):
        out = md_to_html('<img src=x onerror=alert(1)> [x](javascript:alert(1)) [y](JaVa\tScRiPt:alert(1)) [z](java&#115;cript:alert(1))')
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertNotIn("href", out)
        out = md_to_html("```html\n</code></pre><script>alert(1)</script>\n```")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;/code&gt;&lt;/pre&gt;&lt;script&gt;", out)

    def test_block_elements(self):
        out = md_to_html(
            "# Title\n\nPara **bold** and *it* and `a<b`.\n\n- one\n  - nested\n- [x] done\n\n3. three\n4. four\n\n"
            "| Left | Right |\n|:-----|------:|\n| a | `b|c` |\n\n> quoted **text**\n\n---\n"
        )
        for fragment in ("<h1>Title</h1>", "<strong>bold</strong>", "<em>it</em>", "<code>a&lt;b</code>",
                         "<ul>", "nested", '<ol start="3">', '<th style="text-align:left">Left</th>',
                         '<th style="text-align:right">Right</th>', "<blockquote>", "<strong>text</strong>", "<hr>", "☑"):
            self.assertIn(fragment, out)
        self.assertEqual(1, out.count("<table>"))
        self.assertLess(out.index("<ul>"), out.index("nested"))
        self.assertEqual(2, out.count("<ul>"))

    def test_inline_links(self):
        out = md_to_html("See [docs](https://example.com/a?b=1&c=2) or https://example.org/x. Mail [me](mailto:a@example.com).")
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', out)
        self.assertIn('href="https://example.org/x"', out)
        self.assertIn('href="mailto:a@example.com"', out)
        self.assertIn("`**x**`", md_to_html("`` `**x**` ``"))
        self.assertNotIn("<em>", md_to_html("snake_case_name and job_id_here"))

    def test_lists_interrupt_paragraphs_and_deep_nesting_is_bounded(self):
        out = md_to_html("Gaps:\n- SQL\n- Tableau")
        self.assertIn("<p>Gaps:</p>", out)
        self.assertEqual(2, out.count("<li>"))
        md_to_html(">" * 3000 + " deep")
        md_to_html("\n".join("  " * n + "- item" for n in range(400)))


class CodexLogTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    def parse(self, name, records):
        path = self.root / name
        jsonl(path, records)
        return parse_codex_log(path)

    def test_exec_json_events(self):
        log = self.parse("exec.jsonl", EXEC_EVENTS)
        self.assertEqual("exec", log["kind"])
        self.assertEqual(1, log["bad_lines"])
        commands = [e for e in log["events"] if e["cat"] == "command"]
        self.assertEqual(["python3 assistant.py list-ats greenhouse:example", "open https://example.com", "sleep 100"],
                         [c["title"] for c in commands])
        self.assertEqual([False, True, False], [dashboard.is_failed(c) for c in commands])
        self.assertEqual("in_progress", commands[2]["status"])
        counts = dashboard.log_counts(log)
        self.assertEqual((3, 1, 1, 1), (counts["command"], counts["failed"], counts["file"], counts["paths"]))
        self.assertEqual({"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 200}, log["usage"])
        self.assertEqual(1, log["reasoning"])
        rendered = dashboard.render_log(log)
        self.assertIn("exit 1", rendered)
        self.assertIn("&lt;script&gt;alert(3)", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("☑ scout", rendered)

    def test_rollout_response_items(self):
        log = self.parse("rollout.jsonl", ROLLOUT_EVENTS)
        self.assertEqual("rollout", log["kind"])
        self.assertEqual("0.0.0", log["meta"]["cli_version"])
        self.assertIn("sandbox=workspace-write", log["meta"]["approval / sandbox"])
        command = next(e for e in log["events"] if e["cat"] == "command")
        self.assertEqual(("soffice --version", 134, "sandbox denied"), (command["title"], command["exit_code"], command["output"]))
        patch = next(e for e in log["events"] if e["cat"] == "file")
        self.assertEqual(["update jobs/example-1/fit.md"], patch["items"])
        self.assertIn("Success.", patch["output"])
        self.assertEqual(5300, log["usage"]["total_tokens"])
        self.assertEqual(["用户", "Agent"], [e["label"] for e in log["events"] if e["cat"] == "message"])

    def test_rollout_item_completed_is_preferred_over_raw_calls(self):
        log = self.parse("rollout-new.jsonl", ITEM_EVENTS)
        commands = [e for e in log["events"] if e["cat"] == "command"]
        self.assertEqual(["pdftotext x.pdf -"], [c["title"] for c in commands])
        self.assertEqual("1.5s", commands[0]["duration"])
        self.assertEqual(["update jobs/example-1/fit.md"], next(e for e in log["events"] if e["cat"] == "file")["items"])
        self.assertNotIn("未匹配的工具输出", [e["title"] for e in log["events"]])

    def test_rollout_keeps_user_questions_and_sub_agent_steps(self):
        log = self.parse("rollout-new.jsonl", ITEM_EVENTS)
        asked = next(e for e in log["events"] if e["title"] == "request_user_input_async")
        self.assertIn("Approve site permission for example.com?", asked["detail"])
        self.assertEqual("user declined", asked["output"])
        question = next(e for e in log["events"] if e["label"] == "Agent · 向用户提问")
        self.assertEqual((["Use the master resume?（选项：Yes / No）"], "Yes"), (question["items"], question["output"]))
        sub_agent = next(e for e in log["events"] if e["label"] == "子代理")
        self.assertEqual("interacted · /root/scout · collaboration.send_message", sub_agent["title"])
        self.assertIn("check the JD", sub_agent["detail"])
        self.assertIn("上下文已压缩", [e["title"] for e in log["events"]])

    def test_other_jsonl_is_not_a_codex_log(self):
        self.assertIsNone(self.parse("other.jsonl", [{"name": "row", "value": 1}]))

    def test_test_log_summary(self):
        self.assertEqual("pass", summarize_test_log("....\nRan 4 tests in 0.1s\n\nOK\n")[0])
        status, detail = summarize_test_log("Ran 4 tests in 0.1s\n\nFAILED (failures=1)\n")
        self.assertEqual("fail", status)
        self.assertIn("failures=1", detail)
        self.assertEqual("fail", summarize_test_log("===== 3 passed, 1 failed in 0.20s =====")[0])
        self.assertEqual("unknown", summarize_test_log("nothing here")[0])


class DashboardBuildTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name) / "repo"
        self.make_fixture()

    def make_fixture(self):
        root = self.root
        job = root / "jobs" / "example-1"
        (job / "upload").mkdir(parents=True)
        (job / "jd.txt").write_text(JD_TEXT, encoding="utf-8")
        jd_hash = dashboard.hashlib.sha256(JD_TEXT.encode()).hexdigest()
        (job / "jd.md").write_text("# Example\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))\n", encoding="utf-8")
        (job / "fit.md").write_text('| Gate | Status |\n|---|---|\n| Language | met: "English required" |\n', encoding="utf-8")
        (job / "source.json").write_text(json.dumps({
            "job_id": "example-1", "posted_at": "2026-10-01T00:00:00Z", "employment_type": None,
            "recency": {"days": 7, "age_days": 6.5, "status": "met"}}), encoding="utf-8")
        (job / "form.json").write_text(json.dumps({"questions": [
            {"label": "Work authorization?", "required": True,
             "fields": [{"name": "question_1", "type": "multi_value_single_select", "values": [{"label": "Yes"}, {"label": "No"}]}]}]}),
            encoding="utf-8")
        (job / "pre-submit.json").write_text(json.dumps({"job_id": "example-1", "dry_run": True}), encoding="utf-8")
        (job / "resume.pdf").write_bytes(PDF)
        (job / "upload" / "Jane_Doe_CV.pdf").write_bytes(PDF)
        (job / "notes.bin").write_bytes(b"\x00\x01binary")
        (root / "jobs" / "orphan-2").mkdir()
        (root / "jobs" / "orphan-2" / "jd.md").write_text("Orphan note", encoding="utf-8")
        (root / "jobs.csv").write_text(
            FIELDS + "\n"
            f'example-1,Example GmbH,"Product <script>alert(""x"")</script> Manager",Berlin,https://jobs.example.com/1,'
            f"https://jobs.example.com/1/apply,2026-10-07T00:00:00Z,2026-10-08T00:00:00Z,{jd_hash},active_verified,"
            "2026-10-08T00:00:00Z,discovered\n"
            "example-3,Sample AG,Analyst,Remote,https://jobs.example.com/3,https://jobs.example.com/3,"
            "2026-10-07T00:00:00Z,2026-10-07T00:00:00Z,abc,blocked,,discovered\n",
            encoding="utf-8",
        )
        build = root / "private" / "resume_variants" / "build"
        (build / "pm").mkdir(parents=True)
        (build / "pm" / "Jane_Doe_CV.pdf").write_bytes(PDF)
        (build / "pm" / "preview-1.png").write_bytes(tiny_png())
        (build / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "master": "private/source/master.docx", "upload_filename": "Jane_Doe_CV.pdf",
            "generated_at": "2026-10-08T00:00:00Z",
            "variants": [
                {"id": "pm", "role_family": "Product", "description": "Product variant", "status": "approved",
                 "pdf": "private/resume_variants/build/pm/Jane_Doe_CV.pdf", "pdf_sha256": "f" * 64, "pages": 1,
                 "checks": {"fonts": {"ok": True, "detail": "all embedded"}, "ligatures": {"ok": True, "detail": "none"}},
                 "previews": ["private/resume_variants/build/pm/preview-1.png", "../outside.png"],
                 "edits": [{"paragraph_contains": "Summary", "old": "Lead", "new": "Led", "fact_ids": ["F1"], "reason": "tense"}],
                 "approved_by": "Jane", "approved_at": "2026-10-08T01:00:00Z"},
                {"id": "ops", "role_family": "Operations", "status": "failed", "pdf": "missing.pdf",
                 "checks": {"word_coverage": {"ok": False, "detail": "0.98 < 0.995"}}},
            ]}), encoding="utf-8")
        run = root / "runs" / "2026-10-08"
        (run / "trial").mkdir(parents=True)
        (run / "digest.md").write_text("# Digest\n\n- source A: blocked (browser permission denied)\n", encoding="utf-8")
        (run / "dashboard.html").write_text("old dashboard", encoding="utf-8")
        jsonl(run / "trial" / "codex-exec.jsonl", EXEC_EVENTS)
        jsonl(run / "trial" / "rollout.jsonl", ROLLOUT_EVENTS)
        jsonl(run / "trial" / "other.jsonl", [{"name": "row"}])
        (run / "trial" / "notes.md").write_text("Trial **notes**", encoding="utf-8")
        self.trial = run / "trial"
        self.report = Path(self.workspace.name) / "report.md"
        self.report.write_text("# Trial report\n\nDecision: **ship** v2.\n", encoding="utf-8")
        self.tests_log = Path(self.workspace.name) / "tests.log"
        self.tests_log.write_text("..\nRan 2 tests in 0.01s\n\nOK\n", encoding="utf-8")
        self.out = Path(self.workspace.name) / "out" / "dashboard.html"

    def build(self, **kwargs):
        options = dict(report=self.report, trial_dir=self.trial, tests_logs=[self.tests_log])
        options.update(kwargs)
        build_dashboard(self.root, self.out, title="Test <Dashboard>", **options)
        return self.out.read_text(encoding="utf-8")

    def template(self, page, label):
        tid = re.search(r'data-tpl="(tpl-\d+)">' + re.escape(label) + "<", page).group(1)
        return re.search(f'<template id="{tid}">(.*?)</template>', page, re.S).group(1)

    def test_page_is_self_contained_and_escapes_untrusted_text(self):
        page = self.build()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIsNone(re.search(r"<(?:script|img|iframe|link|embed|object)\b[^>]*\b(?:src|href)=\"(?:https?:)?//", page))
        self.assertNotIn("<link", page)
        self.assertNotIn("@import", page)
        self.assertIn("default-src &#x27;none&#x27;", page)
        self.assertNotIn("alert(1)>", page)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("&lt;/template&gt;&lt;script&gt;alert(2)", page)
        for tag in re.findall(r"<script[^>]*>", page):
            self.assertTrue(tag == "<script>" or tag.startswith('<script type="text/plain" id="blob-'), tag)
        self.assertEqual(1, page.count("<script>"))
        self.assertIn("Test &lt;Dashboard&gt;", page)
        self.assertIn("prefers-color-scheme:dark", page)

    def test_report_first_then_overview(self):
        page = self.build()
        self.assertLess(page.index('id="report"'), page.index('id="overview"'))
        self.assertIn("<strong>ship</strong>", page)
        self.assertIn("1/2 已批准", page)
        self.assertIn("3 个岗位", page)  # same set as the jobs table: 2 jobs.csv rows + 1 folder
        self.assertIn("jobs.csv 2 行 + 1 个不在 jobs.csv 的文件夹", page)
        self.assertIn(">1/3<", page)
        self.assertIn("共 3 个", page)
        self.assertIn("dry-run 1", page)
        self.assertIn("1 个岗位来源为 blocked", page)
        self.assertNotIn("id=\"report\"", self.build(report=None))

    def test_jobs_table_and_panels_cover_every_file(self):
        page = self.build()
        for job_id in ("example-1", "example-3", "orphan-2"):
            self.assertIn(f'data-job-id="{job_id}"', page)
        self.assertIn("不在 jobs.csv", page)
        panel = re.search(r'<template id="job-0">(.*?)</template>', page, re.S).group(1)
        labels = re.findall(r'data-tpl="tpl-\d+">([^<]+)<', panel)
        self.assertEqual(["jd.md", "jd.txt", "source.json", "fit.md", "form.json", "resume.pdf", "pre-submit.json",
                          "notes.bin", "upload/Jane_Doe_CV.pdf"], labels)
        self.assertIn("jd.txt 哈希与 jobs.csv 一致", panel)
        self.assertIn("recency met", panel)
        self.assertIn('href="https://jobs.example.com/1/apply"', panel)
        self.assertIn("Work authorization?", self.template(page, "form.json"))
        self.assertIn('data-blob-src="blob-', self.template(page, "resume.pdf"))
        self.assertIn("data-download-blob", self.template(page, "notes.bin"))
        self.assertIn("<td>Language</td>", self.template(page, "fit.md"))
        self.assertIn("&lt;img src=x", self.template(page, "jd.md"))

    def test_identical_pdfs_share_one_blob(self):
        page = self.build()
        self.assertEqual(1, len(re.findall(r'data-mime="application/pdf"', page)))
        self.assertIn('data-mime="application/octet-stream"', page)

    def test_variants_show_status_checks_previews_and_pdf(self):
        page = self.build()
        section = page[page.index('id="variants"'):page.index('id="runs"')]
        self.assertIn("data:image/png;base64,", section)
        self.assertIn("预览缺失", section)
        self.assertIn("data-open-blob", section)
        self.assertIn("检查 0/1 通过", section)
        self.assertIn("<details open><summary>检查 0/1", section)
        self.assertIn("批准：Jane", section)
        self.assertIn("F1", section)

    def test_runs_skip_dashboards_and_trial_folder(self):
        page = self.build()
        section = page[page.index('id="runs"'):page.index('id="trial"')]
        self.assertIn(">digest.md<", section)
        self.assertNotIn("dashboard.html", section)
        self.assertNotIn("codex-exec.jsonl", section)
        without_trial = self.build(trial_dir=None)
        section = without_trial[without_trial.index('id="runs"'):]
        self.assertIn("trial/codex-exec.jsonl", section)

    def test_trial_timeline_and_tests(self):
        page = self.build()
        section = page[page.index('id="trial"'):page.index('id="tests"')]
        self.assertIn("codex exec --json", section)
        self.assertIn("Codex 交互会话 rollout", section)
        self.assertIn("python3 assistant.py list-ats greenhouse:example", section)
        self.assertIn('data-filter="failed"', section)
        self.assertIn("输入 1,000（缓存 400） · 输出 200 · 合计 1,200", section)
        self.assertIn(">notes.md<", section)
        self.assertIn(">other.jsonl<", section)
        self.assertNotIn(">codex-exec.jsonl</button>", section)
        tests = page[page.index('id="tests"'):]
        self.assertIn("共运行 2 个测试 · OK", tests)

    def test_large_files_are_not_embedded(self):
        with mock.patch.object(dashboard, "MAX_EMBED_BYTES", 40):
            page = self.build()
        self.assertIn("文件超过", self.template(page, "resume.pdf"))
        self.assertNotIn('data-mime="application/pdf"', page)
        self.assertIn("PDF 不存在或过大", page)

    def test_malformed_manifest_is_reported(self):
        (self.root / dashboard.MANIFEST).write_text("{not json", encoding="utf-8")
        page = self.build()
        self.assertIn("无法解析或结构不符合预期", page)

    def test_malformed_variant_fields_do_not_crash(self):
        (self.root / dashboard.MANIFEST).write_text(json.dumps({"schema_version": 1, "variants": [
            {"id": "v1", "status": "draft", "previews": 5, "checks": "ok", "edits": [{"fact_ids": 3}]}]}),
            encoding="utf-8")
        page = self.build()
        self.assertIn("manifest 字段格式不对，已忽略：previews, checks", page)

    def test_plain_run_picks_up_latest_trial_and_tests_log(self):
        run = self.root / "runs" / "2026-10-08"
        (run / "tests.log").write_text("..\nRan 2 tests in 0.01s\n\nOK\n", encoding="utf-8")
        (self.root / "runs" / "2026-10-01").mkdir()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, dashboard.main(["--root", str(self.root), "--out", str(self.out)]))
        page = self.out.read_text(encoding="utf-8")
        self.assertIn('id="trial"', page)
        self.assertIn("共运行 2 个测试 · OK", page)
        runs = page[page.index('id="runs"'):page.index('id="trial"')]
        self.assertNotIn("tests.log", runs)
        self.assertNotIn("codex-exec.jsonl", runs)
        self.assertIn("Using trial dir", stdout.getvalue())

    def test_root_without_project_files_warns(self):
        empty = Path(self.workspace.name) / "empty"
        empty.mkdir()
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(0, dashboard.main(["--root", str(empty), "--out", str(self.out)]))
        self.assertIn("is --root the repository?", stderr.getvalue())

    def test_cli_default_output_warning_and_missing_inputs(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, dashboard.main(["--root", str(self.root)]))
        expected = self.root / "runs" / datetime.now().date().isoformat() / "dashboard.html"
        self.assertTrue(expected.is_file())
        self.assertIn("contains personal data; do not publish", stdout.getvalue())
        self.assertIn(expected.resolve().as_uri(), stdout.getvalue())
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(2, dashboard.main(["--root", str(self.root), "--out", str(self.out), "--report", "missing.md"]))
            self.assertEqual(2, dashboard.main(["--root", str(self.root), "--out", str(self.out), "--trial-dir", "nope"]))
        self.assertIn("Blocked:", stderr.getvalue())

    def test_empty_repository_still_builds(self):
        empty = Path(self.workspace.name) / "empty"
        empty.mkdir()
        build_dashboard(empty, self.out)
        page = self.out.read_text(encoding="utf-8")
        self.assertIn("没有岗位", page)
        self.assertIn("未找到", page)


if __name__ == "__main__":
    unittest.main()

---
name: job-run
description: Coordinate discovery, resume variant selection, and reviewed application across jobs, then build the local results page. Use for an end-to-end run or a multi-job status update.
---

# Job run

Read repository `intent.md`, `spec.md`, and the Skill for each requested stage. Route by user intent: discover with `job-scout`, pick resumes for selected jobs with `resume-tailor`, and apply only to selected jobs with `job-apply`. A request to search is not authorization to submit.

## Runtime

- ATS API scouting (`list-ats`, `fetch-ats`) needs no browser and may run non-interactively (`codex exec`); the sandbox must allow network access.
- Anything that needs the browser (browser sources, logins, real application forms) must run in an interactive session (Codex app/TUI, or an interactive Claude Code session; `claude -p` and `codex exec` are non-interactive). In a non-interactive run, mark those steps `blocked` and leave them for an interactive session; never work around a denied permission.
- Resume variants are built and approved only by the user, in a normal terminal, with `render_resume.py`. Never run `render_resume.py build` or `approve`, and never render a resume in the agent session.

## State and failures

Use files, not conversation memory, as workflow state. For each job, require the prior artifact and check its status before moving on. A historical recommendation or search result cannot skip live source verification. Keep failures isolated per job; if one site or form blocks, report it and continue independent authorized work. Never turn `submission_unknown` into a retry.

Two kinds of rehearsal exist (see `job-apply`): a **question dry-run** on a real JD writes `form.json`, `form-map.md`, and a `pre-submit.json` with `"dry_run": true` into `jobs/JOB_ID/` and never enters candidate data; a **SIMULATION** with a fictional JD keeps everything under `runs/YYYY-MM-DD/simulation/` with its own `--store`.

For a real application, run `job-apply`'s login check before form work. Group jobs by actual recruitment domain so an authenticated browser session can be reused, but verify each site's current state on entry. `private/site_sessions.md` is a checkpoint, not a credential store or proof of current authentication.

## Finish

1. Summarize newly discovered, shortlisted, waiting for answers, resume-ready, ready for review, confirmed submitted, unknown, and blocked jobs, with file paths, open questions, and next actions.
2. Run `python3 dashboard.py` and give the user the HTML path (default `runs/YYYY-MM-DD/dashboard.html`). It picks up the newest `runs/*/trial/` logs and `runs/*/tests.log` by itself; pass `--trial-dir DIR` when logs were saved elsewhere. The page contains personal data: never publish, upload, or share it.

Do not claim a real-browser milestone was tested because unit tests pass.

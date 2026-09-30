---
name: job-run
description: Coordinate discovery, truthful resume tailoring, and reviewed application. Use for an end-to-end run or multi-job status update.
---

# Job run

Read repository `intent.md`, `spec.md`, and the Skill relevant to each requested stage. Route by user intent: discover with `job-scout`, tailor selected jobs with `resume-tailor`, and apply only to selected jobs with `job-apply`. A request to search is not authorization to submit.

Use files rather than conversation memory as workflow state. For each job, require the prior artifact and check its status before moving on. A historical recommendation or search result cannot skip live source verification. Keep failures isolated per job; if one site or form blocks, report it and continue independent authorized work. Never turn `submission_unknown` into a retry. In a dry-run, stop before external candidate-data entry and use only local simulation artifacts to exercise tailoring and preflight.

For a real application, call `job-apply`'s login check before form work. Group jobs by actual recruitment domain so an authenticated browser session can be reused, but verify each site's current state on entry. `private/site_sessions.md` is a checkpoint, not a credential store or proof of current authentication.

At the end, summarize newly discovered, shortlisted, tailored, ready for review, confirmed submitted, unknown, and blocked jobs with file paths and next actions. Do not claim a real-browser milestone was tested because `assistant.py` unit tests pass.

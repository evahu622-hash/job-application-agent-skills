---
name: resume-tailor
description: Tailor one truthful PDF resume to a specific verified job using approved career facts. Use for a selected job_id; do not invent experience or submit.
---

# Resume tailor

Read repository `spec.md`, `private/career_facts.md`, source resumes in `private/source/`, and the job's `jd.txt` and `jd.md`. Require a selected `job_id` in `jobs.csv`, an `active_verified` original source and no unmet or unknown hard gate. Facts extracted from a provided resume are source-backed, but not automatically approved as an application version; resolve uncertain claims before any real upload. A simulation may use them only in a clearly labeled local draft.

First write `resume-plan.md`: JD priorities and the facts/projects to keep, reorder, trim, or omit. Then write `resume-diff.md` with each rewritten bullet linked to source fact IDs and its source location. Preserve exact dates, numbers, employers, roles, and skill claims. Never introduce a claim without a source fact. Distinguish team or company results from individual results, and planned work from completed work. State availability, work authorization, and sponsorship only when the user's approved facts support them.

Generate `jobs/JOB_ID/resume.pdf` with available document/PDF tools. Verify selectable text, key content, page boundaries, and that it belongs to this job. Save evidence and concerns in `fact-check.md`. Stop on conflicting facts, unchecked PDF, or a JD change. Do not apply. For a dry-run using a fictional JD, keep all artifacts under `runs/` and label the PDF `SIMULATION`; never put it in an application directory.

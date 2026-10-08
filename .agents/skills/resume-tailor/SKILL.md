---
name: resume-tailor
description: Pick the approved resume variant that best fits one verified job, copy it into the job folder, and document the choice. Use for a selected job_id; never render or re-typeset a resume, invent experience, or submit.
---

# Resume tailor

Read repository `spec.md`, `private/career_facts.md`, the resume variants manifest, and the job's `jd.txt`, `fit.md`, and `source.json` (ATS sources) or `jd.md` (browser sources). The manifest is `private/resume_variants/build/manifest.json`; if `output_dir` was changed, use the `manifest` path printed by `python3 render_resume.py status`. Require a selected `job_id` in `jobs.csv`, an `active_verified` source, no `unmet` hard gate, and no `unknown` hard gate other than recency.

Never render, convert, or re-typeset a resume in this session: no LibreOffice, ReportLab, HTML-to-PDF, or word-processor automation. Never run `render_resume.py build` or `approve`. The user builds and approves variants in a normal terminal (LibreOffice cannot run inside the Codex sandbox). You may run `python3 render_resume.py status` to read the manifest.

## Steps

1. Choose a variant with `status: approved` whose `role_family` best matches this JD. An approved variant with `role_family: general` (usually the master without edits) is an acceptable fallback when no role-specific variant fits; say in the plan that the fallback was used. Confirm the sha256 of its `pdf` equals `pdf_sha256` and that `approved_by` and `approved_at` are set.
2. Check every edit of that variant against its facts: each `fact_ids` entry exists in `private/career_facts.md` and is approved for applications, and the `new` text is fully supported by those facts. Dates, numbers, employers, job titles, and skill claims stay exactly as in the facts; team or company results stay distinct from individual results, and planned work stays distinct from completed work; no claim appears that the facts do not contain. If any edit fails, stop and ask the user to correct `variants.json` and rebuild.
3. Copy the PDF byte-for-byte to `jobs/JOB_ID/resume.pdf` and `jobs/JOB_ID/upload/<upload_filename>` (`upload_filename` from the manifest).
4. Write `jobs/JOB_ID/resume-plan.md`: why this variant; the JD priorities, quoted in straight `"..."` or curly `“...”` quotes from this job's own `jd.txt` (quote marks only for JD quotes; use **bold** for emphasis); which fit gaps from `fit.md` remain visible; other variants considered.
5. Write `jobs/JOB_ID/resume-diff.md`: each edit of the variant versus the master (`old` → `new`, written without quote marks) with fact IDs and reason, taken from the manifest. A variant without edits is the master.
6. Write `jobs/JOB_ID/fact-check.md`: each manifest check (name, ok, detail), pages, the sha256 of both copies, `approved_by`, `approved_at`, and the result of step 2.
7. Run `python3 assistant.py check-quotes JOB_ID --file resume-plan.md` and fix every entry in `missing`.

## Stop and ask the user

Stop, and ask the user to build or approve a variant with `render_resume.py` in a normal terminal (see `SETUP.md`), when:

- no manifest exists, or no variant is approved at all;
- a variant's edit is not supported by its facts (step 2), or a fact is unapproved;
- a hash does not match, a check failed, or the JD changed after the plan was written.

A rebuild changes the PDF and clears its approval. After the user approves the rebuilt variant, run this Skill again for every job prepared with the old PDF; otherwise preflight blocks with `Resume is not an approved resume variant`.

Never introduce a claim without a source fact. State availability, work authorization, and sponsorship only when approved facts support them. Do not apply.

For a SIMULATION with a fictional JD, keep every artifact under `runs/YYYY-MM-DD/simulation/` (its own `jobs.csv` and `jobs/`, used with `python3 assistant.py --store runs/YYYY-MM-DD/simulation/jobs.csv ...`) and label the files and headings `SIMULATION`; never put them in the real `jobs/` or an upload folder.

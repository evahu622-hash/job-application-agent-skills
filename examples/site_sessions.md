# Site login checks — copy to private/site_sessions.md

One row per check. Record what the page showed, never a password, one-time
code, cookie, or token. An `authenticated` row is only a hint for the next
run: the agent checks the live page again every time.

| Site (recruiting domain) | Browser route | Checked at (UTC) | Status | Page evidence |
| --- | --- | --- | --- | --- |
| jobs.example.com | Codex browser | 2026-01-01T09:00:00Z | authenticated | Application form shows "Signed in" and the CV upload field |
| careers.example.org | Claude in Chrome | 2026-01-01T09:05:00Z | login_required | Login page; left open for the user |

Status values: `authenticated`, `login_required`, `blocked` (CAPTCHA or access
error that persisted after the user was asked), `unknown`.

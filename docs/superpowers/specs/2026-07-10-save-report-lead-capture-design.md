# Save-report / lead-capture — design (2026-07-10)

## Context
Since the IA redesign retired the login-gated `/tool` SPA, spck.dev collected **zero
new user emails** — the only page that used the auth API was `/admin`. The instant
`/check` converted anonymous runs but captured nothing. This flow gives a merchant who
just ran a check a real reason to hand over an email, and ties every email to a store
domain — the lead signal that matters for a B2B tool. Regression-watch (re-run weekly +
email on change) is the natural v2 upsell and is out of scope here.

## What it does
After a completed instant check, `/check` offers **"Save this report"**: email → 6-digit
OTP → saved. The user gets an emailed permalink (`/check?report=<id>`, owner-gated), the
report persists 90 days, and every save records the store domain on the user. The admin
dashboard reflects this (store-domains column, reports-saved conversion KPI).

## Reuse (no new backend)
The OTP auth (`send-otp`/`verify-otp`, Resend, sessions) and `/api/reports`
(`saveReport`/`getReport`/`listReports`, REPORTS KV, 90-day TTL) already existed for the
old `/tool`. This flow wires them into `/check` and extends `saveReport`.

## Backend (functions/api/[[path]].js, track.js)
- `saveReport` records the store domain on the user (`user.domains[]`, unique, first-seen,
  capped) — the email→domain mapping — and fires a best-effort `sendReportEmail` (permalink)
  that a Resend failure can never block.
- `sendReportEmail`: emerald-branded email with a `/check?report=<id>` button.
- `getReport` stays owner-walled (another user → 403).
- `track.js` allow-lists `report_saved` (the conversion beacon).
- `adminUsers` surfaces `user.domains`.

## Frontend (public/check.html, admin.html)
- `/check`: after a run, a "Save this report" card (email → OTP → save; one-click for an
  existing session). `?report=<id>` renders the saved run owner-only + a "your reports" list.
  All DOM built via `el()`/`textContent` (security-gate sink rule).
- `/admin`: users table gains a **store domains** column; funnel pane gains **reports saved**.

## Governance / TDD (machine-enforced)
- SITE-R-023 (save flow: auth-gated save, owner-walled retrieval, email→domain mapping,
  best-effort email, `report_saved` allow-listed) → `web_unit:save-report` +
  `site_smoke:check-save`.
- SITE-R-024 (dashboard reflects the product: domains column + conversion KPI) →
  `web_unit:admin-domains`.
- Register 24 rows, 100% traceability. Tests written RED first
  (`tests/web/unit/save_report.test.mjs`), then implemented to GREEN.
- `site_smoke` +2 assertions (save card appears after a fixture run; a stranger hitting a
  permalink gets the sign-in gate) — kill-proven against an injected violation.

## Out of scope (future)
Regression "watch my store" (needs a scheduler), changelog subscription.

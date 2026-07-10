# Admin Portal — design (2026-07-10)

Builds §5-v1 of ops/admin-portal-scope-2026-07-10.md. Approved by Vishal: "design and
build; access control using email and OTP; I can add new users; I am super admin."

## Roles
- **Super-admin**: `SUPER_ADMINS = ['katyal.vishal@gmail.com']` — a CODE constant, the
  root of trust. Cannot be removed or demoted via any API.
- **Admin**: emails in the dynamic allowlist, KV `USERS['admin:allowlist']` (JSON array,
  lowercase). Granted/revoked by a super-admin from the portal.
- Everyone else: may still OTP-login (existing public feature: saved reports, API keys)
  but every `/api/admin/*` call returns 403.

## Auth (reuse, don't rebuild)
Existing email+OTP flow (Resend, 6-digit, 10-min TTL, 5 attempts) + bearer sessions.
`isAdminEmail(env, email)` = super OR allowlisted. `requireAdmin` uses it;
new `requireSuperAdmin` for management routes. `verify-otp`/`me` responses gain
`isAdmin` + `isSuperAdmin` flags (computed, never stored).

## New API (all in functions/api/[[path]].js)
| Route | Who | Behavior |
|---|---|---|
| `GET /api/admin/admins` | admin | `{super_admins:[…], admins:[…]}` |
| `POST /api/admin/admins {email}` | super-admin | validate email; lowercase; idempotent add; audit |
| `DELETE /api/admin/admins {email}` | super-admin | refuse super-admin removal (400); idempotent remove; audit |
| `GET /api/admin/audit` | super-admin | last 200 entries `{at, by, action, email}` from KV `USERS['admin:audit']` |

## UI — public/admin.html
- Standard shell: byte-identical nav, site.css, disclaimer footer, favicon emerald.
- `<meta name="robots" content="noindex,nofollow">`; NOT linked from any nav/sitemap.
- Login card (email → code → verify; bearer token kept in localStorage `spck_admin_token`).
- Dashboard sections (all numbers runtime-fetched — zero static claims):
  1. **Overview** — totalUsers/Logins/TestRuns/Reports, instantChecks, badgeHits.
  2. **Funnel** — home/check/agent/sandbox/docs/coverage views + returning-visitor counts.
  3. **Top domains** — domainsTested table (checks) + badge domains.
  4. **Distribution** — /api/admin/metrics: PyPI recent downloads, GitHub stars/views/
     clones/referrers, Cloudflare 7-day traffic (setup hints render when secrets absent).
  5. **Users & activity** — /api/admin/users + /api/admin/activity.
  6. **Admins** (visible to super-admin only) — list, add-by-email, remove; audit trail.
- All dynamic rendering through `esc()` (security-gate sink rule).

## Funnel fix (found in audit)
track.js EVENTS allow-list gains: `home_view home_return check_view check_return
docs_view docs_return coverage_view coverage_return` (index already sends home_view —
today it is silently dropped). check/docs/coverage pages get the same beacon snippet
index.html uses (view + return flavors). agent/sandbox already instrumented.

## Also in this change
- OTP email template: purple `#5b5bd6`/`#f0f0ff` → emerald `#059669`/`#ecfdf5` (brand).
- `ADMIN_EMAILS` const renamed to `SUPER_ADMINS` (same single email — behavior-compatible).

## Governance / TDD
- New register test kind **`web_unit:<TAG>`**: site_gates.py tdd() accepts it when a
  `// SITE-R-0xx` tag exists in tests/web/unit/*.test.mjs (same mechanism as site_smoke tags).
- SITE-R-020 (admin access control: unauth 401, non-admin 403, super-only management,
  super undeletable) → web_unit tests.
- SITE-R-021 (admin page: noindex, not in nav, standard shell, no static numeric claims)
  → site_smoke:admin-page.
- SITE-R-022 (funnel beacons allow-listed + pages instrumented) → web_unit (track) +
  site_smoke:beacons extension.
- All 7 site gates + site_smoke + responsive must stay green; engine untouched.

## Out of scope (v2/v3 per the scope doc)
Engineering-metrics pane, roadmap render, alerts, KV snapshots, CF Access hardening.
Secrets wiring (GH_METRICS_TOKEN / CF_ANALYTICS_TOKEN / CF_ZONE_ID) is a Vishal
dashboard/token action — the UI renders its absence gracefully.

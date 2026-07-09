# spck.dev website IA redesign — design spec (2026-07-09)

## Problem
The site grew page-by-page and now confuses its core action. Three merchant "tools" of
ambiguous depth coexist: `/check` (no-login instant subset), `/tool` (email-OTP login SPA
running the SAME subset, plus saved reports), and the CLI (the only FULL suite, with
write-path checks). The most prominent CTA ("Launch Tool") leads to a login wall for a
subset, while the deepest capability (CLI) is least visible. The nav mixes audiences
("Merchant Platforms", "Shopping Agents") with features ("Coverage", "Guide", "Launch
Tool") and differs page to page. CTA language is inconsistent ("Launch Tool", "Check your
store", "Check your platform" all mean "test a merchant server").

## Decisions (Vishal, 2026-07-09)
1. **Retire `/tool`** — consolidate to `/check` (instant web preview) + CLI (full suite).
   `/tool` redirects to `/check`. Email-OTP accounts/saved-reports leave the public
   surface (admin/analytics backend unaffected).
2. **Homepage leads with the hook** ("conformance ≠ reliability"), then the two-sided
   merchant/agent fork. The agent story is the differentiator and helps lead.
3. **Scope = A+**: structural reorg + copy rewrite, keeping the existing visual DNA
   (dark hero, purple accent, existing component styles), PLUS two high-value polish
   items: a unified nav/footer across all pages and social-share meta on every page.
   No ground-up visual redesign.

## Information architecture
Sitemap: `/` (hook → fork → proof) · `/check` = Merchants home (depth ladder) ·
`/agent` = Agents home (explainer → sandbox) · `/sandbox` (demo, kept) · `/coverage`
(kept) · `/docs` (repurposed from `/guide`: one real quickstart) · `/tool` → redirect
to `/check`.

Unified nav, byte-identical on every page:
`spck · Merchants · Agents · Coverage · Docs · GitHub↗`
(labels: short "Merchants / Agents" form). No "Launch Tool" button anywhere.

## Page designs

### Homepage `/`
Above the fold: badge "Conformance ≠ reliability" → H1 hook ("Your UCP checkout passes
conformance. Does it actually work?") → one-liner (independent, open, two-sided,
kill-rate-proven) → two primary CTAs: **Check your store →** (/check) and **Test your
agent →** (/agent) → stat strip (193 merchant checks · 42 agent checks · 0 false
greens), numbers sourced from live coverage data, not hardcoded.
Below: ① problem (shape vs behavior) → ② the two-sided fork cards (existing copy,
demoted below the hook) → ③ proof (kill-rate explainer + live-coverage teaser + one
passive upstream-collaboration clause) → ④ sandbox teaser → footer.

### Merchants `/check`
A 3-rung depth ladder on one page:
1. **Instant check** (existing paste-URL box): "30 seconds, no install — discovery +
   profile + read-only catalog."
2. **Full suite (CLI)**: "the real test incl. checkout/cart/order write-paths" —
   `pip install spck-conformance` + `--init` + run commands.
3. **In CI**: the GitHub Action snippet.
Plus a kill-rate "why a pass means something" note and a Coverage link. The web check
is explicitly a preview; the CLI is explicitly the full test.

### Agents `/agent`
Keep the explainer (six failure modes, reverse harness). ONE primary CTA → /sandbox
("watch it live"); secondary = `spck-conformance --agent`. Shared nav/footer.

### Sandbox `/sandbox`, Coverage `/coverage`
Content unchanged; adopt shared nav/footer + social meta.

### Docs `/docs`
Repurpose `/guide` into one quickstart: install, CLI usage, `--agent`, CI Action,
what's checked, spec versions, links (repo/PyPI/coverage). Nav target for "Docs".
`/guide` → redirect to `/docs`.

## Cross-cutting (the A+ items)
- One nav + one footer, identical markup on all 6 pages.
- OG/Twitter share meta on every page (extend the / and /check pattern).
- Consistent button/card/spacing usage from the existing styles.
- Redirects via `public/_redirects` (Cloudflare Pages): `/tool → /check 301`,
  `/guide → /docs 301`.

## Guardrails
- Every page keeps the "independent, unofficial, not a substitute; official suite is
  authoritative" disclaimer.
- All check/defect/coverage numbers must match governance-enforced values (42/43/193,
  87/87/87, agent 53%) — the copy-freshness CI gate must stay green; prefer rendering
  from coverage JSON where a number already comes from data.
- No changes to the conformance engine, coverage gates, or `/api/*` functions.
- Analytics beacons preserved (home_view + agent/sandbox events); add page_view beacons
  to /check and /docs so the funnel is measurable.
- The `web-browser` responsive CI gate must pass (12 checks); test at 320px.

## Error handling / edge cases
- `/tool` deep links (old emails/bookmarks) → 301 to /check (no dead ends).
- Users with saved reports in KV: backend data untouched; public UI for it removed.
  (If anyone asks, reports remain retrievable via admin.)
- External links (dev.to posts, HN comment) point at /, /check, /sandbox — all
  preserved URLs; only /tool and /guide change, both redirected.

## Testing
1. `python3 conformance/ci/run_suite.py` GREEN (esp. web-unit, web-browser, and the
   agent-governance copy-freshness gate — update its expected copy strings if the gate
   pins phrases that the redesign rewrites).
2. Manual pass on all 6 pages: nav identical, CTAs correct, disclaimers present,
   numbers correct, mobile 320px, dark hero renders.
3. Redirect checks: /tool→/check, /guide→/docs (curl -I on the preview deploy).
4. Deploy to a PREVIEW branch first (`npx wrangler pages deploy public
   --project-name=ucp-conformance --branch=redesign-preview`) → Vishal reviews the
   preview URL → then promote to production (deploy --branch=main).

## Out of scope
Ground-up visual identity (approach C), the signature-validator page (separate,
already-planned feature), engine/coverage work, admin dashboard changes.

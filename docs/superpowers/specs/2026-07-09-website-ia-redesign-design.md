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

## Voice & truthfulness (site copy law — applies to EVERY page, now and forever)
Site copy follows the content-voice-rule (memory + here, machine-enforced below):
1. **Value-first**: every section answers "what can YOU do here" — never "what we
   achieved." No achievement framing, no counts of merged PRs, no "first/best/only"
   superlatives. Allowed self-claims are capability statements with proof links
   ("every check is proven to catch its own bug" → links to the kill-rate gate/docs).
2. **Collaborative & grounded**: upstream work appears at most as one passive clause
   ("when the suite surfaces something real, we report it upstream and work it
   through — the project has been responsive"). No one is shown in a bad light;
   findings appear only as reader-usable lessons ("check yours in 30 seconds").
3. **Truthful, always**: every factual claim on the site must be either (a) RENDERED
   from live governed data (coverage.json / agent-coverage.json), or (b) registered in
   a site claims file with its evidence, or (c) removed. No unverifiable statistics
   (the "99%" class), no "certified", no implied endorsement.
4. Every page carries the "independent, unofficial, not a substitute; the official
   suite is authoritative" disclaimer.

## Site governance — the automatic checklist (new CI gates, mirrors the suite's own)
A new `site-governance` lane in run_suite (runs on every public/** or coverage change),
so accuracy/messaging/TDD are enforced on EVERY future enhancement automatically:
- **site-claims gate**: `public/site_claims.json` registers every hardcoded factual
  claim (text, page, evidence). Gate scans built pages: any numeric/factual claim not
  data-rendered and not registered → RED. Numbers that exist in coverage JSON MUST be
  rendered from it (no hardcoded drift possible). Generalizes today's copy-count gate
  to the whole site.
- **voice-lint gate**: banned-pattern list (bragging/superlatives: "we were first",
  "merged upstream" counts, "certified", named third parties per no-names rule,
  unverifiable stats) + required-pattern list (disclaimer on every page, collaboration
  clause form). Plain-text rules file so it's auditable and extendable.
- **freshness**: when checks/defects/coverage/features change, the gates go RED until
  the site reflects them — the site cannot silently lag the product.
- **security gate**: asserts `public/_headers` ships CSP + X-Content-Type-Options +
  X-Frame-Options + Referrer-Policy on all routes; greps pages/functions for unescaped
  `innerHTML`/`document.write` sinks (the 2026-06-30 XSS lesson, now enforced); no
  third-party scripts/CDNs (self-contained pages only).
- **redirect gate**: /tool→/check and /guide→/docs 301s asserted.
- **web-unit + web-browser** (existing) stay required; browser suite extended with the
  redesign's behavioral tests below.
A human-parts checklist (visual pass, screenshots) lives in `docs/SITE-CHECKLIST.md`,
referenced by the gate output — machine checks what it can, names what it can't.

## TDD process (spec → failing tests → implement → green)
The redesign is built test-first. BEFORE touching any page:
1. Write the site test spec: every requirement in this design becomes an executable
   assertion — unified nav byte-identical on all 6 pages; exactly one primary CTA per
   page with the agreed label; depth-ladder sections present on /check in order;
   disclaimers on every page; OG/Twitter meta on every page; numbers match live
   coverage JSON; banned/required voice patterns; redirects; sandbox loads 6 cases;
   /check form runs; 320px no-horizontal-scroll; analytics beacons fire.
2. Run the suite → new tests FAIL (red) against the current site.
3. Implement pages until the full suite is GREEN, without weakening a test.
4. The tests then live permanently in the site-governance lane (step above), so every
   future edit re-proves the whole checklist.

## Security & reliability
- Keep/extend `public/_headers` CSP (from the XSS fix) to all pages incl. new /docs;
  add the missing standard headers if absent (nosniff, frame-deny, referrer).
- All escaping through the existing esc() discipline; the security gate greps for raw
  sinks. No inline third-party resources; pages remain fully self-contained.
- Functions untouched this project; their rate-limiting/validation unchanged.
- Reliability: pages are static on Cloudflare Pages (inherently HA); preview-branch
  deploys before every production promote; rollback = redeploy previous commit.

## Guardrails
- No changes to the conformance engine, coverage gates, or `/api/*` functions.
- Analytics beacons preserved (home_view + agent/sandbox events); add page_view beacons
  to /check and /docs so the funnel is measurable.
- The `web-browser` responsive CI gate must pass; test at 320px.

## Error handling / edge cases
- `/tool` deep links (old emails/bookmarks) → 301 to /check (no dead ends).
- Users with saved reports in KV: backend data untouched; public UI for it removed.
  (If anyone asks, reports remain retrievable via admin.)
- External links (dev.to posts, HN comment) point at /, /check, /sandbox — all
  preserved URLs; only /tool and /guide change, both redirected.

## Testing & rollout (TDD order)
1. Build the site-governance lane + all redesign assertions FIRST; confirm they FAIL
   red against the current site (proves the tests test something).
2. Implement pages until `python3 conformance/ci/run_suite.py` is fully GREEN
   (web-unit, web-browser, site-claims, voice-lint, security, redirects, and the
   existing agent-governance copy gates — updating gate-pinned phrases the redesign
   legitimately rewrites, never deleting an assertion to pass).
3. Also re-audit ALL existing copy (agent.html, sandbox.html, coverage.html, docs)
   against the voice/truthfulness law — the gates enforce the mechanical part; one
   human read for tone.
4. Manual pass on all 6 pages (visual, 320px, dark hero) per docs/SITE-CHECKLIST.md.
5. Deploy to a PREVIEW branch (`npx wrangler pages deploy public
   --project-name=ucp-conformance --branch=redesign-preview`) → redirect checks via
   curl on the preview → Vishal reviews the preview URL → only then promote to
   production (deploy --branch=main).

## Out of scope
Ground-up visual identity (approach C), the signature-validator page (separate,
already-planned feature), engine/coverage work, admin dashboard changes.

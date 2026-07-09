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

## The site requirements register — what "100% TDD" means, mechanically
The site gets the SAME discipline as the conformance suite: a requirements register with
enforced traceability. `conformance/web/site_requirements.json` lists every site
requirement as a row: `{id: "SITE-R-001", requirement, page(s), test_id, status}`.
Rules, enforced by a new **site-tdd gate**:
- Every SITE-R row MUST name ≥1 test that exists in the site test suite → else RED.
- Every site test MUST cite a SITE-R id → orphan tests flagged (keeps the register the
  single source of truth).
- The register is ADD-ONLY (like coverage_lock): removing/weakening a row needs a
  reasoned entry in `site_requirements_retired.json` → silent test deletion impossible.
- "100% TDD" = this gate reports `requirements: N, tested: N, coverage: 100%` — a
  NUMBER the gate computes, not a claim. Anything less is RED.
Bootstrap: every requirement in this design doc becomes a SITE-R row before any
implementation (the initial register IS the executable form of this spec).

## Gate mechanics — analyzed in detail (inputs, algorithm, failure output, false-positives)
**site-claims** — IN: built HTML of all pages + `public/site_claims.json` +
coverage/agent-coverage JSON. ALGO: strip tags/styles/scripts → extract candidate
claims = (a) any number adjacent to a claim noun (check/defect/coverage/MUST/%/spec
version/store/agent), (b) any sentence containing proof-words (proven, validated,
every, all, zero/0, only, first). Each candidate must match one of: [LIVE] rendered
from data (element carries `data-live="coverage.json:$.path"` — gate verifies the
path exists and any static fallback text equals the live value), [REG] registered in
site_claims.json with {text, page, evidence-URL/file, added, review-by}, or [BAN]
neither → RED with page+line+text. FALSE-POSITIVES: sizes/dates/HTTP codes excluded
by allowlist patterns (px, seconds, 2026-, HTTP \d{3}); anything else unregistered
fails LOUD by design — registering a benign claim takes 30 seconds, missing a false
one costs credibility. Register entries have a `review-by` date; expired → RED
(claims re-verify on a cadence, not once).
**voice-lint** — IN: same page text + `conformance/web/voice_rules.json` (auditable
rules file). ALGO: banned regexes (superlatives "first/best/only/leading" outside
registered evidence claims; achievement patterns "we found \d+", "merged upstream",
"certified", "endorsed"; third-party company names per the no-names rule except
"UCP"/"Google's UCP team" in the About attribution); required patterns per page
(the unofficial-disclaimer sentence; ≥1 "you/your"-subject CTA above the fold).
Output names the exact rule id + text. Rules file changes are reviewed like code.
**site-freshness** — two classes, explicit: CLASS-AUTO (anything that exists in
coverage/agent-coverage JSON MUST be data-rendered — hardcoding it is a gate failure;
these update themselves when the product updates). CLASS-GATED (prose facts — new
capability names, feature lists): the freshness gate compares a manifest of product
facts (check count, defect count, versions, capability list — regenerated from the
engine) against `site_claims.json` review dates; product-manifest change with no site
review → RED. So: numbers can never lag; prose can lag at most one CI run.
**security** — IN: public/_headers + all HTML/JS + functions/. ALGO: assert headers
(CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy) cover `/*`; grep AST-
lite for `.innerHTML=`/`document.write(`/`insertAdjacentHTML(` where the right-hand
side isn't wrapped in the esc()/known-safe builder → RED with file:line; assert zero
external script/font/CSS origins; assert no secrets patterns in public/.
**redirects** — parse `public/_redirects`; assert `/tool /check 301` and
`/guide /docs 301` rows; browser test follows each and asserts final URL + 200.
**web-browser (extended)** — existing 12 responsive checks + the redesign behaviors:
nav byte-identical across pages (literal string compare of the nav block), one
`.btn-primary` per page above the fold, /check ladder section order, sandbox loads 6
cases, pages render WITHOUT coverage.json (graceful degradation: fetch mocked to fail
→ no blank page, fallback text shows), zero console errors on load of every page.

## Reliability — defined and tested (not assumed)
Reliability here means: static pages on Cloudflare Pages (inherently HA) + graceful
degradation + safe rollout. Tested: (1) every page renders meaningfully if its JSON
fetch fails (browser test above); (2) /api/track failures are swallowed (already
.catch(()=>{}) — web-unit asserts it); (3) preview-branch deploy before every prod
promote; prod rollback = redeploy prior commit (documented in SITE-CHECKLIST.md);
(4) the functions' error paths keep their existing web-unit coverage.

## The enhancement workflow — how EVERY future change flows (the holistic loop)
Documented in docs/SITE-CHECKLIST.md and enforced by the gates; no step is honor-system
except the two marked HUMAN:
1. Any product change (new checks/features/versions) → coverage JSONs + product
   manifest regenerate → CLASS-AUTO numbers on the site update themselves; freshness
   gate goes RED if prose/claims need a look → forced review.
2. Any site change → author FIRST adds/updates SITE-R rows + tests (site-tdd gate
   enforces: no untested requirement, no orphan test) → implements → full
   run_suite (site-governance lane: tdd, claims, voice, freshness, security,
   redirects, web-unit, web-browser) must be GREEN.
3. HUMAN: visual pass per SITE-CHECKLIST.md; tone read (gates catch the mechanical
   voice violations; a human confirms the spirit).
4. Deploy preview branch → verify → promote to prod. Push to main with a RED site
   lane is blocked by CI like any engine change.
This is the same loop the conformance suite itself lives under — register, gates,
ratchet-like add-only locks — applied to the website.

## Phase 0 deliverable — full audit of the CURRENT site (R1, explicit)
Before any redesign code: a page-by-page claims inventory of the site AS IT IS TODAY —
every factual claim extracted, verified against evidence, classified
(keep-as-LIVE / register-with-evidence / rewrite / remove). Output = the initial
`site_claims.json` + an audit note (`ops/site-content-audit-2026-07.md`) listing
anything found untruthful/bragging with its fix. The audit IS the bootstrap of the
claims register — one artifact, two purposes.

## Site-governance lane — summary (mechanics above are normative)
One new lane in run_suite, triggered by any public/**, functions/**, or coverage-data
change: **site-tdd** (register traceability = the 100%-TDD number) → **site-claims** →
**voice-lint** → **site-freshness** → **security** → **redirects** → **web-unit** →
**web-browser (extended)**. Any RED blocks the push like an engine change. The two
HUMAN-only steps (visual pass, tone read) live in docs/SITE-CHECKLIST.md, which the
lane prints a pointer to on success. TDD build order for THIS redesign: SITE-R register
+ all tests written first → confirmed RED against the current site → implement to
GREEN → tests live on permanently. The concrete assertions include: unified nav
byte-identical on all 6 pages; exactly one primary CTA per page; /check ladder order;
disclaimers everywhere; OG/Twitter meta everywhere; CLASS-AUTO numbers data-rendered;
redirects; sandbox 6 cases; graceful degradation; 320px; zero console errors; beacons.

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

# spck.dev Website IA Redesign + Site Governance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure spck.dev (retire /tool, hook-first home, merchant depth-ladder, unified nav) under a new machine-enforced site-governance lane (100% TDD traceability, claims register, voice lint, security, freshness).

**Architecture:** Static pages in `public/` deployed to Cloudflare Pages; a new `conformance/ci/site_gates.py` (mirroring `web_gates.py`) provides the site-governance gates, registered in `conformance/ci/run_suite.py`; new browser assertions live beside the existing puppeteer suites in `tests/web/browser/`; registers/rules live in `conformance/web/`.

**Tech Stack:** Plain HTML/CSS/JS (self-contained pages), Python gates (stdlib only), node:test + puppeteer-core (existing harness), Cloudflare Pages `_redirects`/`_headers`.

## Global Constraints (from the spec — every task inherits these)
- Voice law: value-first "you can do X"; no achievement counts/superlatives; one passive collaboration clause max; disclaimer on every page: the sentence must contain "independent" + "unofficial" + "not affiliated" + "official" (+"authoritative").
- Every factual claim: [LIVE] `data-live` rendered from coverage JSON, [REG] in `public/site_claims.json` with evidence + review-by, or removed.
- Numbers that exist in coverage/agent-coverage JSON MUST be data-rendered (hardcoding = gate failure). Static fallback text inside a `data-live` element must equal the live value at gate time.
- No third-party scripts/fonts/CSS. All escaping via existing `esc()` discipline.
- Nav (byte-identical block on all 6 pages): spck · Merchants(/check) · Agents(/agent) · Coverage(/coverage) · Docs(/docs) · GitHub↗. No "Launch Tool" anywhere.
- TDD order enforced: tests/gates land and FAIL RED against the current site before page edits.
- `python3 conformance/ci/run_suite.py` must end GREEN before the preview deploy; never weaken/delete an assertion to pass (register is add-only).
- Deploy: preview branch first (`--branch=redesign-preview`); production promote only after Vishal approves the preview URL.

## File Structure
- `ops/site-content-audit-2026-07.md` — Phase-0 audit (new)
- `public/site_claims.json` — claims register (new)
- `conformance/web/site_requirements.json` — SITE-R register (new)
- `conformance/web/site_requirements_retired.json` — retirements, starts empty (new)
- `conformance/web/voice_rules.json` — voice lint rules (new)
- `conformance/ci/site_gates.py` — gates: tdd|claims|voice|security|redirects|freshness (new)
- `tests/web/browser/site_smoke.mjs` — redesign behavioral assertions (new)
- `public/_redirects` — /tool→/check, /guide→/docs (new)
- `public/index.html`, `check.html`, `agent.html`, `sandbox.html`, `coverage.html` — modify
- `public/docs.html` — new (from guide.html); `public/tool.html`, `public/guide.html` — delete
- `docs/SITE-CHECKLIST.md` — human checklist (new)
- `conformance/ci/run_suite.py` — register site-governance lane (modify)

---

### Task 1: Phase-0 content audit → bootstrap `site_claims.json`
**Files:** Create `ops/site-content-audit-2026-07.md`, `public/site_claims.json`.
**Produces:** claims register consumed by Task 3's gate. Schema per entry:
`{"id":"CLAIM-001","text":"<verbatim>","page":"index.html","class":"REG|LIVE|REWRITE|REMOVE","evidence":"<url-or-repo-path>","added":"2026-07-09","review_by":"2026-10-09"}`.

- [ ] Extract visible text of all 7 current pages (strip style/script/tags), list every sentence containing a number or proof-word (proven|validated|every|all|zero|only|first|no false).
- [ ] For each: verify against evidence (coverage JSONs, run_suite gates, repo docs); classify LIVE (number exists in coverage JSON → must become data-rendered), REG (register with evidence), REWRITE (violates voice law), REMOVE (unverifiable).
- [ ] Write the audit doc (table: page | claim | class | evidence | action) and `public/site_claims.json` (REG entries + planned LIVE bindings).
- [ ] Commit: `git commit -m "site: phase-0 content audit + claims register bootstrap"`

### Task 2: SITE-R register + site-tdd gate
**Files:** Create `conformance/web/site_requirements.json`, `conformance/web/site_requirements_retired.json`, `conformance/ci/site_gates.py` (tdd mode). Test: the gate run itself (below).
**Produces:** `python3 conformance/ci/site_gates.py tdd` → exit 0 only when every SITE-R names ≥1 existing test AND every site test cites a SITE-R. Test ids = mjs test names (`// SITE-R-xxx` comment convention) or gate modes (`gate:claims` etc.).

- [ ] Write `site_requirements.json`: one row per spec requirement. Initial set (ids stable):
  R-001 unified nav byte-identical on 6 pages → test `site_smoke:nav-identical`
  R-002 no "Launch Tool" text anywhere → `gate:voice` (banned pattern)
  R-003 exactly one .btn-primary above the fold per page → `site_smoke:primary-cta`
  R-004 /check ladder order instant→CLI→CI → `site_smoke:ladder-order`
  R-005 disclaimer sentence on every page → `gate:voice` (required pattern)
  R-006 OG+Twitter meta on every page → `site_smoke:social-meta`
  R-007 CLASS-AUTO numbers data-rendered & equal to JSON → `gate:claims`
  R-008 no unregistered factual claims → `gate:claims`
  R-009 voice banned patterns absent → `gate:voice`
  R-010 /tool→/check + /guide→/docs 301 → `gate:redirects` + `site_smoke:redirects`
  R-011 security headers + no raw sinks + no external origins → `gate:security`
  R-012 sandbox loads 6 demo cases → `site_smoke:sandbox-cases`
  R-013 pages render if coverage fetch fails → `site_smoke:graceful-degrade`
  R-014 zero console errors on load → `site_smoke:console-clean`
  R-015 320px no horizontal overflow → existing `responsive_smoke` (cite it)
  R-016 beacons: home_view + check/docs page_view → `site_smoke:beacons`
  R-017 product-manifest change forces site review → `gate:freshness`
  R-018 hook-first homepage (badge+H1 hook+2 CTAs above fold) → `site_smoke:home-hook`
- [ ] Implement `site_gates.py` skeleton + `tdd` mode: load register; collect declared test ids from `tests/web/browser/site_smoke.mjs` (`grep "// SITE-R-"`), `responsive_smoke.mjs`, and gate modes; report `requirements N, tested M, coverage X%`; exit 1 unless 100%; verify retired file untouched additions-only vs git HEAD.
- [ ] Run `python3 conformance/ci/site_gates.py tdd` → expect FAIL (site_smoke.mjs doesn't exist yet) — RED confirmed.
- [ ] Commit: `"site: SITE-R register + tdd traceability gate (RED: tests pending)"`

### Task 3: site-claims gate (`claims` mode)
**Files:** Modify `conformance/ci/site_gates.py`.
**Algorithm (from spec):** strip tags/styles/scripts from each public/*.html; candidates = numbers adjacent to claim nouns (check|defect|coverage|MUST|%|version|store|agent|failure) + sentences with proof-words; exclusions: `\d+px`, `\d+ seconds?`, `2026-\d\d`, `HTTP \d{3}`, years in footers. Each candidate must be inside a `data-live="<file>:<jsonpath>"` element whose fallback text equals the resolved JSON value, or match a REG entry (`text` substring, page match, review_by ≥ today). Else print `page:line: text` and exit 1.
- [ ] Implement; include `--explain` flag printing every candidate + classification (debuggability).
- [ ] Run against CURRENT site → expect RED (hardcoded 42/193 stats etc.) — confirms the gate bites.
- [ ] Commit: `"site: claims gate (RED against current site by design)"`

### Task 4: voice-lint gate (`voice` mode) + `voice_rules.json`
**Files:** Create `conformance/web/voice_rules.json`; modify `site_gates.py`.
- [ ] Rules file: `banned`: `(?i)launch tool`, `(?i)\bcertified\b`, `(?i)endorsed by (?!.*not)`, `(?i)we (were|are) (the )?(first|only|best|leading)`, `(?i)merged upstream`, `(?i)\b(we found|we caught) \d+`, `(?i)\b99%`, third-party names list (per no-names rule; allow "UCP","Google's UCP team" only in the About attribution block marked `data-attribution`); `required_per_page`: disclaimer regex `(?i)independent.*unofficial.*not affiliated` (or the exact sentence), ≥1 `you|your` CTA above fold on /, /check, /agent.
- [ ] Implement mode (page text extraction shared with claims mode); failure prints rule id + page + text.
- [ ] Run → current site: expect RED (tool.html "Launch Tool" nav on index/agent). Commit: `"site: voice-lint gate + rules (RED by design)"`

### Task 5: security + redirects gates (`security`, `redirects` modes)
**Files:** Modify `site_gates.py`; create `public/_redirects`.
- [ ] `_redirects` content:
  ```
  /tool  /check  301
  /guide /docs  301
  ```
- [ ] `security` mode: parse `public/_headers` — require nosniff, X-Frame-Options DENY, Referrer-Policy, CSP with default-src 'self' covering `/*`; scan public/*.html + functions/ for `innerHTML\s*=`, `document.write(`, `insertAdjacentHTML(` where RHS line lacks `esc(`/known-safe builder → fail with file:line; assert no `https?://` script/link/font src outside self/data: (allowlist: none).
- [ ] `redirects` mode: assert `_redirects` has exactly those two rows; assert no page links to /tool or /guide.
- [ ] Run security (expect GREEN — headers exist; sinks were esc()'d in the XSS fix; verify) and redirects (expect RED — index/agent still link /tool, /guide links exist). Commit.

### Task 6: freshness gate (`freshness` mode)
**Files:** Modify `site_gates.py`.
- [ ] Build product manifest at gate-time from source of truth: `{merchant_checks: len(merchant register CHECKs from coverage.json totals), agent_checks, agent_defects, versions[]}` — read `public/coverage.json` + `public/agent-coverage.json` + `len(DEFECTS)-1` via `python3 -c` import (same technique as agent_governance copy gate).
- [ ] Compare to `site_claims.json` meta block `{"manifest": {...}, "reviewed": "date"}`: manifest drift with reviewed < today-0 → RED with message "product changed: re-review site claims (update manifest+reviewed after review)".
- [ ] Run → GREEN after writing current manifest into the claims file (Task 1 amend). Commit.

### Task 7: `site_smoke.mjs` — the redesign's behavioral tests (written BEFORE page edits)
**Files:** Create `tests/web/browser/site_smoke.mjs`. Pattern: copy the puppeteer boot/serve conventions from `responsive_smoke.mjs` (CHROME_PATH, BASE :8189, domcontentloaded+settle). Each test block tagged `// SITE-R-xxx`.
- [ ] Assertions (all six pages = index, check, agent, sandbox, coverage, docs):
  - nav-identical: extract `<nav class="site-nav">…</nav>` innerHTML from each page; all 6 strings strictly equal; contains links /check,/agent,/coverage,/docs and no /tool. (R-001)
  - primary-cta: exactly one `.btn-primary` with boundingRect.top < 812 per page. (R-003)
  - ladder-order: on /check, `[data-ladder-step]` values === ["instant","cli","ci"] in DOM order. (R-004)
  - social-meta: `meta[property="og:title"]` + `meta[name="twitter:card"]` present per page. (R-006)
  - sandbox-cases: /sandbox `.case-btn` count ≥ 6 after settle. (R-012)
  - graceful-degrade: reload / with `page.setRequestInterception` aborting `*coverage*.json` → body text still contains the H1 hook, no blank main. (R-013)
  - console-clean: collect console 'error' events on each page load → none. (R-014)
  - beacons: intercept `/api/track` → recorded events include home_view (on /), check_view (on /check), docs_view (on /docs). (R-016)
  - home-hook: on /, above fold: `.badge` text contains "reliability", `h1` contains "passes conformance", exactly 2 hero CTAs linking /check + /agent. (R-018)
  - redirects (file-level done by gate; browser-level here): GET /tool with `redirect:'manual'` via fetch in page → 301/308 to /check (skip gracefully if the static server doesn't honor _redirects locally — assert file rows instead; preview curl covers the live 301). (R-010)
- [ ] Register in `web_gates.py` browser() (it globs tests/web/browser/*.mjs already? verify; if it runs a fixed list, add site_smoke.mjs).
- [ ] Run browser gate → site_smoke FAILS against current site (no site-nav class, no data-ladder-step, docs.html missing…) — RED confirmed, screenshot the failure list into the commit message.
- [ ] Run `site_gates.py tdd` → now 100% (all SITE-R ids cited) GREEN on traceability while claims/voice/redirects/browser are RED. Commit: `"site: behavioral test suite (RED against current site — TDD baseline)"`

### Task 8: shared nav/footer + homepage rework (implement to green, part 1)
**Files:** Modify `public/index.html`.
**Nav block (byte-identical everywhere, adapt palette per page via existing classes):**
  ```html
  <nav class="site-nav"><a class="brand" href="/"><span class="mk">S</span>spck</a>
  <div class="links"><a href="/check">Merchants</a><a href="/agent">Agents</a>
  <a href="/coverage">Coverage</a><a href="/docs">Docs</a>
  <a href="https://github.com/vishkaty/ucp-conformance" rel="noopener">GitHub ↗</a></div></nav>
  ```
**Footer block:** disclaimer sentence (exact): "spck.dev is an independent, unofficial project — not affiliated with, endorsed by, or a substitute for the official UCP conformance suite; the official suite is authoritative." + links (GitHub · PyPI · Coverage · ucp.dev).
- [ ] Replace index nav + hero: badge "CONFORMANCE ≠ RELIABILITY"; H1 "Your UCP checkout passes conformance. Does it actually work?"; sub (one-liner, value-first); CTAs `<a class="btn-primary" href="/check">Check your store →</a> <a class="btn-secondary" href="/agent">Test your agent →</a>`; stat strip: three `data-live` stats — merchant checks `coverage.json:$.versions['2026-04-08'].check` style path (use the real JSON shape), agent checks `agent-coverage.json:$['2026-04-08'].check`… wait agent CHECKS count comes from demo/agent registry not coverage rows — bind to the real sources identified in Task 1; "0 false greens" as REG claim with evidence=kill-rate gate.
- [ ] Keep sections: problem (shape vs behavior), fork cards (demoted), proof (kill-rate + one passive collaboration clause), sandbox teaser, About+disclaimer footer.
- [ ] Re-run browser gate: home-hook, nav (index) assertions pass; commit.

### Task 9: /check depth ladder
**Files:** Modify `public/check.html`.
- [ ] Add shared nav/footer; wrap existing instant-check form as `<section data-ladder-step="instant">`; add `<section data-ladder-step="cli">` (pip install + --init + run commands, "the full test incl. checkout/cart/order write-paths — run only against your own store") and `<section data-ladder-step="ci">` (Action snippet); kill-rate note + Coverage link; add check_view beacon; OG meta exists — keep.
- [ ] Browser gate ladder-order + beacons(check) pass. Commit.

### Task 10: /agent + /sandbox + /coverage adopt shared chrome
**Files:** Modify `public/agent.html`, `public/sandbox.html`, `public/coverage.html`.
- [ ] Swap navs for the shared block; ensure ONE .btn-primary each (agent→/sandbox "Watch it live"; sandbox→its primary run CTA; coverage → none primary? R-003 says exactly one per page — give coverage a primary "Check your store →" footer CTA); add OG meta to sandbox/coverage; ensure numbers on agent/sandbox pages are data-live or REG (from Task 1 audit); footer disclaimer everywhere. Commit per page.

### Task 11: /docs (from guide) + retire /tool
**Files:** Create `public/docs.html` (rework of guide.html: quickstart = install / CLI / --agent / CI Action / what's checked / versions / links); delete `public/tool.html`, `public/guide.html`; add docs_view beacon.
- [ ] Grep all pages+functions for tool.html/guide.html references → update to /check catalog docs links etc.
- [ ] Note in commit: KV reports/auth backend untouched (admin still served via functions).
- [ ] redirects gate now GREEN (no /tool `/guide` links; _redirects rows present). Commit.

### Task 12: claims/voice green + lane wiring + checklist
**Files:** Modify `conformance/ci/run_suite.py` (add gates after web-browser row, same tuple pattern: `("site-tdd", _py(... "site_gates.py","tdd"), None, ())` etc. ×6); create `docs/SITE-CHECKLIST.md` (human steps: visual pass all pages 375/320+desktop, tone read, screenshots, preview→prod promote + rollback cmd).
- [ ] Iterate pages/claims file until claims + voice modes GREEN (register leftover REG claims with evidence; convert stragglers to data-live).
- [ ] `python3 conformance/ci/run_suite.py` → FULL GREEN (existing + 6 new gates + extended browser).
- [ ] `bash packaging/sync_bundle.sh` if any conformance/ file consumed by bundle changed (site_gates not bundled — verify). Commit: `"site: governance lane wired — full suite green"`

### Task 13: preview deploy + verification + handoff
- [ ] `npx wrangler pages deploy public --project-name=ucp-conformance --branch=redesign-preview --commit-dirty=true`
- [ ] `curl -sI https://redesign-preview.ucp-conformance.pages.dev/tool` → 301 → /check; same for /guide→/docs; spot-check all 6 pages + OG tags via curl.
- [ ] Run SITE-CHECKLIST human items against the preview; hand Vishal the preview URL for approval. **Production promote only on his OK** (`--branch=main`), then re-verify live, update ledger/memory.

## Self-review (done)
- Spec coverage: every spec section maps to a task (audit→T1, register/tdd→T2, gate mechanics→T3-6, TDD-first→T2/3/4/7 explicitly RED before T8-11 edits, pages→T8-11, lane+checklist→T12, rollout→T13). ✔
- No placeholders; exact paths; commands with expected RED/GREEN outcomes. ✔
- Type/name consistency: gate modes tdd|claims|voice|security|redirects|freshness; nav class `site-nav`; ladder attr `data-ladder-step`; live attr `data-live`. ✔

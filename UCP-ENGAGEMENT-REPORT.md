# UCP Ecosystem — Engagement & Contribution Report

**Contributor:** `vishkaty` (Vishal Katyal)
**Report date:** 2026-08-11
**Activity window:** 2026-07-03 → 2026-08-11 (~5.5 weeks)
**Scope:** Universal Commerce Protocol org (`ucp`, `conformance`, `samples`, `python-sdk`, `js-sdk`, `ucp-schema`) plus the adjacent Agent Payments Protocol (`google-agentic-commerce/AP2`).

---

## 1. Executive summary

| Metric | Count |
|---|---|
| Pull requests filed (all repos) | **55** |
| — Merged | **31** |
| — Open (in review) | **20** |
| — Closed unmerged | 4 |
| Issues / findings filed | **27** (14 open, 13 closed) |
| Spec-design reviews on others' proposals | **7** threads (4 substantive) |
| Repos where contributing | 7 |

**Leaderboard standing (merged PRs, humans):**

| Repo | Rank | Merged | Top of board |
|---|---|---|---|
| **samples** | **#1 human** (#2 incl. dependabot bot) | 14 | vishkaty 14, damaz91 9 |
| **conformance** | **#2** | 6 | damaz91 8, **vishkaty 6**, nearlyforget 5 |
| **python-sdk** | **#3** | 4 | damaz91 8, nearlyforget 6, **vishkaty 4** |
| **js-sdk** | #4 (of 8) | 4 | nearlyforget 6, damaz91 5, vishkaty 4 |
| **ucp** (spec) | #20 (of 50) | 3 | wry-ry 48, igrigorik 45 (core/Google) |
| **ucp-schema** | contributor (2 open PRs, 3 issues) | 0 yet | igrigorik 12 |
| **AP2** | 2 security PRs open (queue frozen 3mo) | 0 yet | holtskinner/zeroasterisk 7 |

**Bottom line:** top-3 on three of the core implementation repos (samples, conformance, python-sdk) — the stated goal, reached on the repos where merit-by-volume is achievable. On the flagship `ucp` spec repo, where the top ranks are core/Google maintainers, the strategy is deliberately **presence-by-judgment** (design-conversation reviews) rather than volume — and that is landing (see §4).

---

## 2. What the work is about (themes)

The contributions cluster into a coherent, defensible lane rather than scattered fixes:

1. **RFC 9421 message signatures** — request signing/verification on both reference servers, webhook signing, DER-vs-raw encoding correctness, algorithm/key-pairing constraints.
2. **Conformance & differential testing** — new conformance modules, over-strict/unsound check fixes, dual-oracle (Rust vs Python) and differential (Python vs Node) validation.
3. **SDK codegen fidelity** — enforcing JSON-Schema constraints (propertyNames, minProperties, uniqueItems, contains) that the generators silently drop, across both js-sdk (zod) and python-sdk (pydantic).
4. **Reference-server correctness** — server-side authority (checkout id, currency), signature verification, webhook delivery, discovery headers.
5. **Spec correctness** — schema/doc fixes and structural findings, plus cryptographic-mandate correctness in AP2.

---

## 3. Merged contributions (31 PRs)

### ucp — spec (3 merged)
- [#590](https://github.com/Universal-Commerce-Protocol/ucp/pull/590) docs(signatures): make example signature values raw `r||s` as the spec requires
- [#562](https://github.com/Universal-Commerce-Protocol/ucp/pull/562) docs(overview): fix "Capabilities Incompatible" examples to use `capabilities_incompatible`
- [#561](https://github.com/Universal-Commerce-Protocol/ucp/pull/561) docs(fulfillment): correct `options[].total` row to match the schema (totals array)

### conformance — official test suite (6 merged)
- [#73](https://github.com/Universal-Commerce-Protocol/conformance/pull/73) order-event webhook structural conformance module
- [#68](https://github.com/Universal-Commerce-Protocol/conformance/pull/68) fulfillment structural conformance module
- [#64](https://github.com/Universal-Commerce-Protocol/conformance/pull/64) totals integrity conformance module
- [#62](https://github.com/Universal-Commerce-Protocol/conformance/pull/62) discount capability conformance module
- [#59](https://github.com/Universal-Commerce-Protocol/conformance/pull/59) accept the spec's in-band error model for business-level failures
- [#58](https://github.com/Universal-Commerce-Protocol/conformance/pull/58) drive `protocol_test` version/capability asserts from `conformance_input`

### samples — reference servers (14 merged)
- [#168](https://github.com/Universal-Commerce-Protocol/samples/pull/168) rest/nodejs: Cache-Control on discovery + payment_handlers in checkout envelope
- [#167](https://github.com/Universal-Commerce-Protocol/samples/pull/167) rest/python: assign checkout id server-side (stop trusting the request)
- [#162](https://github.com/Universal-Commerce-Protocol/samples/pull/162) rest/nodejs: verify request signatures per RFC 9421
- [#156](https://github.com/Universal-Commerce-Protocol/samples/pull/156) rest/python: determine currency server-side
- [#153](https://github.com/Universal-Commerce-Protocol/samples/pull/153) rest/python: serve discovery profile with Cache-Control
- [#152](https://github.com/Universal-Commerce-Protocol/samples/pull/152) rest/python: seed valid totals on checkout create
- [#146](https://github.com/Universal-Commerce-Protocol/samples/pull/146) rest/nodejs: deliver order object as webhook body
- [#140](https://github.com/Universal-Commerce-Protocol/samples/pull/140) rest/python: deliver order object as webhook body
- [#138](https://github.com/Universal-Commerce-Protocol/samples/pull/138) test(nodejs): checkout behavioral test suite
- [#132](https://github.com/Universal-Commerce-Protocol/samples/pull/132) rest: emit discount totals[] entry as a negative amount
- [#128](https://github.com/Universal-Commerce-Protocol/samples/pull/128) rest/python: match discount codes case-insensitively
- [#122](https://github.com/Universal-Commerce-Protocol/samples/pull/122) rest/python: verify request signatures per RFC 9421
- [#120](https://github.com/Universal-Commerce-Protocol/samples/pull/120) ci: Python server integration-test + happy-path workflow
- [#117](https://github.com/Universal-Commerce-Protocol/samples/pull/117) rest/python: stop null-padding unset optional fields

### python-sdk (4 merged)
- [#66](https://github.com/Universal-Commerce-Protocol/python-sdk/pull/66) enforce propertyNames on extra-allow generated models
- [#62](https://github.com/Universal-Commerce-Protocol/python-sdk/pull/62) ci: verify committed models match regeneration
- [#57](https://github.com/Universal-Commerce-Protocol/python-sdk/pull/57) enforce array contains/minContains/maxContains
- [#55](https://github.com/Universal-Commerce-Protocol/python-sdk/pull/55) enforce minProperties on generated models

### js-sdk (4 merged)
- [#39](https://github.com/Universal-Commerce-Protocol/js-sdk/pull/39) ci: run generated-model tests + verify regeneration freshness
- [#38](https://github.com/Universal-Commerce-Protocol/js-sdk/pull/38) derive the UCP response envelope + registries from source
- [#37](https://github.com/Universal-Commerce-Protocol/js-sdk/pull/37) enforce uniqueItems + contains/minContains/maxContains
- [#34](https://github.com/Universal-Commerce-Protocol/js-sdk/pull/34) enforce JSON Schema value constraints in generated zod schemas

---

## 4. Impact highlights (credibility, not just count)

- **Our review shaped a maintainer's spec work, publicly credited.** On [ucp#692](https://github.com/Universal-Commerce-Protocol/ucp/issues/692) (igrigorik, "negotiate payment terms as a checkout selection"), all four of our review points landed — including a **new `payment_term_changed` warning code we proposed, which the author adopted** — and igrigorik credited the review publicly as "great (sharp eyes) feedback." (Status precision: #692 merged into the `payment-terms` feature-branch chain; the base-to-main PR [#602](https://github.com/Universal-Commerce-Protocol/ucp/pull/602) is still open, so the code is adopted-in-branch, not yet on `main` or in a release.)
- **First-on-thread reviews of foundational proposals** by a Stripe engineer ([ucp#630](https://github.com/Universal-Commerce-Protocol/ucp/issues/630), well-known signals) and a maintainer ([ucp#557](https://github.com/Universal-Commerce-Protocol/ucp/issues/557), amount-representation consolidation) — each comment-free for weeks, each answered with wire-demonstrable schema/codegen findings.
- **Find-and-fix loop closed repeatedly:** many filed issues were resolved by our own subsequent PRs (e.g. the DER-signature finding [ucp#569] → fix [ucp#590]; unsigned-verify [samples#121] → [samples#122]; null-serialization [samples#115] → [samples#117]; zod constraint-drop [js-sdk#33] → [js-sdk#34]).
- **Two verified security fixes in AP2** ([#329](https://github.com/google-agentic-commerce/AP2/pull/329) payment-instrument extension-drop fail-open; [#330](https://github.com/google-agentic-commerce/AP2/pull/330) mandate closed-binding skipped by default), both independently adversarially reviewed.

---

## 5. Issues & findings filed (27)

A substantial share of the value is **precise, reproducible findings** — often in security/signatures/schema territory — filed as issues where a correct fix needed a maintainer decision. Open highlights:

- **Signatures / crypto:** [ucp#676](https://github.com/Universal-Commerce-Protocol/ucp/issues/676) keys[] rename breaks cross-version signature verification; [ucp#571](https://github.com/Universal-Commerce-Protocol/ucp/issues/571) ap2_mandate requires ES512 but only ES256/384 defined; [ucp#599](https://github.com/Universal-Commerce-Protocol/ucp/issues/599) checkout_mandate pattern rejects the AP2 SDK's serialization.
- **Auth / safety:** [ucp#667](https://github.com/Universal-Commerce-Protocol/ucp/issues/667) delegated-IdP trust-chain gaps; [ucp#666](https://github.com/Universal-Commerce-Protocol/ucp/issues/666) permalink CSRF exposure; [ucp#678](https://github.com/Universal-Commerce-Protocol/ucp/issues/678) buyer-consent overlay reopens a locked object.
- **Protocol soundness:** [ucp#665](https://github.com/Universal-Commerce-Protocol/ucp/issues/665) empty actions map passes despite MUST-omit; [ucp#664](https://github.com/Universal-Commerce-Protocol/ucp/issues/664) idempotency raw-body-SHA unworkable over MCP.
- **ucp-schema oracle bugs:** [#43](https://github.com/Universal-Commerce-Protocol/ucp-schema/issues/43)/[#45](https://github.com/Universal-Commerce-Protocol/ucp-schema/issues/45)/[#46](https://github.com/Universal-Commerce-Protocol/ucp-schema/issues/46) self-root `$ref` bundling + recursive-ref stack overflow.

(Full itemized list in the Appendix.)

---

## 6. Spec-design engagement (reviews on others' work)

This is the presence lane on the flagship spec repo — reviewing others' proposals with reproduced, in-lane findings:

| Thread | Author | Our contribution | Outcome |
|---|---|---|---|
| [ucp#692](https://github.com/Universal-Commerce-Protocol/ucp/issues/692) | igrigorik (maintainer) | 4 points; proposed `payment_term_changed` code | **Merged**, credited "sharp eyes" |
| [ucp#557](https://github.com/Universal-Commerce-Protocol/ucp/issues/557) | kmcduffie (maintainer) | 3 schema/codegen points (value:number float risk, oneOf voids sign-refinements, versioned-cutover vs union) | Posted, first-on-thread |
| [ucp#630](https://github.com/Universal-Commerce-Protocol/ucp/issues/630) | prasad-stripe (Stripe) | 4 points on signals extensibility/provenance/testability | Posted, first-on-thread |
| [ucp#698](https://github.com/Universal-Commerce-Protocol/ucp/issues/698) | sakinaroufid | 4 RFC 9421 response-binding composition questions | Posted, first-on-thread |
| ucp#636, #618, #413 | others | lighter clarifying input | concluded / maintainer-directed |

---

## 7. Quality & methodology (why the acceptance rate holds)

Every contribution runs through a disciplined gate before filing:

- **Is it real?** Reproduce from a fresh clone against upstream `main` at filing time (not a pinned copy); adversarial convention review; search open+closed issues/PRs to avoid duplicates.
- **TDD.** Failing test first (watch it fail for the right reason) → fix → kill-test the test (mutate the fix away, confirm red).
- **Sweep the class.** Treat any finding as a defect *class*, grep the whole repo, ship an accounting table (converted vs out-of-scope with reason).
- **Every CI path.** Run the repo's actual workflows locally (pytest, the pinned pre-commit, super-linter/Biome, cspell) — a green PR page on a fork can mean "not run yet."
- **Independent adversarial review.** A separate model-diverse pass sees only the issue + diff and hunts for holes, over-strictness, new security exposure, and duplicate/collision risk — before anything is filed.

This discipline has repeatedly prevented credibility-damaging errors (a would-be duplicate in AP2 that a co-contributor had explicitly declined; a false spec claim caught in a PR body; codegen false-accepts; a changed-file spellcheck gate).

---

## 8. Currently in-flight (20 open PRs)

- **Approved, awaiting merge:** conformance #76/#77/#78/#79, samples #169, ucp-schema #44 (all reviewed/approved).
- **Awaiting first review:** ucp docs/schema batch (#625, #658–#662, #669, #670), js-sdk #40, ucp-schema #47.
- **AP2 (frozen queue):** #329, #330 (security fixes), #311 (lint unblock), #307 (SD-JWT golden vectors).

**Held in reserve:** a signature-conformance-vectors Enhancement Proposal (drafted, adversarially reviewed) pending engagement timing; conformance coverage for newly-settled spec shapes.

---

## Appendix A — All pull requests (55)

**Open (20):** ucp #670 #669 #662 #661 #660 #659 #658 #625 · conformance #79 #78 #77 #76 · samples #169 · js-sdk #40 · ucp-schema #47 #44 · AP2 #330 #329 #311 #307
**Merged (31):** ucp #590 #562 #561 · conformance #73 #68 #64 #62 #59 #58 · samples #168 #167 #162 #156 #153 #152 #146 #140 #138 #132 #128 #122 #120 #117 · python-sdk #66 #62 #57 #55 · js-sdk #39 #38 #37 #34
**Closed unmerged (4):** ucp #623 #568 · conformance #48 · samples #141

## Appendix B — All issues (27)

**Open (14):** ucp #678 #676 #667 #666 #665 #664 #599 #571 · samples #163 #134 · ucp-schema #46 #45 #43 · AP2 #303
**Closed (13):** ucp #663 #570 #569 #560 #559 · samples #136 #135 #133 #121 #118 #115 · js-sdk #33 · ucp-schema #40
*(Many closed issues were resolved by our own subsequent PRs — a find-then-fix pattern rather than rejections.)*

---

*Identity note: all UCP-ecosystem work is filed under `vishkaty` (`katyal.vishal@gmail.com`), CLA-verified, no AI co-author trailers. Data pulled live from the GitHub API on 2026-08-11.*

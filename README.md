# spck-conformance — a UCP conformance checker you can trust

[![PyPI](https://img.shields.io/pypi/v/spck-conformance)](https://pypi.org/project/spck-conformance/)
[![CI](https://github.com/vishkaty/ucp-conformance/actions/workflows/conformance.yml/badge.svg)](https://github.com/vishkaty/ucp-conformance/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Point it at any **Universal Commerce Protocol (UCP)** server — from the browser, `pip`,
or your CI — and get an honest, capability-scoped conformance report. Every check is
**proven to catch the bug it's for**, so a green result means something.

> **Independent, unofficial project.** Not affiliated with, endorsed by, or a substitute
> for the official UCP conformance suite. It reports only the checks it actually runs and
> never claims "certified."

**→ Try it in your browser: [spck.dev/check](https://spck.dev/check)**

## Two sides of the checkout

A UCP checkout takes two systems, and we test both:

- **Merchant platforms** — you expose a UCP agentic interface over your catalog, cart,
  payments, and checkout orchestration. The suite below verifies AI shopping agents can
  actually discover and buy from you. **228 kill-rate-validated checks.**
- **Shopping agents** — you build a shopping-orchestration AI that carts and checks out
  across merchant platforms over UCP. The **agent lane** (`conformance/agent/`) grades your
  agent's *own* behavior — OAuth mix-up / PKCE, request signing, refusing mismatched totals,
  phishing defense, revoke-on-unlink — the reliability and security bugs a schema check can't
  see. **44 checks (45 defects modeled); watch six fail live at [spck.dev/sandbox](https://spck.dev/sandbox).**

> Conformance is not reliability: ~99% of UCP stores pass conformance, yet real agent
> checkouts still fail. Run it yourself: `python3 conformance/agent/run_agent.py`.

Merchant-platform quick start below; agent-lane details in [`conformance/agent/`](conformance/agent/).

## Quick start

```bash
pip install spck-conformance

# scaffold a config tailored to your server's declared capabilities
spck-conformance --server https://api.example.com --init merchant.json

# run the full suite (deviations show expected requirement vs your actual response)
spck-conformance --server https://api.example.com --config merchant.json
```

In CI (fails the build on any MUST deviation, writes a JUnit report):

```yaml
- uses: vishkaty/ucp-conformance@v0.4.0
  with:
    server: https://api.example.com
    config: merchant.json   # optional
```

Or paste a URL at **[spck.dev/check](https://spck.dev/check)** for an instant, no-install
discovery + profile check — the result URL is shareable and you get an embeddable badge:

```markdown
[![UCP conformance](https://spck.dev/api/badge?server=YOUR_STORE)](https://spck.dev/check?server=YOUR_STORE)
```

## Why you can trust a pass

A conformance checker that can silently pass a broken server is worse than none. So every
check is validated three ways, each anchored to something we didn't write:

1. **Kill-rate testing.** For each check we inject the exact defect it should catch (drop a
   required field, flip a status code, corrupt the body). If the check still passes, it's a
   false-pass hazard and it's **blocked from release**. A check ships only if it catches
   100% of its mutations *and* passes cleanly on a known-good server.
2. **The official `ucp-schema` validator as the oracle** — no hand-rolled schema logic.
3. **Verbatim spec citations** — every check traces to a specific normative clause, and a
   `register-completeness` gate proves the citation set is *complete*: every mandatory
   keyword in the pinned prose is a tracked requirement, so nothing normative is silently
   missed from the denominator.
4. **Differential testing against an implementation we didn't write.** The suite is run
   against the independent official Flower Shop server; a check that passes our own fixture
   but flags an independently-conformant target is caught, so a check can't quietly encode a
   fixture-specific misreading.

The whole suite **self-validates in CI** and goes red if any check loses its ability to
catch defects.

## What it checks

Capability-adaptive across **REST and MCP** transports: discovery + profile-schema,
checkout lifecycle (incl. escalation / `continue_url`), order retrieval + adjustments,
validation/errors, idempotency, payment (handlers, credentials, AP2 mandates),
discounts + consent, catalog (search / lookup / get_product / pagination), cart +
cart-to-checkout conversion, fulfillment, eligibility signals, totals invariants,
**RFC 9421 request/response + webhook signatures**, **OAuth 2.0 + PKCE identity-linking**,
**order-event webhooks**, and **HTTPS/TLS transport**. Unsupported capabilities are
`not-applicable`; missing config is `not-tested` — never a silent pass.

Supports spec versions **2026-04-08**, **2026-01-23**, and **2026-01-11**, each with its
own reference-validated controlled golden. See
[docs/merchant-conformance.md](docs/merchant-conformance.md) and the machine-enforced
[coverage matrix](docs/spec-coverage-matrix.md).

## Coverage — live, accounted, ratcheted

**[spck.dev/coverage](https://spck.dev/coverage)** shows every normative MUST in each
pinned spec version as a kill-rate-validated **check**, a documented **exemption**, or an
open **gap** — each requirement deep-linked to the pinned official spec line. The data
([public/coverage.json](public/coverage.json)) is regenerated by
`conformance/coverage/matrix.py` and enforced by a CI gate: stale data, a coverage
regression (ratchet), or a wrong check-count claim on the site fails the build.

Currently **228 kill-rate-validated checks** account for **98% of 2026-04-08**, **89% of
2026-01-23**, and **89% of 2026-01-11** normative MUSTs (check + documented exemption).
The denominator itself is now gated: a `register-completeness` CI gate reconciles **every**
mandatory keyword in the pinned prose against the register, so the percentage is a fraction
of a *proven-complete* set of requirements, not an assumed one. The remaining gap is a
categorized residue — documented spec bugs we won't fake, client/platform-bound obligations,
and a needs-receiver / MCP-A2A-transport tail we haven't built a harness for yet (see
[WORKSTREAMS.md](WORKSTREAMS.md)).

## How it stays honest

The repo ships its own test-of-the-tests: a self-validating CI harness
([`conformance/ci/run_suite.py`](conformance/ci/run_suite.py)) brings up a known-good
reference server and requires every check to be clean-pass **and** kill-safe before it can
grade a real merchant. Confirmed spec/reference ambiguities are documented in
[`conformance/AMBIGUITIES.md`](conformance/AMBIGUITIES.md) rather than silently passed.

**Tests don't disappear.** Pinned spec versions are immutable, so a check that correctly
tests one of their MUSTs is permanent — a CI gate (`coverage-lock`) fails the build if any
covered requirement silently loses its test. A test may only be retired for a
spec-grounded reason (unsound check / superseded / spec defect), recorded and reviewable
in [`conformance/coverage/retirements.json`](conformance/coverage/retirements.json). The
full policy: [docs/TEST-INTEGRITY.md](docs/TEST-INTEGRITY.md).

## Releasing (rc first — the sequence as executed for 0.4.0)

Releases are cut from green `main` only, and a pre-release always goes first. Exactly as run
for 0.4.0 (rc1 on 2026-09-10, final on 2026-09-11):

1. `bash packaging/release_rc.sh --rc 1` bumps the version to `X.Y.Zrc1`, runs the release
   guards and preflight, and prints the tag line. Commit the bump, then
   `git tag vX.Y.Zrc1 && git push origin vX.Y.Zrc1`. `release.yml` re-runs
   `packaging/release_guards.sh` (tag == pyproject == `__version__`; the tagged commit is an
   ancestor of `main`; a completed-success `selftest` check-run exists for that SHA; a
   CHANGELOG entry; the wheel bundles every spec version) and publishes the pre-release to
   PyPI via OIDC trusted publishing.
2. Clean-venv acceptance of the published rc against golden-0825 (`boot_golden_0825`, port
   8197): `python3 -m venv /tmp/v && /tmp/v/bin/pip install spck-conformance==X.Y.Zrc1 &&
   /tmp/v/bin/spck-conformance --server http://localhost:8197 --json` must report
   `verdict.deviations == 0`.
3. Record `acceptance: green <date> <sha> (…)` under the `## X.Y.Zrc1` heading in
   `packaging/CHANGELOG.md` (add-only) and commit it.
4. `bash packaging/release_rc.sh --final` — refused until step 3 is recorded — bumps to
   `X.Y.Z`; commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`; `release.yml` publishes
   the final release under the same guards.
5. Verify PyPI lists `X.Y.Z` as latest and that a clean `pip install spck-conformance`
   resolves it with every spec version bundled.

The check-run guard accepts *any* completed-success `selftest` run for the tagged SHA: the
tag push itself starts a newer run, and that in-progress run must never mask the green one
(`packaging/check_run_verdict.py`, kill-tested by `packaging/test_release_guards.sh`). The
site deploy (`packaging/deploy.sh`, step 4) reads the history the same way.

## Links

- **Web check:** https://spck.dev/check
- **Live coverage matrix:** https://spck.dev/coverage · [Workstreams](WORKSTREAMS.md) · [Architecture](ARCHITECTURE.md) · [Principles](PRINCIPLES.md) · [Decisions](DECISIONS.md)
- **PyPI:** https://pypi.org/project/spck-conformance/
- **Methodology, coverage & ambiguities:** [docs/](docs/) · [conformance/](conformance/)
- **UCP spec (official):** https://ucp.dev
- **Awesome UCP** — a curated list of UCP resources: https://github.com/vishkaty/awesome-ucp

MIT licensed.

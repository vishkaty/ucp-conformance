# Launch content — ready to post

Honest, specific, and deferential to the official UCP project. Everything below is
true and reproducible with the tool. Pick the channel; the copy is drafted.

---

## A) Blog post / dev.to / LinkedIn article

**Title:** We built a self-testing UCP conformance checker — and found deviations in the reference implementations

**Subtitle:** A conformance tool that can't catch bugs is worse than none. So we made ours prove it catches them — then pointed it at the official samples.

### The problem

The Universal Commerce Protocol (UCP) is young and moving fast. If you're implementing
a UCP server — as a merchant, or a platform integrating merchants — you need to know
one thing before you ship: **is it actually conformant?** A tool that answers "yes"
when the truth is "no" is worse than no tool at all.

That failure mode is common. Most checkers do something like "got a 200, looks fine."
A check that never fails when the server is broken is just decoration. So we built the
tool around a single rule:

> **No check ships until we've proven it fails when the server is wrong.**

### How we make the checks trustworthy

Every check is validated three ways, each anchored to something we didn't write:

1. **Kill-rate testing.** For each check we inject the specific defect it's meant to
   catch (drop a required field, flip a status code, corrupt the body). If the check
   still passes, it's a false-pass hazard and it's blocked from release. A check only
   ships if it catches 100% of its own mutations *and* passes cleanly on a known-good
   server.
2. **The official schema validator as the oracle.** We don't hand-roll JSON-Schema
   logic (a classic source of divergence) — we shell out to the official `ucp-schema`
   validator. Payloads are anchored to the spec's own schemas.
3. **Verbatim spec citations.** Every check traces to a specific normative clause,
   quoted from the pinned spec. No "trust us."

And the whole suite tests *itself* on every change: a self-validating CI harness that
goes red if any check loses its ability to catch defects.

### What it found

Pointed at real implementations, it flagged genuine issues — and, importantly, it can
show its work (expected requirement vs the server's actual response):

- **The official Node.js reference sample** serves `capabilities` as a JSON **array**
  and `services.<name>` as an **object**, where the pinned 2026 profile schema
  (`ucp.json`) requires a keyed **object** and an **array** respectively. Both the
  Python reference server and a real production Shopify store use the spec-correct
  shapes — so this is a real deviation, not a spec ambiguity.
- **Genuine spec/reference gaps**, which we document rather than silently pass:
  the reference server doesn't implement case-insensitive discount codes (DSC-003);
  its error bodies use `{detail, code}` rather than the spec's
  `{type, code, content, severity}` envelope (ERR-001); and version-negotiation maps
  to HTTP 422 in the spec vs 400 in the official suite.

None of this is a dunk on the UCP project — the spec is excellent and the samples are
genuinely useful. It's exactly the kind of drift a conformance tool exists to surface.

### Try it in 30 seconds

```bash
pip install spck-conformance
spck-conformance --server https://your-store.example.com --init merchant.json
spck-conformance --server https://your-store.example.com --config merchant.json
```

Or paste a URL at **https://spck.dev/check** for an instant discovery + profile check.
Or drop it in CI:

```yaml
- uses: vishkaty/ucp-conformance@main
  with: { server: https://your-store.example.com }
```

37+ kill-rate-validated checks across discovery, checkout, order, discount, catalog,
cart, totals — over both REST and MCP transports. It's an unofficial, independent
project; it reports only the checks it actually runs, and it never claims
"certified."

> Source & methodology: https://github.com/vishkaty/ucp-conformance

---

## B) Show HN

**Title:** Show HN: A self-testing UCP conformance checker that proves its checks catch bugs

**Body:**

I built an unofficial conformance checker for the Universal Commerce Protocol (UCP).
The thing I cared about most: a conformance tool that can false-pass is worse than
nothing, so every check has to *prove* it fails when the server is wrong.

Each check is kill-rate tested (I inject the exact defect it should catch; if it still
passes, it's blocked from release), schema-anchored to the official `ucp-schema`
validator, and traced to a verbatim spec clause. The whole suite self-validates in CI
and goes red if any check loses its ability to catch defects.

Pointing it at the reference implementations turned up real deviations — e.g. the
official Node.js sample serves `capabilities` as an array and `services` as an object
where the profile schema requires the opposite shapes (the Python sample and a real
Shopify store use the correct forms).

- pip: `pip install spck-conformance`
- web: https://spck.dev/check (paste a store URL)
- CI: a GitHub Action
- code: https://github.com/vishkaty/ucp-conformance

It's capability-adaptive (only runs checks for what the server declares), reports
"not-tested" honestly instead of silently passing, and never claims "certified." Happy
to answer questions about the kill-rate methodology or the findings.

---

## C) Short social (X / LinkedIn post)

Building a UCP server? Before you ship, check it's actually conformant.

`pip install spck-conformance` → point it at your store → get an honest, capability-scoped
report that shows *expected vs your actual response* for anything broken.

Every check is kill-rate-tested (proven to catch the bug it's for). It found real
deviations in the official reference samples.

Free, unofficial, open: https://spck.dev/check

---

## D) Posting checklist

- [ ] Post the blog version on dev.to + your own site; cross-post to LinkedIn.
- [ ] Show HN on a weekday morning (US); reply to every comment for the first few hours.
- [ ] Share the short social on X/LinkedIn, tag the UCP/agentic-commerce conversation.
- [ ] Lead with the *findings*, not the tool — the bugs are the hook.
- [ ] Stay humble and deferential to the official project in every version.
- [ ] Add your conformance badge to the repo README before posting (dogfood the loop).

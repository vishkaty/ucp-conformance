# AP2 mandate testbed (EXPERIMENTAL — interop lane)

An independent, community-built harness for standing up the AP2 mandate flow
end-to-end (user → agent → merchant) and checking it. **Unofficial. Not affiliated
with or endorsed by the AP2/UCP maintainers. Not a certification.**

This lane is deliberately kept **separate from the locked merchant conformance
suite** (the 193-check register) and does **not** contribute to that denominator.
AP2 mandates are a delegate-SD-JWT chain defined by a **moving IETF draft**
(`draft-gco-oauth-delegate-sd-jwt-00`), so this lane follows the interop discipline
of W3C Web Platform Tests and the QUIC interop matrix rather than issuing conformance
verdicts on a spec that isn't frozen.

## Two vocabularies — the core rule

| Layer | What it binds to | What we say |
|---|---|---|
| **frozen** | RFC 9901 (SD-JWT), RFC 8785 (JCS), RFC 7515 (JWS) — settled standards, our OWN code, anchored to published test vectors | a **conformance check** (definite pass / definite reject / inconclusive) |
| **moving** | the delegate-chain semantics of the draft | an **interop observation** vs the pinned reference — "agrees / diverges @ commit on draft-NN, as of DATE" — **never a verdict on anyone's implementation** |

A stamped, dated interop observation ages into a historical fact. A bare verdict on
a moving draft ages into an error under our name. We only ever publish the former.

## The reference is a fixture, not a subject under test

The moving-layer oracle is the official reference implementation
(`google-agentic-commerce/AP2`), **pinned by commit** (see `provenance.py`). It is
used as a fixture. Its own behavior — including any of its self-failing tests — is
**never rendered as a defect** by this testbed. If a discrepancy in the reference is
worth raising, it is raised upstream, privately, as a question — never surfaced as a
public scoreboard cell.

## Fail closed

When our independent code and the reference (or the RFC vectors) disagree on a
frozen computation, that is a bug in *our* code to fix — the self-test fails loudly.
In any future artifact that tests a third party's implementation, a leg-disagreement
must render **inconclusive**, never a false "fail" against a correct implementation.

## Provenance

Every output carries the test basis (draft id + pinned reference commit + frozen
standards) via `provenance.basis_banner()`. Goldens carry the `ref_sha` they were
generated at. Re-running against a new draft/commit produces a **new dated run**, not
a retroactive edit of old results.

## Never (the hard "don't" list)

- No public pass/fail dashboard that names or grades third-party implementations.
- No "% AP2-conformant" score, badge, or seal on the moving layer.
- No red/defect rendering of the reference implementation's behavior.
- No output without draft + commit + date provenance.
- The words "certified", "official", "authoritative" — nowhere.
- No surfacing of the doc-vs-code (`+kb` vs `~~`) gap or reference test gaps as a
  "gotcha" ahead of a humble, question-framed upstream contribution.

## Files

- `provenance.py` — the pinned test basis + the banner every output prints.
- `sdjwt.py` (in `common/`) — our independent frozen-layer codec.
- `gen_goldens.py` — drives the pinned reference to emit golden chains (maintainer step).
- `frozen.py` — frozen-layer verifier + tamper mutators (always runs, our code only).
- `semantic.py` — reference-backed interop cases (runs when the pinned reference is installed).

Gates: `sdjwt-vs-reference` (frozen-layer byte-parity vs the reference + RFC 9901
§5.1 vector) and `ap2-e2e` (frozen tamper cases always; reference-backed interop
cases when available).

# speclint — grading the spec against itself

`speclint` is an offline, read-only lane that lints the **SHA-pinned vendored UCP
spec** (`conformance/.vendor`) for places where two machine artifacts that MUST
agree mechanically disagree. It never touches the network or `main`, and it never
files anything: a finding is a *candidate* for the manual five-gate upstream-filing
protocol (`ops/filing-protocol.md`), verified against current upstream before any
report.

It is deliberately a sibling of `checks/`, **outside** the `checks/*.py` +
`selfcheck/*.py` glob that `coverage/matrix.py` scans, so it can never perturb the
193-check merchant denominator. (`validate_speclint.py` asserts this stays true.)

## Design invariant: zero false positives

A linter that cries wolf destroys the credibility the whole project exists to build.
So every rule here must be **structural and unambiguous** — no inferential
example→schema binding, no "this example is probably a complete instance of schema
X." A rule ships only when it fires on a real, independently hand-verified
contradiction AND stays silent on a consistent-but-different input, both proven by
`validate_speclint.py`.

## Rules

### SPL-PARITY-IDEM — cross-transport header-requirement parity  *(shipped)*

For every operation present in both transports, compares which **comparable**
headers (ones both transports model: `Idempotency-Key`, `UCP-Agent`) each marks
required. REST models them as OpenAPI header parameters; MCP models them as `meta`
fields. Transport-specific headers (`Request-Id`, `Content-Type`/`Accept*`, and the
auth/signing headers) are on an explicit allowlist and never compared.

At pin `a2d8bf0b` (re-verified on current main `63be476`) this fires on exactly four
operations — `create_checkout`, `update_checkout`, `create_cart`, `update_cart` —
where REST requires `Idempotency-Key` but MCP's `meta.required` omits it, even
though MCP *does* require it on `complete`/`cancel`. The transports disagree on the
retry-safety guarantee of create/update. Zero false positives by construction: it is
a pure structural comparison of two contracts, no example-binding heuristic.

Run: `python3 conformance/speclint/speclint.py report`

## Why there is no example-validator (yet)

The tempting "render `{{ macros }}`, then JSON-Schema-validate every fenced JSON
example against its schema" rule was prototyped and **rejected for the MVP** after
empirical testing against the real spec + the `ucp-schema` oracle:

1. **Doc examples are legitimately elided.** The identity-linking profile example is
   shown as only its `ucp.capabilities` fragment. Validating it against the full
   discovery-profile schema fires on missing `version`/`services`/`payment_handlers`
   — an elision artifact, not a defect. Naive whole-example validation false-fires on
   essentially every partial example in the docs.
2. **The oracle is operation/annotation-oriented, not a generic root validator.**
   `validate` requires an `--op` shape; a capability/entity container has none, so
   there is no clean "validate this entity envelope against its `$id`" path. A
   generic JSON-Schema validator would be needed, which the stdlib-only +
   single-oracle-authority constraints rule out.
3. The one clean finding such a rule would have caught — an identity-linking example
   using `"version": "Working Draft"` against the `^\d{4}-\d{2}-\d{2}$` pattern — was
   **already fixed upstream** by the time we verified against current main.

A credible example-validator therefore needs a reliable "this fragment is a complete
instance of schema X" signal that the current docs do not carry. Until that exists,
shipping one would mean shipping false positives — the opposite of the point. Tracked
as a follow-up in `ops/speclint-build-plan-2026-07-13.md`.

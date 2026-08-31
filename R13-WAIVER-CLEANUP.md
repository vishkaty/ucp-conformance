# R13-WAIVER-CLEANUP — completeness-matcher fix, full-register sweep

Per the R13 brief (GAP-LEDGER-0825): `verify_register_completeness.py`'s per-line
substring matcher could not mark a keyword line covered when a register row's `"..."`
quote elision landed MID physical line (proven on IDL-012/030/050). Fixed in
`conformance/selfcheck/verify_register_completeness.py` (`flatten_lines()` +
rewritten `covered_lines_for()`): matching now happens against the whole file's
flattened, line-boundary-agnostic text, so a line is covered whenever a row's quote
fragment's matched span OVERLAPS that line — still exact quote-CONTENT matching (no
proximity window), just unbound from physical line wrapping. Kill-tested in
`conformance/selfcheck/validate_completeness_matcher.py` (wired into `run_suite.py` as
gate `completeness-matcher`): the fix is proven against the three real known cases AND
a frozen copy of the old algorithm is proven to still reproduce the historical miss on
those same three cases (so this gate would have caught the original bug, not merely
observed it fixed), plus a synthetic hermetic positive and a class-negative against
over-matching.

This lane does **not** bulk-edit `conformance/coverage/register_completeness_waivers.json`
(shared with concurrent lanes — see `WAIVERS-PROPOSED.md`'s precedent). One single,
necessary addition WAS applied directly (see "Addition applied" below, needed to keep
the 2026-04-08 GATE green — a pre-existing gap, unrelated to the R13 bug itself, that
the pre-fix matcher happened to mask by accident; see reasoning below). Every other
change below is a **proposal**, not applied, for whoever reviews/merges this lane.

## Before / after, full register sweep

Method: `conformance/selfcheck/verify_register_completeness.py --json`, run before
this lane's matcher fix (old algorithm, pre-existing waivers file) and after (fixed
algorithm, current waivers file including the one addition below). All four spec
versions swept; `verify_register.py` (quote-verbatim gate, independent of this matcher)
stays 1701/1701 throughout — this fix changes only which LINE a verbatim quote is
credited to, never whether the quote itself is real.

| version | total kw | covered (before→after) | waived (before→after) | scoped | missed (before→after) |
|---|---|---|---|---|---|
| 2026-01-11 | 201 | 94 → 96 | 17 → 15 | 90 | 0 → 0 |
| 2026-01-23 | 204 | 97 → 97 | 15 → 15 | 92 | 0 → 0 |
| 2026-04-08 | 478 | 227 → 235 | 78 → 70 | 173 | 0 → 0 |
| 2026-08-25 | 922 | 509 → 527 | 20 → 16 | 95 | 298 → 284 |

08-25 stays REPORT MODE throughout (flip-by 2026-09-20, `spec_versions.REPORT_MODE_UNTIL`)
so its 284 remaining misses do not gate; **298 → 284 is a legitimate 14-line drop from
the matcher fix alone**, not from any row/waiver work (P2b's own extraction is separate
and already landed on main before this lane branched).

## Full per-line diff (every line whose bucket changed)

Four transition classes, computed by re-running the OLD algorithm against the
pre-lane waivers file and the FIXED algorithm against the current one, then diffing
every (version, file, line) key's classification (`covered` / `waived` / `scoped` /
`missed`):

### 1. `missed → covered` (14 lines, 2026-08-25 only) — the fix's direct yield

Real keyword occurrences the old per-line matcher could not credit to ANY row (not
covered, not waived — genuinely absent from both the numerator and the accounted
set) that the fixed flattened matcher now correctly credits to an existing row's
verbatim quote. Nothing added or edited to produce these — pure matcher accuracy:

```
docs/specification/common/extensions/loyalty.md:225
docs/specification/common/extensions/loyalty.md:231
docs/specification/common/location/lookup.md:65
docs/specification/common/location/search.md:183
docs/specification/common/location/search.md:258
docs/specification/overview/index.md:869
docs/specification/overview/index.md:954
docs/specification/overview/index.md:2198
docs/specification/payment/extensions/actions/device-data-collection.md:106
docs/specification/payment/extensions/actions/three-ds-challenge.md:218
docs/specification/payment/extensions/authentication.md:196
docs/specification/payment/extensions/split-payments.md:231
docs/specification/shopping/extensions/buyer-consent.md:395
docs/specification/signatures.md:415
```

### 2. `waived → covered` (15 lines) — now-redundant duplicate-class waivers

**PROPOSED REMOVAL, not applied.** Every one of these 15 waiver entries is class
`duplicate`; the fixed matcher now covers their target line DIRECTLY via the row's own
quote (the same row the waiver's `duplicate_of` already pointed at, or — for the 4
2026-08-25 identity-linking entries — the exact IDL-012/030/050 rows the R13 bug
report named). Leaving them in place is HARMLESS (the gate does not fail on a stale
waiver, only reports it — see `verify_register_completeness.py`'s `stale_waivers`
list, which is exactly this set), but they no longer do any work and a future spec
edit that moves these lines would carry dead waiver entries silently. Ready to remove
whenever the shared waivers file is next safely editable (paste-ready `jq` selector:
match on `(.version, .file, .line)` below).

| version | file | line | class | duplicate_of |
|---|---|---|---|---|
| 2026-01-11 | docs/specification/ap2-mandates.md | 107 | duplicate | PAY-022 |
| 2026-01-11 | docs/specification/order.md | 331 | duplicate | ORD-015 |
| 2026-04-08 | docs/specification/discount.md | 200 | duplicate | DSC-015 |
| 2026-04-08 | docs/specification/discount.md | 228 | duplicate | DSC-017 |
| 2026-04-08 | docs/specification/identity-linking.md | 143 | duplicate | IDL-012 |
| 2026-04-08 | docs/specification/identity-linking.md | 416 | duplicate | IDL-040 |
| 2026-04-08 | docs/specification/identity-linking.md | 428 | duplicate | IDL-042 |
| 2026-04-08 | docs/specification/identity-linking.md | 491 | duplicate | IDL-045 |
| 2026-04-08 | docs/specification/identity-linking.md | 581 | duplicate | IDL-050 |
| 2026-04-08 | docs/specification/identity-linking.md | 583 | duplicate | IDL-050 |
| 2026-04-08 | docs/specification/signatures.md | 178 | duplicate | SIG-010 |
| 2026-08-25 | docs/specification/common/identity-linking/index.md | 175 | duplicate | IDL-012 |
| 2026-08-25 | docs/specification/common/identity-linking/index.md | 278 | duplicate | IDL-030 |
| 2026-08-25 | docs/specification/common/identity-linking/index.md | 979 | duplicate | IDL-050 |
| 2026-08-25 | docs/specification/common/identity-linking/index.md | 981 | duplicate | IDL-050 |

Note the last 4: these were an EXPLICIT stopgap for the exact R13 bug (IDL-012/030/050's
own mid-line elisions at the 08-25 pin) — i.e. someone already "waived around" the bug
this lane was asked to fix properly instead. They are the cleanest possible confirmation
that the real fix works: the workaround they patched over is now provably unnecessary.

### 3. `covered → waived` (1 line, 2026-04-08) — addition APPLIED, not merely proposed

```
docs/specification/identity-linking.md:589   "Businesses **MUST**\n  return `iss` in
    every authorization response."
```

This is a DIFFERENT failure class from the R13 elision bug — a pre-existing gap in the
human-curated waiver set, unrelated to elisions, that the OLD matcher's looser
per-line containment check (`len(nl) >= 12 and nl in nf`) happened to mask by
accident: line 589's normalized text is just `"businesses must"` (16 chars, short and
generic), which was a substring of SIX unrelated, far-away rows' fragments (IDL-021,
IDL-023, IDL-036, IDL-049, IDL-050, IDL-055), so the old algorithm marked it "covered"
by coincidence, not by any row actually quoting it. The fixed matcher searches
fragment-into-fulltext with exact positional overlap (not line-into-fragment), which
structurally eliminates this false-positive class — and correctly reveals that NO row
quotes line 589 verbatim.

The line sits in the same "Security Considerations" recap bullet as three SIBLING
lines that already carry duplicate-class waivers for the exact same reason
(L586 → IDL-012, L593 → IDL-026, both already in the waivers file) — L589 was simply
the one line in that bullet nobody had reason to waive, because the bug hid it. Since
2026-04-08 is GATE mode (not report mode), leaving this unaccounted would have
regressed the build the moment the R13 fix landed. Applied directly (see
`conformance/coverage/register_completeness_waivers.json`, one appended entry,
`duplicate_of: IDL-018` — IDL-018 at L168-169 is "MUST return the iss parameter in the
authorization response", the row L589 restates): a single, isolated, fully-reasoned
addition, distinct from the bulk "propose only" removals above. `register-completeness`
gate: 2026-04-08 missed 0 → 1 (mid-fix, transiently) → 0 (after this addition).

### 4. `covered → missed` (1 line, 2026-08-25) — same class as #3, left unwaived

```
docs/specification/common/identity-linking/index.md:987   "Businesses **MUST**\n
    return `iss` in every authorization response." — the 08-25 twin of item 3, same
    "Mix-Up Attack prevention" bullet, same false-positive-masking mechanism.
```

2026-08-25 is REPORT MODE (flip-by 2026-09-20), so this does not gate the build and no
waiver was added. **Proposed** (not applied) for whoever does the P2b/L2 census
extraction pass: a duplicate-class waiver, `duplicate_of` = the 08-25 register's
equivalent of IDL-018 (verify the exact row id at the 08-25 pin — the identity-linking
row numbering may not be 1:1 with 04-08's after the #723 reorg + new surface rows).

## Net result

- `register-completeness` gate: **GREEN at all four versions** (2026-01-11,
  2026-01-23, 2026-04-08 gate-mode PASS with 0 missed each; 2026-08-25 report-mode,
  284 unaccounted, unchanged gating posture).
- `verify_register.py`: **1701/1701**, unchanged (this fix only reassigns which line a
  verbatim quote credits, never whether a quote is real).
- New gate `completeness-matcher` (wired into `run_suite.py`) proves the fix AND that
  it would have caught the original bug (frozen old-algorithm mutant).
- 15 duplicate-class waivers proposed for removal (harmless if left; genuinely dead).
- 1 duplicate-class waiver proposed for addition (2026-08-25 L987, report-mode, not
  urgent).
- 1 duplicate-class waiver added (2026-04-08 L589, gate-mode, was urgent — applied).

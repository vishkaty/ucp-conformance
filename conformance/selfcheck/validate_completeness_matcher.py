#!/usr/bin/env python3
"""
validate_completeness_matcher.py — kill-test for the R13 completeness-matcher fix.

verify_register_completeness.py's covered_lines_for() proves a keyword line is
"covered" only when a register row's own verbatim quote text sits on it. Before this
fix, the matcher worked PER PHYSICAL LINE: a fragment had to sit wholly on one line,
or a line's full text had to sit wholly inside the fragment. That breaks the moment a
row's "..." quote elision lands MID physical line — the elided cut can fall inside
what the vendored spec renders as a single wrapped line (a prose sentence continues
past where the row's quote resumes after "..."), so neither containment direction
holds even though the row's quote is genuinely verbatim over the span it covers.

Proven register-wide-real cases (GAP-LEDGER-0825 R13): IDL-012, IDL-030, IDL-050 in
conformance/requirements/2026-08-25/identity-linking.json, each citing
docs/specification/common/identity-linking/index.md. This gate:

  1. POSITIVE CONTROL — reproduces the three known cases against the REAL vendored
     spec + REAL register rows and proves the FIXED matcher (imported from
     verify_register_completeness) covers every mandatory-keyword line inside each
     row's cited span.
  2. REGRESSION KILL-TEST — re-implements the OLD per-line algorithm inline (frozen
     here, never imported, so it can't silently start tracking future matcher
     changes) and proves it MISSES at least one of those same lines for each case —
     i.e. this gate would have caught the R13 bug, not merely observed it fixed.
  3. CLASS NEGATIVE — a small synthetic fixture with a normal, non-elided quote and a
     lookalike short phrase placed elsewhere in the same file proves the flattened,
     line-boundary-agnostic matcher does not over-match: the unrelated line stays
     uncovered.
  4. SYNTHETIC POSITIVE — a minimal, hermetic (no vendor-tree dependency) fixture
     reproducing the mid-line-elision shape directly, so the fix is also proven
     without depending on the vendored tree's exact current text.

Hermetic except step 1, which reads the already-required vendored tree and committed
register rows (no network).

Run:  python3 conformance/selfcheck/validate_completeness_matcher.py
Exit 0 = the fix holds and the regression is provably caught; 1 = a failure.
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from verify_register_completeness import (   # noqa: E402
    covered_lines_for, scan_keywords, norm, parse_source,
)

VENDOR = ROOT / "conformance" / ".vendor" / "ucp-2026-08-25"
REGISTER_FILE = (ROOT / "conformance" / "requirements" / "2026-08-25"
                  / "identity-linking.json")
SPEC_REL = "docs/specification/common/identity-linking/index.md"

KNOWN_CASES = ("IDL-012", "IDL-030", "IDL-050")


def _old_covered_lines_for(rows, flines):
    """FROZEN copy of the pre-R13 per-line algorithm — the known-bad mutant this gate
    proves the real matcher no longer matches behavior with, on the three known cases.
    Never imported from production code; a deliberate, static snapshot of the bug."""
    covered = set()
    nfile_lines = [norm(x) for x in flines]
    for row in rows:
        _, _, cited = parse_source(row.get("source", ""))
        for L in cited:
            covered.add(L)
        for frag in re.split(r"\.\.\.|…", row.get("quote", "")):
            nf = norm(frag)
            if len(nf) < 8:
                continue
            for idx, nl in enumerate(nfile_lines, start=1):
                if nf in nl or (len(nl) >= 12 and nl in nf):
                    covered.add(idx)
    return covered


def _load_rows():
    data = json.loads(REGISTER_FILE.read_text())
    by_id = {r["id"]: r for r in data.get("rows", []) if r["id"] in KNOWN_CASES}
    missing = set(KNOWN_CASES) - set(by_id)
    if missing:
        raise AssertionError(f"known cases not found in {REGISTER_FILE}: {sorted(missing)}")
    return by_id


def _keyword_lines_in_span(occ, lo, hi):
    return {lineno for (lineno, _kw, _raw) in occ if lo <= lineno <= hi}


def check_known_cases():
    """Steps 1 + 2 above."""
    failures = []
    spec_path = VENDOR / SPEC_REL
    if not spec_path.is_file():
        return [f"vendored spec not found at {spec_path} — run conformance/ci/fetch_sources.sh"]

    rows_by_id = _load_rows()
    occ, flines = scan_keywords(spec_path)

    for case_id in KNOWN_CASES:
        row = rows_by_id[case_id]
        _, _, cited = parse_source(row["source"])
        lo, hi = min(cited), max(cited)
        kw_lines = _keyword_lines_in_span(occ, lo, hi)
        if not kw_lines:
            failures.append(f"{case_id}: no mandatory-keyword line found in its own "
                            f"cited span {lo}-{hi} — fixture assumption broken, fix "
                            f"the test")
            continue

        new_covered = covered_lines_for([row], flines)
        new_missed = kw_lines - new_covered
        if new_missed:
            failures.append(f"{case_id}: FIXED matcher still misses keyword line(s) "
                            f"{sorted(new_missed)} in {lo}-{hi} — R13 not fixed")

        old_covered = _old_covered_lines_for([row], flines)
        old_missed = kw_lines - old_covered
        if not old_missed:
            failures.append(f"{case_id}: the frozen OLD per-line algorithm did NOT "
                            f"miss anything in {lo}-{hi} — this case no longer "
                            f"demonstrates the R13 bug (spec text moved?); replace it "
                            f"with a reproducing case so this gate keeps proving the "
                            f"fix actually matters")

    return failures


# --- synthetic fixtures (hermetic, no vendor dependency) -------------------------

SYN_FLINES = [
    "* **MUST** validate the widget identifier",                                # 1
    "    to prevent tampering. The platform **MUST** verify that the widget",   # 2
    "    value matches the registry's canonical form (as declared in its",      # 3
    "    published manifest",                                                   # 4
    "    metadata). If the values do not match, the platform **MUST**",         # 5
    "    abort and discard the response.",                                      # 6
    "",                                                                          # 7 (blank)
    "Unrelated section below, sharing no text with the quoted row.",            # 8
    "* **SHOULD** log a warning when a widget lookup misses the cache.",        # 9
]

SYN_ROW = {
    "id": "SYN-001",
    "source": "ucp:synthetic/widget.md#L1-L6",
    "quote": ("**MUST** validate the widget identifier\n"
              "    to prevent tampering. The platform **MUST** verify that the widget\n"
              "    value matches the registry's canonical form "
              "... If the values do not match, the platform **MUST**\n"
              "    abort and discard the response."),
}


def check_synthetic_positive():
    """Step 4: the same mid-line-elision shape, built by hand so this proof does not
    depend on the vendored tree's current text."""
    failures = []
    occ = []
    for i, raw in enumerate(SYN_FLINES, start=1):
        probe = raw.replace("**", "")
        for m in re.compile(r"\b(MUST NOT|MUST|SHALL NOT|SHALL|REQUIRED)\b").finditer(probe):
            occ.append((i, m.group(1), raw))
    kw_lines_in_span = {ln for (ln, _, _) in occ if 1 <= ln <= 6}
    # line 5 carries the MUST whose quote-text is truncated by the elision (the
    # elision cuts inside physical line 3-5's wrapped sentence) — the exact shape
    # of the real IDL-012 bug, built synthetically.
    assert 5 in kw_lines_in_span, "fixture setup bug: expected a MUST on line 5"

    new_covered = covered_lines_for([SYN_ROW], SYN_FLINES)
    missed = kw_lines_in_span - new_covered
    if missed:
        failures.append(f"synthetic positive: FIXED matcher misses {sorted(missed)}")

    old_covered = _old_covered_lines_for([SYN_ROW], SYN_FLINES)
    if not (kw_lines_in_span - old_covered):
        failures.append("synthetic positive: frozen OLD algorithm did not reproduce "
                        "the bug — fixture no longer demonstrates it")
    return failures


def check_class_negative():
    """Step 3: the flattened matcher must not mark an UNRELATED line covered just
    because it sits in the same file as a long quote — line 9 ('SHOULD log a
    warning...') shares no text with SYN_ROW's quote and must stay uncovered."""
    covered = covered_lines_for([SYN_ROW], SYN_FLINES)
    if 9 in covered:
        return ["class negative: line 9 (unrelated SHOULD) was marked covered by "
                "SYN_ROW's quote — the flattened matcher is over-matching"]
    return []


def main():
    failures = []
    failures += check_known_cases()
    failures += check_synthetic_positive()
    failures += check_class_negative()
    if failures:
        print("COMPLETENESS-MATCHER GATE: FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("COMPLETENESS-MATCHER GATE: PASS")
    print(f"  known cases {KNOWN_CASES}: fixed matcher covers every keyword line in "
          "each row's cited span; frozen old algorithm reproducibly misses at least "
          "one per case")
    print("  synthetic positive: mid-line-elision shape covered by the fix, missed "
          "by the frozen old algorithm")
    print("  class negative: unrelated line stays uncovered (no over-matching)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

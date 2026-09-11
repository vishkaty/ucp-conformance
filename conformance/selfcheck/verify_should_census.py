#!/usr/bin/env python3
"""
verify_should_census.py — the SHOULD-class census, REPORT-ONLY (D2-10, PLAN-v3 §2.4 /
D2 design §A16; decision 8: the published "airtight" claim is MUST / MUST NOT / REQUIRED
/ SHALL only, SHOULD-class obligations are censused and reported, never gated).

For every pinned version and every spec prose file (docs/specification/**/*.md, outside
code fences, RFC-2119 boilerplate skipped — the same scan the mandatory census uses):
  hits                         SHOULD / SHOULD NOT / RECOMMENDED / NOT RECOMMENDED keyword
                               occurrences (per occurrence, not per line)
  should / should_not / recommended / not_recommended   the split of `hits`
  rows                         register rows keyed SHOULD / SHOULD NOT at that version
  hit_lines_under_a_row_quote  keyword LINES that sit under ANY register row's verbatim
                               quote (covered_lines_for — quote-content anchored)
  uncovered                    keyword lines under no row's quote

Always exits 0. `--json` feeds coverage.json `surface.should` (matrix.py subprocess);
the totals are byte-compared by the coverage gate and pinned by validate_evidence_class.

  python3 conformance/selfcheck/verify_should_census.py            # text report
  python3 conformance/selfcheck/verify_should_census.py --json     # machine report
  python3 conformance/selfcheck/verify_should_census.py --selftest # hermetic fixture
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
from common.keywords import SHOULD_CLASS, SHOULD_RE  # noqa: E402


def selftest():
    """A 2-line file with one SHOULD line covered by a row's quote -> hits 2, covered 1,
    uncovered 1; SHOULD NOT and RECOMMENDED are counted and split; a fenced SHOULD is
    not a hit; the RFC-2119 boilerplate line is not a hit."""
    import tempfile
    bad = 0

    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1

    text = "\n".join([
        "The key words MUST, SHOULD and MAY are to be interpreted as described in RFC 2119.",
        "The business SHOULD return totals.",
        "The platform SHOULD NOT cache the profile.",
        "```",
        "example: SHOULD be ignored inside a fence",
        "```",
    ]) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
        path = pathlib.Path(f.name)
    rows = [{"id": "ZZZ-001", "keyword": "SHOULD", "source": f"ucp:{path.name}#L2",
             "quote": "The business SHOULD return totals."}]
    try:
        r = census_file(path, rows)
    except NameError as e:
        r = f"NameError: {e}"
    ok = isinstance(r, dict) and r.get("hits") == 2 and r.get("should") == 1 and r.get("should_not") == 1 \
        and r.get("recommended") == 0 and r.get("hit_lines_under_a_row_quote") == 1 and r.get("uncovered") == 1
    bad += case("2-line fixture: hits 2 (SHOULD 1 · SHOULD NOT 1) · covered 1 · uncovered 1; fence + boilerplate ignored",
                ok, repr(r)[:200])
    try:
        r2 = census_file(path, [])
        ok2 = r2.get("hit_lines_under_a_row_quote") == 0 and r2.get("uncovered") == 2
    except NameError as e:
        r2, ok2 = f"NameError: {e}", False
    bad += case("no rows -> covered 0 · uncovered 2", ok2, repr(r2)[:200])
    path.unlink(missing_ok=True)
    print(f"\nshould-census selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

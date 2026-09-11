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


import verify_register_completeness as vrc  # noqa: E402 — scan_keywords / covered_lines_for / rows_by_version_file

KEYS = ("should", "should_not", "recommended", "not_recommended")
_KEY_OF = {"SHOULD": "should", "SHOULD NOT": "should_not", "RECOMMENDED": "recommended",
           "NOT RECOMMENDED": "not_recommended"}


def census_file(path, rows):
    """SHOULD-class census of ONE prose file against the register rows citing it.
    -> {hits, should, should_not, recommended, not_recommended, hit_lines,
        hit_lines_under_a_row_quote, uncovered}. Coverage is quote-content anchored
    (covered_lines_for): a keyword line counts as covered only when some row's verbatim
    quote sits on it — the same rule as the mandatory census."""
    occ, flines = vrc.scan_keywords(pathlib.Path(path), kw_re=SHOULD_RE)
    out = {"hits": len(occ), **{k: 0 for k in KEYS}}
    for _, kw, _raw in occ:
        out[_KEY_OF.get(kw, "should")] += 1
    lines = sorted({ln for ln, _, _ in occ})
    cov = vrc.covered_lines_for(rows, flines) if lines else set()
    covered = [ln for ln in lines if ln in cov]
    out["hit_lines"] = len(lines)
    out["hit_lines_under_a_row_quote"] = len(covered)
    out["uncovered"] = len(lines) - len(covered)
    return out


def census(versions=None):
    """{version: {hits, should, …, rows, hit_lines_under_a_row_quote, uncovered, by_file}}
    over docs/specification/**/*.md of each pinned version. `rows` = register rows keyed
    SHOULD / SHOULD NOT at that version (the SHOULD-class rows the register already
    carries; MAY-class rows are not counted)."""
    rvf = vrc.rows_by_version_file()
    out = {}
    for ver, ucp_dir in vrc.VERSION_TREE.items():
        if versions and ver not in versions:
            continue
        tot = {"hits": 0, **{k: 0 for k in KEYS}, "rows": 0, "hit_lines": 0,
               "hit_lines_under_a_row_quote": 0, "uncovered": 0, "by_file": {}}
        for path in vrc.spec_files(ucp_dir):
            rel = str(path.relative_to(vrc.VENDOR / ucp_dir))
            rows = rvf.get((ver, rel), [])
            r = census_file(path, rows)
            if r["hits"]:
                tot["by_file"][rel] = {k: r[k] for k in ("hits", "hit_lines_under_a_row_quote", "uncovered")}
            for k in ("hits", "hit_lines", "hit_lines_under_a_row_quote", "uncovered", *KEYS):
                tot[k] += r[k]
        seen = set()
        for (v, _rel), rows in rvf.items():
            if v == ver:
                for r in rows:
                    if r.get("keyword") in SHOULD_CLASS and r.get("id") not in seen:
                        seen.add(r.get("id"))
        tot["rows"] = len(seen)
        tot["pin"] = None
        out[ver] = tot
    try:
        lock = json.load(open(ROOT / "conformance" / "SOURCES.lock.json"))
        for ver in out:
            out[ver]["pin"] = (lock["spec"]["versions"].get(ver, {}).get("commit") or "")[:8]
    except Exception:
        pass
    return out


def main(argv):
    as_json = "--json" in argv
    versions = [a for a in argv if a.startswith("20")]
    per = census(versions or None)
    if as_json:
        print(json.dumps({"_about": "SHOULD-class census, report-only (decision 8 / D2-10): the "
                                    "published airtight claim is MUST/MUST NOT/REQUIRED/SHALL; "
                                    "SHOULD-class obligations are counted and their coverage by "
                                    "register-row quotes reported here, never gated.",
                          "keywords": list(SHOULD_CLASS), "per_version": per}, indent=1))
        return 0
    print("SHOULD-class census (report-only, decision 8)\n")
    for ver, t in per.items():
        print(f"  {ver} @{t.get('pin')}: {t['hits']} hits (SHOULD {t['should']} · SHOULD NOT {t['should_not']} · "
              f"RECOMMENDED {t['recommended']} · NOT RECOMMENDED {t['not_recommended']}) on {t['hit_lines']} lines · "
              f"{t['hit_lines_under_a_row_quote']} lines under a row quote · {t['uncovered']} uncovered · "
              f"{t['rows']} SHOULD-keyed rows")
        top = sorted(t["by_file"].items(), key=lambda kv: -kv[1]["uncovered"])[:5]
        for rel, f in top:
            print(f"      {f['uncovered']:3} uncovered / {f['hits']:3} hits  {rel}")
    print("\nshould-census: report-only (always rc 0)")
    return 0


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

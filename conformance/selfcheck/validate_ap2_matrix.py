#!/usr/bin/env python3
"""
validate_ap2_matrix.py — the AP2 49-case matrix gate.

The matrix (testbed/casematrix.py) is the design doc's §3 case list as DATA,
layer-tagged and bound to the executable cases that prove each row. This gate
keeps the matrix and the testbed from drifting apart, in BOTH directions, and
executes the hermetic tier the matrix owns:

  STRUCTURE   49 rows, unique case numbers 1..49, legal group/layer tags, every
              row carries at least one binding, binding tiers legal.
  RESOLUTION  every binding (and xref) names a case that actually exists:
              frozen.FROZEN_MUTANTS / the committed nested goldens /
              structural.CASES / semantic.CASES.
  REVERSE     every executable case is bound (or xref'd) by some row — an
              implemented case outside the matrix is unaccounted coverage —
              except the documented casematrix.EXTRAS (which must NOT also be
              bound: an extra that is bound is a stale justification).
  EXECUTION   every structural.CASES row runs here, hermetically (our own
              frozen-standard code only — no reference SDK, no network), and
              must produce its expected outcome. Failures print the matrix
              row(s) + LAYER, so a failure attributes to the right layer.
              (frozen/nested/semantic execution is owned by validate_ap2_e2e —
              single ownership, no duplicate execution semantics.)

--selftest kill-proves the gate itself: a ghost binding, a duplicate case
number, an illegal layer, an unbound structural case, a bound EXTRA, and a
structural case producing the wrong outcome must each redden the gate.

Exit 0 = matrix consistent + hermetic tier green; 1 = a failure.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "testbed"))
sys.path.insert(0, str(HERE.parents[0] / "common"))
import casematrix  # noqa: E402
import frozen  # noqa: E402
import provenance  # noqa: E402
import semantic  # noqa: E402
import structural  # noqa: E402

NESTED_DIR = HERE / "fixtures" / "ap2" / "golden" / "nested"
NESTED_GOLDENS = {"valid", "missing_mauth", "tampered_terms", "hash_mismatch"}


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def _known_cases():
    return {
        "frozen": set(frozen.FROZEN_MUTANTS) | {"golden"},
        "nested": set(NESTED_GOLDENS),
        "structural": {cid for cid, _e, _f in structural.CASES},
        "semantic": {cid for cid, _r, _m, _e, _f in semantic.CASES},
    }


def matrix_problems(rows, extras, known):
    """Pure consistency check -> list of problem strings (selftest-able)."""
    problems = []
    nos = [r[0] for r in rows]
    if len(rows) != 49:
        problems.append(f"matrix must carry the design doc's 49 rows, has {len(rows)}")
    if sorted(nos) != list(range(1, len(rows) + 1)):
        problems.append("case numbers must be exactly 1..N with no gaps/duplicates")
    referenced = {t: set() for t in known}
    for no, group, title, layer, bindings, xrefs, _note in rows:
        if group not in casematrix.GROUPS:
            problems.append(f"row {no}: unknown group {group!r}")
        if layer not in casematrix.LAYERS:
            problems.append(f"row {no}: unknown layer {layer!r}")
        if not bindings:
            problems.append(f"row {no}: carries no binding — an unbound row is "
                            "a claim with no executable evidence")
        for tier, cid in list(bindings) + list(xrefs):
            if tier not in known:
                problems.append(f"row {no}: unknown binding tier {tier!r}")
            elif cid not in known[tier]:
                problems.append(f"row {no}: binding {tier}/{cid} names no "
                                "existing case (ghost binding)")
            else:
                referenced[tier].add(cid)
    # reverse completeness: every executable case is accounted for.
    for tier in ("frozen", "nested", "structural", "semantic"):
        for cid in sorted(known[tier] - referenced[tier]):
            if tier == "semantic" and cid in extras:
                continue
            problems.append(f"{tier}/{cid}: implemented but bound by no matrix "
                            "row (unaccounted coverage)")
    for cid, why in extras.items():
        if cid not in known["semantic"]:
            problems.append(f"EXTRAS names ghost case {cid}")
        elif cid in referenced["semantic"]:
            problems.append(f"EXTRAS entry {cid} is also bound by a row — the "
                            f"exemption ({why!r}) is stale; delete it")
    return problems


def _rows_for(case_id):
    hits = [(r[0], r[3]) for r in casematrix.ROWS
            if any(cid == case_id for _t, cid in list(r[4]) + list(r[5]))]
    return ", ".join(f"row {n} [{layer}]" for n, layer in hits) or "unbound"


def run_structural(ok):
    print("hermetic tier (structural.CASES — our frozen-standard code only):")
    for cid, expect, run in structural.CASES:
        try:
            got = run()
        except Exception as exc:
            got = f"ERR {type(exc).__name__}: {exc}"
        ok &= check(f"  {cid} ({_rows_for(cid)}): expect {expect} -> {got}",
                    got == expect)
    return ok


def _selftest():
    fails = []
    known = _known_cases()
    rows = [list(r) for r in casematrix.ROWS]

    if matrix_problems(casematrix.ROWS, casematrix.EXTRAS, known):
        fails.append("the committed matrix must be consistent")

    ghost = [tuple(r) for r in rows]
    ghost[0] = (ghost[0][0], ghost[0][1], ghost[0][2], ghost[0][3],
                [("structural", "st.ghost_case")], ghost[0][5], ghost[0][6])
    if not any("ghost binding" in p for p in
               matrix_problems(ghost, casematrix.EXTRAS, known)):
        fails.append("a binding to a nonexistent case must be flagged")

    dup = [tuple(r) for r in rows]
    dup[1] = (dup[0][0],) + tuple(dup[1][1:])
    if not any("case numbers" in p for p in
               matrix_problems(dup, casematrix.EXTRAS, known)):
        fails.append("a duplicate case number must be flagged")

    badlayer = [tuple(r) for r in rows]
    badlayer[2] = badlayer[2][:3] + ("vibes",) + badlayer[2][4:]
    if not any("unknown layer" in p for p in
               matrix_problems(badlayer, casematrix.EXTRAS, known)):
        fails.append("an illegal layer tag must be flagged")

    unbound_known = {t: set(v) for t, v in known.items()}
    unbound_known["structural"].add("st.orphaned_case")
    if not any("unaccounted coverage" in p for p in
               matrix_problems(casematrix.ROWS, casematrix.EXTRAS, unbound_known)):
        fails.append("an implemented-but-unbound case must be flagged")

    bound_extra = dict(casematrix.EXTRAS)
    bound_extra["e2e.checkout_happy_path"] = "bogus exemption"
    if not any("stale" in p for p in
               matrix_problems(casematrix.ROWS, bound_extra, known)):
        fails.append("an EXTRAS entry that is actually bound must be flagged")

    # the runner must fail a structural case that produces the wrong outcome.
    orig = structural.CASES
    try:
        structural.CASES = [("st.checkout_valid_accepts", "REJECT",
                             lambda: "PASS")]
        if run_structural(True):
            fails.append("a structural case with the wrong outcome must fail "
                         "the gate (runner kill)")
    finally:
        structural.CASES = orig

    if fails:
        print("ap2-matrix selftest: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("ap2-matrix selftest: PASS — ghost bindings, duplicate/illegal rows, "
          "unaccounted cases, stale EXTRAS and wrong-outcome runs all redden "
          "the gate.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="AP2 49-case matrix gate.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--summary", action="store_true",
                    help="print the layer/oracle breakdown and exit")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.summary:
        print(json.dumps(casematrix.summarize(), indent=2))
        return 0

    print(provenance.basis_banner())
    print()
    ok = True
    problems = matrix_problems(casematrix.ROWS, casematrix.EXTRAS, _known_cases())
    for p in problems:
        ok &= check(f"matrix: {p}", False)
    if not problems:
        s = casematrix.summarize()
        print(f"matrix consistent: {s['rows']} rows — layers {s['by_layer']}, "
              f"oracle {s['by_oracle']}")
    ok = run_structural(ok)
    print("\nap2-matrix: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

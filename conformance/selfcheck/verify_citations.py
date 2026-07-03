#!/usr/bin/env python3
"""
verify_citations.py — the citation-soundness gate (spec-truth guard).

The 2026-04-08 registers RENUMBERED many CHK/DSC/ORD/PAY ids onto DIFFERENT
requirements than the 01-11/01-23 registers carry (see the wave-1 reconciliation).
A version-adaptive or mis-scoped check can therefore end up claiming to cover an id
at a version where that id names a DIFFERENT rule than the check actually validates —
green on the reference gate, but grading the wrong requirement. The register quote
gate catches quote drift; the reference gates prove kill-safety; NEITHER catches a
single check attributing one id across versions whose requirement TEXT diverges.

This gate closes that hole. For every shipped check OBJECT it computes, per the SAME
scoping the matrix uses (chk.versions / module VERSIONS / filename token; ids per
version via chk.req_ids_map), the set of versions each id is attributed to. If one
check attributes an id at >=2 versions whose register `requirement` strings are less
than THRESHOLD similar, that is a citation-soundness failure — UNLESS the id is in
REVIEWED_EQUIVALENT with a written justification (a requirement that is semantically
identical across versions but reworded enough to trip the similarity screen).

Exit 0 = every multi-version citation is text-equivalent or reviewed; 1 = a check
grades divergent requirements under one id (fix its req_ids_map / versions=).
"""
import json, glob, os, sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "conformance", "coverage"))
sys.path.insert(0, os.path.join(ROOT, "conformance", "checks"))
sys.path.insert(0, HERE)
import matrix  # noqa: E402

THRESHOLD = 0.55

# id -> written justification that its requirement is SEMANTICALLY IDENTICAL across
# the versions it is attributed to, despite reworded text scoring below THRESHOLD.
# Each entry was verified by reading the pinned-spec quotes at all cited versions.
REVIEWED_EQUIVALENT = {
    "ORD-001": ("Same invariant every version — 'return the full order entity as a "
                "current-state snapshot': 01-11/01-23 phrase it 'Get Order MUST return "
                "the full order entity...'; 04-08 generalizes to 'Businesses MUST return "
                "the full order entity on every response, not deltas'. The predicate "
                "(p_order_shape: required top-level fields present => a full entity, not "
                "a delta) validates that invariant identically at each version, and is "
                "reference-gated clean-pass + kill-safe on all three goldens. Verified "
                "2026-07-03 (spec-truth sweep)."),
}


def _reg(ver):
    d = {}
    for f in glob.glob(os.path.join(ROOT, "conformance", "requirements", ver, "*.json")):
        for r in json.load(open(f)).get("rows", []):
            d[r["id"]] = r
    return d


def run():
    regs = {v: _reg(v) for v in matrix.VERSIONS}
    allids = {v: set(regs[v]) for v in matrix.VERSIONS}
    failures, reviewed_used = [], set()
    for path in matrix.check_files():
        checks, mod = matrix._module_checks(path)
        if not checks:
            continue
        mv = getattr(mod, "VERSIONS", None)
        ft = matrix._file_targets(path)
        base = os.path.basename(path)
        for chk in checks:
            scope = [v for v in (getattr(chk, "versions", None) or mv or ft) if v in ft]
            vmap = getattr(chk, "req_ids_map", None) or {}
            per = {}
            for v in scope:
                for i in vmap.get(v, list(getattr(chk, "req_ids", []) or [])):
                    if i in allids[v]:
                        per.setdefault(i, []).append(v)
            for i, vers in per.items():
                if len(vers) < 2:
                    continue
                worst = min(SequenceMatcher(None,
                            regs[a][i]["requirement"].lower(),
                            regs[b][i]["requirement"].lower()).ratio()
                            for x, a in enumerate(vers) for b in vers[x + 1:])
                if worst >= THRESHOLD:
                    continue
                if i in REVIEWED_EQUIVALENT:
                    reviewed_used.add(i)
                    continue
                failures.append((base, getattr(chk, "id", "?"), i, tuple(vers), round(worst, 2)))
    return failures, reviewed_used


def main():
    failures, reviewed_used = run()
    for i in sorted(reviewed_used):
        print(f"  · {i}: reviewed-equivalent across versions (documented)")
    if failures:
        print("\ncitation-soundness gate: FAIL — a check grades divergent requirements "
              "under one id (fix req_ids_map / versions=):")
        for base, cid, i, vers, sim in failures:
            print(f"  ✗ {base}::{cid} attributes {i} at {'/'.join(vers)} (sim {sim})")
        # dead-code check: an allowlist entry no longer needed is itself a smell
        stale = set(REVIEWED_EQUIVALENT) - reviewed_used
        if stale:
            print(f"  (note: stale allowlist entries {sorted(stale)})")
        return 1
    print("\ncitation-soundness gate: PASS — every multi-version citation is "
          "text-equivalent or reviewed-equivalent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

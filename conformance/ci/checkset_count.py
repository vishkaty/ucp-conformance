#!/usr/bin/env python3
"""
checkset_count.py — ONE counting technique for "how many merchant checks does the product
have" (D5-16 / H1, SITE-R-036): the runtime check set, `merchant_checks.all_checks()`, which
includes every auto-discovered version-scoped module AND the TLS module the old
`^    MCheck(` source regex never saw (228 at 2026-09-11, not 227). Used by the coverage gate's
copy freshness, site_gates (site-freshness manifest, docclaims live values), and the
site_claims writer — so the four can never disagree. A duplicate check id is refused (the
reach report, probe hygiene and the evidence split all key by id).

    python3 conformance/ci/checkset_count.py            # `merchant checks: 228 (0 duplicate id(s))`
    python3 conformance/ci/checkset_count.py --json     # {"merchant_checks": 228, "duplicates": []}
SPCK_CHECKS_DIR points at a scratch copy of conformance/checks (kill-tests). Exit 1 on duplicates.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SNIPPET = ("import sys,json,collections;sys.path.insert(0,sys.argv[1]);sys.path.insert(1,sys.argv[2]);sys.path.insert(2,sys.argv[3]);"
            "import merchant_checks as m;"
            "ids=[c.id for c in m.all_checks()];d=sorted(k for k,v in collections.Counter(ids).items() if v>1);"
            "print(json.dumps({'merchant_checks':len(ids),'duplicates':d}))")


def merchant_check_count(checks_dir=None):
    """(count, duplicates) from an isolated interpreter importing the given checks dir."""
    d = str(checks_dir or os.environ.get("SPCK_CHECKS_DIR") or (ROOT / "conformance" / "checks"))
    # the engine imports verdict_gate from conformance/selfcheck, and (W1, D1-16a) the envelope
    # module imports seq_invariants from conformance/ci — a scratch checks dir borrows both real dirs
    r = subprocess.run([sys.executable, "-c", _SNIPPET, d, str(ROOT / "conformance" / "selfcheck"),
                        str(ROOT / "conformance" / "ci")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"merchant_checks.all_checks() failed in {d}: {r.stderr[-300:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    return out["merchant_checks"], out["duplicates"]


def main(argv):
    n, dups = merchant_check_count()
    if "--json" in argv:
        print(json.dumps({"merchant_checks": n, "duplicates": dups}))
    else:
        for d in dups:
            print(f"  x duplicate check id {d!r} — two MChecks share one id (reach/evidence/probe hygiene key by id)")
        print(f"merchant checks: {n} ({len(dups)} duplicate id(s))")
    return 1 if dups else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

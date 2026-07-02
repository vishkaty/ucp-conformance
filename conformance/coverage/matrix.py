#!/usr/bin/env python3
"""
matrix.py — the coverage accounting engine (Phase-0 backbone of the 100% goal).

For every UCP version, it buckets EVERY normative MUST/MUST NOT into exactly one of:
  - CHECK     : referenced by a shipped conformance check (its id appears in a check's req-id list)
  - EXEMPT    : listed in coverage/exemptions.json with a written justification
  - GAP       : neither — unaccounted (this is what we drive to zero)

GAP is sub-classified by the register's `testability` so we see what's actionable now
(testable) vs what needs a harness (needs-receiver / needs-oauth) vs what must become a
documented exemption (manual / untestable).

Usage:
  python3 conformance/coverage/matrix.py                 # print the accounting for all versions
  python3 conformance/coverage/matrix.py --md FILE       # also write a markdown matrix
  python3 conformance/coverage/matrix.py --require testable            # exit 1 if any TESTABLE gap remains
  python3 conformance/coverage/matrix.py --require all --version 2026-01-23   # exit 1 if ANY gap remains (version "closed" gate)

Coverage attribution by version (a check counts for a version when its id is a MUST there AND):
  - file name contains 04_08/04-08  -> attributes to 2026-04-08 only
  - file name contains 01_23        -> 2026-01-23
  - file name contains 01_11        -> 2026-01-11
  - otherwise (merchant_checks.py, engine, area_*, selfcheck) -> version-adaptive: all versions where the id is a MUST
This is intentionally conservative; per-version applicability is tightened in each version's
Stage-C reconciliation.
"""
import json, os, re, glob, sys, argparse
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONF = os.path.join(ROOT, "conformance")
REQ = os.path.join(CONF, "requirements")
EXEMPT_FILE = os.path.join(CONF, "coverage", "exemptions.json")
VERSIONS = ["2026-01-11", "2026-01-23", "2026-04-08"]
ID_RE = re.compile(r'\b([A-Z]{2,6}-\d{2,3})\b')
# capture the req-id list: Check("name", [ ... ]) / MCheck("name", [ ... ])
REQIDS_RE = re.compile(r'M?Check\(\s*"[^"]*"\s*,\s*\[([^\]]*)\]', re.S)


def load_rows(ver):
    rows = []
    for f in glob.glob(os.path.join(REQ, ver, "*.json")):
        d = json.load(open(f))
        rows += d if isinstance(d, list) else d.get("requirements", d.get("rows", []))
    return rows


def load_rows_with_area(ver):
    """Like load_rows but each row carries its register file's `_area`."""
    rows = []
    for f in sorted(glob.glob(os.path.join(REQ, ver, "*.json"))):
        d = json.load(open(f))
        area = (d.get("_area") if isinstance(d, dict) else None) \
            or os.path.basename(f).replace(".json", "")
        for r in (d if isinstance(d, list) else d.get("requirements", d.get("rows", []))):
            rows.append({**r, "_area": area})
    return rows


def check_files():
    return glob.glob(os.path.join(CONF, "checks", "*.py")) + \
           glob.glob(os.path.join(CONF, "selfcheck", "*.py"))


def coverage_map():
    """Return {version: {req_id: sorted[check file basenames]}} referenced by shipped
    checks, attributed per the rules above (the traceability layer of the matrix)."""
    cov = {v: defaultdict(set) for v in VERSIONS}
    # gather row ids per version so we only attribute real rows
    all_ids = {v: {r.get("id") for r in load_rows(v)} for v in VERSIONS}
    for path in check_files():
        name = os.path.basename(path).lower()
        txt = open(path).read()
        ids = set()
        for grp in REQIDS_RE.findall(txt):
            ids |= set(ID_RE.findall(grp))
        if not ids:
            continue
        if "04_08" in name or "04-08" in name:
            targets = ["2026-04-08"]
        elif "01_23" in name:
            targets = ["2026-01-23"]
        elif "01_11" in name:
            targets = ["2026-01-11"]
        else:
            targets = VERSIONS  # version-adaptive engine (merchant_checks, area_*, selfcheck)
        for v in targets:
            for i in ids:
                if i in all_ids[v]:
                    cov[v][i].add(os.path.basename(path))
    return {v: {i: sorted(fs) for i, fs in m.items()} for v, m in cov.items()}


def covered_ids_by_version():
    """Return {version: set(ids)} referenced by shipped checks."""
    return {v: set(m.keys()) for v, m in coverage_map().items()}


def _spec_pins():
    """{version: commit_sha} from SOURCES.lock.json (for pinned-spec deep links)."""
    lock = os.path.join(CONF, "SOURCES.lock.json")
    try:
        d = json.load(open(lock))
        return {v: info.get("commit", "") for v, info in
                d.get("spec", {}).get("versions", {}).items()}
    except Exception:
        return {}


def export_json():
    """The full requirements-traceability export: per version, every MUST row with its
    bucket (check/exempt/gap), testability, verbatim requirement, pinned-spec source,
    and the check files that cover it. Deterministic ordering (stable for drift-diff).
    This is the single data source for the public coverage page AND the coverage gate."""
    covmap = coverage_map()
    exempt = load_exemptions()
    pins = _spec_pins()
    out = {"_about": "spck.dev UCP conformance coverage — every normative MUST accounted "
                     "as CHECK (kill-rate-validated), EXEMPT (documented), or GAP. "
                     "Generated by conformance/coverage/matrix.py --json; the `coverage` "
                     "CI gate fails if this file is stale or coverage regresses.",
           "spec_repo": "Universal-Commerce-Protocol/ucp",
           "spec_pins": {v: pins.get(v, "") for v in VERSIONS},
           "versions": {}}
    for ver in VERSIONS:
        rows = [r for r in load_rows_with_area(ver)
                if r.get("keyword") in ("MUST", "MUST NOT")]
        areas = {}
        jrows = []
        n_check = n_exempt = 0
        gap_by_test = Counter()
        for r in sorted(rows, key=lambda x: x.get("id", "")):
            rid = r.get("id")
            if rid in covmap[ver]:
                status = "check"; n_check += 1
            elif rid in exempt:
                status = "exempt"; n_exempt += 1
            else:
                status = "gap"
                gap_by_test[r.get("testability", "?")] += 1
            a = areas.setdefault(r["_area"], Counter())
            a["musts"] += 1
            a[status] += 1
            if status == "gap":
                a["gap_" + r.get("testability", "?")] += 1
            jrows.append({"id": rid, "area": r["_area"], "keyword": r.get("keyword"),
                          "testability": r.get("testability", "?"), "status": status,
                          "requirement": r.get("requirement", ""),
                          "source": r.get("source", ""),
                          "covered_by": covmap[ver].get(rid, []),
                          **({"exempt_reason": exempt[rid].get("reason", "")}
                             if status == "exempt" and isinstance(exempt.get(rid), dict) else {})})
        n = len(rows)
        out["versions"][ver] = {
            "musts": n, "check": n_check, "exempt": n_exempt,
            "gap": n - n_check - n_exempt,
            "accounted_pct": round(100 * (n_check + n_exempt) / n) if n else 0,
            "gap_by_testability": dict(sorted(gap_by_test.items())),
            "areas": [{"area": k, **dict(sorted(v.items()))}
                      for k, v in sorted(areas.items())],
            "rows": jrows,
        }
    return out


def load_exemptions():
    if not os.path.exists(EXEMPT_FILE):
        return {}
    return json.load(open(EXEMPT_FILE))


def account(ver, cov, exempt):
    rows = load_rows(ver)
    musts = [r for r in rows if r.get("keyword") in ("MUST", "MUST NOT")]
    buckets = {"CHECK": [], "EXEMPT": [], "GAP": []}
    gap_by_test = defaultdict(list)
    cov_ver = cov.get(ver, set())
    for r in musts:
        rid = r.get("id")
        if rid in cov_ver:
            buckets["CHECK"].append(rid)
        elif rid in exempt:
            buckets["EXEMPT"].append(rid)
        else:
            buckets["GAP"].append(rid)
            gap_by_test[r.get("testability", "?")].append(rid)
    return musts, buckets, gap_by_test


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    ap.add_argument("--json", help="write the full traceability export (per-row status, "
                                   "covered_by, pinned-spec sources) to FILE")
    ap.add_argument("--require", choices=["testable", "all"], help="hard-fail on remaining gaps of this class")
    ap.add_argument("--version", help="restrict --require to one version")
    a = ap.parse_args()

    cov = covered_ids_by_version()
    exempt = load_exemptions()
    md = ["# UCP Conformance Coverage Matrix\n",
          "_Every MUST is CHECK (has a kill-rate check), EXEMPT (documented), or GAP (unaccounted)._\n"]
    failed = False

    for ver in VERSIONS:
        musts, b, gap_by_test = account(ver, cov, exempt)
        n = len(musts)
        pct = 100 * (len(b["CHECK"]) + len(b["EXEMPT"])) / n if n else 0
        print(f"\n===== {ver} =====")
        print(f"  MUSTs: {n} | CHECK: {len(b['CHECK'])} | EXEMPT: {len(b['EXEMPT'])} | GAP: {len(b['GAP'])}  -> accounted {pct:.0f}%")
        if gap_by_test:
            print("  GAP by testability:", {k: len(v) for k, v in sorted(gap_by_test.items())})
        md.append(f"\n## {ver} — {pct:.0f}% accounted ({len(b['CHECK'])} check · {len(b['EXEMPT'])} exempt · {len(b['GAP'])} gap of {n} MUSTs)\n")
        for k in sorted(gap_by_test):
            md.append(f"- GAP/{k}: {', '.join(sorted(gap_by_test[k]))}")

        if a.require and (not a.version or a.version == ver):
            if a.require == "all" and b["GAP"]:
                print(f"  ✗ {ver}: {len(b['GAP'])} MUST(s) unaccounted (require=all)"); failed = True
            elif a.require == "testable":
                tg = gap_by_test.get("testable", [])
                if tg:
                    print(f"  ✗ {ver}: {len(tg)} TESTABLE gap(s) remain: {sorted(tg)}"); failed = True

    if a.md:
        open(a.md, "w").write("\n".join(md) + "\n")
        print(f"\nmatrix written -> {a.md}")

    if a.json:
        open(a.json, "w").write(json.dumps(export_json(), indent=1, sort_keys=False) + "\n")
        print(f"traceability export written -> {a.json}")

    if failed:
        print("\nMATRIX GATE: FAIL"); sys.exit(1)
    print("\nMATRIX GATE: OK" if a.require else "\n(report only; pass --require to enforce)")


if __name__ == "__main__":
    main()

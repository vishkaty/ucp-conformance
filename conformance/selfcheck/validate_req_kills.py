#!/usr/bin/env python3
"""
validate_req_kills.py — per-requirement-id kill attribution (PLAN-v3 §2.2, D1-12 B2a).

A check that cites SEVERAL register ids and carries ONE mutation list proves that its
mutations break "the check", never that each id it claims has a defect the check would
catch (D1 N8: 41 multi-id MChecks, 15 in the B5 shape; CAT-042/043 is the positive
control — one comparator check, two ids, negatives that exercise only the formula id).
Every check kind gains an optional `kills = {req_id: [mutation, …]}` (MCheck / engine
Check: mutation strings; golden Row: mutant names; struct Check: negative arg-tuples),
the runners record `per_id: {rid: {declared, killed}}` (validate_merchant_checks
--record, golden_check_08_25 --record, struct_check_08_25 --record), and this gate
requires, per (check, id) attribution at the version:

  dedicated  a single-id check (every kill is the id's) or `kills[rid]` non-empty
  shared     a multi-id check with no `kills[rid]`  -> RED at 2026-08-25 (report-only at
             the older versions until D1-21 backfills the 04-08 population)
  unkilled   recorded run where no declared kill for rid was observed killed -> RED
  unrecorded no run record covers the check (reported; the gate that runs it is the
             evidence — a SKIPPED golden gate cannot silently pass here, it is named)

    python3 conformance/selfcheck/validate_req_kills.py --version 2026-08-25 [--records DIR]
    python3 conformance/selfcheck/validate_req_kills.py --selftest
Exit 0 = every attribution has >=1 dedicated kill; 1 = shared/unkilled named; 2 = usage.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONF = ROOT / "conformance"
for p in (str(CONF / "checks"), str(CONF / "coverage"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

ENFORCED_VERSIONS = ("2026-08-25",)      # D1-21 widens this to every version (286/286)


# ---------------------------------------------------------------------------
# the attribution table (matrix walk) and the pure assessment
# ---------------------------------------------------------------------------
def _kind(chk):
    if hasattr(chk, "cfg_needs"):
        return "MCheck"
    if hasattr(chk, "make_request") and hasattr(chk, "mutant"):
        return "Row"
    if hasattr(chk, "negatives") and hasattr(chk, "valid") and hasattr(chk, "fn"):
        return "struct"
    if hasattr(chk, "schema_rel"):
        return "schema-tier"
    if hasattr(chk, "mutations"):
        return "engine"
    return "unknown"


def _kill_declaration(chk):
    """(n_kills, {rid: [kill key…]}) — the check's whole kill set size and its per-id map in
    the record key form each runner writes (mutation strings / mutant names / struct
    case keys)."""
    kind = _kind(chk)
    kills = getattr(chk, "kills", None) or {}
    if kind in ("MCheck", "engine"):
        return len(list(chk.mutations)), {r: [str(m) for m in v] for r, v in kills.items()}
    if kind == "Row":
        names = list(chk.mutant) if isinstance(chk.mutant, (list, tuple)) else [chk.mutant]
        return len([n for n in names if n]), {r: [str(m) for m in v] for r, v in kills.items()}
    if kind == "struct":
        import struct_check_08_25
        return len(chk.negatives), {r: [struct_check_08_25.case_key(c) for c in v] for r, v in kills.items()}
    if kind == "schema-tier":
        negs = getattr(chk, "negatives", None)
        return (len(negs) if isinstance(negs, (list, tuple)) else 1), {}
    return 0, {}


def attributions(version):
    """[{key, check_id, kind, rids, kills, n_kills}] — one entry per check OBJECT attributed
    at `version` by matrix.attribution() (the single walk coverage and evidence use), its
    rids at that version in walk order. Text-scan rows (no object) carry no kill set and
    are not attributions of a check."""
    import matrix
    groups = {}
    for v, rid, base, chk in matrix.attribution():
        if v != version or chk is None:
            continue
        key = f"{pathlib.Path(base).stem}:{chk.id}"
        g = groups.get(key)
        if g is None:
            n, kmap = _kill_declaration(chk)
            g = groups[key] = {"key": key, "check_id": chk.id, "kind": _kind(chk), "rids": [],
                               "kills": kmap, "n_kills": n}
        if rid not in g["rids"]:
            g["rids"].append(rid)
    return list(groups.values())


def assess(entries, records, enforce=True):
    """Pure. entries = attributions(); records = {key or check_id: per_id} from the run
    records (None = no record). -> (rc, findings, stats)."""
    findings = []
    stats = {"attributions": 0, "dedicated": 0, "shared": 0, "unkilled": 0, "unrecorded": 0,
             "recorded": 0, "checks": len(entries)}
    for e in entries:
        rec = records.get(e["key"])
        if rec is None:
            rec = records.get(e["check_id"])
        if rec is None:
            stats["unrecorded"] += 1
        else:
            stats["recorded"] += 1
        multi = len(e["rids"]) > 1
        for rid in e["rids"]:
            stats["attributions"] += 1
            declared = e["kills"].get(rid) if multi else None
            if multi and not declared:
                stats["shared"] += 1
                findings.append(f"shared: {e['key']} cites {rid} with no dedicated kill "
                                f"(kills= names none of its {e['n_kills']} kills for this id)")
                continue
            if not multi and e["n_kills"] == 0:
                stats["shared"] += 1
                findings.append(f"shared: {e['key']} cites {rid} but declares no kills at all")
                continue
            if rec is not None:
                killed = ((rec.get(rid) or {}).get("killed")) or []
                if not killed:
                    stats["unkilled"] += 1
                    findings.append(f"unkilled: {e['key']} declares kills for {rid} but the run record "
                                    f"observed none of them killed ({(rec.get(rid) or {}).get('declared')})")
                    continue
            stats["dedicated"] += 1
    rc = 1 if enforce and (stats["shared"] or stats["unkilled"]) else 0
    return rc, findings, stats


def load_records(d):
    """{check_id: per_id} over every *.json in DIR carrying a `per_id` block (the merchant
    gates' --record, golden_check_08_25 --record, struct_check_08_25 --record)."""
    out = {}
    d = pathlib.Path(d) if d else None
    if d is None or not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            doc = json.loads(f.read_text())
        except ValueError:
            continue
        for cid, per_id in (doc.get("per_id") or {}).items():
            out[cid] = per_id
    return out


def run(version, records_dir=None):
    entries = attributions(version)
    records = load_records(records_dir)
    enforce = version in ENFORCED_VERSIONS
    rc, findings, st = assess(entries, records, enforce=enforce)
    for f in findings:
        print(f"  {'✗' if enforce else '!'} {f}")
    short = version[5:].replace("-", "-")
    tag = f"{short}"
    if st["shared"] == 0 and st["unkilled"] == 0:
        head = (f"all {tag} attributions have >=1 dedicated kill "
                f"({st['attributions']} attributions, 0 shared)")
    else:
        head = (f"{st['dedicated']}/{st['attributions']} {tag} attributions dedicated · "
                f"{st['shared']} shared · {st['unkilled']} unkilled")
    mode = "" if enforce else " (report-only until D1-21)"
    print(f"req-kills {version}: {'PASS' if rc == 0 else 'FAIL'}{mode} — {head} · "
          f"{st['checks']} checks ({st['recorded']} recorded, {st['unrecorded']} unrecorded)")
    return rc


# ---------------------------------------------------------------------------
# --selftest (hermetic): the planted two-id check through the REAL merchant runner
# ---------------------------------------------------------------------------
def selftest():
    from types import SimpleNamespace
    import engine
    from engine import CLEAN, DEVIATION
    import merchant_checks
    from merchant_checks import MCheck
    bad = 0

    def case(label, ok, detail=""):
        nonlocal bad
        print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f"  <-- {detail}"))
        bad += 0 if ok else 1

    ctx = SimpleNamespace(version="2026-08-25", has_rest=True, has_mcp=False, capabilities=set(),
                          product_id="p", config={}, profile={}, shopping_endpoint="http://x",
                          base="http://x")
    body = {"a": 1, "b": 2}

    def fetch(_ctx):
        return engine.Resp(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

    def pred(r):
        j = r.json or {}
        return CLEAN if j.get("a") == 1 and j.get("b") == 2 else DEVIATION

    muts = ["set:a=9", "set:b=9"]
    plain = MCheck("k.two_ids", ["ZZA-001", "ZZA-002"], "MUST", fetch, pred, muts)
    try:
        good = MCheck("k.two_ids_kills", ["ZZA-001", "ZZA-002"], "MUST", fetch, pred, muts,
                      kills={"ZZA-001": ["set:a=9"], "ZZA-002": ["set:b=9"]})
        weak = MCheck("k.two_ids_weak", ["ZZA-001", "ZZA-002"], "MUST", fetch, pred, muts + ["set:c=1"],
                      kills={"ZZA-001": ["set:a=9"], "ZZA-002": ["set:c=1"]})
    except TypeError as e:
        case("MCheck accepts kills=", False, str(e))
        good = weak = None

    def per_id(chk):
        _, det = merchant_checks.run_merchant_checks(ctx, [chk])
        return det[0][1].get("per_id")

    p_plain = per_id(plain)
    case("runner records per_id for a multi-id check", isinstance(p_plain, dict), f"per_id={p_plain!r}")
    if isinstance(p_plain, dict):
        case("(1) one mutation list, two ids -> nothing DECLARED per id (shared)",
             p_plain == {"ZZA-001": {"declared": [], "killed": []}, "ZZA-002": {"declared": [], "killed": []}},
             p_plain)
    if good is not None:
        p_good = per_id(good)
        case("(2) kills={A:[m1],B:[m2]} -> both declared AND killed",
             p_good == {"ZZA-001": {"declared": ["set:a=9"], "killed": ["set:a=9"]},
                        "ZZA-002": {"declared": ["set:b=9"], "killed": ["set:b=9"]}}, p_good)
        p_weak = per_id(weak)
        case("(3) kills naming a surviving mutant for B -> B declared, not killed",
             isinstance(p_weak, dict) and p_weak.get("ZZA-002") == {"declared": ["set:c=1"], "killed": []}
             and p_weak.get("ZZA-001", {}).get("killed") == ["set:a=9"], p_weak)
    single = MCheck("k.one_id", ["ZZA-003"], "MUST", fetch, pred, muts)
    p_single = per_id(single)
    case("(4) single-id check: every mutation is the id's (dedicated by construction)",
         p_single == {"ZZA-003": {"declared": muts, "killed": muts}}, p_single)

    # the gate's pure assessment over attributions + records
    try:
        fn = assess
    except NameError as e:
        case("assess() exists", False, str(e))
        fn = None
    if fn is not None:
        def entry(chk, key):
            return {"key": key, "check_id": chk.id, "rids": list(chk.req_ids),
                    "kills": dict(getattr(chk, "kills", None) or {}), "n_kills": len(chk.mutations)}
        recs = {"merchant_checks:k.two_ids": p_plain, "merchant_checks:k.two_ids_kills": per_id(good) if good else None,
                "merchant_checks:k.two_ids_weak": per_id(weak) if weak else None,
                "merchant_checks:k.one_id": p_single}
        rc, findings, stats = fn([entry(plain, "merchant_checks:k.two_ids")], recs, enforce=True)
        case("(5) shared attribution -> rc 1 naming both ids",
             rc == 1 and stats["shared"] == 2 and any("ZZA-002" in f for f in findings), (rc, findings, stats))
        if good is not None:
            rc, findings, stats = fn([entry(good, "merchant_checks:k.two_ids_kills")], recs, enforce=True)
            case("(6) per-id kills declared and observed -> rc 0, 0 shared",
                 rc == 0 and stats["shared"] == 0 and stats["dedicated"] == 2, (rc, findings, stats))
            rc, findings, stats = fn([entry(weak, "merchant_checks:k.two_ids_weak")], recs, enforce=True)
            case("(7) declared but surviving kill -> rc 1 (unkilled ZZA-002)",
                 rc == 1 and stats["unkilled"] == 1 and any("ZZA-002" in f for f in findings), (rc, findings, stats))
        rc, findings, stats = fn([entry(single, "merchant_checks:k.one_id")], {}, enforce=True)
        case("(8) single-id check without a record -> dedicated, reported unrecorded, rc 0",
             rc == 0 and stats["dedicated"] == 1 and stats["unrecorded"] == 1, (rc, findings, stats))
        rc, findings, stats = fn([entry(plain, "merchant_checks:k.two_ids")], recs, enforce=False)
        case("(9) report-only (older version): shared reported, rc 0", rc == 0 and stats["shared"] == 2, (rc, stats))
        # positive control (real registry, 2026-08-25): CAT-042/CAT-043 is a two-id struct
        # check — the gate must SEE it as shared unless kills= names a negative per id.
        try:
            ents = attributions("2026-08-25")
            cat = [e for e in ents if e["check_id"] == "catalog.unit_price_comparator_formula"]
            case("(10) positive control: CAT-042/043 enumerated at 2026-08-25 as a two-id attribution",
                 len(cat) == 1 and sorted(cat[0]["rids"]) == ["CAT-042", "CAT-043"], cat)
        except NameError as e:
            case("(10) positive control: CAT-042/043 enumerated at 2026-08-25", False, str(e))
    print(f"\nreq-kills selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="per-req_id kill attribution gate (D1-12)")
    ap.add_argument("--version", help="spec version to assess")
    ap.add_argument("--records", help="run-record directory (validate_merchant_checks/golden_check/struct_check --record)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.version:
        ap.print_usage(); return 2
    try:
        return run(a.version, a.records)
    except NameError as e:
        print(f"req-kills: FAIL — no gate yet ({e})"); return 1


if __name__ == "__main__":
    sys.exit(main())

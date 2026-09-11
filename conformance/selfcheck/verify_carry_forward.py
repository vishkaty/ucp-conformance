#!/usr/bin/env python3
"""
verify_carry_forward.py — the register CARRY-FORWARD gate (D2-09, PLAN-v3 §2.4 / D2 design
§A15): every row of the previous register version is accounted for exactly once in the
next one — carried, renamed, reworded, dead (with a mechanical dead-proof), merged
(into a surviving row) or downgraded — so a requirement can never fall out of the
register between versions unnoticed, and a "carried" row can never silently change
its quote.

Data: conformance/requirements/<to>/carry_forward.json
  {"from": "2026-04-08", "to": "2026-08-25", "spec_pins": {"from": "a2d8bf0b", "to": "cd78fb38"},
   "entries": [{"id": "CAT-006", "disposition": "dead", "reason": "…", "search_terms": ["…"]},
               {"id": "PAY-033", "disposition": "renamed", "to_id": "PAY-033", "what": "…"},
               {"id": "CHK-043", "disposition": "merged", "into": "CHK-001", "on": "2026-09-10"},
               {"id": "CHK-001", "disposition": "carried"}, …]}

Invariants (evaluate() is pure; main() does the I/O):
  1. every `from`-version row id appears EXACTLY once in entries (rows the `from` version
     itself received by backport from `to` — lineage.disposition == backported — are the
     reverse direction, D2-15, and are checked separately: their `to` counterpart must
     exist with the same id and a normalized-identical quote);
  2. carried + renamed + reworded + dead + merged + downgraded == the `from` row count;
  3. every `to` row with lineage.from == `from` has an entry with disposition in
     {carried, renamed, reworded} whose to_id (default: id) is that row; a `to` row
     without lineage must not reuse a `from` id unless an entry maps onto it;
  4. carried rows: norm(quote_to) == norm(quote_from), else the `to` row carries
     lineage.drift_note (the P1 "quote drift" review as data);
  5. dead rows: each search_terms term has 0 hits in the `to` spec tree outside code
     fences (mechanical dead-proof; the caller supplies the hit counts);
  6. merged rows: `into` (id or list of ids) exists in the `to` rows.

  python3 conformance/selfcheck/verify_carry_forward.py            # gate (rc 1 on any failure)
  python3 conformance/selfcheck/verify_carry_forward.py --selftest # hermetic fixtures
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQ_DIR = ROOT / "conformance" / "requirements"
VENDOR = ROOT / "conformance" / ".vendor"
sys.path.insert(0, str(ROOT / "conformance"))
from common.spec_versions import VERSION_TREE  # noqa: E402

DISPOSITIONS = ("carried", "renamed", "reworded", "dead", "merged", "downgraded")
FORWARD = ("carried", "renamed", "reworded")


def norm(s):
    s = (s or "").replace("**", "").replace("`", "").replace("_", "")
    s = s.replace("|", " ").replace("…", "...")
    return re.sub(r"\s+", " ", s).strip().lower()


# ------------------------------------------------------------------ selftest fixtures
def _fixture():
    from_rows = [
        {"id": "A-001", "quote": "The server **MUST** reply."},
        {"id": "A-002", "quote": "Clients MUST send a key."},
        {"id": "A-003", "quote": "Pages MUST hold at least 10 items."},
        {"id": "A-004", "quote": "Totals MUST balance."},
        {"id": "A-005", "quote": "Old duplicate of A-004."},
    ]
    to_rows = [
        {"id": "A-001", "quote": "The server MUST reply.", "lineage": {"from": "V1", "disposition": "carried"}},
        {"id": "A-002", "quote": "Platforms MUST send a key.", "lineage": {"from": "V1", "disposition": "reworded"}},
        {"id": "A-004", "quote": "Totals MUST balance.", "lineage": {"from": "V1", "disposition": "carried"}},
        {"id": "B-001", "quote": "A brand-new rule."},
    ]
    cf = {"from": "V1", "to": "V2", "entries": [
        {"id": "A-001", "disposition": "carried"},
        {"id": "A-002", "disposition": "reworded"},
        {"id": "A-003", "disposition": "dead", "search_terms": ["at least 10 items"]},
        {"id": "A-004", "disposition": "carried"},
        {"id": "A-005", "disposition": "merged", "into": "A-004"},
    ]}
    hits = {("A-003", "at least 10 items"): 0}
    return cf, from_rows, to_rows, hits


def selftest():
    bad = 0

    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1

    cf, fr, to, hits = _fixture()
    try:
        counts, errs = evaluate(cf, fr, to, hits)
        bad += case("sums correct (5 = carried 2 · reworded 1 · dead 1 · merged 1) -> PASS",
                    errs == [] and counts["carried"] == 2 and counts["merged"] == 1 and counts["total"] == 5,
                    repr((counts, errs))[:200])
        # old id missing from the entries
        cf2 = dict(cf, entries=[e for e in cf["entries"] if e["id"] != "A-005"])
        _, errs = evaluate(cf2, fr, to, hits)
        bad += case("old id absent from entries -> FAIL naming it",
                    any("A-005" in e for e in errs), repr(errs)[:200])
        # carried row whose quote changed, no drift_note
        to2 = [dict(r) for r in to]
        to2[0]["quote"] = "The server MUST reply within 5 seconds."
        _, errs = evaluate(cf, fr, to2, hits)
        bad += case("carried row with a changed quote and no drift_note -> FAIL",
                    any("A-001" in e and "drift" in e for e in errs), repr(errs)[:200])
        to2[0]["lineage"] = dict(to2[0]["lineage"], drift_note="reflowed; SLA sentence added upstream")
        _, errs = evaluate(cf, fr, to2, hits)
        bad += case("… with a drift_note -> PASS", errs == [], repr(errs)[:200])
        # dead term still present in the `to` tree
        _, errs = evaluate(cf, fr, to, {("A-003", "at least 10 items"): 2})
        bad += case("dead row whose search term still has hits -> FAIL",
                    any("A-003" in e and "dead" in e for e in errs), repr(errs)[:200])
        # a `to` row claims lineage from an id the entries call dead
        cf3 = dict(cf, entries=[dict(e, disposition="dead", search_terms=["x"]) if e["id"] == "A-004" else e
                                for e in cf["entries"]])
        _, errs = evaluate(cf3, fr, to, dict(hits, **{("A-004", "x"): 0}))
        bad += case("entry says dead but a `to` row carries lineage from it -> FAIL",
                    any("A-004" in e for e in errs), repr(errs)[:200])
        # merged into a row that does not exist
        cf4 = dict(cf, entries=[dict(e, into="Z-999") if e["id"] == "A-005" else e for e in cf["entries"]])
        _, errs = evaluate(cf4, fr, to, hits)
        bad += case("merged `into` an id absent from the `to` rows -> FAIL",
                    any("A-005" in e and "Z-999" in e for e in errs), repr(errs)[:200])
        # reverse direction (D2-15): a backported row in `from` must exist in `to` verbatim
        fr5 = fr + [{"id": "LOY-001", "quote": "Loyalty MUST verify.", "lineage": {"from": "V2", "disposition": "backported"}}]
        _, errs = evaluate(cf, fr5, to, hits)
        bad += case("backported `from` row without a `to` counterpart -> FAIL (and not counted in the 5)",
                    any("LOY-001" in e for e in errs), repr(errs)[:200])
        to5 = to + [{"id": "LOY-001", "quote": "Loyalty **MUST** verify."}]
        counts, errs = evaluate(cf, fr5, to5, hits)
        bad += case("… with the counterpart present -> PASS, total still 5",
                    errs == [] and counts["total"] == 5 and counts["backported"] == 1, repr((counts, errs))[:200])
    except NameError as e:
        print(f"  ✗ carry-forward fixtures: {e}")
        bad += 1
    print(f"\ncarry-forward selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

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


# ------------------------------------------------------------------ pure evaluation
def evaluate(cf, from_rows, to_rows, dead_term_hits):
    """-> (counts, errors). `from_rows` / `to_rows` are the rows of the two versions,
    `dead_term_hits` = {(id, term): hits in the `to` spec tree outside fences} for every
    dead entry's search term (the caller does that I/O). Pure, hermetic."""
    errs = []
    from_v, to_v = cf.get("from"), cf.get("to")
    entries = cf.get("entries", [])
    from_by_id = {r["id"]: r for r in from_rows}
    to_by_id = {r["id"]: r for r in to_rows}
    # reverse direction (D2-15): rows the `from` version received by backport from `to`
    backported = {i for i, r in from_by_id.items()
                  if (r.get("lineage") or {}).get("disposition") == "backported"}
    for i in sorted(backported):
        src = (from_by_id[i].get("lineage") or {}).get("from")
        if src != to_v:
            errs.append(f"{i}: backported into {from_v} from {src!r}, expected {to_v}")
        t = to_by_id.get(i)
        if t is None:
            errs.append(f"{i}: backported into {from_v} but no {to_v} row with that id exists")
        elif norm(t.get("quote")) != norm(from_by_id[i].get("quote")):
            errs.append(f"{i}: backported row's quote differs from its {to_v} source row (normalized)")
    forward_ids = set(from_by_id) - backported
    # 1. exactly once
    seen = {}
    for e in entries:
        i, d = e.get("id"), e.get("disposition")
        if d not in DISPOSITIONS:
            errs.append(f"{i}: disposition {d!r} not in {DISPOSITIONS}")
        if i in seen:
            errs.append(f"{i}: listed twice in carry_forward.json")
        seen[i] = e
        if i not in forward_ids:
            errs.append(f"{i}: entry has no {from_v} row (or the row is a backport)")
    for i in sorted(forward_ids - set(seen)):
        errs.append(f"{i}: {from_v} row has no carry_forward entry (carried / renamed / reworded / dead / merged / downgraded?)")
    # 2. sums
    counts = {d: 0 for d in DISPOSITIONS}
    for e in entries:
        if e.get("disposition") in counts:
            counts[e["disposition"]] += 1
    counts["total"] = len(forward_ids)
    counts["backported"] = len(backported)
    if sum(counts[d] for d in DISPOSITIONS) != counts["total"] and not any("no carry_forward entry" in x or "listed twice" in x for x in errs):
        errs.append(f"dispositions sum {sum(counts[d] for d in DISPOSITIONS)} != {from_v} rows {counts['total']}")
    # 3. forward lineage agreement
    target_of = {}
    for e in entries:
        if e.get("disposition") in FORWARD:
            target_of[e.get("to_id") or e["id"]] = e
    drift_flagged = 0
    for t in to_rows:
        lin = t.get("lineage") or {}
        if lin.get("from") == from_v and lin.get("disposition") != "backported":
            e = target_of.get(t["id"])
            if e is None:
                errs.append(f"{t['id']}: {to_v} row carries lineage from {from_v} but no carried/renamed/reworded entry maps onto it")
                continue
            # 4. carried quote fidelity
            if e["disposition"] == "carried":
                src = from_by_id.get(e["id"])
                if src is not None and norm(src.get("quote")) != norm(t.get("quote")):
                    if not str(lin.get("drift_note", "")).strip():
                        errs.append(f"{t['id']}: carried from {from_v} but the normalized quote differs and lineage has no drift_note")
                    else:
                        drift_flagged += 1
        elif not lin and t["id"] in forward_ids and t["id"] not in target_of:
            errs.append(f"{t['id']}: {to_v} row reuses a {from_v} id with no lineage and no entry mapping onto it")
    counts["drift_flagged"] = drift_flagged
    # 5. dead-proof
    for e in entries:
        if e.get("disposition") != "dead":
            continue
        terms = e.get("search_terms") or []
        if not terms:
            errs.append(f"{e['id']}: dead entry without search_terms (no mechanical dead-proof)")
        for term in terms:
            h = dead_term_hits.get((e["id"], term))
            if h is None:
                errs.append(f"{e['id']}: dead-proof term {term!r} was not scanned")
            elif h:
                errs.append(f"{e['id']}: dead entry but its search term {term!r} still has {h} hit(s) in the {to_v} spec tree")
        if e["id"] in target_of:
            errs.append(f"{e['id']}: dead entry but a {to_v} row still claims lineage from it")
    # 6. merged targets exist
    for e in entries:
        if e.get("disposition") == "merged":
            into = e.get("into")
            for tgt in (into if isinstance(into, list) else [into]):
                if tgt not in to_by_id:
                    errs.append(f"{e['id']}: merged into {tgt!r} which is not a {to_v} row")
    return counts, errs


# ------------------------------------------------------------------ I/O
def load_rows(ver):
    rows = []
    vdir = REQ_DIR / ver
    for af in sorted(vdir.glob("*.json")):
        if af.name.startswith("_"):
            continue
        d = json.loads(af.read_text())
        rows += d.get("rows", []) if isinstance(d, dict) else []
    return rows


def term_hits(ver, term):
    """Hits of `term` (normalized, emphasis-insensitive) in the vendored spec tree of
    `ver` — docs/**/*.md outside code fences, plus source/**/*.json."""
    base = VENDOR / VERSION_TREE.get(ver, "ucp")
    nt = norm(term)
    hits = 0
    for f in sorted((base / "docs").rglob("*.md")):
        in_fence = False
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            st = raw.lstrip()
            if st.startswith("```") or st.startswith("~~~"):
                in_fence = not in_fence
                continue
            if not in_fence and nt in norm(raw):
                hits += 1
    for f in sorted((base / "source").rglob("*.json")):
        if nt in norm(f.read_text(encoding="utf-8", errors="replace")):
            hits += 1
    return hits


def run(cf_path):
    cf = json.loads(pathlib.Path(cf_path).read_text())
    from_rows, to_rows = load_rows(cf["from"]), load_rows(cf["to"])
    hits = {}
    for e in cf.get("entries", []):
        if e.get("disposition") == "dead":
            for term in e.get("search_terms") or []:
                hits[(e["id"], term)] = term_hits(cf["to"], term)
    return cf, evaluate(cf, from_rows, to_rows, hits)


def main(argv):
    files = [pathlib.Path(a) for a in argv] or sorted(REQ_DIR.glob("*/carry_forward.json"))
    if not files:
        print("carry-forward: no carry_forward.json under conformance/requirements/<ver>/ — nothing to verify")
        return 1
    rc = 0
    for f in files:
        cf, (c, errs) = run(f)
        for e in errs:
            print(f"  ✗ {e}")
        line = (f"carry-forward {cf['from']}→{cf['to']}: {c['total']} = carried {c['carried']} · renamed {c['renamed']} "
                f"· reworded {c['reworded']} · dead {c['dead']} · merged {c['merged']} · downgraded {c['downgraded']} "
                f"· drift-flagged {c['drift_flagged']} (all noted)"
                + (f" · backported {c['backported']} (reverse, {cf['to']}→{cf['from']})" if c['backported'] else ""))
        print(f"{line} — {'PASS' if not errs else f'FAIL ({len(errs)})'}")
        rc |= 1 if errs else 0
    return rc


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
        _, errs = evaluate(cf3, fr, to, {**hits, ("A-004", "x"): 0})
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

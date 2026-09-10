#!/usr/bin/env python3
"""
validate_killset_lock.py — no kill set can shrink or drift unnoticed (PLAN-v3 §2.2, D1-06).

Every public kill-rate claim rests on the kill sets: the mutation strings on MChecks and
engine Checks, the named golden mutants on golden_check rows, the negatives on struct
and schema-tier checks, the `kill_mutation` on agent checks. L5 (F8) proved that
deleting a mutation (`"status:201"` from idempotency.conflict_409 — R2) or a negative
(`{"ids": []}` from catalog.lookup_request_ids_required — M5) leaves every gate green:
nothing locked them. This gate compares the committed
conformance/selfcheck/killset_lock.json (written by gen_killset_lock.py) with the kill
sets as they are NOW:

  * an entry whose hash changed        -> red, naming module_stem:check_id (drift —
                                          regenerate DELIBERATELY, in the same commit)
  * an entry present now but not locked (or locked but gone) -> red, "drift, regenerate"
  * n_kills below its locked floor      -> red unless shrink_notes[key] carries an
                                          unexpired review_by (a shrink is a decision)
  * pins != conformance/SOURCES.lock.json -> red (a re-pin must regenerate)

    python3 conformance/selfcheck/validate_killset_lock.py            # the committed lock
    python3 conformance/selfcheck/validate_killset_lock.py --selftest # scratch-registry kill-tests
Exit 0 = locked, 0 drift, pins OK; 1 = drift/shrink/pin mismatch (named).
"""
import copy
import json
import pathlib
import sys
import tempfile
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "checks"))

LOCK = HERE / "killset_lock.json"


def validate(lock, entries, pins, today):
    """(rc, lines). Pure: `lock` is the committed lock dict, `entries` the freshly built
    entries {key: {kind, n_kills, hash}}, `pins` the live SOURCES.lock pins."""
    fails, lines = [], []
    locked = lock.get("entries", {})
    notes = lock.get("shrink_notes", {})
    if lock.get("pins") != pins:
        fails.append("pins differ from conformance/SOURCES.lock.json — re-pin means regenerate")
    for key in sorted(set(locked) - set(entries)):
        fails.append(f"locked kill set no longer exists: {key} (drift, regenerate)")
    for key in sorted(set(entries) - set(locked)):
        fails.append(f"unlocked kill set: {key} (drift, regenerate)")
    for key in sorted(set(entries) & set(locked)):
        cur, old = entries[key], locked[key]
        if cur["hash"] != old["hash"]:
            fails.append(f"kill set changed: {key} ({old['n_kills']} -> {cur['n_kills']} kills; "
                         f"drift, regenerate deliberately)")
        floor = old.get("n_kills_floor", old["n_kills"])
        if cur["n_kills"] < floor:
            note = notes.get(key)
            if not note:
                fails.append(f"kill set SHRANK below its floor without shrink_notes: {key} "
                             f"({cur['n_kills']} < floor {floor})")
            elif str(note.get("review_by", "")) < today:
                fails.append(f"shrink note expired for {key} (review_by {note.get('review_by')})")
    for key, note in notes.items():
        if key not in entries:
            fails.append(f"shrink note for an unknown kill set: {key}")
    counts = {}
    for e in entries.values():
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    order = ("MCheck", "engine", "schema-tier", "struct", "Row", "ACheck")
    summary = " · ".join(f"{counts.get(k, 0)} {k}" for k in order)
    lines.append(f"{summary} locked · {len(fails)} drift · pins {'OK' if lock.get('pins') == pins else 'MISMATCH'}")
    return (1 if fails else 0), fails + lines


# ---------------------------------------------------------------------------
# --selftest: a scratch registry, kill-tested (L5's M5 and R2 are the named cases)
# ---------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    try:
        import gen_killset_lock as gen
    except ImportError as e:
        print(f"  ✗ import gen_killset_lock — {e}")
        print("killset-lock: FAIL (script absent)")
        return 1
    from merchant_checks import MCheck
    from engine import CLEAN

    def dummy(*a, **k):
        return CLEAN

    SchemaCheck = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction")
    StructCheck = namedtuple("Check", "id req_ids fn valid negatives")
    Row = namedtuple("Row", "id req_ids evidence doc make_request predicate mutant")
    defects = {"mutants": [{"name": "planted-mutant", "route": {"method": "GET", "path": "/x"},
                            "patch": [{"op": "drop", "path": ["a"]}]}]}

    def registry():
        return [
            ("MCheck", "scratch", MCheck("idempotency.conflict_409", ["ZZZ-004"], "MUST", dummy, dummy,
                                          ["status:200", "status:201"]), None),
            ("MCheck", "scratch", MCheck("checkout.cancel", ["ZZZ-005"], "MUST", dummy, dummy,
                                          ["status:500", "drop:status"]), None),
            ("schema-tier", "scratch_schema",
             SchemaCheck("catalog.lookup_request_ids_required", ["ZZZ-028"], "schemas/x.json", "d",
                         {"ids": ["a"]}, [{}, {"ids": []}, {"ids": "a"}], "lookup", "request"), None),
            ("struct", "scratch_struct",
             StructCheck("capability.schema_required", ["ZZZ-001"], dummy,
                         [({"a": 1},)], [({"a": None},), ({},)]), None),
            ("Row", "scratch_golden", Row("SIG-007", ["SIG-007"], "fixture-schema", "doc", dummy, dummy,
                                          "planted-mutant"), defects),
        ]

    pins = {"spec": {"2026-08-25": "abc123"}, "schema_validator": "def456"}
    today = "2026-09-10"
    base = gen.build_entries(registry())
    check("entries keyed module_stem:check_id",
          set(base) == {"scratch:idempotency.conflict_409", "scratch:checkout.cancel",
                        "scratch_schema:catalog.lookup_request_ids_required",
                        "scratch_struct:capability.schema_required", "scratch_golden:SIG-007"},
          f"{sorted(base)}")
    check("n_kills per kind", (base["scratch:idempotency.conflict_409"]["n_kills"],
                                base["scratch_schema:catalog.lookup_request_ids_required"]["n_kills"],
                                base["scratch_struct:capability.schema_required"]["n_kills"],
                                base["scratch_golden:SIG-007"]["n_kills"]) == (2, 3, 2, 1),
          f"{ {k: v['n_kills'] for k, v in base.items()} }")
    lock = gen.generate(base, pins, previous=None, shrink_notes={}, today=today)
    rc, lines = validate(lock, base, pins, today)
    check("fresh lock validates green", rc == 0, "\n".join(lines))
    check("summary line", any(l.endswith("locked · 0 drift · pins OK") for l in lines), lines[-1])

    # (a) R2: delete mutation "status:201" from idempotency.conflict_409 -> red naming module:id
    reg = registry()
    reg[0][2].mutations.remove("status:201")
    rc, lines = validate(lock, gen.build_entries(reg), pins, today)
    check("(a) R2 deleted mutation -> red naming scratch:idempotency.conflict_409",
          rc == 1 and any("scratch:idempotency.conflict_409" in l for l in lines), "\n".join(lines))
    # (a') M5: delete the {"ids": []} negative from the schema-tier check -> red naming it
    reg = registry()
    sc = reg[2][2]
    reg[2] = ("schema-tier", "scratch_schema", sc._replace(negatives=[n for n in sc.negatives if n != {"ids": []}]), None)
    rc, lines = validate(lock, gen.build_entries(reg), pins, today)
    check("(a) M5 deleted negative -> red naming scratch_schema:catalog.lookup_request_ids_required",
          rc == 1 and any("scratch_schema:catalog.lookup_request_ids_required" in l for l in lines),
          "\n".join(lines))
    # Row: change the golden mutant's patch -> red
    reg = registry()
    reg[4] = ("Row", "scratch_golden", reg[4][2],
              {"mutants": [{"name": "planted-mutant", "route": {"method": "GET", "path": "/x"},
                            "patch": [{"op": "drop", "path": ["b"]}]}]})
    rc, lines = validate(lock, gen.build_entries(reg), pins, today)
    check("Row mutant patch changed -> red naming scratch_golden:SIG-007",
          rc == 1 and any("scratch_golden:SIG-007" in l for l in lines), "\n".join(lines))
    # struct: drop a negative -> red
    reg = registry()
    st = reg[3][2]
    reg[3] = ("struct", "scratch_struct", st._replace(negatives=st.negatives[:1]), None)
    rc, lines = validate(lock, gen.build_entries(reg), pins, today)
    check("struct negative dropped -> red naming scratch_struct:capability.schema_required",
          rc == 1 and any("scratch_struct:capability.schema_required" in l for l in lines), "\n".join(lines))

    # (b) add a mutation -> red "drift, regenerate"
    reg = registry()
    reg[1][2].mutations.append("corrupt-json")
    rc, lines = validate(lock, gen.build_entries(reg), pins, today)
    check("(b) added mutation -> red 'drift, regenerate'",
          rc == 1 and any("scratch:checkout.cancel" in l and "regenerate" in l for l in lines),
          "\n".join(lines))
    # a NEW check without a lock entry is also drift
    reg = registry() + [("MCheck", "scratch", MCheck("new.check", ["X-1"], "MUST", dummy, dummy, ["empty"]), None)]
    rc, lines = validate(lock, gen.build_entries(reg), pins, today)
    check("(b) unlocked new check -> red 'drift, regenerate'",
          rc == 1 and any("scratch:new.check" in l and "regenerate" in l for l in lines), "\n".join(lines))

    # (c) regenerate after a shrink WITHOUT shrink_notes -> refused (red)
    reg = registry()
    reg[0][2].mutations.remove("status:201")
    shrunk = gen.build_entries(reg)
    try:
        gen.generate(shrunk, pins, previous=lock, shrink_notes={}, today=today)
        check("(c) regenerate after shrink without shrink_notes -> refused", False, "generate() accepted the shrink")
    except gen.ShrinkWithoutNote as e:
        check("(c) regenerate after shrink without shrink_notes -> refused",
              "scratch:idempotency.conflict_409" in str(e), str(e))
    # …and WITH an unexpired note it is accepted, floor lowered, validates green
    notes = {"scratch:idempotency.conflict_409": {"note": "status:201 was unreachable (selftest)",
                                                  "review_by": "2099-01-01"}}
    lock2 = gen.generate(shrunk, pins, previous=lock, shrink_notes=notes, today=today)
    rc, lines = validate(lock2, shrunk, pins, today)
    check("(c) shrink with an unexpired note regenerates and validates green", rc == 0, "\n".join(lines))
    check("(c) floor lowered to the noted size",
          lock2["entries"]["scratch:idempotency.conflict_409"]["n_kills_floor"] == 1)
    # a lock whose floor is above the current size and whose note EXPIRED -> red
    lock3 = copy.deepcopy(lock2)
    lock3["entries"]["scratch:idempotency.conflict_409"]["n_kills_floor"] = 2
    lock3["shrink_notes"]["scratch:idempotency.conflict_409"]["review_by"] = "2026-01-01"
    rc, lines = validate(lock3, shrunk, pins, today)
    check("(c) expired shrink note -> red", rc == 1 and any("expired" in l for l in lines), "\n".join(lines))
    # a lock edited by hand to hide a shrink (floor lowered, no note) -> the FLOOR is what
    # the generator writes; the validator trusts the lock, so this case is the generator's
    # refusal above (c). Pin it: the floor never drops silently on regeneration.
    lock4 = gen.generate(base, pins, previous=lock2, shrink_notes=notes, today=today)
    check("(c) floor ratchets back up when kills grow",
          lock4["entries"]["scratch:idempotency.conflict_409"]["n_kills_floor"] == 2)

    # (d) pin mismatch -> red
    rc, lines = validate(lock, base, {"spec": {"2026-08-25": "moved"}, "schema_validator": "def456"}, today)
    check("(d) pin mismatch -> red", rc == 1 and any("pins" in l for l in lines), "\n".join(lines))

    # round-trip through a file
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "killset_lock.json"
        gen.write_lock(lock, p)
        rc, lines = validate(json.loads(p.read_text()), base, pins, today)
        check("lock round-trips through JSON", rc == 0, "\n".join(lines))

    print(f"killset-lock selftest: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    import gen_killset_lock as gen
    import time
    if not LOCK.exists():
        print(f"killset-lock: FAIL — {LOCK.relative_to(ROOT)} missing; run gen_killset_lock.py")
        return 1
    lock = json.loads(LOCK.read_text())
    entries = gen.build_entries(gen.real_registry())
    rc, lines = validate(lock, entries, gen.real_pins(), time.strftime("%Y-%m-%d"))
    for l in lines[:-1]:
        print(f"  x {l}")
    print(f"killset-lock: {'PASS' if rc == 0 else 'FAIL'} — {lines[-1]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

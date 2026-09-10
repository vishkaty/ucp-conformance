#!/usr/bin/env python3
"""
gen_killset_lock.py — write conformance/selfcheck/killset_lock.json: a per-check hash of
every kill set in the suite, so validate_killset_lock.py can red any silent shrink or
drift (PLAN-v3 §2.2, D1-06).

Kinds and what is hashed (keyed `module_stem:check_id`):
  MCheck       checks/merchant_checks*.py + tls_check_01_11_01_23  — mutation strings (+ the
               optional per-id `kills` map, D1-12)
  engine       checks/v2026_*.py core + area_*.py via the manifest loaders — same fields
  schema-tier  checks/schema_check*.py namedtuple rows — every non-callable field (valid,
               negatives, controls, removed_op…) + sha256 of any fixture file a field names
  struct       checks/struct_check_08_25.py — negatives + valid payloads
  Row          checks/golden_check_08_25.py rows — the named golden mutant's route + patch
               from testbed/golden-0825/server/defects_config.json
  ACheck       agent/agent_checks.py — kill_mutation + sha256 of reference_agent.DEFECTS

`n_kills_floor` never drops on regeneration unless `shrink_notes[key]` carries an
unexpired `review_by` (a shrink is a reviewed decision, never a side effect); the
generator REFUSES a shrink without a note. Pins are copied from SOURCES.lock.json and
must match at validation time.

    python3 conformance/selfcheck/gen_killset_lock.py            # (re)generate the lock
    python3 conformance/selfcheck/gen_killset_lock.py --check    # dry run: print the summary
Contract: every task that adds or edits a check module regenerates the lock in the same
commit (D3-08/10/23/25 carry this edge).
"""
import glob
import hashlib
import importlib
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CHK = ROOT / "conformance" / "checks"
AGENT = ROOT / "conformance" / "agent"
FIXTURES = HERE / "fixtures"
LOCK = HERE / "killset_lock.json"
SOURCES_LOCK = ROOT / "conformance" / "SOURCES.lock.json"
DEFECTS_CONFIG = ROOT / "conformance" / "testbed" / "golden-0825" / "server" / "defects_config.json"

for p in (str(CHK), str(HERE), str(AGENT)):
    if p not in sys.path:
        sys.path.insert(0, p)


class ShrinkWithoutNote(RuntimeError):
    pass


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _file_sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# per-kind hashing
# ---------------------------------------------------------------------------
def _mutations_entry(chk):
    muts = list(chk.mutations)
    kills = getattr(chk, "kills", None) or {}
    return {"n_kills": len(muts), "hash": _sha({"mutations": muts, "kills": kills})}


def _row_entry(row, defects):
    names = row.mutant if isinstance(row.mutant, (list, tuple)) else [row.mutant]
    table = {}
    for kind in ("mutants", "self_referenced_mutants", "fixture_only"):
        for m in (defects or {}).get(kind, []):
            table[m["name"]] = m
    resolved = []
    for n in names:
        if n is None:
            continue
        m = table.get(n)
        if m is None:
            raise KeyError(f"golden row {row.id} names mutant {n!r} absent from defects_config.json")
        resolved.append({"name": n, "route": m.get("route"), "patch": m.get("patch"),
                         "fixture": m.get("fixture")})
    return {"n_kills": len(resolved), "hash": _sha(resolved)}


def _struct_entry(chk):
    return {"n_kills": len(chk.negatives),
            "hash": _sha({"negatives": chk.negatives, "valid": chk.valid})}


def _schema_entry(row):
    d = {}
    for k, v in row._asdict().items():
        if callable(v):
            continue
        d[k] = v
        if isinstance(v, str) and v.endswith(".json") and (FIXTURES / v).exists():
            d[f"{k}__sha"] = _file_sha(FIXTURES / v)
    negs = getattr(row, "negatives", None)
    n = len(negs) if isinstance(negs, (list, tuple)) else 1      # RCheck & co: one removal
    return {"n_kills": n, "hash": _sha(d)}


def _acheck_entry(chk, defects_sha):
    return {"n_kills": 1, "hash": _sha({"kill_mutation": chk.kill_mutation, "defects": defects_sha})}


def entry_for(kind, obj, extra):
    if kind in ("MCheck", "engine"):
        e = _mutations_entry(obj)
    elif kind == "Row":
        e = _row_entry(obj, extra)
    elif kind == "struct":
        e = _struct_entry(obj)
    elif kind == "schema-tier":
        e = _schema_entry(obj)
    elif kind == "ACheck":
        e = _acheck_entry(obj, extra)
    else:
        raise ValueError(f"unknown kind {kind}")
    e["kind"] = kind
    return e


def build_entries(registry):
    """registry: iterable of (kind, module_stem, check, extra) -> {key: entry}. A key
    that collides (same stem + id twice) is an error: the lock must be unambiguous."""
    out = {}
    for kind, stem, obj, extra in registry:
        key = f"{stem}:{obj.id}"
        if key in out:
            raise KeyError(f"duplicate kill-set key {key} — the lock needs distinct ids per module")
        out[key] = entry_for(kind, obj, extra)
    return out


# ---------------------------------------------------------------------------
# the real registry
# ---------------------------------------------------------------------------
def real_registry():
    reg = []
    # MCheck: the same enumeration merchant_checks.all_checks() performs, stem-aware
    import merchant_checks
    reg += [("MCheck", "merchant_checks", c, None) for c in merchant_checks.CHECKS]
    for f in sorted(glob.glob(str(CHK / "merchant_checks_*.py"))):
        stem = pathlib.Path(f).stem
        mod = importlib.import_module(stem)
        for attr in sorted(dir(mod)):
            if attr.startswith("CHECKS"):
                reg += [("MCheck", stem, c, None) for c in getattr(mod, attr)]
    import tls_check_01_11_01_23
    reg += [("MCheck", "tls_check_01_11_01_23", c, None) for c in tls_check_01_11_01_23.CHECKS_TLS]
    # engine: core + manifest-loaded areas, exactly as the two suite runners load them
    import run_01_23, run_04_08
    for runner in (run_01_23, run_04_08):
        for c in runner.collect():
            stem = pathlib.Path(sys.modules[c.fetch_fn.__module__].__file__).stem \
                if c.fetch_fn.__module__ in sys.modules else "engine"
            reg.append(("engine", stem, c, None))
    # schema-tier: every namedtuple row list in checks/schema_check*.py
    for f in sorted(glob.glob(str(CHK / "schema_check*.py"))):
        stem = pathlib.Path(f).stem
        mod = importlib.import_module(stem)
        for attr in sorted(dir(mod)):
            val = getattr(mod, attr)
            if attr.isupper() and isinstance(val, (list, tuple)) and val \
               and all(hasattr(x, "_fields") and "id" in x._fields for x in val):
                reg += [("schema-tier", stem, r, None) for r in val]
    # struct
    import struct_check_08_25
    reg += [("struct", "struct_check_08_25", c, None) for c in struct_check_08_25.CHECKS]
    # Row (golden-0825 rows, named mutants from defects_config)
    import golden_check_08_25
    defects = json.loads(DEFECTS_CONFIG.read_text())
    reg += [("Row", "golden_check_08_25", r, defects) for r in golden_check_08_25.CHECKS]
    # ACheck
    import agent_checks, reference_agent
    dsha = _sha({str(k): v for k, v in reference_agent.DEFECTS.items()})
    reg += [("ACheck", "agent_checks", c, dsha) for c in agent_checks.CHECKS]
    return reg


def real_pins():
    d = json.loads(SOURCES_LOCK.read_text())
    pins = {"spec": {v: e.get("commit") for v, e in d.get("spec", {}).get("versions", {}).items()}}
    for k in ("schema_validator", "reference_sample_server", "reference_sdk", "ap2_reference"):
        if isinstance(d.get(k), dict):
            pins[k] = d[k].get("commit") or d[k].get("pypi_pin") or d[k].get("ref")
    return pins


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
def generate(entries, pins, previous=None, shrink_notes=None, today=None):
    """A new lock dict. `previous` (the committed lock) supplies floors; a key whose
    n_kills fell below its floor needs shrink_notes[key] with review_by >= today."""
    today = today or time.strftime("%Y-%m-%d")
    prev_entries = (previous or {}).get("entries", {})
    notes = dict((previous or {}).get("shrink_notes", {}))
    notes.update(shrink_notes or {})
    out, shrunk = {}, []
    for key, e in sorted(entries.items()):
        floor = max(prev_entries.get(key, {}).get("n_kills_floor", 0), e["n_kills"])
        if e["n_kills"] < prev_entries.get(key, {}).get("n_kills_floor", 0):
            note = notes.get(key)
            if note and str(note.get("review_by", "")) >= today:
                floor = e["n_kills"]
            else:
                shrunk.append(f"{key} ({e['n_kills']} < floor {prev_entries[key]['n_kills_floor']})")
        out[key] = {"kind": e["kind"], "n_kills": e["n_kills"], "n_kills_floor": floor, "hash": e["hash"]}
    if shrunk:
        raise ShrinkWithoutNote("refusing to regenerate: kill set(s) shrank without an unexpired "
                                "shrink_notes entry: " + "; ".join(shrunk))
    counts = {}
    for e in out.values():
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {"_about": ("Kill-set lock (D1-06): per-check sha256 of every kill set, keyed "
                       "module_stem:check_id. Regenerate DELIBERATELY with gen_killset_lock.py in "
                       "the same commit as a check change; a shrink needs shrink_notes[key] with "
                       "a review_by date. validate_killset_lock.py reds any drift."),
            "generated": today, "pins": pins, "counts": counts,
            "shrink_notes": {k: v for k, v in notes.items() if k in out},
            "entries": out}


def write_lock(lock, path=LOCK):
    pathlib.Path(path).write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")


def main():
    entries = build_entries(real_registry())
    pins = real_pins()
    previous = json.loads(LOCK.read_text()) if LOCK.exists() else None
    try:
        lock = generate(entries, pins, previous=previous)
    except ShrinkWithoutNote as e:
        print(f"gen_killset_lock: {e}")
        return 1
    summary = " · ".join(f"{lock['counts'].get(k, 0)} {k}"
                         for k in ("MCheck", "engine", "schema-tier", "struct", "Row", "ACheck"))
    if "--check" in sys.argv:
        print(f"would lock: {summary}")
        return 0
    write_lock(lock)
    print(f"wrote {LOCK.relative_to(ROOT)}: {summary} ({len(lock['entries'])} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

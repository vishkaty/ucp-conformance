#!/usr/bin/env python3
"""
add_expiry_clocks.py — stamp the expiry clock (`review_by` + `spec_pin`) on every entry
of every register in expiry_registers.json that lacks one (D2-04; horizons and stagger
per clock_tier from D2-19 / decision 23). Idempotent: an entry that already carries
both hands is never touched, so re-running after a partial stamp changes nothing.

  review_by  = seed date + horizon(clock_tier), staggered per register (D2-19) so the
               seed batch expires as a trickle, never a cliff;
  spec_pin   = the lock's 8-hex commit for the entry's version (a {version: pin} map
               when the entry spans several versions).

The seed batch is recorded in review_signoffs.json as `<seed.batch>` with the A10
sample contract: a deterministic >=10% sample (seed recorded, so the selection is
reproducible) whose `human_review` block the OWNER flips from `pending` to `recorded`
after re-reading the sampled entries — this script never claims a human review.

Usage: python3 conformance/coverage/add_expiry_clocks.py [--dry-run] [--seed-date YYYY-MM-DD]
"""
import argparse, json, math, os, random, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONF = os.path.join(ROOT, "conformance")
sys.path.insert(0, CONF)
sys.path.insert(0, os.path.join(CONF, "selfcheck"))
from common.spec_versions import VERSIONS                       # noqa: E402
import validate_expiry_clocks as vec                            # noqa: E402

SIGN = os.path.join(HERE, "review_signoffs.json")
HORIZON_DAYS = dict(vec.HORIZON_DAYS)                           # decision 23 (D2-19): 30 / 90
# Stagger (D2-19): within a register the entries round-robin over the tier's three
# offsets (moving: days 10/20/30; pin-only: 30/60/90 from the seed date), and each
# register of a tier is shifted back one more day than the previous (slot 0 lands on
# the offsets themselves, slot 1 a day earlier, ...) so no two registers share a date.
# Every date stays <= the tier's horizon, and the largest same-day share is about a
# third of the largest register — well under the 25% cliff guard for this corpus.
STAGGER = {"moving": (10, 20, 30), "pin-only": (30, 60, 90)}
HUMAN_REVIEW_WINDOW_DAYS = 7


def dump_like(path, doc):
    """Write `doc` in the byte style the file already uses (indent width and
    ensure_ascii), so a stamp is a minimal diff and never a reformat."""
    raw = open(path).read()
    orig = json.loads(raw)
    for ind in (1, 2, 4):
        for ea in (True, False):
            if json.dumps(orig, indent=ind, ensure_ascii=ea) + "\n" == raw:
                open(path, "w").write(json.dumps(doc, indent=ind, ensure_ascii=ea) + "\n")
                return
    open(path, "w").write(json.dumps(doc, indent=2) + "\n")


def review_by_for(tier, register_slot, index, seed_date):
    """The staggered review_by for entry `index` of the `register_slot`-th register of
    `tier`: seed + offsets[index % 3] - register_slot days (never past the horizon)."""
    offs = STAGGER[tier]
    days = offs[index % len(offs)] - register_slot
    return (seed_date + timedelta(days=max(days, 1))).isoformat()


def stamp(registers, seed_date, pins, dry_run=False, restamp=False):
    """Stamp missing hands (or, with `restamp`, recompute every review_by in the
    stamp-eligible registers — spec_pin untouched); returns [(entry, register)]."""
    stamped = []
    docs = {}
    slots = {"moving": 0, "pin-only": 0}
    for reg in registers:
        if reg.get("clock") == "none" or reg.get("scope") == "none":
            continue
        tier = reg.get("clock_tier", "pin-only")
        slot = slots[tier]; slots[tier] += 1
        path = os.path.join(ROOT, reg["file"])
        doc = docs.get(path) or json.load(open(path))
        docs[path] = doc
        for i, (ename, e) in enumerate(vec.iter_entries(reg, doc)):
            if not isinstance(e, dict):
                continue
            vers = vec.entry_versions(reg, e, VERSIONS)
            touched = False
            if restamp or not e.get("review_by"):
                rb = review_by_for(tier, slot, i, seed_date)
                if e.get("review_by") != rb:
                    e["review_by"] = rb; touched = True
            if vers and not e.get("spec_pin"):
                e["spec_pin"] = pins[vers[0]] if len(vers) == 1 else {v: pins[v] for v in vers}
                touched = True
            if "clock_tier" not in e:
                e["clock_tier"] = tier; touched = True
            if touched:
                stamped.append((f"{os.path.basename(reg['file'])} {ename}", reg["name"]))
    if not dry_run:
        for path, doc in docs.items():
            dump_like(path, doc)
    return stamped


def repin(registers, version, pins, dry_run=False):
    """D2-15: after SOURCES.lock.json re-pins `version`, every clocked entry in scope of
    that version carries the OLD 8-hex in `spec_pin` (pin-drift, by design: a re-pin
    invalidates the review made against the old pin). Re-stamp `spec_pin` for exactly
    those entries to the lock's new pin (string, or the version's slot of a map);
    review_by is untouched. Returns [(entry, register, old_pin)] for the batch record —
    the caller records the re-stamp as a sample batch (the human sample is the review)."""
    new_pin = pins[version]
    touched = []
    docs = {}
    for reg in registers:
        if reg.get("clock") == "none" or reg.get("scope") == "none":
            continue
        path = os.path.join(ROOT, reg["file"])
        doc = docs.get(path) or json.load(open(path))
        docs[path] = doc
        for ename, e in vec.iter_entries(reg, doc):
            if not isinstance(e, dict) or version not in vec.entry_versions(reg, e, VERSIONS):
                continue
            sp = e.get("spec_pin")
            if isinstance(sp, dict):
                if sp.get(version) != new_pin:
                    touched.append((f"{os.path.basename(reg['file'])} {ename}", reg["name"], sp.get(version)))
                    sp[version] = new_pin
            elif sp != new_pin:
                touched.append((f"{os.path.basename(reg['file'])} {ename}", reg["name"], sp))
                e["spec_pin"] = new_pin
    if not dry_run:
        for path, doc in docs.items():
            dump_like(path, doc)
    return touched


def record_repin_batch(version, old_pin, new_pin, touched, on, dry_run=False):
    """Record the re-stamp in review_signoffs.json as a `kind: sample` batch (decision 13:
    max(10%, 10) human sample, PENDING until the owner records it)."""
    import math, random
    names = sorted(t[0] for t in touched)
    size = min(len(names), max(10, math.ceil(0.10 * len(names))))
    ids = sorted(random.Random(int(on.replace("-", ""))).sample(names, size)) if names else []
    batch = {"batch": f"repin-{version}-{on}", "kind": "sample", "date": on,
             "reviewer": f"re-pin re-stamp (mechanical, add_expiry_clocks.py --repin {version}); the human sample below is the review",
             "spec_reverified": True,
             "notes": f"SOURCES.lock.json re-pinned {version} {old_pin} -> {new_pin} (decision 3, D2-15). Every clocked "
                      f"entry in scope of {version} ({len(names)}) had its spec_pin re-stamped to the new pin; review_by "
                      f"untouched. The re-pin's diff is loyalty.md + loyalty.json (new) and one example line in "
                      f"discount.md, and every pre-existing {version} register quote re-verifies verbatim at the new pin "
                      f"(register gate), so no entry's subject moved. The sample is a deterministic {size}/{len(names)} "
                      f"selection (random.Random({int(on.replace('-', ''))}).sample over the sorted entry names) the owner "
                      f"re-reads at {new_pin} and records as human_review.status=recorded; PENDING until then.",
             "sample": {"seed": int(on.replace("-", "")), "of": len(names), "size": size, "ids": ids,
                        "human_review": {"status": "pending", "by": None, "on": None,
                                         "due": (date.fromisoformat(on) + timedelta(days=7)).isoformat()}}}
    if dry_run:
        return batch
    path = os.path.join(ROOT, "conformance", "coverage", "review_signoffs.json")
    doc = json.load(open(path))
    doc["signoffs"] = [b for b in doc.get("signoffs", []) if b.get("batch") != batch["batch"]] + [batch]
    dump_like(path, doc)
    return batch


def record_seed_batch(seed, stamped, seed_date, dry_run=False):
    """The A10 sample contract on the seed batch (>=10%, seed-recorded, human pending)."""
    names = sorted(n for n, _ in stamped)
    k = max(math.ceil(0.10 * len(names)), 1) if names else 0
    rng_seed = int(seed_date.strftime("%Y%m%d"))
    sample = sorted(random.Random(rng_seed).sample(names, k)) if names else []
    entry = {
        "batch": seed["batch"], "date": seed_date.isoformat(),
        "reviewer": "expiry-clock seed (mechanical stamp by add_expiry_clocks.py; the human sample below is the review)",
        "spec_reverified": True,
        "notes": (f"Seed batch of the expiry-clock schema (D2-04/D2-19, decision 23): every entry of every "
                  f"register in expiry_registers.json received review_by (per clock_tier horizon, staggered by "
                  f"register) and spec_pin (lock commit per version). {len(names)} entries stamped. The sample "
                  f"below is a deterministic {k}/{len(names)} selection (random.Random({rng_seed}).sample over "
                  f"the sorted entry names) that the owner re-reads and records as human_review.status=recorded; "
                  f"until then the batch is PENDING and verify_review_signoffs reds past the due date."),
        "sample": {"seed": rng_seed, "of": len(names), "size": k, "ids": sample,
                   "human_review": {"status": "pending", "by": None, "on": None,
                                    "due": (seed_date + timedelta(days=HUMAN_REVIEW_WINDOW_DAYS)).isoformat()}},
    }
    d = json.load(open(SIGN))
    if any(s.get("batch") == seed["batch"] for s in d.get("signoffs", [])):
        return entry, False
    d["signoffs"].append(entry)
    if not dry_run:
        dump_like(SIGN, d)
    return entry, True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed-date")
    ap.add_argument("--repin", metavar="VERSION",
                    help="D2-15: re-stamp spec_pin to the lock's CURRENT pin for every entry in scope of "
                         "VERSION and record a sample batch (pass --old-pin for the batch note)")
    ap.add_argument("--old-pin", default="?")
    ap.add_argument("--restamp", action="store_true",
                    help="recompute every review_by with the current stagger (D2-19 migration); "
                         "spec_pin and the seed-batch sample record are untouched")
    a = ap.parse_args(argv)
    regfile = vec.load_registers()
    seed = regfile["seed"]
    seed_date = date.fromisoformat(a.seed_date or seed["date"])
    pins = vec.lock_pins()
    if a.repin:
        touched = repin(regfile["registers"], a.repin, pins, a.dry_run)
        on = a.seed_date or date.today().isoformat()
        b = record_repin_batch(a.repin, a.old_pin, pins[a.repin], touched, on, a.dry_run)
        print(f"repin {a.repin} -> {pins[a.repin]}: {len(touched)} entr{'y' if len(touched) == 1 else 'ies'} re-stamped; "
              f"batch {b['batch']} sample {b['sample']['size']}/{b['sample']['of']} human PENDING (due {b['sample']['human_review']['due']})"
              + (" [dry-run]" if a.dry_run else ""))
        return 0
    stamped = stamp(regfile["registers"], seed_date, pins, a.dry_run, a.restamp)
    by_reg = {}
    for _, r in stamped:
        by_reg[r] = by_reg.get(r, 0) + 1
    for r, n in sorted(by_reg.items()):
        print(f"  stamped {n:4} · {r}")
    if stamped and not a.restamp:
        entry, added = record_seed_batch(seed, stamped, seed_date, a.dry_run)
        print(f"  seed batch {seed['batch']}: {'recorded' if added else 'already recorded'} — "
              f"sample {entry['sample']['size']}/{entry['sample']['of']} (seed {entry['sample']['seed']}), "
              f"human_review {entry['sample']['human_review']['status']} due {entry['sample']['human_review']['due']}")
    print(f"add_expiry_clocks: {len(stamped)} entries stamped{' (dry run)' if a.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

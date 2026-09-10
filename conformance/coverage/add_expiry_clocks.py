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
HORIZON_DAYS = {"moving": 30, "pin-only": 90}                   # decision 23 (D2-19)
# stagger offsets per tier, applied round-robin over the registers of that tier
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


def horizon_for(reg, tier_slot):
    tier = reg.get("clock_tier", "pin-only")
    offs = STAGGER[tier]
    return offs[tier_slot % len(offs)]


def stamp(registers, seed_date, pins, dry_run=False):
    """Stamp missing hands; returns [(entry_name, register_name)] of stamped entries."""
    stamped = []
    docs = {}
    slots = {"moving": 0, "pin-only": 0}
    for reg in registers:
        if reg.get("clock") == "none" or reg.get("scope") == "none":
            continue
        tier = reg.get("clock_tier", "pin-only")
        days = horizon_for(reg, slots[tier]); slots[tier] += 1
        rb = (seed_date + timedelta(days=days)).isoformat()
        path = os.path.join(ROOT, reg["file"])
        doc = docs.get(path) or json.load(open(path))
        docs[path] = doc
        for ename, e in vec.iter_entries(reg, doc):
            if not isinstance(e, dict):
                continue
            vers = vec.entry_versions(reg, e, VERSIONS)
            touched = False
            if not e.get("review_by"):
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
    a = ap.parse_args(argv)
    regfile = vec.load_registers()
    seed = regfile["seed"]
    seed_date = date.fromisoformat(a.seed_date or seed["date"])
    pins = vec.lock_pins()
    stamped = stamp(regfile["registers"], seed_date, pins, a.dry_run)
    by_reg = {}
    for _, r in stamped:
        by_reg[r] = by_reg.get(r, 0) + 1
    for r, n in sorted(by_reg.items()):
        print(f"  stamped {n:4} · {r}")
    if stamped:
        entry, added = record_seed_batch(seed, stamped, seed_date, a.dry_run)
        print(f"  seed batch {seed['batch']}: {'recorded' if added else 'already recorded'} — "
              f"sample {entry['sample']['size']}/{entry['sample']['of']} (seed {entry['sample']['seed']}), "
              f"human_review {entry['sample']['human_review']['status']} due {entry['sample']['human_review']['due']}")
    print(f"add_expiry_clocks: {len(stamped)} entries stamped{' (dry run)' if a.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
validate_expiry_clocks.py — the expiry-clock gate (P-2 / PLAN-v3 §2.4, task D2-04).

Every entry of every register listed in conformance/coverage/expiry_registers.json
(exemptions, agent exemptions, completeness waivers and scope exclusions, schema-census
and fenced-hit rulings, agent-lane overrides, site claims; retirements carry
`clock: none`) must carry TWO clock hands:
  review_by  — an ISO date; FAIL when it is in the past (the entry's truth was reviewed
               for a horizon and the horizon ended), WARN inside the last 14 days;
  spec_pin   — the 8-hex commit of the pinned spec the entry was reviewed against (a
               `{version: pin}` map when the entry spans versions with different pins);
               FAIL on drift from SOURCES.lock.json (a re-pin invalidates every review
               made against the old pin, mechanically — nobody has to remember).
Missing either hand is `missing-clock`. Findings name the entry as `<file> <path>`.

Usage:
  python3 conformance/selfcheck/validate_expiry_clocks.py            # the gate
  ... --today 2027-01-01                                            # clock override
  ... --lock PATH                                                   # alternate lock (drift proof)
  ... --selftest                                                    # hermetic kill-tests
"""
import argparse, json, os, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONF = os.path.join(ROOT, "conformance")
sys.path.insert(0, CONF)
sys.path.insert(0, os.path.join(CONF, "coverage"))
from common.spec_versions import VERSIONS  # noqa: E402

REGISTERS = os.path.join(CONF, "coverage", "expiry_registers.json")
LOCK = os.path.join(CONF, "SOURCES.lock.json")
WARN_DAYS = 14


# ----------------------------------------------------------------------------- data

def load_registers(path=REGISTERS):
    return json.load(open(path))


def lock_pins(path=LOCK):
    """{version: 8-hex commit} from SOURCES.lock.json."""
    d = json.load(open(path))
    return {v: (i.get("commit") or "")[:8] for v, i in d.get("spec", {}).get("versions", {}).items()}


def iter_entries(reg, doc):
    """Yield (path, entry) for one register's entry-path over its loaded document.
    Paths:  `$.*`        every value of an id-keyed dict, `_`-prefixed keys skipped;
                         a list value yields one entry per element as `<id>[i]`
            `$.key[*]`   every element of the list under `key`
    Note-only entries (every key `_`-prefixed, e.g. a scratch `_note` object) are
    skipped — they are commentary, not clocked claims; so is any entry lacking the
    register's `entry_requires` key (site_claims: an object without `text` is not a
    claim, whatever else it carries)."""
    spec = reg["entries"]
    need = reg.get("entry_requires")

    def is_note(e):
        if not isinstance(e, dict):
            return False
        if all(k.startswith("_") for k in e):
            return True
        return bool(need) and need not in e
    if spec == "$.*":
        for k, v in doc.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                for i, e in enumerate(v):
                    if not is_note(e):
                        yield f"{k}[{i}]", e
            elif not is_note(v):
                yield k, v
    elif spec.startswith("$.") and spec.endswith("[*]"):
        key = spec[2:-3]
        for i, e in enumerate(doc.get(key) or []):
            if not is_note(e):
                yield f"{key}[{i}]", e
    else:
        raise ValueError(f"unsupported entries path {spec!r} in register {reg.get('name')}")


def entry_versions(reg, entry, versions=VERSIONS):
    """The spec versions an entry's truth is pinned to, per the register's scope rule."""
    scope = reg.get("scope", "versions")
    if scope == "none":
        return []
    if scope == "all":
        return list(versions)
    if scope == "version":
        v = entry.get("version")
        return [v] if v else []
    if scope == "versions":
        return list(entry.get("versions") or [])
    raise ValueError(f"unsupported scope {scope!r} in register {reg.get('name')}")


def _parse_date(s):
    try:
        return date.fromisoformat(s)
    except Exception:                                   # noqa: BLE001 — a bad date is a finding
        return None


def evaluate(registers, load, pins, today, versions=VERSIONS):
    """Pure: registers (list of register dicts), load(file)->doc, pins {version: 8-hex},
    today (date) -> findings [{kind, register, entry, detail}]. Kinds:
      expired · pin-drift · missing-clock · expiring-soon (warn only)."""
    findings = []

    def add(kind, reg, path, detail):
        findings.append({"kind": kind, "register": reg["name"],
                         "entry": f"{os.path.basename(reg['file'])} {path}", "detail": detail})

    for reg in registers:
        if reg.get("clock") == "none":
            continue
        doc = load(reg["file"])
        if doc is None:
            findings.append({"kind": "missing-clock", "register": reg["name"],
                             "entry": os.path.basename(reg["file"]), "detail": "register file unreadable"})
            continue
        for path, e in iter_entries(reg, doc):
            if not isinstance(e, dict):
                add("missing-clock", reg, path, "entry is not an object")
                continue
            vers = entry_versions(reg, e, versions)
            rb = e.get("review_by")
            pin = e.get("spec_pin")
            missing = []
            if not rb:
                missing.append("review_by")
            if vers and not pin:
                missing.append("spec_pin")
            if missing:
                add("missing-clock", reg, path, "no " + "/".join(missing))
                continue
            d = _parse_date(rb)
            if d is None:
                add("missing-clock", reg, path, f"review_by {rb!r} is not an ISO date")
                continue
            if d < today:
                add("expired", reg, path, f"review_by {rb} < today {today.isoformat()}")
            elif (d - today).days <= WARN_DAYS:
                add("expiring-soon", reg, path, f"review_by {rb} in {(d - today).days} d")
            for v in vers:
                want = pins.get(v)
                have = pin.get(v) if isinstance(pin, dict) else pin
                if want is None:
                    add("pin-drift", reg, path, f"version {v} is not pinned in the lock")
                elif have != want:
                    add("pin-drift", reg, path, f"{v}: spec_pin {have} != lock {want}")
    return findings


def _repo_loader(root=ROOT):
    def load(rel):
        try:
            return json.load(open(os.path.join(root, rel)))
        except Exception:                               # noqa: BLE001 — reported as a finding
            return None
    return load


def summarize(findings, n_entries, extra=""):
    c = {}
    for f in findings:
        c[f["kind"]] = c.get(f["kind"], 0) + 1
    return (f"expiry-clocks: {n_entries} entries" + extra +
            f" · {c.get('expired', 0)} expired · {c.get('pin-drift', 0)} pin-drift"
            f" · {c.get('missing-clock', 0)} missing-clock")


def count_entries(registers, load):
    n = 0
    for reg in registers:
        doc = load(reg["file"])
        if doc is not None:
            n += sum(1 for _ in iter_entries(reg, doc))
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="expiry-clock gate")
    ap.add_argument("--today", help="ISO date override (default: today)")
    ap.add_argument("--lock", default=LOCK, help="alternate SOURCES.lock.json (re-pin proof)")
    ap.add_argument("--registers", default=REGISTERS)
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else date.today()
    regs = load_registers(a.registers)["registers"]
    load = _repo_loader()
    findings = evaluate(regs, load, lock_pins(a.lock), today)
    hard = [f for f in findings if f["kind"] != "expiring-soon"]
    for f in findings:
        mark = "!" if f["kind"] == "expiring-soon" else "✗"
        print(f"  {mark} {f['kind']:<14} {f['entry']}: {f['detail']}")
    print(summarize(findings, count_entries(regs, load)))
    return 1 if hard else 0


def selftest():
    """Kill-tests (hermetic: synthetic registers/docs/lock, no repo I/O)."""
    today = date(2026, 9, 10)
    pins = {"2026-04-08": "a2d8bf0b", "2026-08-25": "cd78fb38"}
    regs = [{"name": "waivers", "file": "w.json", "entries": "$.waivers[*]",
             "scope": "version", "clock": "review_by"},
            {"name": "retirements", "file": "r.json", "entries": "$.retirements[*]",
             "scope": "versions", "clock": "none"}]
    docs = {"w.json": {"waivers": [
                {"version": "2026-08-25", "review_by": "2026-09-09", "spec_pin": "cd78fb38"},   # (a)
                {"version": "2026-04-08", "review_by": "2026-12-01", "spec_pin": "deadbeef"},   # (b)
                {"version": "2026-08-25"},                                                      # (c)
                {"version": "2026-08-25", "review_by": "2026-12-01", "spec_pin": "cd78fb38"}]}, # clean
            "r.json": {"retirements": [{"versions": ["2026-04-08"]}]}}                          # (d)
    bad = 0

    def case(label, ok, detail=""):
        nonlocal bad
        print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f"  <-- {detail}"))
        bad += 0 if ok else 1

    f = evaluate(regs, docs.get, pins, today)
    by = {}
    for x in f:
        by.setdefault(x["entry"], []).append(x["kind"])
    case("(a) review_by yesterday -> expired", by.get("w.json waivers[0]") == ["expired"], by)
    case("(b) spec_pin deadbeef -> pin-drift", by.get("w.json waivers[1]") == ["pin-drift"], by)
    case("(c) no clock hands -> missing-clock", by.get("w.json waivers[2]") == ["missing-clock"], by)
    case("(d) clock: none register -> no finding", "r.json retirements[0]" not in by, by)
    case("clean entry -> no finding", "w.json waivers[3]" not in by, by)
    f2 = evaluate(regs, docs.get, pins, date(2026, 1, 1))
    case("(e) --today override: 2026-01-01 sees no expiry",
         not any(x["kind"] == "expired" for x in f2), [x["kind"] for x in f2])
    # (f) the seed batch is a sign-off WITH a recorded >=10% sample, or it is invalid
    from verify_review_signoffs import seed_batch_errors
    seed_ok = {"batch": "expiry-clock-seed-2026-09", "date": "2026-09-10", "reviewer": "x",
               "spec_reverified": True, "notes": "n" * 40,
               "sample": {"seed": 1, "of": 30, "size": 3, "ids": ["a", "b", "c"],
                          "human_review": {"status": "recorded", "by": "owner", "on": "2026-09-10"}}}
    seed_none = {k: v for k, v in seed_ok.items() if k != "sample"}
    seed_thin = {**seed_ok, "sample": {**seed_ok["sample"], "size": 2, "ids": ["a", "b"]}}
    case("(f) seed batch without a sample -> red", bool(seed_batch_errors(seed_none, today)))
    case("(f') seed batch sample < 10% -> red", bool(seed_batch_errors(seed_thin, today)))
    case("(f'') seed batch with a recorded >=10% sample -> clean",
         not seed_batch_errors(seed_ok, today), seed_batch_errors(seed_ok, today))

    # ---- D2-19: two-tier horizon (decision 23) + cliff detection
    regs2 = [{"name": "ki", "file": "k.json", "entries": "$.rows[*]", "scope": "version",
              "clock": "review_by"}]                                   # no register-level tier
    docs2 = {"k.json": {"rows": [
        {"version": "2026-08-25", "review_by": "2026-10-01", "spec_pin": "cd78fb38"},                       # (g) no tier
        {"version": "2026-08-25", "review_by": "2026-10-11", "spec_pin": "cd78fb38", "clock_tier": "moving"},   # (h) 31 d
        {"version": "2026-08-25", "review_by": "2026-10-10", "spec_pin": "cd78fb38", "clock_tier": "moving"},   # 30 d ok
        {"version": "2026-08-25", "review_by": "2026-12-10", "spec_pin": "cd78fb38", "clock_tier": "pin-only"}, # 91 d
        {"version": "2026-08-25", "review_by": "2026-12-09", "spec_pin": "cd78fb38", "clock_tier": "pin-only"}, # 90 d ok
        {"version": "2026-08-25", "review_by": "2026-10-01", "spec_pin": "cd78fb38", "clock_tier": "sideways"}]}}  # bad tier
    f3 = evaluate(regs2, docs2.get, pins, today)
    by3 = {}
    for x in f3:
        by3.setdefault(x["entry"], []).append(x["kind"])
    case("(g) entry without clock_tier (register has none) -> missing-tier",
         by3.get("k.json rows[0]") == ["missing-tier"], by3)
    case("(h) moving entry with review_by 31 d out -> horizon-exceeded",
         by3.get("k.json rows[1]") == ["horizon-exceeded"], by3)
    case("(h2) moving entry at exactly 30 d -> clean", "k.json rows[2]" not in by3, by3)
    case("(h3) pin-only entry 91 d out -> horizon-exceeded",
         by3.get("k.json rows[3]") == ["horizon-exceeded"], by3)
    case("(h4) pin-only entry at exactly 90 d -> clean", "k.json rows[4]" not in by3, by3)
    case("unknown tier -> missing-tier", by3.get("k.json rows[5]") == ["missing-tier"], by3)
    # (i) cliff: 6 of 10 entries expire on one date -> red; (j) staggered -> pass
    cliffed = [{"version": "2026-08-25", "review_by": "2026-11-01", "spec_pin": "cd78fb38", "clock_tier": "pin-only"}] * 6 + \
              [{"version": "2026-08-25", "review_by": f"2026-11-0{i}", "spec_pin": "cd78fb38", "clock_tier": "pin-only"} for i in range(2, 6)]
    staggered = [{"version": "2026-08-25", "review_by": f"2026-11-{10 + i:02d}", "spec_pin": "cd78fb38", "clock_tier": "pin-only"} for i in range(10)]
    hist_c = expiry_histogram(regs2, {"k.json": {"rows": cliffed}}.get)
    hist_s = expiry_histogram(regs2, {"k.json": {"rows": staggered}}.get)
    case("(i) 60% of entries on one expiry date -> cliff red",
         cliff_share(hist_c) == 0.6 and cliff_share(hist_c) > CLIFF_MAX_SHARE, cliff_share(hist_c))
    case("(j) staggered seed (max 10% same-day) -> cliff clean",
         cliff_share(hist_s) == 0.1 and cliff_share(hist_s) <= CLIFF_MAX_SHARE, cliff_share(hist_s))

    print(f"\nexpiry-clocks selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())

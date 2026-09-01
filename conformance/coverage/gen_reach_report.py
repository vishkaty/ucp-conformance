#!/usr/bin/env python3
"""
gen_reach_report.py — generate the differential REACH REPORT consumed by the
evidence-class layer (evidence.py) and published per-row in public/coverage.json.

For every WIRE-probing check (MCheck merchant checks + live engine checks), this
records which INDEPENDENT targets — servers we did not author, from the same
register the differential gate uses (ci/differential_targets.json) — the check
actually GRADED on in a real run, and with what status. A wire check that never
grades an independent target is the honest fixture-circularity residue: it has only
ever been exercised against our own controlled fixture, and evidence.py classifies
it self-referenced until a run proves otherwise.

The output (coverage/reach_report.json) is COMMITTED DATA, like a re-pin: the
coverage export must stay deterministic for the coverage gate's byte-compare, so
the matrix never probes the network itself. Regenerate deliberately when targets,
configs, or checks change:

    conformance/ci/serve_golden.sh          # flower golden on :8182
    conformance/ci/serve_node_reference.sh  # node reference on :3000
    python3 conformance/coverage/gen_reach_report.py

Targets come ONLY from ci/differential_targets.json — the same "implementations we
did not author" register the differential gate trusts. Per target the URL resolves
from its `server_env` environment variable, else its `local_server` (the serve
script's local address). Unreachable targets are recorded as unprobed (their
column simply stays absent — never fabricated).

Statuses recorded per (version:check, target): the runner's own detail status —
clean-pass/deviation (GRADED: the check ran on the wire and produced a verdict), or
the not-applicable/not-tested/version-skip/error reason (NOT graded — named, not
hidden). evidence.py counts only clean-pass/deviation as corroboration.

VERSION AXIS + MERGE-BY-SLICE (R4 fix, 2026-08-31 — Fable-reviewed design): every
key is "version:module_stem:check_id" (evidence.reach_key) — a grading run against
a target serving spec version V is corroboration for V ONLY, never a sibling
version the same check object happens to also be attributed to (see evidence.py's
own module docstring for the concrete leak this closes: before this fix a single
04-08-only run had been silently "corroborating" 2026-01-11/2026-01-23 since the
evidence layer's introduction, and would have handed 2026-08-25 live-wire credit
for a version with zero independently-authored implementation).

Because each target serves exactly ONE version at a time, and the file is committed
(re-)generated data, a naive whole-file overwrite would make multi-version
corroboration mutually erase itself: pin flower-shop to serve 2026-01-23 today to
honestly earn 01-23 evidence, and a plain overwrite DELETES every 04-08 entry the
previous run earned (including the published homepage hero stat) the moment this
script runs again — reddening the freshness gate for the wrong reason and
punishing the exact deliberate, honest act this script exists to reward. So this
generator MERGES: it loads the existing committed report, and for each reachable
target this run, replaces ONLY that target's entries within the SLICE keyed by
(target_name, version_the_target_serves_right_now) — every other target's slice,
and this same target's entries under any OTHER version (stale: the target no
longer serves that version, so its old grading can no longer be reconfirmed and
must not linger silently), is left untouched or stripped respectively. A target
whose served version cannot be determined (engine.served_version returns None —
discovery unreadable, or an ambiguous/absent version field) is REFUSED outright:
recording graded evidence under an unknown or guessed version would be exactly the
class of claim this fix exists to prevent (a forged/undetectable version tag), so
that target's prior slice is left exactly as committed and the run reports the
refusal by name (P-2: a can't-tell must say so loudly, never silently keep stale
data AND never silently fabricate a version for it).
"""
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.dirname(HERE)
ROOT = os.path.dirname(CONF)
OUT = os.path.join(HERE, "reach_report.json")
TARGETS = os.path.join(CONF, "ci", "differential_targets.json")

sys.path.insert(0, os.path.join(CONF, "checks"))
sys.path.insert(0, os.path.join(CONF, "selfcheck"))
sys.path.insert(0, HERE)

import evidence                                            # noqa: E402
import matrix                                              # noqa: E402

# check object -> defining module stem, via the SAME introspection walk the matrix
# attributes coverage with — so reach keys ("module:check_id") always match what
# evidence.py looks up during export. First definition wins for shared objects.
_STEM = {}


def _stem_of(chk):
    if not _STEM:
        for path in matrix.check_files():
            checks, _mod = matrix._module_checks(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            for c in checks or []:
                _STEM.setdefault(id(c), stem)
    got = _STEM.get(id(chk))
    if got is None:
        raise RuntimeError(f"check {chk.id} not found in any CHECKS list — reach "
                           f"key would not match matrix attribution")
    return got


def server_up(url, timeout=3):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/.well-known/ucp",
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def independent_targets():
    """The differential register's targets with a resolvable URL: env override
    first (same contract as differential.py), else the committed local_server."""
    out = []
    for t in json.load(open(TARGETS)).get("targets", []):
        url = os.environ.get(t.get("server_env", ""), "") or t.get("local_server", "")
        if url:
            out.append({"name": t["name"], "server": url,
                        "config": t.get("config")})
    return out


def _record(checks, version, module_stem, chk, target_name, status):
    key = evidence.reach_key(version, module_stem, chk)
    entry = checks.setdefault(key, {"check": chk.id, "module": module_stem,
                                    "version": version, "targets": {}})
    entry["targets"][target_name] = status


def _strip_stale_slice(checks, target_name, keep_version):
    """Remove `target_name`'s entry from every check-key whose version prefix is
    NOT `keep_version` — that target no longer serves that version (it serves
    `keep_version` now), so its old grading there can no longer be reconfirmed
    and must not linger silently as if still current (P-2). Drops an entry
    entirely once its targets dict empties. Entries under `keep_version` are
    left alone here — the caller overwrites them fresh via `_record`."""
    for key in list(checks.keys()):
        entry = checks[key]
        if entry.get("version") != keep_version and target_name in (entry.get("targets") or {}):
            del entry["targets"][target_name]
            if not entry["targets"]:
                del checks[key]


def run_merchant_lane(checks, target, served):
    """Grade every MCheck against `target` via the real runner (the exact code path
    validate_merchant_checks / the public CLI use) and record each detail status
    under `served` — the ONE version this target speaks this run (shared with
    run_engine_lane, computed once in main() via engine.served_version — a single
    source of "what version does this target speak" so the two lanes can never
    disagree about which slice they're writing)."""
    import merchant_checks
    from merchant import MerchantCtx, discover
    profile, _ = discover(target["server"])
    cfg = json.load(open(os.path.join(ROOT, target["config"]))) if target.get("config") else {}
    ctx = MerchantCtx(target["server"], profile, cfg)
    _res, detail = merchant_checks.run_merchant_checks(ctx)
    for chk, d in detail:
        _record(checks, served, _stem_of(chk), chk, target["name"], str(d.get("status")))
    return len(detail)


def run_engine_lane(checks, target, served):
    """Grade the live (wire) ENGINE checks — the 01-era core/area checksets — against
    `target`, honoring the served-version scope gate exactly like run_01_23.py, and
    recording under `served` (see run_merchant_lane's docstring)."""
    import engine
    import run_01_23
    runchecks = run_01_23.collect()
    n = 0
    for chk in runchecks:
        stem = _stem_of(chk)
        if evidence.acquisition(chk) != "wire":
            continue                        # fixture-based: reach is not its evidence
        n += 1
        if not engine.version_applicable(chk, served):
            _record(checks, served, stem, chk, target["name"],
                    f"version-skip (server speaks {served})")
            continue
        _res, det = engine.run_check(chk, target["server"])
        _record(checks, served, stem, chk, target["name"], str(det.get("clean")))
    return n


def _load_existing():
    """The previously-committed report, or an empty shell — this generator MERGES
    into it per (target, served-version) slice rather than overwriting wholesale
    (see the module docstring's MERGE-BY-SLICE section for why a plain overwrite
    would make multi-version corroboration mutually erase itself)."""
    try:
        d = json.load(open(OUT))
        return d.get("checks", {}), d.get("targets", {})
    except Exception:
        return {}, {}


def main():
    import engine
    targets = independent_targets()
    reachable = [t for t in targets if server_up(t["server"])]
    print(f"independent targets registered: {[t['name'] for t in targets]}")
    print(f"reachable now: {[t['name'] for t in reachable]}")
    if not reachable:
        print("no independent target reachable — refusing to write an empty report "
              "(the committed report is evidence; absence of a run is not evidence)")
        return 2

    checks, meta = _load_existing()
    today = datetime.date.today().isoformat()
    probed_any = False
    for t in reachable:
        served = engine.served_version(t["server"])
        if not served:
            print(f"\n{t['name']} @ {t['server']}: served spec version UNDETECTABLE — "
                  "REFUSING to record for this target this run (would otherwise record "
                  "graded evidence under an unknown/guessed version — P-2). Its "
                  "previously-committed slice, if any, is left exactly as-is.")
            continue
        probed_any = True
        print(f"\nprobing {t['name']} @ {t['server']} (spec {served}) ...")
        _strip_stale_slice(checks, t["name"], served)
        nm = run_merchant_lane(checks, t, served)
        ne = run_engine_lane(checks, t, served)
        meta[t["name"]] = {"server": t["server"], "spec_version": served,
                           "generated": today,
                           "merchant_checks_run": nm, "engine_wire_checks": ne}
        graded = sum(1 for e in checks.values()
                     if e.get("version") == served and e["targets"].get(t["name"])
                     in ("clean-pass", "deviation"))
        recorded_this_target = sum(1 for e in checks.values()
                                    if t["name"] in e.get("targets", {}))
        print(f"  {t['name']}: {graded} wire checks GRADED "
              f"(clean-pass/deviation) of {recorded_this_target} recorded @ {served}")

    if not probed_any:
        print("\nevery reachable target had an undetectable served version — refusing "
              "to write (no slice was actually probed this run)")
        return 2

    pins = {}
    try:
        lock = json.load(open(os.path.join(CONF, "SOURCES.lock.json")))
        pins = {v: i.get("commit", "") for v, i in
                lock.get("spec", {}).get("versions", {}).items()}
    except Exception:
        pass

    out = {
        "_about": "Differential REACH REPORT — for each (spec VERSION, WIRE-probing "
                  "check) pair, which independent targets (ci/differential_targets"
                  ".json — servers we did not author) actually GRADED it, at THAT "
                  "version, in the recorded run, with the runner's own status. "
                  "clean-pass/deviation = graded on the wire; every other status "
                  "names why it did not reach that target. Keys are "
                  "'version:module_stem:check_id' (evidence.reach_key) — the "
                  "version axis is load-bearing: a check graded against a target "
                  "serving version V corroborates V only, never a sibling version "
                  "the same check object also happens to be attributed to (R4). "
                  "Consumed by coverage/evidence.py: a (version, check) pair with "
                  "no graded independent target is classified self-referenced (the "
                  "fixture-circularity class we flag upstream, conformance#79). "
                  "Committed data, MERGED per (target, served-version) slice on "
                  "each run — never a wholesale overwrite (see this script's module "
                  "docstring) — regenerate with gen_reach_report.py after booting "
                  "the targets (serve_golden.sh / serve_node_reference.sh).",
        "generated": today,
        "spec_pins": pins,
        "targets": meta,
        "checks": dict(sorted(checks.items())),
    }
    json.dump(out, open(OUT, "w"), indent=1)
    open(OUT, "a").write("\n")
    print(f"\nreach report written -> {os.path.relpath(OUT, ROOT)} "
          f"({len(checks)} (version,check) entries, {len(reachable)} independent "
          f"target(s) reachable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

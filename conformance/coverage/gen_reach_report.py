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

CI DRIFT STEP (D4-02 / B3, 2026-09-10): `--check` regenerates in memory against the live
targets and compares the GRADED statuses per (version:check, target) with the committed
report — `reach report: N drift`, rc 1 when N > 0 — and NEVER writes or commits: a moved
live-wire label is a public number, published only by an owner commit under decision 6/34
(never auto-applied, never a bot commit — decision 24). Dates are stable: a target whose
slice did not change keeps its committed `generated` date, so an unchanged rerun is a
no-op and drift means exactly "a check's graded status moved on an independent target".
`--selftest` is the hermetic kill-proof (planted flip in a temp copy -> 1 drift; unchanged
rerun -> 0 drift; write/read round-trip stable) — run_suite gate `reach-selftest`.
"""
import argparse
import copy
import datetime
import json
import os
import sys
import tempfile
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


GRADED = ("clean-pass", "deviation")


def _slice(checks, target_name, version):
    """The (key -> status) view of one target's entries under one version."""
    return {k: e["targets"][target_name] for k, e in checks.items()
            if e.get("version") == version and target_name in (e.get("targets") or {})}


def drift(old_checks, new_checks):
    """[(key, target, old_status, new_status)] wherever a GRADED status differs between the
    committed report and a fresh regeneration — an entry graded on one side and absent or
    ungraded on the other counts too. Non-graded statuses (version-skip, not-applicable,
    error text) may change freely: they are reasons, not evidence."""
    out = []
    keys = set(old_checks) | set(new_checks)
    for k in sorted(keys):
        ot = (old_checks.get(k) or {}).get("targets") or {}
        nt = (new_checks.get(k) or {}).get("targets") or {}
        for t in sorted(set(ot) | set(nt)):
            o, n = ot.get(t), nt.get(t)
            og, ng = (o in GRADED), (n in GRADED)
            if (og or ng) and o != n:
                out.append((k, t, o, n))
    return out


def build_report(checks, meta, today=None):
    pins = {}
    try:
        lock = json.load(open(os.path.join(CONF, "SOURCES.lock.json")))
        pins = {v: i.get("commit", "") for v, i in
                lock.get("spec", {}).get("versions", {}).items()}
    except Exception:
        pass
    dates = [m.get("generated") for m in meta.values() if m.get("generated")]
    return {
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
                  "the targets (serve_golden.sh / serve_node_reference.sh); CI runs "
                  "`--check` and FAILS on graded-status drift (D4-02), the owner "
                  "commits regenerated labels under decision 6.",
        "generated": (max(dates) if dates else (today or datetime.date.today().isoformat())),
        "spec_pins": pins,
        "targets": meta,
        "checks": dict(sorted(checks.items())),
    }


def write_report(path, report):
    json.dump(report, open(path, "w"), indent=1)
    open(path, "a").write("\n")


def regenerate(reachable, checks, meta, today=None):
    """Probe every reachable target and MERGE its slice into (checks, meta) — pure of any
    file I/O. Returns (checks, meta, probed_any). A target whose new slice equals its
    committed one keeps its committed `generated` date (stable reruns)."""
    import engine
    today = today or datetime.date.today().isoformat()
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
        before = _slice(checks, t["name"], served)
        prev_meta = dict(meta.get(t["name"]) or {})
        _strip_stale_slice(checks, t["name"], served)
        nm = run_merchant_lane(checks, t, served)
        ne = run_engine_lane(checks, t, served)
        after = _slice(checks, t["name"], served)
        unchanged = (before == after and prev_meta.get("spec_version") == served)
        meta[t["name"]] = {"server": t["server"], "spec_version": served,
                           "generated": prev_meta.get("generated", today) if unchanged else today,
                           "merchant_checks_run": nm, "engine_wire_checks": ne}
        graded = sum(1 for e in checks.values()
                     if e.get("version") == served and e["targets"].get(t["name"]) in GRADED)
        recorded_this_target = sum(1 for e in checks.values()
                                    if t["name"] in e.get("targets", {}))
        print(f"  {t['name']}: {graded} wire checks GRADED "
              f"(clean-pass/deviation) of {recorded_this_target} recorded @ {served}"
              f"{' (slice unchanged)' if unchanged else ''}")
    return checks, meta, probed_any


def _probe_targets():
    targets = independent_targets()
    reachable = [t for t in targets if server_up(t["server"])]
    print(f"independent targets registered: {[t['name'] for t in targets]}")
    print(f"reachable now: {[t['name'] for t in reachable]}")
    return reachable


def check():
    """CI drift step: regenerate in memory, compare graded statuses with the committed
    report, write NOTHING. rc 0 = `reach report: 0 drift`; 1 = drift (owner decision 6);
    2 = no target reachable / nothing probed (never a false 0)."""
    reachable = _probe_targets()
    if not reachable:
        print("no independent target reachable — cannot check drift (rc 2, not 0)")
        return 2
    old_checks, old_meta = _load_existing()
    checks, meta, probed = regenerate(reachable, copy.deepcopy(old_checks), copy.deepcopy(old_meta))
    if not probed:
        print("\nevery reachable target had an undetectable served version — cannot check drift")
        return 2
    d = drift(old_checks, checks)
    for k, t, o, n in d:
        print(f"  drift  {k} @ {t}: committed {o!r} -> regenerated {n!r}")
    print(f"reach report: {len(d)} drift")
    if d:
        print("::error::reach report drifted — a graded status moved on an independent target; "
              "regenerate locally and commit under decision 6 (never auto-applied)")
        return 1
    return 0


def selftest():
    """Hermetic kill-proof of the drift step: a planted status flip in a TEMP copy of the
    committed report must produce exactly 1 drift; an unchanged rerun 0; and a
    write/read round-trip of the unchanged report must also be 0 (so the step can only
    go red on a real status move). No network, repo untouched."""
    ok = True
    old_checks, old_meta = _load_existing()
    graded = [(k, t) for k, e in old_checks.items() for t, s in e["targets"].items() if s in GRADED]
    if not graded:
        print("no graded entry in the committed report — nothing to flip"); print("FAIL"); return 1
    k, t = graded[0]
    planted = copy.deepcopy(old_checks)
    cur = planted[k]["targets"][t]
    planted[k]["targets"][t] = "deviation" if cur == "clean-pass" else "clean-pass"
    d1 = drift(old_checks, planted)
    c1 = (d1 == [(k, t, cur, planted[k]["targets"][t])])
    print(f"  {'✓' if c1 else '✗'} planted flip {k} @ {t} ({cur} -> {planted[k]['targets'][t]}): "
          f"{len(d1)} drift {'CAUGHT' if c1 else 'MISSED (step is blind!)'}")
    ok &= c1
    d2 = drift(old_checks, copy.deepcopy(old_checks))
    c2 = (d2 == [])
    print(f"  {'✓' if c2 else '✗'} unchanged rerun: {len(d2)} drift")
    ok &= c2
    # a non-graded reason text changing is NOT drift (reasons are not evidence)
    reasoned = copy.deepcopy(old_checks)
    for kk, e in reasoned.items():
        for tt, ss in e["targets"].items():
            if ss not in GRADED:
                e["targets"][tt] = ss + " (rephrased)"; break
        else:
            continue
        break
    d3 = drift(old_checks, reasoned)
    c3 = (d3 == [])
    print(f"  {'✓' if c3 else '✗'} non-graded reason text changed: {len(d3)} drift (reasons are not evidence)")
    ok &= c3
    # write/read round-trip of the UNCHANGED report through the writer is drift-free
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "reach_report.json")
        write_report(tmp, build_report(copy.deepcopy(old_checks), copy.deepcopy(old_meta)))
        rt = json.load(open(tmp))
        d4 = drift(old_checks, rt.get("checks", {}))
        c4 = (d4 == [] and rt["generated"] == max(m["generated"] for m in old_meta.values()))
        print(f"  {'✓' if c4 else '✗'} write/read round-trip of the unchanged report: {len(d4)} drift, "
              f"generated date kept ({rt['generated']})")
        ok &= c4
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    reachable = _probe_targets()
    if not reachable:
        print("no independent target reachable — refusing to write an empty report "
              "(the committed report is evidence; absence of a run is not evidence)")
        return 2

    checks, meta = _load_existing()
    checks, meta, probed_any = regenerate(reachable, checks, meta)
    if not probed_any:
        print("\nevery reachable target had an undetectable served version — refusing "
              "to write (no slice was actually probed this run)")
        return 2
    write_report(OUT, build_report(checks, meta))
    print(f"\nreach report written -> {os.path.relpath(OUT, ROOT)} "
          f"({len(checks)} (version,check) entries, {len(reachable)} independent "
          f"target(s) reachable)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="differential reach report generator")
    ap.add_argument("--check", action="store_true",
                    help="CI drift step: regenerate in memory, compare graded statuses, write nothing")
    ap.add_argument("--selftest", action="store_true", help="hermetic kill-proof (no network)")
    a = ap.parse_args()
    sys.exit(selftest() if a.selftest else check() if a.check else main())

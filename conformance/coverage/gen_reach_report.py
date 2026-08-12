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

Statuses recorded per (check, target): the runner's own detail status — clean-pass
/ deviation (GRADED: the check ran on the wire and produced a verdict), or the
not-applicable / not-tested / version-skip / error reason (NOT graded — named, not
hidden). evidence.py counts only clean-pass/deviation as corroboration.
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


def _record(report, module_stem, chk, target_name, status):
    key = evidence.reach_key(module_stem, chk)
    entry = report.setdefault(key, {"check": chk.id, "module": module_stem,
                                    "targets": {}})
    entry["targets"][target_name] = status


def run_merchant_lane(report, target):
    """Grade every MCheck against `target` via the real runner (the exact code path
    validate_merchant_checks / the public CLI use) and record each detail status."""
    import merchant_checks
    from merchant import MerchantCtx, discover
    profile, _ = discover(target["server"])
    cfg = json.load(open(os.path.join(ROOT, target["config"]))) if target.get("config") else {}
    ctx = MerchantCtx(target["server"], profile, cfg)
    _res, detail = merchant_checks.run_merchant_checks(ctx)
    for chk, d in detail:
        _record(report, _stem_of(chk), chk, target["name"], str(d.get("status")))
    return len(detail)


def run_engine_lane(report, target):
    """Grade the live (wire) ENGINE checks — the 01-era core/area checksets — against
    `target`, honoring the served-version scope gate exactly like run_01_23.py."""
    import engine
    import run_01_23
    checks = run_01_23.collect()
    served = engine.served_version(target["server"])
    n = 0
    for chk in checks:
        stem = _stem_of(chk)
        if evidence.acquisition(chk) != "wire":
            continue                        # fixture-based: reach is not its evidence
        n += 1
        if not engine.version_applicable(chk, served):
            _record(report, stem, chk, target["name"],
                    f"version-skip (server speaks {served})")
            continue
        _res, det = engine.run_check(chk, target["server"])
        _record(report, stem, chk, target["name"], str(det.get("clean")))
    return n


def main():
    targets = independent_targets()
    reachable = [t for t in targets if server_up(t["server"])]
    print(f"independent targets registered: {[t['name'] for t in targets]}")
    print(f"reachable now: {[t['name'] for t in reachable]}")
    if not reachable:
        print("no independent target reachable — refusing to write an empty report "
              "(the committed report is evidence; absence of a run is not evidence)")
        return 2

    report = {}
    meta = {}
    for t in reachable:
        import engine
        served = engine.served_version(t["server"])
        print(f"\nprobing {t['name']} @ {t['server']} (spec {served}) ...")
        nm = run_merchant_lane(report, t)
        ne = run_engine_lane(report, t)
        meta[t["name"]] = {"server": t["server"], "spec_version": served,
                           "merchant_checks_run": nm, "engine_wire_checks": ne}
        graded = sum(1 for e in report.values()
                     if e["targets"].get(t["name"]) in ("clean-pass", "deviation"))
        print(f"  {t['name']}: {graded} wire checks GRADED "
              f"(clean-pass/deviation) of {len(report)} recorded")

    pins = {}
    try:
        lock = json.load(open(os.path.join(CONF, "SOURCES.lock.json")))
        pins = {v: i.get("commit", "") for v, i in
                lock.get("spec", {}).get("versions", {}).items()}
    except Exception:
        pass

    out = {
        "_about": "Differential REACH REPORT — for each WIRE-probing check, which "
                  "independent targets (ci/differential_targets.json — servers we "
                  "did not author) it actually GRADED on in the recorded run, with "
                  "the runner's own status. clean-pass/deviation = graded on the "
                  "wire; every other status names why it did not reach that target. "
                  "Consumed by coverage/evidence.py: a wire check with no graded "
                  "independent target is classified self-referenced (the "
                  "fixture-circularity class we flag upstream, conformance#79). "
                  "Committed data — regenerate with gen_reach_report.py after "
                  "booting the targets (serve_golden.sh / serve_node_reference.sh).",
        "generated": datetime.date.today().isoformat(),
        "spec_pins": pins,
        "targets": meta,
        "checks": dict(sorted(report.items())),
    }
    json.dump(out, open(OUT, "w"), indent=1)
    open(OUT, "a").write("\n")
    print(f"\nreach report written -> {os.path.relpath(OUT, ROOT)} "
          f"({len(report)} wire checks, {len(reachable)} independent target(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())

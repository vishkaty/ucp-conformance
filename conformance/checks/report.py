#!/usr/bin/env python3
"""
report.py — the authoritative register-driven conformance CLI.

Runs the verified, kill-rate-gated check suite for a spec version and emits the
honest, coverage-gated verdict (text or JSON). This is the CLI form of the engine:
every check cites a register requirement, is mutation-validated, and the verdict
gate refuses any green from partial coverage.

  report.py --version 2026-01-23 --server http://localhost:8182 [--json]
  report.py --version 2026-04-08 [--json]        # fixture-based (no server needed)

JSON output is stable and CI-friendly: {aggregate, coverage, counts, scope_stamp,
disclaimer, checks:[{id, req_ids, verdict, kill_safe}]}.
"""
import sys, json, argparse, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import run_report          # noqa: E402

SUITES = {
    "2026-01-23": ("run_01_23", "http://localhost:8182"),
    "2026-04-08": ("run_04_08", "fixtures://"),
}

def build(version, server):
    import importlib
    mod = importlib.import_module(SUITES[version][0])
    core = importlib.import_module(mod.core.__name__)
    checks = mod.collect()
    rep, details = run_report(checks, server, version, core.SCOPE_STAMP, core.DISCLAIMER)
    return core, rep, details

def to_json(version, server, core, rep, details):
    return {
        "spec_version": version,
        "server": server,
        "aggregate": rep.aggregate,
        "coverage": rep.coverage,
        "counts": rep.counts,
        "scope_stamp": core.SCOPE_STAMP,
        "disclaimer": core.DISCLAIMER,
        "checks": [
            {"id": c.id, "req_ids": c.req_ids,
             "verdict": d["clean"], "kill_rate": d["kills"], "kill_safe": d["kill_safe"]}
            for c, d in details
        ],
    }

def main():
    ap = argparse.ArgumentParser(description="UCP conformance report (unofficial).")
    ap.add_argument("--version", required=True, choices=sorted(SUITES))
    ap.add_argument("--server", default=None, help="target server base URL (01-23)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    server = args.server or SUITES[args.version][1]
    core, rep, details = build(args.version, server)
    if args.json:
        print(json.dumps(to_json(args.version, server, core, rep, details), indent=2))
    else:
        cc = rep.counts
        print(f"UCP conformance report (UNOFFICIAL) — spec {args.version} "
              f"@ {core.SCOPE_STAMP['spec_commit'][:7]}\ntarget: {server}\n")
        for c, d in details:
            print(f"  {c.id:36} {str(d['clean']):11} kill_safe={d['kill_safe']}")
        print(f"\naggregate: {rep.aggregate.upper()}   "
              f"MUST coverage: {cc['musts_clean_pass']}/{cc['inscope_musts']} "
              f"({round(100*rep.coverage)}%)   deviations: {cc['deviations']}")
        print(f"\n{core.DISCLAIMER}")
    # exit code: 0 pass, 1 incomplete/blocked, 2 fail (a MUST deviation)
    return {"pass": 0, "incomplete": 1, "blocked": 1, "fail": 2}.get(rep.aggregate, 1)

if __name__ == "__main__":
    sys.exit(main())

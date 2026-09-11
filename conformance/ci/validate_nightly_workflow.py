#!/usr/bin/env python3
"""
validate_nightly_workflow.py — the nightly.yml contract (D4-10), hermetic.

  jobs        `crosscheck`, `seqfuzz`, `oracles` present; NO job named discovery-live /
              discovery_live and no step invoking discovery_live.py --sample (decision 4: the
              sampler runs only on the owner's machine)
  timeouts    every job carries `timeout-minutes`
  ports       every literal port in the workflow (and the ports its scripts bind: the
              official_crosscheck goldens :8382/:8398 + mocks 8484/8485) is registered in
              conformance/ci/ports.json AND is NOT in the push-gate sweep set (sweep: true)
  assertions  no `continue-on-error` on an assertion step (a `run:` step invoking one of our
              gate scripts); installs may carry it
  ops         no step writes under ops/ (not mounted in Actions; decision 24)
  artifacts   every job uploads an artifact (pull_feeds.py pulls them)
Exit 0 PASS · 1 finding.  --selftest: kill-tests on mutated copies (port 8182 -> red,
unregistered port -> red, a discovery-live job -> red, continue-on-error on an assertion
step -> red, ops/ write -> red).
"""
import argparse, copy, json, pathlib, re, sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
PORTS = HERE / "ports.json"
REQUIRED_JOBS = ("crosscheck", "seqfuzz", "oracles")
GATE_SCRIPTS = ("official_crosscheck.py", "seqfuzz_gate.py", "validate_dual_oracle.py",
                "oracle_verdict_diff.py", "validate_schema_oracle_manifest.py")
# ports the nightly scripts bind by construction (official_crosscheck.py reads ports.json)
SCRIPT_PORTS = {"official_crosscheck.py": ("nightly-golden-a", "nightly-golden-b", "official-mock-a", "official-mock-b")}


def load(path=NIGHTLY):
    return yaml.safe_load(pathlib.Path(path).read_text())


def ports_registry(path=PORTS):
    d = json.loads(pathlib.Path(path).read_text())
    reg = {k: v for k, v in d.items() if not k.startswith("_")}
    by_port = {v["port"]: k for k, v in reg.items()}
    sweep = {v["port"] for v in reg.values() if v.get("sweep")}
    return reg, by_port, sweep


def steps_of(job):
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def literal_ports(text):
    return {int(p) for p in re.findall(r"(?<![\d.])(?::|--port=|port[ =:]+)(\d{4,5})\b", text)}


def check(doc, reg=None, by_port=None, sweep=None):
    f = []
    if reg is None:
        reg, by_port, sweep = ports_registry()
    jobs = doc.get("jobs") or {}
    for j in REQUIRED_JOBS:
        if j not in jobs:
            f.append(f"job {j!r} missing")
    on = doc.get("on", doc.get(True, {}))
    if not isinstance(on, dict) or "schedule" not in on:
        f.append("no schedule trigger")
    for name, job in jobs.items():
        if re.search(r"discovery[-_]live", name):
            f.append(f"job {name!r}: the discovery-live sampler never runs in Actions (decision 4)")
        if "timeout-minutes" not in job:
            f.append(f"job {name!r}: no timeout-minutes")
        uploads = False
        for s in steps_of(job):
            run = s.get("run") or ""
            uses = s.get("uses") or ""
            if "upload-artifact" in uses:
                uploads = True
            if re.search(r"discovery_live\.py\s+--sample|run_sampler", run):
                f.append(f"job {name!r}: a step runs the discovery-live sampler (decision 4)")
            if re.search(r"(>|>>|cp |mv |tee )\s*\S*ops/", run) or re.search(r"\bops/feeds", run):
                f.append(f"job {name!r}: a step writes under ops/ (not mounted in Actions; decision 24)")
            if s.get("continue-on-error") and any(g in run for g in GATE_SCRIPTS):
                f.append(f"job {name!r}: continue-on-error on an assertion step ({run.strip()[:60]})")
            ports = literal_ports(run)
            for g, names in SCRIPT_PORTS.items():
                if g in run:
                    ports |= {reg[n]["port"] for n in names if n in reg}
            for p in sorted(ports):
                if p not in by_port:
                    f.append(f"job {name!r}: port {p} is not registered in ports.json")
                elif p in sweep:
                    f.append(f"job {name!r}: port {p} ({by_port[p]}) is a push-gate sweep port — nightly must not share it")
        if not uploads:
            f.append(f"job {name!r}: no upload-artifact step (feeds are artifacts, decision 24)")
    return f


def selftest():
    ok = True

    def case(tag, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'✓' if cond else '✗'} {tag}" + (f" — {detail}" if detail else ""))

    doc = load()
    reg, by_port, sweep = ports_registry()
    base = check(doc, reg, by_port, sweep)
    case("real nightly.yml: 0 findings", not base, "; ".join(base)[:300])
    m = copy.deepcopy(doc); m["jobs"]["crosscheck"]["steps"].append({"run": "python3 x.py --port=8182"})
    case("mutant: a job port set to 8182 (push-gate sweep) -> red", any("sweep port" in x for x in check(m, reg, by_port, sweep)))
    # an unregistered port built at runtime (the ports-registry scanner reads source literals,
    # and this mutant must never look like a port the harness binds)
    unregistered = max(reg[k]["port"] for k in reg) + 1000
    m = copy.deepcopy(doc); m["jobs"]["seqfuzz"]["steps"].append({"run": f"python3 x.py --port={unregistered}"})
    case("mutant: an unregistered port -> red", any("not registered" in x for x in check(m, reg, by_port, sweep)))
    m = copy.deepcopy(doc); m["jobs"]["discovery-live"] = {"runs-on": "ubuntu-latest", "timeout-minutes": 5, "steps": [{"run": "echo"}]}
    case("mutant: a discovery-live job -> red", any("decision 4" in x for x in check(m, reg, by_port, sweep)))
    m = copy.deepcopy(doc); m["jobs"]["oracles"]["steps"].append({"run": "python3 conformance/ci/discovery_live.py --sample"})
    case("mutant: a step running the sampler -> red", any("sampler" in x for x in check(m, reg, by_port, sweep)))
    m = copy.deepcopy(doc)
    for s in m["jobs"]["seqfuzz"]["steps"]:
        if "seqfuzz_gate.py" in (s.get("run") or ""):
            s["continue-on-error"] = True
    case("mutant: continue-on-error on an assertion step -> red", any("continue-on-error" in x for x in check(m, reg, by_port, sweep)))
    m = copy.deepcopy(doc); m["jobs"]["oracles"]["steps"].append({"run": "cp x.json ops/feeds/x.json"})
    case("mutant: a step writing under ops/ -> red", any("ops/" in x for x in check(m, reg, by_port, sweep)))
    m = copy.deepcopy(doc); del m["jobs"]["crosscheck"]["timeout-minutes"]
    case("mutant: a job without timeout-minutes -> red", any("timeout-minutes" in x for x in check(m, reg, by_port, sweep)))
    m = copy.deepcopy(doc); m["jobs"]["seqfuzz"]["steps"] = [s for s in m["jobs"]["seqfuzz"]["steps"] if "upload-artifact" not in (s.get("uses") or "")]
    case("mutant: a job without an artifact upload -> red", any("upload-artifact" in x for x in check(m, reg, by_port, sweep)))
    print("nightly-workflow selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    f = check(load())
    for x in f:
        print(f"  ✗ {x}")
    print(f"nightly-workflow: {'PASS' if not f else 'FAIL'} ({len(load()['jobs'])} jobs)")
    return 0 if not f else 1


if __name__ == "__main__":
    sys.exit(main())

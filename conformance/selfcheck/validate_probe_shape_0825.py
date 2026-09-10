#!/usr/bin/env python3
"""
validate_probe_shape_0825.py — gate `probe-shape-0825`: the merchant CLI, pointed at
golden-0825 with REF_CONFIG, must report ZERO deviations in BOTH probe modes, and must
actually have run checks (PLAN-v3 D1-04).

Why: before wire_shapes.py (D1-01) the CLI sent a 2026-04-08 `destinations[]` to the
2026-08-25 golden and reported 8 false deviations about a conformant server. This gate
is the kill-proof for that delta: revert it and the default mode goes red. The second
mode, `--omit-destination-type`, sends destinations WITHOUT `type` — the platform is
allowed to (fulfillment_destination.json `ucp_request: optional`) and the golden must
default it per method (C3b, D3-04; mutant `destination_type_required_on_request`).
Both modes must show 0 deviations, an aggregate that is not `fail`, and >= MIN_RUN
checks run (a run that exercised nothing proves nothing — vacuity is red).

    python3 conformance/selfcheck/validate_probe_shape_0825.py --server http://localhost:8197
    python3 conformance/selfcheck/validate_probe_shape_0825.py --selftest
Exit 0 = both modes clean; 1 = deviations / vacuous / aggregate fail; 2 = golden down.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "checks"))
MIN_RUN = 29


def assess(default_doc, omit_doc):
    """(rc, message) from the two CLI JSON documents (default probe shape, omit-type)."""
    fails, parts = [], []
    for label, doc in (("default", default_doc), ("omit-type", omit_doc)):
        if not isinstance(doc, dict) or "verdict" not in doc:
            fails.append(f"{label} mode: CLI output unparseable"); continue
        v = doc["verdict"]
        dev = int(v.get("deviations", 0) or 0)
        run = sum(1 for c in doc.get("checks", []) if c.get("status") in ("clean-pass", "deviation"))
        if dev:
            fails.append(f"{label} mode: {dev} deviations on the conformant golden")
        if run < MIN_RUN:
            fails.append(f"{label} mode: vacuous — only {run} checks run (< {MIN_RUN})")
        if v.get("aggregate") == "fail":
            fails.append(f"{label} mode: aggregate fail")
        parts.append(f"{dev} deviations · {run} checks run" if label == "default"
                     else f"omit-type {dev} deviations")
    return (1 if fails else 0), ("; ".join(fails) + " · " if fails else "") + " · ".join(parts)


def _cli(server, config_path, extra=()):
    p = subprocess.run([sys.executable, str(ROOT / "conformance" / "checks" / "merchant.py"),
                        "--server", server, "--config", str(config_path), "--json", *extra],
                       capture_output=True, text=True, timeout=600)
    try:
        return json.loads(p.stdout[p.stdout.index("{"):]), p
    except ValueError:
        return None, p


def run(server):
    from validate_merchant_checks import REF_CONFIG
    from merchant import discover
    try:
        discover(server)
    except SystemExit as e:
        print(f"probe-shape-0825: SKIP — golden-0825 not reachable at {server} ({e})")
        return 2
    with tempfile.TemporaryDirectory(prefix="probe_shape_") as tmp:
        cfg = pathlib.Path(tmp) / "ref_config.json"
        cfg.write_text(json.dumps(REF_CONFIG))
        default_doc, p1 = _cli(server, cfg)
        omit_doc, p2 = _cli(server, cfg, ("--omit-destination-type",))
    rc, msg = assess(default_doc, omit_doc)
    if default_doc is None or omit_doc is None:
        print((p1 if default_doc is None else p2).stderr.strip()[-400:])
    print(f"probe-shape-0825: {'PASS' if rc == 0 else 'FAIL'} — {msg}")
    return rc


def _boot_teardown_case(check):
    """boot_golden_0825 (run_suite) must bring :8197 up and leave nothing listening."""
    def listening(port):
        return bool(subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                   capture_output=True, text=True).stdout.strip())
    sys.path.insert(0, str(ROOT / "conformance" / "ci"))
    import run_suite
    if listening(8197):
        check("boot helper: :8197 free before", False, "already in use — cannot prove boot/teardown")
        return
    g = run_suite.boot_golden_0825(port=8197)
    try:
        check("boot helper: golden-0825 UP on :8197", g is not None and listening(8197))
    finally:
        if g is not None:
            g.stop()
    check("boot helper: teardown leaves :8197 free", not listening(8197))


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    fn = globals().get("assess")
    if fn is None:
        print("  ✗ assess() — script absent")
        print("probe-shape-0825 selftest: FAIL (script absent)")
        return 1

    def doc(deviations, run, aggregate=None):
        agg = aggregate or ("fail" if deviations else "incomplete")
        checks = [{"id": f"c{i}", "status": "deviation" if i < deviations else "clean-pass"} for i in range(run)]
        return {"verdict": {"aggregate": agg, "deviations": deviations}, "checks": checks,
                "checks_summary": {"run": run, "deviation": deviations, "clean": run - deviations}}

    rc, msg = fn(doc(7, 29), doc(0, 29))
    check("deviations 7 (default mode) -> rc 1", rc == 1 and "7 deviations" in msg, msg)
    rc, msg = fn(doc(0, 29), doc(0, 29))
    check("deviations 0, run 29 (both modes) -> rc 0", rc == 0, msg)
    check("summary names both modes",
          "0 deviations" in msg and "29 checks run" in msg and "omit-type 0 deviations" in msg, msg)
    rc, msg = fn(doc(0, 0), doc(0, 29))
    check("run 0 -> rc 1 (vacuity)", rc == 1 and "vacu" in msg.lower(), msg)
    rc, msg = fn(doc(0, 29), doc(3, 29))
    check("omit-mode deviations 3 -> rc 1", rc == 1 and "omit-type 3 deviations" in msg, msg)
    rc, msg = fn(doc(0, 29, aggregate="fail"), doc(0, 29))
    check("aggregate fail with 0 deviations -> rc 1", rc == 1, msg)
    rc, msg = fn(doc(0, 28), doc(0, 29))
    check("run below MIN_RUN -> rc 1", rc == 1, msg)
    rc, msg = fn(None, doc(0, 29))
    check("unparseable CLI output -> rc 1", rc == 1, msg)

    _boot_teardown_case(check)

    print(f"probe-shape-0825 selftest: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="probe-shape-0825 gate.")
    ap.add_argument("--server", default="http://localhost:8197")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    fn = globals().get("run")
    if fn is None:
        print("probe-shape-0825: FAIL — script absent"); return 1
    return fn(args.server)


if __name__ == "__main__":
    sys.exit(main())

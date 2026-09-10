#!/usr/bin/env python3
"""
validate_ports_registry.py — every literal port the harness binds is REGISTERED in
conformance/ci/ports.json, and no two names claim one port (PLAN-v3 §2.18, D5-19).

Why: ports were assigned by convention across run_suite.py, selftest.sh, the golden
boot scripts, selfcheck stubs and CI, so the same number could be claimed twice
(8197 was double-claimed by two Wave-0 tasks) and selftest.sh's sweep list silently
missed 8198/8199. The registry is the single source: selftest.sh derives its sweep
from it, and this gate reds on any literal port in code that the registry lacks.

  validate_ports_registry.py             # real tree: N ports registered · 0 unregistered · 0 collisions
  validate_ports_registry.py --selftest  # hermetic kill-tests on a scratch tree (planted literal → red;
                                         # two names on one port → red; clean → green)
Exit 0 pass · 1 fail. Stdlib only.
"""
import json, os, pathlib, re, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]


def selftest():
    bad = 0

    def case(name, want_red, registry, files):
        nonlocal bad
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "conformance" / "ci").mkdir(parents=True)
            (root / "conformance" / "ci" / "ports.json").write_text(json.dumps(registry))
            for rel, body in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
            fails = check(root)
            got_red = bool(fails)
            ok = got_red == want_red
            print(f"  {'✓' if ok else '✗'} {name}: {'RED' if got_red else 'GREEN'}"
                  + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}")
                  + (("\n      " + "\n      ".join(fails)) if fails else ""))
            bad += 0 if ok else 1
            return fails

    reg = {"golden": {"port": 8182, "owner_task": "existing", "used_by": ["run_suite.py"]},
           "proxy": {"port": 8183, "owner_task": "existing", "used_by": ["run_suite.py"]}}
    good = {"conformance/ci/run_suite.py":
            "PROXY_PORT = 8183\nSERVER = 'http://localhost:8182'\n# RFC 9421 is not a port\n",
            "conformance/ci/selftest.sh":
            "PORTS=($(python3 \"$ROOT/conformance/ci/validate_ports_registry.py\" --sweep))\n"}

    f = case("scratch run_suite with an UNREGISTERED literal port 9977", True, reg,
             {**good, "conformance/ci/run_suite.py": good["conformance/ci/run_suite.py"] + "STUB_PORT = 9977\n"})
    if not any("9977" in x and "run_suite.py" in x for x in f):
        print("  ✗ the failure must name the literal AND the file"); bad += 1

    f = case("two registry names on ONE port (collision)", True,
             {**reg, "proxy-twin": {"port": 8183, "owner_task": "x", "used_by": []}}, good)
    if not any("collision" in x and "8183" in x for x in f):
        print("  ✗ the failure must say 'collision' and name the port"); bad += 1

    case("hand-maintained PORTS=(…) literal array in selftest.sh (must derive from the registry)",
         True, reg, {**good, "conformance/ci/selftest.sh": "PORTS=(8182 8183)\n"})

    case("RFC numbers / non-port digits are not ports", False, reg,
         {**good, "conformance/selfcheck/x.py": "# RFC 9901 §7.1; amount 9999; year 2026\n"})

    case("clean scratch tree (every literal registered, no collision)", False, reg, good)

    print(f"\nports-registry selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())

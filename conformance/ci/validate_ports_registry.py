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

REGISTRY = "conformance/ci/ports.json"
# Files whose port literals must be registered: the suite runner, the local sweep, CI
# workflows, the golden boot/stop scripts, and every selfcheck/boot helper that binds a
# stub. (checks/ and agent/ carry RFC numbers, not ports — out of scope by design.)
SCAN_GLOBS = ("conformance/ci/*.py", "conformance/ci/*.sh", "conformance/selfcheck/*.py",
              "conformance/checks/golden_check_08_25.py",
              "conformance/testbed/golden-0825/*.sh", "conformance/testbed/golden-0825/smoke/*.py",
              ".github/workflows/*.yml", "packaging/*.sh")
# A literal is a PORT when it sits in a port-shaped context; bare four-digit numbers
# (RFC 9421, amounts, years) are not.
PORT_CTX = [
    re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d{4,5})\b"),
    re.compile(r"\b[A-Z_]*PORTS?[A-Z_0-9]*\s*[:=]\s*\(?[\"']?(\d{4,5})\b"),      # PORT = 8184 / NODE_PORT: "3000"
    re.compile(r"\$\{[A-Z_0-9]*PORT[A-Z_0-9]*:-(\d{4,5})\}"),                  # ${PORT:-8283}
    re.compile(r"(?i)\bports?\s+(\d{4,5})\b"),                                # "port 8199" (prose/comments)
    re.compile(r"\btcp:(\d{4,5})\b"),
    re.compile(r"--port[ =](\d{4,5})\b"),
    re.compile(r"(?i)_PORT\b[^\n]*?[\"'](\d{4,5})[\"']"),                        # get("..._PORT", "8399")
]
# a line that names a port variable may list more ports in its trailing comment
LOOSE = re.compile(r"\b(3000|8[1-4]\d\d|9[0-9]{3})\b")
# a hand-maintained sweep list: PORTS=( … 8183 8184 … ) with digits and no $(…) derivation
LITERAL_ARRAY = re.compile(r"^\s*PORTS=\((?![^)]*\$\()([^)]*\b\d{4,5}\b[^)]*)\)", re.M)


def load_registry(root):
    f = pathlib.Path(root) / REGISTRY
    if not f.exists():
        return None
    d = json.load(open(f))
    return {k: v for k, v in d.items() if not k.startswith("_")}


def scan_literals(root):
    """[(relpath, line_no, port)] for every port-shaped literal under SCAN_GLOBS."""
    import glob
    out = []
    for g in SCAN_GLOBS:
        for f in sorted(glob.glob(str(pathlib.Path(root) / g))):
            rel = os.path.relpath(f, root)
            if rel == "conformance/ci/validate_ports_registry.py":
                continue                       # its own selftest plants literals on purpose
            for i, line in enumerate(open(f, encoding="utf-8", errors="replace"), 1):
                hits = set()
                for cre in PORT_CTX:
                    hits |= {int(m.group(1)) for m in cre.finditer(line)}
                if re.search(r"(?i)\bport", line) and not re.search(r"\bRFC\s*\d{4}", line):
                    hits |= {int(x) for x in LOOSE.findall(line)}
                for m in LITERAL_ARRAY.finditer(line):
                    hits |= {int(x) for x in re.findall(r"\b(\d{4,5})\b", m.group(1))}
                for port in sorted(hits):
                    if 1024 <= port <= 65535:
                        out.append((rel, i, port))
    return out


def check(root):
    """Failure strings (empty = green) for the tree at `root`."""
    fails = []
    reg = load_registry(root)
    if reg is None:
        return [f"{REGISTRY} missing — the registry is the single source of every harness port"]
    by_port = {}
    for name, row in reg.items():
        for k in ("port", "owner_task", "used_by"):
            if k not in row:
                fails.append(f"registry row {name!r} lacks {k!r}")
        p = row.get("port")
        if not isinstance(p, int) or not (1024 <= p <= 65535):
            fails.append(f"registry row {name!r}: port {p!r} is not a valid TCP port"); continue
        by_port.setdefault(p, []).append(name)
    for p, names in sorted(by_port.items()):
        if len(names) > 1:
            fails.append(f"collision: port {p} is claimed by {len(names)} names {names}")
    registered = set(by_port)
    seen = set()
    for rel, ln, port in scan_literals(root):
        if port not in registered and (rel, port) not in seen:
            seen.add((rel, port))
            fails.append(f"unregistered literal port {port} in {rel}:{ln} — add it to {REGISTRY} first")
    st = pathlib.Path(root) / "conformance" / "ci" / "selftest.sh"
    if st.exists():
        txt = st.read_text()
        if LITERAL_ARRAY.search(txt):
            fails.append("selftest.sh carries a hand-maintained PORTS=(…) literal array — derive it "
                         "from the registry (validate_ports_registry.py --sweep)")
        elif "ports.json" not in txt and "validate_ports_registry.py" not in txt:
            fails.append("selftest.sh does not derive its sweep from the ports registry")
    return fails


def sweep_ports(root):
    reg = load_registry(root) or {}
    return sorted({r["port"] for r in reg.values() if r.get("sweep", True)})


def main():
    # the hermetic kill-tests run first on every real run: a checker that cannot be
    # made to fail on a planted literal validates nothing
    if selftest() != 0:
        return 1
    print()
    fails = check(ROOT)
    reg = load_registry(ROOT) or {}
    lits = scan_literals(ROOT)
    unreg = len({(r, p) for r, _, p in lits if p not in {x["port"] for x in reg.values()}})
    coll = sum(1 for f in fails if f.startswith("collision"))
    for f in fails:
        print(f"  x {f}")
    sw = set(sweep_ports(ROOT))
    want = set(range(8194, 8200))
    if not want <= sw:
        fails.append(f"selftest sweep must cover 8194-8199 (golden-0825 family); missing {sorted(want - sw)}")
        print(f"  x {fails[-1]}")
    print(f"ports-registry: {len(reg)} ports registered · {unreg} unregistered literals · {coll} collisions"
          f" · sweep {len(sw)} ports" + ("" if not fails else f" · FAIL ({len(fails)} finding(s))"))
    return 1 if fails else 0


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
    if "--sweep" in sys.argv[1:]:          # consumed by selftest.sh: the pre-clean port list
        print(" ".join(str(p) for p in sweep_ports(ROOT)))
        sys.exit(0)
    sys.exit(main())

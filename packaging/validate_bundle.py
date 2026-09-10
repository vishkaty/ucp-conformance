#!/usr/bin/env python3
"""
validate_bundle.py — the pip/Action bundle must be COMPLETE, not merely current.

CI's drift step (`sync_bundle.sh && git diff --exit-code packaging/spck_conformance/_bundle`)
proves only that every file sync_bundle.sh copies is up to date — never that every file
`merchant.main` needs IS copied. A module added to conformance/checks (wire_shapes.py,
D1-01) that misses the cp list ships a wheel whose `spck-conformance --server` dies on
ImportError while every gate stays green (D1 N17). This validator closes that gap
(PLAN-v3 D1-05):

  1. imports `merchant` from the bundle in a SUBPROCESS whose sys.path is restricted to
     the bundle (the installed-wheel condition; the source tree must be unreachable),
     runs `merchant_checks.all_checks()` and `wire_shapes.shapes_for(v)` for every
     reviewed version;
  2. diffs the bundle's module list against the transitive first-party imports of
     merchant.py resolved from SOURCE — a module the source runner imports that the
     bundle lacks is named, red;
  3. the register data the runner reads (`_area_capabilities.json` per version) must be
     present in the bundle's requirements/ copy.

    python3 packaging/validate_bundle.py             # validate the committed bundle
    python3 packaging/validate_bundle.py --selftest  # kill-tests on a scratch bundle
Exit 0 = complete; 1 = something the runner needs is missing (named).
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BUNDLE = HERE / "spck_conformance" / "_bundle" / "conformance"
SOURCE = ROOT / "conformance"


SHAPE_VERSIONS = ("2026-01-11", "2026-01-23", "2026-04-08", "2026-08-25")


def _first_party_modules(src_root):
    """Relative paths (under conformance/) of every first-party module the packaged
    runner can reach, resolved from SOURCE by walking `import`/`from … import` with
    ast, starting at checks/merchant.py — plus what merchant_checks.all_checks()
    discovers dynamically (checks/merchant_checks_*.py, tls_check_01_11_01_23)."""
    import ast
    import glob as _glob
    entry = [src_root / "checks" / "merchant.py",
             src_root / "checks" / "tls_check_01_11_01_23.py"]
    entry += [pathlib.Path(f) for f in _glob.glob(str(src_root / "checks" / "merchant_checks_*.py"))]
    seen, todo = set(), list(entry)
    while todo:
        f = todo.pop()
        rel = f.relative_to(src_root)
        if rel in seen or not f.exists():
            continue
        seen.add(rel)
        tree = ast.parse(f.read_text(), filename=str(f))
        # an import inside `try: … except (ImportError|ModuleNotFoundError|Exception):`
        # is OPTIONAL by construction (the runner degrades honestly — e.g. the
        # schema_oracle import in merchant_checks returns INCONCLUSIVE without it);
        # such modules are not bundle requirements.
        guarded = set()
        for t in ast.walk(tree):
            if isinstance(t, ast.Try) and any(
                    h.type is None or any(n in ast.dump(h.type) for n in
                                          ("ImportError", "ModuleNotFoundError", "Exception"))
                    for h in t.handlers):
                for inner in t.body:
                    for g in ast.walk(inner):
                        if isinstance(g, (ast.Import, ast.ImportFrom)):
                            guarded.add(id(g))
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            else:
                continue
            for n in names:
                top = n.split(".")[0]
                for cand in (f.parent / f"{top}.py",                       # sibling
                             src_root / (n.replace(".", "/") + ".py"),     # common.x
                             src_root / "selfcheck" / f"{top}.py"):       # merchant.py adds selfcheck/
                    if cand.exists():
                        todo.append(cand)
                        break
    return sorted(seen)


def _data_files(src_root):
    """Register data the runner reads at run time, relative to conformance/."""
    import glob as _glob
    out = [pathlib.Path(f).relative_to(src_root)
           for f in _glob.glob(str(src_root / "requirements" / "*" / "_area_capabilities.json"))]
    out += [pathlib.Path(f).relative_to(src_root)
            for f in _glob.glob(str(src_root / "checks" / "expected_skips_*.json"))]
    return sorted(out)


def validate(bundle):
    """(rc, report). The bundle at `bundle` (a conformance/ tree) must carry every
    first-party module + data file the runner needs, and must import + run in an
    isolated interpreter that cannot see the source tree."""
    bundle = pathlib.Path(bundle)
    lines, rc = [], 0
    modules = _first_party_modules(SOURCE)
    missing = [str(m) for m in modules if not (bundle / m).exists()]
    if missing:
        rc = 1
        lines.append("MISSING module(s) the runner imports (add to packaging/sync_bundle.sh): "
                     + ", ".join(missing))
    data = _data_files(SOURCE)
    missing_data = [str(d) for d in data if not (bundle / d).exists()]
    if missing_data:
        rc = 1
        lines.append("MISSING data file(s) the runner reads: " + ", ".join(missing_data))
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(bundle / 'checks')!r})\n"
        "import merchant, merchant_checks, wire_shapes\n"
        "n = len(merchant_checks.all_checks())\n"
        f"vs = [v for v in {SHAPE_VERSIONS!r} if wire_shapes.shapes_for(v)]\n"
        "for v in vs:\n"
        "    merchant.area_capabilities(v)\n"
        "assert callable(merchant.main)\n"
        "print(json.dumps({'checks': n, 'shapes': len(vs)}))\n"
    )
    with tempfile.TemporaryDirectory() as cwd:      # -I: no cwd/PYTHONPATH -> source unreachable
        r = subprocess.run([sys.executable, "-I", "-c", code], cwd=cwd,
                           capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        rc = 1
        tail = (r.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        lines.append(f"bundle import FAILED in an isolated interpreter: {tail}")
        shapes, checks = 0, 0
    else:
        got = json.loads(r.stdout.strip().splitlines()[-1])
        shapes, checks = got["shapes"], got["checks"]
        if shapes != len(SHAPE_VERSIONS):
            rc = 1
            lines.append(f"shapes_for covers {shapes} versions, expected {len(SHAPE_VERSIONS)}")
    if rc == 0:
        lines.append(f"bundle complete: {len(modules)} modules, merchant.main importable, "
                     f"shapes {shapes} versions ({checks} checks, {len(data)} data files)")
    else:
        lines.append("bundle INCOMPLETE")
    return rc, "\n".join(lines)


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    validator = globals().get("validate")
    if validator is None:
        print("  ✗ validate() — no validator exists yet")
        print("package-bundle: FAIL (no validator)")
        return 1

    # a scratch copy of the real bundle, minus one module the runner imports
    with tempfile.TemporaryDirectory(prefix="bundle_selftest_") as tmp:
        scratch = pathlib.Path(tmp) / "conformance"
        shutil.copytree(BUNDLE, scratch)
        (scratch / "checks" / "wire_shapes.py").unlink()
        rc, report = validator(scratch)
        check("missing wire_shapes.py → rc 1", rc == 1, f"rc={rc}")
        check("names the missing module", "wire_shapes" in report, report[-300:])

    with tempfile.TemporaryDirectory(prefix="bundle_selftest_") as tmp:
        scratch = pathlib.Path(tmp) / "conformance"
        shutil.copytree(BUNDLE, scratch)
        (scratch / "requirements" / "2026-08-25" / "_area_capabilities.json").unlink()
        rc, report = validator(scratch)
        check("missing _area_capabilities.json → rc 1", rc == 1, f"rc={rc}")
        check("names the missing data file", "_area_capabilities.json" in report, report[-300:])

    rc, report = validator(BUNDLE)
    check("the committed bundle validates", rc == 0, report[-400:])

    print(f"package-bundle: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    validator = globals().get("validate")
    if validator is None:
        print("validate_bundle.py: no validator implemented"); sys.exit(1)
    rc, report = validator(BUNDLE)
    print(report)
    sys.exit(rc)

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
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BUNDLE = HERE / "spck_conformance" / "_bundle" / "conformance"
SOURCE = ROOT / "conformance"


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

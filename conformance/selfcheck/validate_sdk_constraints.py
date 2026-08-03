#!/usr/bin/env python3
"""
validate_sdk_constraints.py — regression watch for the released ucp-sdk validators
(python-sdk#57 contains/minContains/maxContains + python-sdk#59 uniqueItems, both
first shipped in 0.4.4, the release SOURCES.lock pins for the golden).

The golden resolves ucp-sdk from PyPI at reference_sdk.pypi_pin, so a future
release that silently drops these validators would change what the golden accepts
without anything in our repo noticing — the suite's merchant probes never exercise
SDK-side model validation directly. This gate is what goes red in that case:

  * CURRENT leg: run sdk_constraints_probe.py inside the golden's venv. Every
    constraint must be enforced (exit 0). Also asserts the venv's ucp-sdk equals
    reference_sdk.pypi_pin, so the leg provably grades the pinned release.
  * MUTANT  leg: the same probe inside a throwaway venv with ucp-sdk==0.4.3 — the
    real predecessor release, which lacks both validators. The probe must FAIL
    there (nonzero), proving on every run that it can detect a validator-less SDK
    rather than passing vacuously.

Exit 0 = proven both ways; 1 = failed; 2 = environment skip (uv or vendored golden
absent, or PyPI unreachable for the mutant venv).
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "conformance" / ".vendor" / "samples" / "rest" / "python" / "server"
PROBE = pathlib.Path(__file__).resolve().parent / "sdk_constraints_probe.py"
LOCK = ROOT / "conformance" / "SOURCES.lock.json"
MUTANT_VERSION = "0.4.3"   # last release WITHOUT #57/#59; a fixed, deterministic mutant


def main():
    if shutil.which("uv") is None or not SERVER_DIR.is_dir():
        print("sdk-constraints: uv or vendored golden absent (skip)")
        return 2
    pin = json.loads(LOCK.read_text())["reference_sdk"].get("pypi_pin", "")

    got = subprocess.run(["uv", "run", "python", "-c",
                          "from importlib.metadata import version; "
                          "print(version('ucp-sdk'))"],
                         cwd=SERVER_DIR, capture_output=True, text=True, timeout=300)
    installed = got.stdout.strip()
    if got.returncode != 0 or not installed:
        print("sdk-constraints: cannot resolve golden ucp-sdk version (skip)")
        return 2
    if installed != pin:
        print(f"sdk-constraints: FAIL — golden venv runs ucp-sdk {installed} but "
              f"SOURCES.lock pins {pin}; this leg would grade the wrong release")
        return 1

    print(f"sdk-constraints — pinned release ({installed}):")
    cur = subprocess.run(["uv", "run", "python", str(PROBE)],
                         cwd=SERVER_DIR, capture_output=True, text=True, timeout=300)
    sys.stdout.write(cur.stdout)
    if cur.returncode != 0:
        print("sdk-constraints: FAIL — the pinned ucp-sdk release does not enforce "
              "every watched constraint (regression of python-sdk#57/#59?)")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        venv = pathlib.Path(tmp) / "venv"
        mk = subprocess.run(["uv", "venv", str(venv)], capture_output=True, timeout=300)
        inst = subprocess.run(
            ["uv", "pip", "install", "--python", str(venv / "bin" / "python"),
             f"ucp-sdk=={MUTANT_VERSION}", "pydantic"],
            capture_output=True, timeout=600) if mk.returncode == 0 else mk
        if inst.returncode != 0:
            print("sdk-constraints: mutant venv unavailable (offline?) — skip")
            return 2
        print(f"sdk-constraints — mutant release ({MUTANT_VERSION}, must fail):")
        mut = subprocess.run([str(venv / "bin" / "python"), str(PROBE)],
                             capture_output=True, text=True, timeout=300)
        sys.stdout.write(mut.stdout)
        if mut.returncode == 0:
            print("sdk-constraints: FAIL — probe passed on the validator-less "
                  f"{MUTANT_VERSION}; it cannot detect a regression (vacuous)")
            return 1

    print("sdk-constraints: PASS — pinned release enforces all watched constraints "
          f"and the probe goes red on {MUTANT_VERSION} (non-vacuous)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

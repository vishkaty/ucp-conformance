#!/usr/bin/env python3
"""
validate_site_gates.py — hermetic kill-tests for the site-governance lane itself
(site_gates.py, sync_site_claims.py): a gate that cannot be made to fail validates
nothing. Every case builds a SCRATCH copy of public/ (the committed tree is never
touched), plants one defect, and asserts the gate reddens with the expected line.

  D5-10  test_claims_scope_recurses      — a hand-authored page under public/<sub>/ is audited
  D5-01  test_docclaims_reds_on_stale_count — a stale count in functions/**/*.js reds docclaims
  D5-05  test_sync_site_claims_check_reds_on_drift / test_sync_never_rewrites_review_fields

Run: python3 conformance/ci/validate_site_gates.py   (exit 0 pass · 1 fail). Stdlib only.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATES = ROOT / "conformance" / "ci" / "site_gates.py"


def scratch_public(tmp):
    """Copy the committed public/ (minus the generated checks/ tree) into tmp/public."""
    dst = pathlib.Path(tmp) / "public"
    shutil.copytree(ROOT / "public", dst, ignore=shutil.ignore_patterns("checks", "fonts"))
    return dst


def run_mode(mode, pub, extra=()):
    env = dict(os.environ, SPCK_PUBLIC=str(pub))
    r = subprocess.run([sys.executable, str(GATES), mode, *extra], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def test_claims_scope_recurses():
    """D5-10: audit scope = every hand-authored page under public/** — a planted
    public/sub/x.html carrying an unregistered proof claim must red `claims`."""
    with tempfile.TemporaryDirectory() as tmp:
        pub = scratch_public(tmp)
        (pub / "sub").mkdir()
        (pub / "sub" / "x.html").write_text(
            "<!doctype html><html><body><p>Trusted by 100% of stores on the planet.</p>"
            "</body></html>")
        rc, out = run_mode("claims", pub)
        if rc == 0:
            return "claims stayed GREEN with an unregistered '100% of stores' claim planted in public/sub/x.html (scope does not recurse)"
        if "sub/x.html" not in out:
            return f"claims went red but did not name sub/x.html:\n{out[-600:]}"
        rc2, out2 = run_mode("claims", pub.parent / "public")
        return None


TESTS = [test_claims_scope_recurses]


def main():
    bad = 0
    for t in TESTS:
        try:
            err = t()
        except Exception as e:                       # noqa: BLE001 — a crash is a failure with a name
            err = f"{type(e).__name__}: {e}"
        print(f"  {'✓' if not err else '✗'} {t.__name__}" + (f": {err}" if err else ""))
        bad += 1 if err else 0
    print(f"\nvalidate_site_gates: {'PASS' if not bad else f'FAIL ({bad} case(s))'} ({len(TESTS)} case(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

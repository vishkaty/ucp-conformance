#!/usr/bin/env python3
"""
validate_site_gates.py — hermetic kill-tests for the site-governance lane itself
(site_gates.py, sync_site_claims.py): a gate that cannot be made to fail validates
nothing. Every case builds a SCRATCH copy of public/ (the committed tree is never
touched), plants one defect, and asserts the gate reddens with the expected line.

  D5-10  test_claims_scope_recurses      — a hand-authored page under public/<sub>/ is audited
  D5-01  test_docclaims_reds_on_stale_count — a stale count in functions/**/*.js reds docclaims
  D5-05  test_sync_site_claims_check_reds_on_drift / test_sync_never_rewrites_review_fields
  D5-12  test_checkdocs_known_issues_drift  — a hand-edited cell on the generated known-issues page reds checkdocs + --check
  D5-16  test_manifest_counts_all_checks_and_reds_on_duplicate — the count is len(all_checks()); a planted duplicate id reds

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


def run_docclaims(docroot, extra=()):
    env = dict(os.environ, SPCK_DOCROOT=str(docroot))
    r = subprocess.run([sys.executable, str(GATES), "docclaims", *extra], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def test_docclaims_reds_on_stale_count():
    """D5-01: copy in NON-page files (functions/**/*.js, README, ci/README, packaging README,
    ROADMAP) is swept like public/*.html — a scratch functions/x.js advertising
    '37 kill-rate-validated checks' must red `docclaims` with a line naming the file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "functions").mkdir()
        (root / "functions" / "x.js").write_text(
            "/** The full 37 kill-rate-validated checks run only in the CLI. */\n")
        (root / "README.md").write_text("# scratch\n")
        rc, out = run_docclaims(root)
        if rc == 0:
            return "docclaims stayed GREEN with a stale '37 kill-rate-validated checks' planted in functions/x.js (functions/ not scanned)"
        if "functions/x.js" not in out or "37" not in out:
            return f"docclaims went red but did not name functions/x.js and the stale count:\n{out[-500:]}"
        return None


SYNC = ROOT / "conformance" / "web" / "sync_site_claims.py"


def run_sync(args, cwd=None):
    r = subprocess.run([sys.executable, str(SYNC), *args], cwd=str(cwd or ROOT),
                       capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout + r.stderr


def test_sync_site_claims_check_reds_on_drift():
    """D5-05: `sync_site_claims.py --check` byte-compares the generated manifest +
    evidence.per_version blocks with the registry — a scratch registry whose
    manifest.merchant_checks reads 1 must exit 1 NAMING the field."""
    with tempfile.TemporaryDirectory() as tmp:
        reg = pathlib.Path(tmp) / "site_claims.json"
        doc = json.loads((ROOT / "public" / "site_claims.json").read_text())
        doc["manifest"]["merchant_checks"] = 1
        reg.write_text(json.dumps(doc, indent=2) + "\n")
        rc, out = run_sync(["--check", "--file", str(reg)])
        if rc == 0:
            return "--check stayed GREEN with manifest.merchant_checks planted as 1"
        if "merchant_checks" not in out:
            return f"--check went red but did not name merchant_checks:\n{out[-400:]}"
        return None


def test_sync_never_rewrites_review_fields():
    """D5-05 (RV2 T9): the writer regenerates ONLY manifest + evidence.per_version; every
    claim's text/evidence/review_by and both `reviewed` stamps come back byte-identical."""
    with tempfile.TemporaryDirectory() as tmp:
        reg = pathlib.Path(tmp) / "site_claims.json"
        before = json.loads((ROOT / "public" / "site_claims.json").read_text())
        before["manifest"]["merchant_checks"] = 1            # force a rewrite of the manifest
        before["evidence"]["per_version"] = {}
        reg.write_text(json.dumps(before, indent=2) + "\n")
        rc, out = run_sync(["--write", "--file", str(reg)])
        if rc != 0:
            return f"writer failed rc={rc}:\n{out[-400:]}"
        after = json.loads(reg.read_text())
        if after["manifest"]["merchant_checks"] == 1 or not after["evidence"]["per_version"]:
            return "writer did not regenerate manifest/evidence"
        frozen = lambda d: (d["manifest"].get("reviewed"), d["evidence"].get("reviewed"), d["evidence"].get("_about"),
                            [(c.get("id"), c.get("text"), c.get("evidence"), c.get("review_by"), c.get("page"), c.get("added"))
                             for c in d["claims"]], d.get("retired_claims"), d.get("_about"))
        if frozen(before) != frozen(after):
            return "writer touched reviewed/review_by/claim text/evidence — forbidden"
        return None


def scratch_public_full(tmp):
    """Copy the committed public/ INCLUDING the generated checks/ tree (checkdocs needs it)."""
    dst = pathlib.Path(tmp) / "public"
    shutil.copytree(ROOT / "public", dst, ignore=shutil.ignore_patterns("fonts"))
    return dst


def test_checkdocs_known_issues_drift():
    """D5-12: the known-issues page is GENERATED from conformance/ci/known_issues.json and
    byte-compared by `checkdocs` (and by `gen_known_issues.py --check`): a hand-edited cell
    on public/known-issues.html must red both, naming the page."""
    gen = ROOT / "conformance" / "web" / "gen_known_issues.py"
    if not gen.exists():
        return "conformance/web/gen_known_issues.py absent — no known-issues page/generator"
    with tempfile.TemporaryDirectory() as tmp:
        pub = scratch_public_full(tmp)
        env = dict(os.environ, SPCK_PUBLIC=str(pub))
        r = subprocess.run([sys.executable, str(gen), "--write"], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return f"gen_known_issues.py --write failed on the scratch copy:\n{(r.stdout + r.stderr)[-400:]}"
        page = pub / "known-issues.html"
        if not page.exists():
            return "generator wrote no public/known-issues.html"
        html = page.read_text(encoding="utf-8")
        if "KI-001" not in html:
            return "generated page does not carry the first row id KI-001"
        page.write_text(html.replace("KI-001", "KI-901", 1), encoding="utf-8")   # one hand-edited cell
        rc, out = run_mode("checkdocs", pub)
        if rc == 0:
            return "checkdocs stayed GREEN with a hand-edited cell on known-issues.html (no known-issues byte-compare)"
        if "known-issues" not in out:
            return f"checkdocs went red but did not name known-issues:\n{out[-500:]}"
        r = subprocess.run([sys.executable, str(gen), "--check"], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            return "gen_known_issues.py --check stayed GREEN with a hand-edited cell"
        if "known-issues.html" not in r.stdout + r.stderr:
            return f"--check went red without naming known-issues.html:\n{(r.stdout + r.stderr)[-400:]}"
        return None


def test_manifest_counts_all_checks_and_reds_on_duplicate():
    """D5-16 (H1): the product's merchant-check count is len(merchant_checks.all_checks()) — the
    runtime set, incl. modules the old `^    MCheck(` regex never saw (228, not 227) — counted by
    ONE helper (conformance/ci/checkset_count.py) that every copy/manifest gate uses, and a
    duplicate MCheck id planted in a scratch checks dir must red it naming the id."""
    helper = ROOT / "conformance" / "ci" / "checkset_count.py"
    if not helper.exists():
        return "conformance/ci/checkset_count.py absent — counts still come from the `^    MCheck(` regex (227 vs 228 at runtime)"
    r = subprocess.run([sys.executable, str(helper), "--json"], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return f"checkset_count.py failed on the real tree:\n{(r.stdout + r.stderr)[-300:]}"
    got = json.loads(r.stdout)
    want = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'conformance/checks');"
                           "import merchant_checks as m;print(len(m.all_checks()))"], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120).stdout.strip()
    if str(got.get("merchant_checks")) != want:
        return f"checkset_count says {got.get('merchant_checks')} but len(all_checks()) is {want}"
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "checks"
        shutil.copytree(ROOT / "conformance" / "checks", d, ignore=shutil.ignore_patterns("__pycache__"))
        (d / "merchant_checks_zz_planted_dup.py").write_text(
            "from merchant_checks import MCheck, profile_resp, p_version\n"
            "CHECKS_PLANTED = [MCheck('discovery.version', ['DISC-013'], 'MUST', profile_resp, p_version, ['drop:version'])]\n")
        env = dict(os.environ, SPCK_CHECKS_DIR=str(d))
        r2 = subprocess.run([sys.executable, str(helper), "--json"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
        if r2.returncode == 0:
            return "checkset_count stayed GREEN with a duplicate 'discovery.version' planted in a scratch checks dir"
        if "discovery.version" not in r2.stdout + r2.stderr:
            return f"duplicate refused but not named:\n{(r2.stdout + r2.stderr)[-300:]}"
    return None


TESTS = [test_claims_scope_recurses, test_docclaims_reds_on_stale_count,
         test_sync_site_claims_check_reds_on_drift, test_sync_never_rewrites_review_fields,
         test_checkdocs_known_issues_drift, test_manifest_counts_all_checks_and_reds_on_duplicate]


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

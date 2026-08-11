#!/usr/bin/env python3
"""
validate_area_module_loading.py — the area-module loading-integrity guard.

P0-3 integrity hole: a broken `checks/area_04_08_*.py` module vanished from the run
WITHOUT reddening anything.
  * run_04_08.collect() caught the ImportError, printed to stderr, and CONTINUED, and
    main() only reds on a *loaded* unsound check — so a syntax error in e.g.
    area_04_08_cart.py silently dropped its 5 checks (37 -> 32) and the gate still
    exited 0 (PASS).
  * matrix.py's _module_checks caught the ImportError and FELL BACK to a text-scan of
    the file's citations, so the vanished module's ids still bucketed as CHECK in the
    public coverage matrix — the kill-tests stopped running while the matrix said
    nothing had changed.

The fix makes both sides fail LOUD, and this gate proves it (golden_boot_guards
pattern — every case carries the mutant that would slip past a weaker guard):

  A. area_loader.load_area_checks enforces a MANIFEST: every listed module must be
     present, importable, and export EXACTLY its expected CHECKS count. A missing
     module, an unimportable module, a count drift (vanished / shrunk / grew), or an
     on-disk module absent from the manifest each RAISES AreaManifestError. The real
     04-08 manifest matches the real modules (no false red).
  B. matrix.py RECORDS every checks/ import failure and treats it as a gate FAILURE
     (import_failures() non-empty -> matrix main reds), instead of silently
     text-scanning the broken module's ids into the matrix.

Hermetic: temp modules on a temp path; no golden, no oracle, no network.
--selftest: exit 0 pass, 1 fail.
"""
import importlib
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHK = HERE.parents[0] / "checks"
COV = HERE.parents[0] / "coverage"
sys.path.insert(0, str(CHK))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COV))

import checkset_manifest                                  # noqa: E402
from checkset_manifest import load_area_checks, AreaManifestError  # noqa: E402


def _write(dirp, stem, body):
    (dirp / f"{stem}.py").write_text(body)


def _raises(fn):
    try:
        fn()
        return False
    except AreaManifestError:
        return True


def _part_a():
    """area_loader manifest enforcement — every branch + its inverse."""
    results = []
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        sys.path.insert(0, str(d))
        try:
            _write(d, "xarea_good_1", "CHECKS = [1, 2]\n")
            _write(d, "xarea_good_2", "CHECKS = ['a', 'b', 'c']\n")
            manifest = {"xarea_good_1": 2, "xarea_good_2": 3}
            core = ["core0"]

            # (0) all present, correct counts -> no raise, combined = core + 5
            try:
                got = load_area_checks(d, "xarea_good_*.py", manifest, core)
                ok = (len(got) == 1 + 2 + 3)
            except AreaManifestError:
                ok = False
            results.append(("all-present-correct", ok, True))

            # (1) a manifest module MISSING from disk -> raise
            results.append(("missing-module",
                            _raises(lambda: load_area_checks(
                                d, "xarea_good_*.py",
                                {**manifest, "xarea_absent": 1}, core)), True))

            # (2) an on-disk module NOT in the manifest -> raise (deliberate-add guard)
            _write(d, "xarea_good_3", "CHECKS = [9]\n")
            results.append(("unlisted-on-disk",
                            _raises(lambda: load_area_checks(
                                d, "xarea_good_*.py", manifest, core)), True))
            (d / "xarea_good_3.py").unlink()

            # (3) a module that FAILS to import (syntax error) -> raise
            _write(d, "xarea_broken", "CHECKS = [1]\nthis is not python !!!\n")
            results.append(("unimportable-module",
                            _raises(lambda: load_area_checks(
                                d, "xarea_*.py",
                                {**manifest, "xarea_broken": 1}, core)), True))

            # (4) a count DRIFT (module exports fewer than manifest) -> raise
            results.append(("count-drift",
                            _raises(lambda: load_area_checks(
                                d, "xarea_good_*.py",
                                {"xarea_good_1": 2, "xarea_good_2": 99}, core)), True))
        finally:
            sys.path.remove(str(d))
            for m in list(sys.modules):
                if m.startswith("xarea_"):
                    del sys.modules[m]

    # (5) INTEGRATION: the real run_04_08 manifest matches the real modules (no false red)
    import run_04_08
    try:
        checks = run_04_08.collect()
        ok = len(checks) >= 1     # collect succeeds and returns a non-empty set
        detail = f"real collect() -> {len(checks)} checks"
    except AreaManifestError as e:
        ok, detail = False, f"real manifest FALSE-RED: {e}"
    results.append(("real-manifest-matches-disk", ok, True, detail))
    return results


def _part_b():
    """matrix.py treats a checks/ import failure as a gate FAILURE, not a text-scan shrug."""
    import matrix
    results = []
    with tempfile.TemporaryDirectory() as td:
        # matrix only import-attempts files whose parent dir is named "checks"
        chkdir = pathlib.Path(td) / "checks"
        chkdir.mkdir()
        sys.path.insert(0, str(chkdir))
        good = chkdir / "xmatrix_good.py"
        good.write_text("CHECKS = []\n")
        bad = chkdir / "xmatrix_broken.py"
        bad.write_text("CHECKS = [1]\ndef (  # syntax error\n")
        orig_check_files = matrix.check_files
        try:
            # clean inverse: only a good module -> no import failures recorded
            matrix.check_files = lambda: [str(good)]
            matrix._reset_import_failures()
            matrix.covered_ids_by_version()
            clean = (len(matrix.import_failures()) == 0)
            results.append(("clean-no-import-failure", clean, True))

            # the hole: an unimportable checks/ module -> recorded as a failure
            matrix.check_files = lambda: [str(good), str(bad)]
            matrix._reset_import_failures()
            matrix.covered_ids_by_version()
            recorded = (len(matrix.import_failures()) >= 1)
            results.append(("import-failure-recorded", recorded, True))

            # enforcement: import_failures present -> the gate must FAIL
            results.append(("enforcement-reds",
                            matrix.has_import_failures(), True))
        finally:
            matrix.check_files = orig_check_files
            matrix._reset_import_failures()
            sys.path.remove(str(chkdir))
            for m in list(sys.modules):
                if m.startswith("xmatrix_"):
                    del sys.modules[m]
    return results


def _selftest():
    ok = True
    for row in _part_a() + _part_b():
        name, got, want = row[0], row[1], row[2]
        detail = row[3] if len(row) > 3 else ""
        good = got == want
        ok &= good
        print(f"  {'OK ' if good else 'XX '} {name:28} -> {got} (want {want}) {detail}")
    print(f"\narea-module loading-integrity guard: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())

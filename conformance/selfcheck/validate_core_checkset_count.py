#!/usr/bin/env python3
"""
validate_core_checkset_count.py — the CORE-checkset loading-integrity guard (P0-4).

P0-3 locked the AREA module count, but NOT the CORE checkset count. So emptying
`v2026_01_23.CHECKS` (12 core checks) or `v2026_04_08.CHECKS` (1) left the run's AREA
modules loading and passing — run_01_23 / run_04_08 stayed exit 0, the coverage-lock
stayed rc 0, and the matrix stayed rc 0 (its text-scan fallback still bucketed the
core ids as CHECK for a module that imports fine but exports no CHECKS via
`return (checks or None)`). A whole core checkset could silently vanish, its live
kill-tests stop running, and every gate stay GREEN.

This guard proves both sides now fail LOUD (golden_boot_guards pattern — every case
carries the mutant that would slip past a weaker guard):

  A. checkset_manifest.load_area_checks enforces the manifest's `core_checks`: the CORE
     checkset (core.CHECKS, passed as `core_checks`) must equal it or the load RAISES
     AreaManifestError — emptied / shrunk / grown all red. `expected_total` (core+areas)
     is enforced too. `expected_core=None` keeps the OLD signature inert (backward compat).
     The real run_01_23 / run_04_08 collect() still succeed on the real tree (no false red).
  B. matrix.py RECORDS a core module that imports fine but drifts from its committed
     `core_checks` (in particular EMPTIED) as a gate FAILURE — has_core_checkset_failures()
     non-empty -> matrix main reds — instead of silently text-scanning its ids into the
     matrix. Expected counts come from the committed manifests, never hardcoded.

Hermetic: temp modules on a temp path; no golden, no oracle, no network.
--selftest: exit 0 pass, 1 fail.
"""
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHK = HERE.parents[0] / "checks"
COV = HERE.parents[0] / "coverage"
sys.path.insert(0, str(CHK))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COV))

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
    """checkset_manifest core-count + total enforcement — every branch + its inverse."""
    results = []
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        sys.path.insert(0, str(d))
        try:
            _write(d, "ycore_area_1", "CHECKS = [1, 2]\n")        # one area, 2 checks
            manifest = {"ycore_area_1": 2}
            core2 = ["c0", "c1"]                                  # a 2-check core checkset

            # (0) correct core count + correct total -> no raise, combined = 2 core + 2 area
            try:
                got = load_area_checks(d, "ycore_area_*.py", manifest, core2,
                                       expected_core=2, expected_total=4)
                ok = (len(got) == 4)
            except AreaManifestError:
                ok = False
            results.append(("core-correct-count", ok, True))

            # (1) THE P0-4 HOLE: an EMPTIED core (0 checks) vs expected 2 -> raise
            results.append(("core-emptied",
                            _raises(lambda: load_area_checks(
                                d, "ycore_area_*.py", manifest, [],
                                expected_core=2, expected_total=2)), True))

            # (2) a SHRUNK core (1 vs 2) -> raise
            results.append(("core-shrunk",
                            _raises(lambda: load_area_checks(
                                d, "ycore_area_*.py", manifest, ["c0"],
                                expected_core=2, expected_total=3)), True))

            # (3) a GROWN core (3 vs 2) -> raise (a silent add without a manifest bump)
            results.append(("core-grown",
                            _raises(lambda: load_area_checks(
                                d, "ycore_area_*.py", manifest, ["c0", "c1", "c2"],
                                expected_core=2, expected_total=5)), True))

            # (4) expected_total arithmetic drift (core+areas ok individually) -> raise
            results.append(("total-drift",
                            _raises(lambda: load_area_checks(
                                d, "ycore_area_*.py", manifest, core2,
                                expected_core=2, expected_total=999)), True))

            # (5) BACKWARD-COMPAT: expected_core=None keeps the old signature INERT — a
            #     wrong-sized core does NOT raise when no core lock is requested. (This is
            #     the mutant a "always enforce" guard would miss: the lock must be opt-in
            #     so existing callers/tests are unaffected, yet the runners DO opt in.)
            try:
                got = load_area_checks(d, "ycore_area_*.py", manifest, [])   # no expected_core
                ok = (len(got) == 2)                                          # only areas
            except AreaManifestError:
                ok = False
            results.append(("core-lock-opt-in", ok, True))
        finally:
            sys.path.remove(str(d))
            for m in list(sys.modules):
                if m.startswith("ycore_"):
                    del sys.modules[m]

    # (6) INTEGRATION: the REAL runners lock their real core modules with no false red.
    import run_01_23, run_04_08
    for name, mod, want_core in (("run_01_23", run_01_23, 12), ("run_04_08", run_04_08, 1)):
        try:
            checks = mod.collect()
            ok = len(checks) >= want_core
            detail = f"real collect() -> {len(checks)} checks (core lock {want_core})"
        except AreaManifestError as e:
            ok, detail = False, f"real manifest FALSE-RED: {e}"
        results.append((f"real-core-lock-{name}", ok, True, detail))
    return results


def _part_b():
    """matrix.py treats an emptied/drifted core module as a gate FAILURE, not a text scan."""
    import matrix
    results = []
    with tempfile.TemporaryDirectory() as td:
        # matrix only import-attempts files whose parent dir is named "checks"
        chkdir = pathlib.Path(td) / "checks"
        chkdir.mkdir()
        sys.path.insert(0, str(chkdir))
        good = chkdir / "ymatrix_core_good.py"
        good.write_text("CHECKS = [1, 2]\n")        # matches its expected count of 2
        empty = chkdir / "ymatrix_core_empty.py"
        empty.write_text("CHECKS = []\n"             # imports FINE, exports ZERO -> the hole
                         "# Check(\"x\", [\"CHK-001\"]) citation still present for text scan\n")
        orig_check_files = matrix.check_files
        orig_core_expected = matrix._core_expected
        try:
            # inject the expected core counts (hermetic: no real manifest involved)
            matrix._core_expected = lambda: {"ymatrix_core_good": 2,
                                             "ymatrix_core_empty": 2}

            # clean inverse: a correctly-sized core module -> no core-checkset failure
            matrix.check_files = lambda: [str(good)]
            matrix.covered_ids_by_version()
            clean = (len(matrix.core_checkset_failures()) == 0)
            results.append(("clean-no-core-failure", clean, True))

            # the hole: an emptied core module -> recorded as a core-checkset failure
            matrix.check_files = lambda: [str(good), str(empty)]
            matrix.covered_ids_by_version()
            recorded = any(stem == "ymatrix_core_empty"
                           for stem, _, _ in matrix.core_checkset_failures())
            results.append(("emptied-core-recorded", recorded, True))

            # enforcement: a core-checkset failure present -> the gate must FAIL
            results.append(("enforcement-reds", matrix.has_core_checkset_failures(), True))
        finally:
            matrix.check_files = orig_check_files
            matrix._core_expected = orig_core_expected
            matrix._reset_import_failures()
            sys.path.remove(str(chkdir))
            for m in list(sys.modules):
                if m.startswith("ymatrix_"):
                    del sys.modules[m]

    # INTEGRATION: the real tree's core modules pass the matrix core-checkset check
    matrix.covered_ids_by_version()
    results.append(("real-matrix-no-core-failure",
                    not matrix.has_core_checkset_failures(), True,
                    f"real scan core failures={matrix.core_checkset_failures()}"))
    matrix._reset_import_failures()
    return results


def _selftest():
    ok = True
    for row in _part_a() + _part_b():
        name, got, want = row[0], row[1], row[2]
        detail = row[3] if len(row) > 3 else ""
        good = got == want
        ok &= good
        print(f"  {'OK ' if good else 'XX '} {name:28} -> {got} (want {want}) {detail}")
    print(f"\ncore-checkset loading-integrity guard: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())

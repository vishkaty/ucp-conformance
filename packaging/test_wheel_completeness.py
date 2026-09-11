#!/usr/bin/env python3
"""test_wheel_completeness.py — every module sync_bundle.sh puts in the bundle must
survive into the BUILT WHEEL.

validate_bundle.py proves the bundle DIRECTORY is complete; release_guards.sh proves the
wheel carries every spec version and enough check modules. Neither compares the two, so a
module copied into a bundle subdirectory that `[tool.setuptools.package-data]` has no glob
for is silently dropped at build time: the tree is green, the gate is green, and the
installed CLI dies on ImportError. That is how `conformance/ci/seq_invariants.py` (D1-16a,
copied by sync_bundle.sh line 26) reached CI — the Action's own-checkout job failed with
`ModuleNotFoundError: No module named 'seq_invariants'` while every local gate passed.

Contract: bundle *.py set == wheel *.py set, under _bundle/conformance/.
"""
import glob, importlib, pathlib, shutil, subprocess, sys, tempfile, unittest, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "packaging" / "spck_conformance" / "_bundle" / "conformance"


class WheelCompleteness(unittest.TestCase):
    def test_every_bundled_module_is_in_the_wheel(self):
        want = {p.relative_to(BUNDLE).as_posix()
                for p in BUNDLE.rglob("*.py") if "__pycache__" not in p.parts}
        self.assertTrue(want, "no modules found in the bundle — wrong path?")
        with tempfile.TemporaryDirectory() as dist:
            # Build from a PRISTINE copy: setuptools reuses spck_conformance.egg-info's
            # cached SOURCES.txt, so an in-place build keeps shipping files whose
            # package-data glob has already been deleted — the mutant would pass.
            src = pathlib.Path(dist) / "pkg"
            shutil.copytree(ROOT / "packaging", src,
                            ignore=shutil.ignore_patterns("*.egg-info", "build", "dist",
                                                          "__pycache__"))
            build = subprocess.run([sys.executable, "-m", "build", "--wheel",
                                    "--outdir", dist, str(src)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0,
                             f"wheel build failed:\n{build.stdout[-2000:]}{build.stderr[-2000:]}")
            whl = sorted(glob.glob(dist + "/*.whl"))[-1]
            names = zipfile.ZipFile(whl).namelist()
            marker = "_bundle/conformance/"
            got = {n.split(marker, 1)[1] for n in names
                   if marker in n and n.endswith(".py")}
        missing = sorted(want - got)
        self.assertFalse(
            missing,
            "modules in the bundle but NOT in the wheel (add a package-data glob in "
            f"packaging/pyproject.toml): {missing}")


def _have_build():
    """Probe `python -m build` in a SUBPROCESS. `import build` is a false positive here:
    packaging/ contains a `build/` directory, so with packaging/ on sys.path the import
    finds that directory as a namespace package and reports success on an interpreter
    that cannot build anything (observed: the skip path ran the test and died on
    `No module named build`)."""
    return subprocess.run([sys.executable, "-m", "build", "--version"],
                          capture_output=True).returncode == 0


def _ensure_build():
    """`python -m build` is not on a bare CI runner (release_guards.sh installs it the
    same way). If it cannot be made available, SKIP with rc 2 — run_suite's skip
    convention — rather than red on the runner's toolchain. Never a false green: the
    skip line names why, and CI installs `build` in its setup step."""
    if _have_build():
        return True
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "build"],
                   capture_output=True)
    return _have_build()


if __name__ == "__main__":
    if not _ensure_build():
        print("- SKIP: `build` unavailable on this interpreter — wheel completeness not checked")
        sys.exit(2)
    unittest.main(verbosity=2)

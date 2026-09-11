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
import glob, pathlib, shutil, subprocess, sys, tempfile, unittest, zipfile

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

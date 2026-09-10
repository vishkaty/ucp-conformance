#!/usr/bin/env python3
"""
pypi_job_mode.py — the MECHANICAL expiry of the Action's `source: pypi` report-only job
(D5-06, RV2 V2). The published PyPI package (0.3.1) predates the 2026-08-25 register, so
`source: pypi` runs are report-only ONLY while packaging/pyproject.toml's version is
below 0.4.0; from 0.4.0 on the job is counted like any other. No flag to forget.

  pypi_job_mode.py                    # reads packaging/pyproject.toml → prints report-only|counted
  pypi_job_mode.py --version 0.4.0rc1 # explicit version (tests)
  pypi_job_mode.py --github-output    # prints `mode=…` for $GITHUB_OUTPUT
Pre-releases order below their final (PEP 440): 0.4.0rc1 < 0.4.0 → report-only.
"""
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPIRY = (0, 4, 0)


def parse(v):
    """(release tuple, is_prerelease) for a PEP 440-ish version string."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)(.*)$", v)
    if not m:
        raise ValueError(f"unparsable version {v!r}")
    rel = tuple(int(x) for x in m.group(1).split("."))
    rel = rel + (0,) * (3 - len(rel))
    pre = bool(re.match(r"^(\.?(a|b|rc|dev)\d*)", m.group(2).strip()))
    return rel, pre


def mode(v):
    rel, pre = parse(v)
    if rel < EXPIRY or (rel == EXPIRY and pre):
        return "report-only"
    return "counted"


def main(argv):
    v = None
    if "--version" in argv:
        v = argv[argv.index("--version") + 1]
    else:
        txt = (ROOT / "packaging" / "pyproject.toml").read_text()
        v = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M).group(1)
    m = mode(v)
    print(f"mode={m}" if "--github-output" in argv else m)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

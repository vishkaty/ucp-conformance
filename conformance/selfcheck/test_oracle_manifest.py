#!/usr/bin/env python3
"""D4-04 (B6, decision 7b) — per-version schema oracle from MERGED SHAs.

Failing-first: schema_oracle has a single `BIN` and no `bin_for(version)`; there is no
per-version manifest (conformance/ci/oracle_manifest.json), no
validate_schema_oracle_manifest.py (--selftest / --boot-guard / --scan-selfroot) and no
oracle_verdict_diff.py. Target: the selftest's four cases pass — (a) bin_for("2026-08-25")
resolves to the b52518f5 build; (b) an entry not `merged: true` is rejected; (c)
scan_selfroot(08-25 base) returns the two self-root files; (d) a verdict-diff over a
3-item corpus reports `crash` for rc 134.

Run:  python3 -m pytest conformance/selfcheck/test_oracle_manifest.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import schema_oracle as so  # noqa: E402

VALIDATOR = HERE / "validate_schema_oracle_manifest.py"


def test_bin_for_0825_resolves_to_the_merged_main_build():
    b = so.bin_for("2026-08-25")
    assert b.name == "ucp-schema" and "ucp-schema-0825" in str(b), b
    assert so.manifest_entry("2026-08-25")["commit"].startswith("b52518f5")
    assert so.manifest_entry("2026-01-23")["commit"] == so.manifest_entry("2026-04-08")["commit"]


def test_selftest_four_cases_pass():
    r = subprocess.run([sys.executable, str(VALIDATOR), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for case in ("case a", "case b", "case c", "case d"):
        assert case in r.stdout, r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

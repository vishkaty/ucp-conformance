#!/usr/bin/env python3
"""
ops_tool_gate.py — run a tool that lives in the PRIVATE ops/ repo as a run_suite gate.

    python3 conformance/ci/ops_tool_gate.py tools/filing_lint.py --selftest

ops/ is a separate private git repo mounted at <root>/ops on the owner's machines and absent
in CI clones. When the named tool is not there this prints a SKIP line and exits 2 (the
run_suite skip convention) — never a false green, never a false red. Otherwise it execs the
tool with the same interpreter and propagates its exit code and output unchanged.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def main(argv):
    if not argv:
        print("usage: ops_tool_gate.py <path-under-ops> [args...]")
        return 2
    tool = ROOT / "ops" / argv[0]
    if not tool.is_file():
        print(f"- SKIP: ops/ not mounted (no {tool.relative_to(ROOT)}) — private ops tool gate not run")
        return 2
    return subprocess.call([sys.executable, str(tool), *argv[1:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

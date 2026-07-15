#!/usr/bin/env python3
"""
speclint.py — offline, read-only linter for the SHA-pinned vendored UCP spec.

speclint grades the spec against itself: where two machine artifacts that must
agree (a schema and an example, two transports' contracts) mechanically disagree,
it emits a Finding with both sides cited to file:line. It runs entirely against
conformance/.vendor (never the network, never main) and NEVER files anything —
findings feed the manual five-gate upstream-filing protocol.

Modes:
  list              list the registered rules.
  report            run every rule against the vendored spec and print findings.

Run:  python3 conformance/speclint/speclint.py report
Exit 0 always for `list`; `report` exits 0 (clean) or 1 (findings present).
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from parsers.openapi import required_headers_by_operation      # noqa: E402
from parsers.openrpc import required_meta_by_method            # noqa: E402
from predicates import transport_header_parity                 # noqa: E402
import rules                                                   # noqa: E402

ROOT = HERE.parents[1]
VERSION_TREE = {"2026-04-08": "ucp", "2026-01-23": "ucp-2026-01-23",
                "2026-01-11": "ucp-2026-01-11"}


def _vendor_dir(version):
    return ROOT / "conformance" / ".vendor" / VERSION_TREE[version]


def _first_line_matching(path, needle, near_operation=None):
    """Best-effort 1-based line number of `needle` in a file (for a citation)."""
    try:
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if needle in line:
                return i
    except OSError:
        pass
    return None


def _run_transport_parity(rule):
    base = _vendor_dir(rule.version) / "source"
    rest_path = base / "services" / "shopping" / "rest.openapi.json"
    mcp_path = base / "services" / "shopping" / "mcp.openrpc.json"
    rest = required_headers_by_operation(json.loads(rest_path.read_text()))
    mcp = required_meta_by_method(json.loads(mcp_path.read_text()))
    findings = transport_header_parity(rest, mcp)
    rel_rest = rest_path.relative_to(ROOT)
    rel_mcp = mcp_path.relative_to(ROOT)
    out = []
    for f in findings:
        a_line = _first_line_matching(rest_path, f'"{f.header}"')
        out.append({
            "rule": rule.id,
            "operation": f.operation,
            "summary": (f'{f.header} is required in {f.required_in.upper()} but '
                        f'optional in {f.optional_in.upper()} for "{f.operation}"'),
            "side_a": f"{rel_rest}" + (f"#L{a_line}" if a_line else ""),
            "side_b": f"{rel_mcp} (methods[name={f.operation}].meta.required)",
        })
    return out


_RUNNERS = {"transport_header_parity": _run_transport_parity}


def cmd_list():
    for r in rules.RULES:
        print(f"{r.id}  [{r.disposition}]  v{r.version}  ({r.predicate_class})")
        print(f"    A: {r.side_a}")
        print(f"    B: {r.side_b}")
    return 0


def cmd_report():
    all_findings = []
    for r in rules.RULES:
        runner = _RUNNERS.get(r.predicate_class)
        if runner is None:
            print(f"  (no runner for {r.id}; skipped)", file=sys.stderr)
            continue
        all_findings.extend(runner(r))
    if not all_findings:
        print("speclint: no findings.")
        return 0
    for f in all_findings:
        print(f"[{f['rule']}] {f['summary']}")
        print(f"    A  {f['side_a']}")
        print(f"    B  {f['side_b']}")
    print(f"\nspeclint: {len(all_findings)} finding(s).")
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="speclint")
    ap.add_argument("mode", choices=["list", "report"])
    args = ap.parse_args(argv)
    return {"list": cmd_list, "report": cmd_report}[args.mode]()


if __name__ == "__main__":
    sys.exit(main())

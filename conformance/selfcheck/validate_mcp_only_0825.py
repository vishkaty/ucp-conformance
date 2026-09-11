#!/usr/bin/env python3
"""
validate_mcp_only_0825.py — gate `merchant-mcp-only-0825` (D1-11, C2b). HERMETIC: boots
conformance/fixtures/profiles/mcp_only_0825.py on an ephemeral loopback port and proves
two things about the Shopify-shaped MCP-only 2026-08-25 profile:

  1. the merchant gate grades it against its PINNED near-empty population
     (checks/expected_skips_mcp_only_0825.json: 2 run / 226 pinned, every in-scope REST
     check `transport-not-declared`) — `validate_merchant_checks.py --golden mcp-only-0825
     --expected-skips FILE` exits 0 with `0 unexplained`;
  2. the CLI prints NO coverage fraction for it: `verdict.coverage == null`,
     `support == "rest-not-declared"`, transport banner present (PLAN-v3 §2.3).

Kill directions (recorded in the D1-11 landing): delete a pinned id -> (1) red naming it;
force `MerchantCtx.has_rest = True` -> REST probes hit the fixture's 404s -> deviations ->
(1) red and (2) prints a number.

Exit 0 = both hold; 1 = a finding (named).
"""
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURE_DIR = ROOT / "conformance" / "fixtures" / "profiles"
sys.path.insert(0, str(FIXTURE_DIR))
import mcp_only_0825  # noqa: E402

VMC = HERE / "validate_merchant_checks.py"
CLI = ROOT / "conformance" / "checks" / "merchant.py"
SKIPS = ROOT / "conformance" / "checks" / "expected_skips_mcp_only_0825.json"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    record = None
    if "--record" in argv:
        record = argv[argv.index("--record") + 1]
    fails, lines = [], []
    with mcp_only_0825.served() as base:
        cmd = [sys.executable, str(VMC), "--server", base, "--golden", "mcp-only-0825",
               "--expected-skips", str(SKIPS)] + (["--record", record] if record else [])
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        tail = [l for l in r.stdout.strip().splitlines() if l.strip()]
        gate_line = tail[-1].strip() if tail else ""
        for l in r.stdout.splitlines():
            if l.strip().startswith("✗"):
                fails.append(f"merchant gate: {l.strip()}")
        if r.returncode != 0:
            fails.append(f"merchant gate rc {r.returncode}: {gate_line or r.stderr.strip()[-300:]}")
        lines.append(gate_line)
        c = subprocess.run([sys.executable, str(CLI), "--server", base, "--json"],
                           capture_output=True, text=True, cwd=str(ROOT))
        text = c.stdout
        try:
            doc = json.loads(text[text.index("{"):])
        except ValueError:
            doc, fails = None, fails + [f"CLI --json produced no JSON (rc {c.returncode}): {text[-200:]}"]
        v = (doc or {}).get("verdict") or {}
        if doc is not None:
            if "coverage" not in v or v["coverage"] is not None:
                fails.append(f"CLI verdict.coverage must be null for an MCP-only server, got {v.get('coverage')!r}")
            if v.get("support") != "rest-not-declared":
                fails.append(f"CLI verdict.support must be 'rest-not-declared', got {v.get('support')!r}")
            if "REST transport not declared" not in ((doc.get("banner") or "")):
                fails.append("CLI banner missing (REST transport not declared)")
    verdict = "FAIL" if fails else "PASS"
    for f in fails:
        print(f"  ✗ {f}")
    print(f"merchant-mcp-only-0825: {verdict} — {lines[0] if lines else ''} · "
          f"coverage {json.dumps(v.get('coverage')) if v else '?'} · support {v.get('support') if v else '?'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

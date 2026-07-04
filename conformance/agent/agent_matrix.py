#!/usr/bin/env python3
"""
agent_matrix.py — the AGENT coverage axis (separate from the merchant matrix).

The merchant matrix accounts business/server obligations. This one accounts the
platform/agent obligations — a distinct denominator, a distinct coverage %, its own
ratchet/lock (added as checks arrive). It lives in conformance/agent/ so it is invisible
to the merchant coverage_map (which globs conformance/checks/*.py, non-recursive) — the
merchant 87/87/87 cannot move because of anything here.

Agent-subject denominator = register rows whose obligation binds the platform/agent
(subject heuristic) OR that the merchant side already classed `client-bound`. Each such
row is accounted as an agent CHECK (covered by an agent check), an agent EXEMPT
(irreducibly manual — the agent's private reasoning / pure UI), or a GAP.

  agent_matrix.py                 # report agent coverage
  agent_matrix.py --require all   # gate: fail on any agent GAP (used once we reach 100%)
"""
import argparse, glob, importlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
REQ = os.path.join(ROOT, "conformance", "requirements")
EXEMPT = os.path.join(ROOT, "conformance", "coverage", "exemptions.json")
AGENT_EXEMPT = os.path.join(HERE, "agent_exemptions.json")
VERSIONS = ["2026-01-11", "2026-01-23", "2026-04-08"]

AGENT_WORDS = ("platform must", "platforms must", "the platform", "agent must",
               "agents must", "mcp client", "client must", "consumer")

# Obligations that bind the platform/agent AS A VERIFIER of business RESPONSES or AS THE
# SIGNER of its own REQUESTS. The subject heuristic misses these (they read "Verification
# must…" / "REST request signed components MUST include…" with no platform keyword), but a
# UCP client that receives signed responses IS the verifier and that signs its API requests
# IS the request-signer — so they belong on the agent axis too. (They are ALSO merchant
# obligations — the business verifies inbound request signatures and signs its webhook
# requests — so these ids are shared across the two axes, graded independently on each.)
AGENT_EXTRA = {"SIG-001", "SIG-002", "SIG-036",        # response verification (agent verifies)
               "SIG-014", "SIG-015", "SIG-016", "SIG-018"}  # request signing (agent signs)

# Denominator-accuracy exclusions: rows the heuristic/client-bound class wrongly pulled in
# whose MUST binds ONLY the business/server (a business error response, a business-authored
# order-schema shape, an MCP endpoint the server exposes, an A2A response the business agent
# returns). They are NOT achievable on the agent axis and must not inflate the denominator.
# Every id is spec-cited in agent_denominator_audit.json (independent adjudication + verify).
NOT_AGENT_BOUND = {"A2A-001", "MCP-001", "NEG-001", "NEG-002", "NEG-003", "NEG-004",
                   "OVR-011", "OVR-012", "PAY-038", "ORD-018", "ORD-019"}


def _client_bound_ids():
    if not os.path.exists(EXEMPT):
        return set()
    d = json.load(open(EXEMPT))
    out = set()
    for k, v in d.items():
        for e in (v if isinstance(v, list) else [v]):
            if isinstance(e, dict) and e.get("class") == "client-bound":
                out.add(k)
    return out


def agent_rows(ver):
    """The agent-subject MUST ids at `ver`."""
    cb = _client_bound_ids()
    ids = set()
    for f in glob.glob(os.path.join(REQ, ver, "*.json")):
        for r in json.load(open(f)).get("rows", []):
            if ver not in (r.get("versions") or [ver]):
                continue
            if r.get("keyword") not in ("MUST", "MUST NOT"):
                continue
            if r["id"] in NOT_AGENT_BOUND:         # business-only (denominator-accuracy audit)
                continue
            text = (r.get("requirement", "") + " " + r.get("quote", "")).lower()
            if any(w in text for w in AGENT_WORDS) or r["id"] in cb or r["id"] in AGENT_EXTRA:
                ids.add(r["id"])
    return ids


def agent_check_ids(ver):
    """req_ids covered by agent checks at `ver`."""
    sys.path.insert(0, HERE)
    mod = importlib.import_module("agent_checks")
    out = set()
    for chk in getattr(mod, "CHECKS", []):
        if chk.versions and ver not in chk.versions:
            continue
        out.update(chk.req_ids)
    return out


def agent_exempt_ids():
    if not os.path.exists(AGENT_EXEMPT):
        return {}
    return json.load(open(AGENT_EXEMPT))


def account(ver):
    rows = agent_rows(ver)
    checks = agent_check_ids(ver)
    ex = agent_exempt_ids()
    check = sorted(r for r in rows if r in checks)
    exempt = sorted(r for r in rows if r not in checks and r in ex)
    gap = sorted(r for r in rows if r not in checks and r not in ex)
    return rows, check, exempt, gap


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--require", choices=["all"])
    ap.add_argument("--json")
    args = ap.parse_args()
    failed = False
    summary = {}
    print("AGENT coverage axis (platform/agent obligations) — separate from merchant\n")
    for ver in VERSIONS:
        rows, check, exempt, gap = account(ver)
        n = len(rows)
        pct = round(100 * (len(check) + len(exempt)) / n) if n else 0
        summary[ver] = {"agent_musts": n, "check": len(check), "exempt": len(exempt),
                        "gap": len(gap), "accounted_pct": pct}
        print(f"  {ver}: {n:3} agent MUSTs | CHECK {len(check):3} | EXEMPT {len(exempt):3} "
              f"| GAP {len(gap):3}  -> accounted {pct}%")
        if args.require == "all" and gap:
            print(f"    x {ver}: {len(gap)} agent GAP(s) remain"); failed = True
    if args.json:
        open(args.json, "w").write(json.dumps(summary, indent=1) + "\n")
        print(f"\nagent coverage written -> {args.json}")
    if failed:
        print("\nAGENT MATRIX GATE: FAIL"); return 1
    print("\n(Phase A: agent denominator established; coverage grows as Phase B checks land.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

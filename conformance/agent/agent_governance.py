#!/usr/bin/env python3
"""
agent_governance.py — the agent side's requirement-tracking governance, mirroring the
merchant coverage/lock/review machinery so BOTH lanes are held to the same rigor.

Four checks (all pass trivially at zero agent checks; they bite as coverage grows):
  1. FRESHNESS  — committed agent_coverage.json matches what agent_matrix regenerates.
  2. RATCHET    — agent accounted (check+exempt) per version never drops below the floors
                  in agent_ratchet.json (floors only ever raised deliberately).
  3. LOCK       — every id in agent_coverage_lock.json (add-only) is still accounted; an
                  agent check/exemption can't silently vanish (agent tests are permanent).
  4. SIGN-OFF   — every agent CHECK id carries an adversarial-review sign-off in
                  agent_review_signoffs.json (coverage can't grow without review).

Run standalone or via run_agent (the agent lane calls it), so the single agent lane
enforces the whole agent governance loop.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_matrix   # noqa: E402
import agent_checks   # noqa: E402

COV = os.path.join(HERE, "agent_coverage.json")
RATCHET = os.path.join(HERE, "agent_ratchet.json")
LOCK = os.path.join(HERE, "agent_coverage_lock.json")
SIGN = os.path.join(HERE, "agent_review_signoffs.json")


def _live_coverage():
    out = {}
    for ver in agent_matrix.VERSIONS:
        rows, check, exempt, gap = agent_matrix.account(ver)
        out[ver] = {"agent_musts": len(rows), "check": len(check),
                    "exempt": len(exempt), "gap": len(gap),
                    "accounted_pct": round(100 * (len(check) + len(exempt)) / len(rows)) if rows else 0}
    return out


def run():
    fails = []
    live = _live_coverage()

    # 1. freshness
    if os.path.exists(COV):
        committed = json.load(open(COV))
        if committed != live:
            fails.append("agent_coverage.json is STALE — regenerate: "
                         "python3 conformance/agent/agent_matrix.py --json "
                         "conformance/agent/agent_coverage.json")
    else:
        fails.append("agent_coverage.json missing — generate it")

    # 2. ratchet
    if os.path.exists(RATCHET):
        floors = json.load(open(RATCHET))
        for ver, f in floors.items():
            acc = live.get(ver, {}).get("check", 0) + live.get(ver, {}).get("exempt", 0)
            if acc < f.get("accounted", 0):
                fails.append(f"agent RATCHET {ver}: accounted {acc} < floor {f['accounted']} "
                             f"— coverage regressed")

    # 3. lock (add-only): locked ids must still be accounted
    if os.path.exists(LOCK):
        lock = json.load(open(LOCK)).get("versions", {})
        for ver, locked in lock.items():
            _, check, exempt, _ = agent_matrix.account(ver)
            accounted = set(check) | set(exempt)
            for i in locked.get("check", []) + locked.get("exempt", []):
                if i not in accounted:
                    fails.append(f"agent LOCK {ver} {i}: was locked (check/exempt) but is now "
                                 f"unaccounted — an agent test vanished")

    # 4. review sign-off: every agent CHECK id must be signed
    signed = set()
    if os.path.exists(SIGN):
        for s in json.load(open(SIGN)).get("signoffs", []):
            for ids in (s.get("ids") or {}).values():
                signed.update(ids)
    for chk in agent_checks.CHECKS:
        for rid in chk.req_ids:
            if rid not in signed:
                fails.append(f"agent SIGN-OFF: check {chk.id} covers {rid} with no recorded "
                             f"adversarial review — add it to agent_review_signoffs.json")
    return fails, live


def main():
    fails, live = run()
    if fails:
        print("agent-governance: FAIL")
        for f in fails:
            print(f"  x {f}")
        return 1
    tot = sum(d["check"] + d["exempt"] for d in live.values())
    print(f"agent-governance: PASS — coverage fresh + ratchet held + lock intact + all agent "
          f"checks reviewed ({tot} accounted across {len(live)} versions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
agent_governance.py — the agent side's requirement-tracking governance, mirroring the
merchant coverage/lock/review machinery so BOTH lanes are held to the same rigor.

Five checks (all pass trivially at zero agent checks; they bite as coverage grows):
  1. FRESHNESS  — committed agent_coverage.json matches what agent_matrix regenerates.
  2. RATCHET    — agent accounted (check+exempt) per version never drops below the floors
                  in agent_ratchet.json (floors only ever raised deliberately).
  3. LOCK       — every id in agent_coverage_lock.json (add-only) is still accounted; an
                  agent check/exemption can't silently vanish (agent tests are permanent).
  4. SIGN-OFF   — every agent CHECK id carries an adversarial-review sign-off in
                  agent_review_signoffs.json (coverage can't grow without review).
  7. EVIDENCE   — every (check, version) attribution is backed by fresh run evidence at the
                  current spec pin — THIS run's evidence when run_suite hands it over
                  (`--in-run FILE`, written by the agent-lane's `run_agent.py --evidence-out`;
                  CI-1 / decision 24), else the tracked agent_run_evidence.json (rewritten
                  only by an explicit `run_agent.py --record` at release time) — or the
                  version is a DECLARED unrun version with a review_by (D5-04 / decision 10:
                  08-25 is unrun until D3-11's sandbox exists). Kill-tested in
                  test_attribution_guard.py. The ONLY governance check for attribution
                  evidence — D3-11 writes evidence, adds no check.
  5. DENOMINATOR-DRIFT — the live agent-denominator MEMBERSHIP matches the reviewed snapshot
                  in agent_denominator_lock.json. Fails if any row silently enters/leaves the
                  denominator (a new spec row, a heuristic change, a mis-classified business
                  row) — the accuracy audit can't erode as coverage scales. Normal coverage
                  growth never trips it (covering a gap doesn't change membership); moving the
                  denominator requires a deliberate re-review + snapshot update.

Run standalone or via run_agent (the agent lane calls it), so the single agent lane
enforces the whole agent governance loop.
"""
import datetime, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import agent_matrix   # noqa: E402
import agent_checks   # noqa: E402
from reference_agent import DEFECTS   # noqa: E402

COV = os.path.join(HERE, "agent_coverage.json")
PUBLIC_COV = os.path.join(ROOT, "public", "agent-coverage.json")
RATCHET = os.path.join(HERE, "agent_ratchet.json")
LOCK = os.path.join(HERE, "agent_coverage_lock.json")
SIGN = os.path.join(HERE, "agent_review_signoffs.json")
DENOM = os.path.join(HERE, "agent_denominator_lock.json")


def evidence_failures(checks, evidence, today, pins, unrun):
    """Check 7 (EVIDENCE), pure: failure strings for every attribution without fresh
    evidence at the current pin, unless its version is a declared, unexpired unrun
    version. Also reds unknown check ids in the evidence file and expired declarations."""
    fails = []
    known = {c.id for c in checks}
    for ver, d in sorted((unrun or {}).items()):
        if (d or {}).get("review_by", "") < today.isoformat():
            fails.append(f"agent EVIDENCE: unrun declaration for {ver} expired {d.get('review_by')} — "
                         f"re-review (has a sandbox for {ver} landed?)")
    for cid in sorted(evidence or {}):
        if cid not in known:
            fails.append(f"agent EVIDENCE: evidence for unknown check {cid} — stale entry")
    for chk in checks:
        for ver in (chk.versions or sorted(pins)):
            entry = (evidence or {}).get(chk.id, {}).get(ver)
            declared = ver in (unrun or {}) and (unrun[ver] or {}).get("review_by", "") >= today.isoformat()
            problem = agent_matrix.evidence_problem(entry, today, pins.get(ver))
            if problem is None:
                continue
            if entry is None and declared:
                continue                          # suppressed by default; honest GAP
            fails.append(f"agent EVIDENCE {ver} {chk.id}: {problem} — an attribution at {ver} "
                         f"needs a green run_agent.py run against a {ver} sandbox (or declare "
                         f"{ver} unrun in agent_run_evidence.json)")
    return fails


def _live_coverage():
    out = {}
    for ver in agent_matrix.VERSIONS:
        rows, check, exempt, gap = agent_matrix.account(ver)
        out[ver] = {"agent_musts": len(rows), "check": len(check),
                    "exempt": len(exempt), "gap": len(gap),
                    "accounted_pct": round(100 * (len(check) + len(exempt)) / len(rows)) if rows else 0}
    return out


def run(in_run=None):
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

    # 1b. the PUBLIC agent coverage (rendered by /coverage) must match the live matrix too —
    #     the two-sided coverage page can't drift from reality.
    if os.path.exists(PUBLIC_COV):
        if json.load(open(PUBLIC_COV)) != live:
            fails.append("public/agent-coverage.json is STALE — regenerate: "
                         "python3 conformance/agent/agent_matrix.py --json public/agent-coverage.json")
    else:
        fails.append("public/agent-coverage.json missing — the /coverage agent lane needs it: "
                     "python3 conformance/agent/agent_matrix.py --json public/agent-coverage.json")

    # 2. ratchet
    if os.path.exists(RATCHET):
        floors = json.load(open(RATCHET))
        for ver, f in floors.items():
            acc = live.get(ver, {}).get("check", 0) + live.get(ver, {}).get("exempt", 0)
            if acc < f.get("accounted", 0):
                fails.append(f"agent RATCHET {ver}: accounted {acc} < floor {f['accounted']} "
                             f"— coverage regressed")

    # 3. lock (add-only): locked ids must still be accounted — except a version whose
    #    attribution is SUSPENDED with a reason + review_by (D5-04: the 08-25 ids were locked
    #    by the versions-list extension, never by a run; they re-lock when evidence exists)
    if os.path.exists(LOCK):
        lock_doc = json.load(open(LOCK))
        lock = lock_doc.get("versions", {})
        suspended = lock_doc.get("suspensions", {})
        for ver, s in sorted(suspended.items()):
            if s.get("review_by", "") < datetime.date.today().isoformat():
                fails.append(f"agent LOCK {ver}: suspension expired {s.get('review_by')} — re-review")
        for ver, locked in lock.items():
            if ver in suspended and suspended[ver].get("review_by", "") >= datetime.date.today().isoformat():
                continue
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

    # 7. evidence: every attribution is backed by a fresh run at the current pin — the
    #    in-run handoff when it exists (never rescued by the tracked file, exactly as
    #    validate_battery_freshness treats --in-run), else the tracked file.
    evd = agent_matrix.load_evidence(in_run if (in_run and os.path.exists(in_run)) else None)
    fails += evidence_failures(agent_checks.CHECKS, evd["evidence"], datetime.date.today(),
                               agent_matrix.spec_pins(), evd["unrun_versions"])

    # 5. denominator-drift: live membership must equal the reviewed snapshot
    if os.path.exists(DENOM):
        snap = json.load(open(DENOM)).get("versions", {})
        for ver in agent_matrix.VERSIONS:
            live_ids = set(agent_matrix.agent_rows(ver))
            locked_ids = set(snap.get(ver, []))
            added = sorted(live_ids - locked_ids)
            removed = sorted(locked_ids - live_ids)
            if added or removed:
                fails.append(f"agent DENOMINATOR-DRIFT {ver}: membership changed vs the reviewed "
                             f"snapshot (added {added}, removed {removed}). Re-adjudicate the "
                             f"subject of each changed row (agent_denominator_audit.json), then "
                             f"regenerate agent_denominator_lock.json deliberately.")
    else:
        fails.append("agent_denominator_lock.json missing — snapshot the reviewed denominator")

    # 6. copy freshness: advertised AGENT numbers on the public site / docs must equal reality.
    #    Agent CHECK count = len(CHECKS); agent DEFECT (failure-mode) count = non-None DEFECTS.
    #    Mirrors the merchant coverage-gate copy-freshness, kept on the agent side for isolation.
    fails += _agent_copy_freshness()
    return fails, live


def _agent_copy_freshness():
    n_checks = len(agent_checks.CHECKS)
    n_defects = len([k for k in DEFECTS if k])
    # (regex, expected, label) — each captured integer MUST equal `expected`
    check_res = [
        (re.compile(r'stat-num">(\d+)\+?</div><div class="stat-label">Agent-side checks'), "Agent-side checks stat"),
        (re.compile(r"ran \d+ ops[^;<]*;\s*(\d+) checks?"), "agent-lane terminal line"),
        (re.compile(r"(\d+)\+? agent[- ]side checks?"), "'N agent-side checks' prose"),
        (re.compile(r"(\d+)\+? agent checks?\b"), "'N agent checks' prose"),
        # D5-01: the README's "N checks (M defects modeled)" pair escaped every regex above
        (re.compile(r"(\d+)\+? checks? \(\d+ defects modeled\)"), "'N checks (M defects modeled)' prose"),
    ]
    defect_res = [
        (re.compile(r'stat-num">(\d+)\+?</div><div class="stat-label">Failure modes'), "Failure-modes stat"),
        (re.compile(r"(\d+)\+? (?:client )?defects? modeled"), "'N defects modeled' prose"),
        (re.compile(r"(\d+)\+? failure modes"), "'N failure modes' prose"),
        (re.compile(r"\d+\+? checks? \((\d+) defects modeled\)"), "'N checks (M defects modeled)' prose"),
    ]
    files = glob.glob(os.path.join(ROOT, "public", "*.html")) + [
        os.path.join(ROOT, "README.md"), os.path.join(ROOT, "docs", "ROADMAP.md"),
        os.path.join(ROOT, "docs", "TWO-LANE.md")]
    out = []
    for page in files:
        if not os.path.exists(page):
            continue
        txt = open(page).read()
        rel = os.path.relpath(page, ROOT)
        for cre, what in check_res:
            for m in cre.finditer(txt):
                if int(m.group(1)) != n_checks:
                    out.append(f"{rel}: {what} claims {m.group(1)} but there are {n_checks} agent "
                               f"checks — update the copy (or the count drifted)")
        for cre, what in defect_res:
            for m in cre.finditer(txt):
                if int(m.group(1)) != n_defects:
                    out.append(f"{rel}: {what} claims {m.group(1)} but {n_defects} defects are "
                               f"modeled — update the copy")
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="agent-axis governance gate")
    ap.add_argument("--in-run", metavar="FILE",
                    help="this run's evidence (run_suite agent-lane --evidence-out); wins when it "
                         "exists, else the tracked agent_run_evidence.json is used")
    args = ap.parse_args(argv)
    in_run = args.in_run if (args.in_run and os.path.exists(args.in_run)) else None
    source = f"in-run ({os.path.basename(args.in_run)})" if in_run else "tracked (agent_run_evidence.json)"
    fails, live = run(in_run=in_run)
    if fails:
        print(f"agent-governance: FAIL — evidence {source}")
        for f in fails:
            print(f"  x {f}")
        return 1
    tot = sum(d["check"] + d["exempt"] for d in live.values())
    print(f"agent-governance: PASS — coverage fresh + ratchet held + lock intact + all agent "
          f"checks reviewed ({tot} accounted across {len(live)} versions) · evidence {source}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
run_agent.py — the agent-conformance lane (reference-gate + kill-rate).

Mirrors the merchant reference gate: before any agent check can grade a real agent, it
must clean-pass on the known-good ReferenceAgent AND be kill-safe (fail on its targeted
defect). This is the TDD loop for the agent side.

  run_agent.py --server URL          # run the lane against a merchant sandbox
  run_agent.py --server URL --json

Phase A: with zero checks the lane trivially passes, proving the plumbing (reference
agent drives a flow; the harness can run/kill-test checks) end-to-end. The `--agent`
CLI mode and the hosted spck.dev/agent sandbox build on this.
"""
import argparse, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from reference_agent import ReferenceAgent, DEFECTS   # noqa: E402
import agent_checks   # noqa: E402
import sandbox   # noqa: E402


def reference_gate():
    """Every ACheck must PASS on the clean reference agent and FAIL on its kill_mutation.
    Checks are grouped by their `scenario`; one sandbox is booted per scenario (conformant,
    bad_signature, ...) and the reference agent shops against it."""
    from collections import defaultdict
    by_scenario = defaultdict(list)
    for chk in agent_checks.CHECKS:
        by_scenario[chk.scenario].append(chk)
    results, unsound, ref_ops = [], [], 0
    agent_jwks = [ReferenceAgent.signing_jwk()]
    for scenario in sorted(by_scenario):
        with sandbox.serve(scenario=scenario, agent_jwks=agent_jwks) as (base, _srv):
            clean_log = ReferenceAgent(base).run_flow()
            if scenario == "conformant":
                ref_ops = len(clean_log)
            for chk in by_scenario[scenario]:
                on_clean = chk.predicate(clean_log)
                mut_log = ReferenceAgent(base, defect=chk.kill_mutation).run_flow()
                on_mut = chk.predicate(mut_log)
                sound = (on_clean == agent_checks.CLEAN) and (on_mut == agent_checks.DEVIATION)
                results.append({"id": chk.id, "req_ids": chk.req_ids, "scenario": scenario,
                                "clean": on_clean, "mutant": on_mut,
                                "kill_mutation": chk.kill_mutation, "sound": sound})
                if not sound:
                    unsound.append(chk.id)
    return results, unsound, ref_ops


def write_run_evidence(results, sandbox_version, path=None):
    """Record this green run as attribution evidence (D5-04): for every SOUND check that
    declares `sandbox_version`, {date, spec_pin} at that version. Preserves the file's
    _about/unrun_versions; never writes from the pip bundle (site-packages is not a repo)."""
    import agent_matrix
    path = path or agent_matrix.EVIDENCE
    if "_bundle" in HERE.replace("\\", "/"):
        return None
    pin = agent_matrix.spec_pins().get(sandbox_version)
    if not pin:
        return None
    doc = json.load(open(path)) if os.path.exists(path) else {}
    doc.setdefault("_about", "Agent-lane RUN EVIDENCE (D5-04 / decision 10): {check_id: {sandbox_version: {date, spec_pin}}} "
                   "written by run_agent.py on every green run; agent_matrix counts a check at a version only with an "
                   "entry here no older than 30 days at the current spec pin. unrun_versions declares versions with no "
                   "sandbox yet (agent_governance reds an undeclared unrun attribution).")
    doc.setdefault("unrun_versions", {})
    ev = doc.setdefault("evidence", {})
    by_id = {c.id: c for c in agent_checks.CHECKS}
    today = datetime.date.today().isoformat()
    for r in results:
        chk = by_id.get(r["id"])
        if not r.get("sound") or chk is None:
            continue
        if chk.versions and sandbox_version not in chk.versions:
            continue
        ev.setdefault(chk.id, {})[sandbox_version] = {"date": today, "spec_pin": pin}
    for cid in list(ev):
        if cid not in by_id:
            del ev[cid]
    open(path, "w").write(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    return path


def sandbox_version_of(log):
    for e in log:
        if e.get("op") == "discover":
            body = (e.get("response") or {}).get("body") or {}
            v = (body.get("ucp") or {}).get("version") or body.get("version")
            if v:
                return v
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-evidence", action="store_true",
                    help="do not record this run in agent_run_evidence.json")
    args = ap.parse_args()

    # The agent lane is self-contained: it boots its own adversarial sandbox(es) in-process,
    # so it's hermetic and never depends on the merchant fixture.
    with sandbox.serve(agent_jwks=[ReferenceAgent.signing_jwk()]) as (base, _srv):
        log = ReferenceAgent(base).run_flow()      # conformant sanity flow
        # the order-cancel probe only exists under incremental_scope; it legitimately 404s on
        # the conformant surface, so don't count that expected 404 against the boot sanity.
        # prm_discovery (08-25 identity-linking Step 1, RFC 9728) also legitimately 404s on
        # every scenario here -- the sandbox never serves protected-resource metadata, which
        # IS the documented default case (discover_protected_resource_metadata falls back to
        # the business domain) -- so its 404 is expected, not a boot failure either.
        booted = all(e["response"]["status"] in (200, 201)
                     or e["request"]["path"].endswith("/cancel")
                     or e["op"] == "prm_discovery" for e in log)
    results, unsound, ref_ops = reference_gate()
    ref_ops = ref_ops or len(log)
    evidence_path = None
    if booted and not unsound and not args.no_evidence:
        sv = sandbox_version_of(log)
        if sv:
            evidence_path = write_run_evidence(results, sv)

    if args.json:
        print(json.dumps({"agent_checks": len(agent_checks.CHECKS), "reference_flow_ops": len(log),
                          "reference_flow_ok": booted, "unsound": unsound,
                          "results": results}, indent=1))
        return 1 if unsound else 0

    print(f"agent lane: reference agent ran {len(log)} ops "
          f"({'ok' if booted else 'FLOW FAILED — is the sandbox up?'}); "
          f"{len(agent_checks.CHECKS)} agent check(s); defects available: "
          f"{sorted(k for k in DEFECTS if k)}")
    if unsound:
        print(f"agent lane: FAIL — {len(unsound)} check(s) not kill-safe: {unsound}")
        return 1
    if agent_checks.CHECKS:
        print("agent lane: PASS — every agent check clean-passes the reference agent and "
              "kills its targeted defect."
              + (f" Run evidence recorded -> {os.path.relpath(evidence_path, ROOT_DIR)}"
                 if evidence_path else ""))
    else:
        print("agent lane: PASS — foundation green (zero checks; Phase A). Kill-rate loop "
              "ready for Phase B checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

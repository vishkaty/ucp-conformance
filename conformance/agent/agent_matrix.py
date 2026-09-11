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
import argparse, datetime, glob, importlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONF = os.path.dirname(HERE)
sys.path.insert(0, CONF)
# VERSIONS used to be a private copy here — one of five independent lists across the
# suite (PLAN-0825 G0-b / A.4) — and had silently drifted: it never gained 2026-08-25,
# so the agent axis had no visibility into the newest spec at all. VERSIONS now comes
# from the single shared source; see conformance/common/spec_versions.py.
#
# AGENT_REGISTER_ONLY_VERSIONS (NOT the merchant matrix.py's REGISTER_ONLY_VERSIONS —
# a SEPARATE set, on purpose, since 2026-08-31): this axis used to short-circuit on
# the SAME shared REGISTER_ONLY_VERSIONS the merchant matrix uses, which meant the
# merchant lane's 2026-08-25 Check-conversion-phase graduation silently ALSO
# un-short-circuited the agent axis — agent_rows() started returning the full,
# never-reviewed 2026-08-25 denominator (~220 ids), caught only because
# agent_governance.py's DENOMINATOR-DRIFT lock correctly refused the silent
# widening. A shared set makes one lane's graduation structurally unable to leave
# the other lane's wall standing, so the two are separate sets now: this axis
# graduates a version only when ITS OWN denominator has been reviewed
# (agent_denominator_audit.json + a regenerated agent_denominator_lock.json), fully
# independent of the merchant matrix's own graduation. See spec_versions.py's
# docstring for the fuller incident writeup.
from common.spec_versions import VERSIONS, AGENT_REGISTER_ONLY_VERSIONS  # noqa: E402
from common.keywords import MANDATORY  # noqa: E402 — D2-01: one mandatory-keyword tuple
REQ = os.path.join(ROOT, "conformance", "requirements")
EXEMPT = os.path.join(ROOT, "conformance", "coverage", "exemptions.json")
AGENT_EXEMPT = os.path.join(HERE, "agent_exemptions.json")
# RUN EVIDENCE (D5-04 / A12, decision 10): written by `run_agent.py --record` (owner, release
# time; a gate only hands in-run evidence to RECORD_DIR — CI-1 / decision 24) as
# {check_id: {sandbox_version: {date, spec_pin}}}. A check is attributed at version V ONLY
# with evidence at V no older than EVIDENCE_WINDOW_DAYS and at V's current spec pin —
# extending an ACheck's `versions` list attributes nothing by itself (the 2026-09-01
# incident: 40 checks / 51 req_ids read CHECK at 08-25 with no 08-25 sandbox in existence).
EVIDENCE = os.path.join(HERE, "agent_run_evidence.json")
SOURCES_LOCK = os.path.join(ROOT, "conformance", "SOURCES.lock.json")
EVIDENCE_WINDOW_DAYS = 30


def spec_pins():
    """{version: spec commit} from SOURCES.lock.json — the pin evidence must match."""
    d = json.load(open(SOURCES_LOCK))
    return {v: x["commit"] for v, x in d["spec"]["versions"].items()}


def load_evidence(path=None):
    path = path or EVIDENCE
    if not os.path.exists(path):
        return {"unrun_versions": {}, "evidence": {}}
    d = json.load(open(path))
    return {"unrun_versions": d.get("unrun_versions", {}), "evidence": d.get("evidence", {})}


def evidence_problem(entry, today, pin):
    """None if `entry` ({date, spec_pin}) is fresh evidence at `pin`; else the reason."""
    if not entry:
        return "no run evidence"
    try:
        age = (today - datetime.date.fromisoformat(entry.get("date", ""))).days
    except ValueError:
        return f"malformed date {entry.get('date')!r}"
    if age < 0 or age > EVIDENCE_WINDOW_DAYS:
        return f"stale: {age} days old (window {EVIDENCE_WINDOW_DAYS})"
    if entry.get("spec_pin") != pin:
        return f"recorded at spec pin {str(entry.get('spec_pin'))[:8]}, current pin {str(pin)[:8]}"
    return None

AGENT_WORDS = ("platform must", "platforms must", "the platform", "agent must",
               "agents must", "mcp client", "client must", "consumer")

# NOT_AGENT_BOUND and AGENT_EXTRA used to be literal id sets HERE (with their reasons
# as comments). They are now DATA in agent_lane_overrides.json (D2-04): each id carries
# its reason, the versions it applies at, and an expiry clock (`review_by` + `spec_pin`)
# so the override is re-adjudicated on a horizon and invalidated by a re-pin like every
# other register entry (validate_expiry_clocks.py). The module attribute names are kept
# for every consumer (agent_governance, validate_spec_versions, spec_versions doctrine).
# D2-08 (role field) deletes both sets and this file once role-driven agent_rows lands.
OVERRIDES = os.path.join(HERE, "agent_lane_overrides.json")


def _load_overrides(path=OVERRIDES):
    d = json.load(open(path))
    return (frozenset(e["id"] for e in d.get("agent_extra", [])),
            frozenset(e["id"] for e in d.get("not_agent_bound", [])))


AGENT_EXTRA, NOT_AGENT_BOUND = _load_overrides()


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
    """The agent-subject MUST ids at `ver`.

    AGENT_REGISTER_ONLY_VERSIONS short-circuit (2026-08-30, closing the seam
    matrix.py already walls off on the merchant axis; split into its own set
    2026-08-31 — see this module's import comment and spec_versions.py's
    docstring): a register-only-on-THIS-AXIS version's rows have had zero agent-
    subject review — attributing real agent-MUST ids to it here would be exactly
    the zero-review auto-attribution hazard matrix.py's attribution()/exempt_at()
    already guard against on the merchant axis, just via a different (agent-subject
    heuristic) code path AND a different (agent-only) review clock. Empty set is
    the honest number: zero rows, not a silent guess."""
    if ver in AGENT_REGISTER_ONLY_VERSIONS:
        return set()
    cb = _client_bound_ids()
    ids = set()
    for f in glob.glob(os.path.join(REQ, ver, "*.json")):
        for r in json.load(open(f)).get("rows", []):
            if ver not in (r.get("versions") or [ver]):
                continue
            if r.get("keyword") not in MANDATORY:
                continue
            if r["id"] in NOT_AGENT_BOUND:         # business-only (denominator-accuracy audit)
                continue
            text = (r.get("requirement", "") + " " + r.get("quote", "")).lower()
            if any(w in text for w in AGENT_WORDS) or r["id"] in cb or r["id"] in AGENT_EXTRA:
                ids.add(r["id"])
    return ids


def agent_check_ids(ver, evidence=None, today=None, pins=None, label_unrun=False, labels=None):
    """req_ids covered by agent checks at `ver` — ONLY those with fresh run evidence at
    `ver` (see EVIDENCE). `evidence`/`today`/`pins` default to the committed file, today,
    and SOURCES.lock; tests inject them. Default = suppress an unrun attribution;
    `label_unrun` instead counts a check that has fresh evidence at SOME other version and
    records that version in `labels` ({check_id: sandbox_version}) so the export can say
    `evidence: {"sandbox-version": …}` rather than imply a run that never happened."""
    sys.path.insert(0, HERE)
    mod = importlib.import_module("agent_checks")
    evidence = load_evidence()["evidence"] if evidence is None else evidence
    today = today or datetime.date.today()
    pins = pins or spec_pins()
    out = set()
    for chk in getattr(mod, "CHECKS", []):
        if chk.versions and ver not in chk.versions:
            continue
        e = evidence.get(chk.id, {}) or {}
        if evidence_problem(e.get(ver), today, pins.get(ver)) is None:
            out.update(chk.req_ids)
        elif label_unrun:
            ran = [v for v, x in e.items() if evidence_problem(x, today, pins.get(v)) is None]
            if ran:
                out.update(chk.req_ids)
                if labels is not None:
                    labels[chk.id] = sorted(ran)[-1]
    return out


def agent_exempt_ids():
    if not os.path.exists(AGENT_EXEMPT):
        return {}
    return json.load(open(AGENT_EXEMPT))


def account(ver, label_unrun=False, labels=None):
    rows = agent_rows(ver)
    checks = agent_check_ids(ver, label_unrun=label_unrun, labels=labels)
    ex = agent_exempt_ids()
    check = sorted(r for r in rows if r in checks)
    exempt = sorted(r for r in rows if r not in checks and r in ex)
    gap = sorted(r for r in rows if r not in checks and r not in ex)
    return rows, check, exempt, gap


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--require", choices=["all"])
    ap.add_argument("--json")
    ap.add_argument("--label-unrun", action="store_true",
                    help="count checks that ran only on another sandbox version and LABEL the "
                         "export with evidence: {sandbox-version} (default: suppress them)")
    args = ap.parse_args()
    failed = False
    summary = {}
    print("AGENT coverage axis (platform/agent obligations) — separate from merchant\n")
    for ver in VERSIONS:
        labels = {}
        rows, check, exempt, gap = account(ver, label_unrun=args.label_unrun, labels=labels)
        n = len(rows)
        pct = round(100 * (len(check) + len(exempt)) / n) if n else 0
        summary[ver] = {"agent_musts": n, "check": len(check), "exempt": len(exempt),
                        "gap": len(gap), "accounted_pct": pct}
        if labels:
            summary[ver]["evidence"] = {"sandbox-version": sorted(set(labels.values()))[-1],
                                        "labeled_unrun_checks": len(labels)}
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

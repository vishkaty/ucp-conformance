#!/usr/bin/env python3
"""
done2_status.py — the DONE-2 definition (PLAN-v3-FINAL §1, items 1-12) as ONE machine
report (D2-16). A program-review artefact, not a run_suite gate: it never fails the
build, it says — per item — whether the mechanical condition holds today and what the
evidence is. `collect()` gathers inputs (exports, gate outputs, registers); `evaluate()`
is pure over those inputs so the selftest can feed scratch data.

  python3 conformance/coverage/done2_status.py            # twelve lines: item N: PASS/FAIL — evidence
  python3 conformance/coverage/done2_status.py --json     # {item: {pass, evidence}}
  python3 conformance/coverage/done2_status.py --selftest # hermetic: scratch inputs flip items 2 and 8

Items (each is decision 16's addition when it goes beyond PLAN-0825 §G):
   1 census (prose gate mode + schema --enforce + surface published)
   2 per role / per transport (--require all --role merchant|agent, per bound transport, agent axis)
   3 evidence classes published (seven classes)
   4 kill discipline (per-id kills, killset lock 0 drift, dormancy floor)
   5 independent corroboration (dual-oracle-0825 + pydantic referee)
   6 agent lane (08-25 run evidence from a 2026-08-25 sandbox)
   7 ratchet + lock + state (both lanes at 08-25; converting state machine)
   8 records (expiry clocks 0/0/0; known issues valid)
   9 SHOULD scope (census feeds surface.should; site sentence)
  10 review (every sample batch's human sample recorded)
  11 converting state (08-25 `converting` with the open testable-tier count)
  12 attribution (hook gate green; filing_lint --branches 0 hits)
"""
import json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONF = ROOT / "conformance"
sys.path.insert(0, str(CONF))
sys.path.insert(0, str(CONF / "selfcheck"))

V = "2026-08-25"
ITEMS = 12


SEVEN_CLASSES = ("live-wire", "fixture-schema", "fixture-crypto", "self-referenced",
                 "register-selfcheck", "reference-impl", "discovery-live")


def _rc_ok(rc, skip_ok=()):
    return rc == 0 or rc in skip_ok


def evaluate(inp):
    """Pure: inputs dict (see collect()) -> {item: {pass, evidence}} for items 1..12."""
    out = {}
    e = ((inp.get("export") or {}).get("versions") or {}).get(V) or {}
    surf = e.get("surface") or {}
    prose, schema = surf.get("prose") or {}, surf.get("schema") or {}
    # 1 census
    prose_ok = prose.get("census_mode") == "gate" and prose.get("missed") == 0
    schema_ok = bool(schema.get("enforce")) and schema.get("atoms_unaccounted") == 0
    out[1] = {"pass": prose_ok and schema_ok and bool(surf),
              "evidence": f"prose census mode {prose.get('census_mode')} missed {prose.get('missed')} · schema enforce "
                          f"{schema.get('enforce')} atoms_unaccounted {schema.get('atoms_unaccounted')} · surface published {bool(surf)}"}
    # 2 per role / per transport
    roles = e.get("roles") or {}
    mg = (roles.get("merchant") or {}).get("gap")
    ax = (inp.get("agent_axis") or {}).get(V) or {}
    ag = ax.get("gap")
    bt = e.get("by_transport") or {}
    tgaps = {t: d.get("gap") for t, d in bt.items() if t != "any"}
    out[2] = {"pass": mg == 0 and ag == 0 and all(g == 0 for g in tgaps.values()) and bool(bt),
              "evidence": f"merchant lane GAP {mg} · agent lane GAP {ag} (agent_matrix) · per-transport GAP {tgaps}"}
    # 3 evidence classes
    classes = list(e.get("evidence_classes") or [])
    missing = [c for c in SEVEN_CLASSES if c not in classes]
    out[3] = {"pass": not missing, "evidence": f"published classes {classes} · missing {missing}"}
    # 4 kill discipline
    rk, kl, dm = inp.get("req_kills_rc"), inp.get("killset_rc"), inp.get("dormancy_rc")
    out[4] = {"pass": rk == 0 and kl == 0 and dm == 0,
              "evidence": f"validate_req_kills rc {rk} · killset-lock rc {kl} · dormancy rc {dm} (None = not available)"}
    # 5 independent corroboration
    do, py = inp.get("dual_oracle_rc"), inp.get("pydantic_rc")
    out[5] = {"pass": do == 0 and py == 0,
              "evidence": f"dual-oracle-0825 rc {do} · pydantic referee rc {py} (None = not available / timed out)"}
    # 6 agent lane evidence at V
    ev = inp.get("agent_evidence") or {}
    unrun = V in (ev.get("unrun_versions") or {})
    pin = inp.get("spec_pin")
    lacking = [c for c in (inp.get("agent_checks_at_v") or [])
               if ((ev.get("evidence") or {}).get(c) or {}).get(V, {}).get("spec_pin") != pin]
    out[6] = {"pass": not unrun and not lacking and bool(inp.get("agent_checks_at_v")),
              "evidence": f"{V} declared unrun {unrun} · agent checks at {V} without evidence at pin {str(pin)[:8]}: "
                          f"{len(lacking)}/{len(inp.get('agent_checks_at_v') or [])}"}
    # 7 ratchet + lock + state
    r_ok = V in (inp.get("ratchet") or {})
    ar_ok = V in (inp.get("agent_ratchet") or {})
    cl = (inp.get("coverage_lock") or {}).get(V)
    acl_all = inp.get("agent_coverage_lock") or {}
    acl = acl_all.get(V)
    susp = V in (inp.get("agent_lock_suspensions") or {})
    st = e.get("state")
    out[7] = {"pass": r_ok and ar_ok and bool(cl) and bool(acl) and not susp and st in ("converting", "live"),
              "evidence": f"ratchet {r_ok} · agent ratchet {ar_ok} · coverage_lock {V} {bool(cl)} · agent lock {bool(acl)}"
                          f"{' (SUSPENDED)' if susp else ''} · state {st}"}
    # 8 records
    findings = [f for f in (inp.get("expiry_findings") or []) if f.get("kind") in ("expired", "pin-drift", "missing-clock")]
    ki = inp.get("known_issues_rc")
    out[8] = {"pass": not findings and _rc_ok(ki, (2,)),
              "evidence": f"expiry clocks: {len(findings)} expired/pin-drift/missing-clock"
                          + (f" ({findings[0]['entry']}: {findings[0]['detail']})" if findings else "")
                          + f" · known-issues rc {ki}"}
    # 9 SHOULD scope
    sh = (inp.get("should") or {}).get(V) or {}
    out[9] = {"pass": sh.get("hits") is not None and bool(inp.get("rubric_sentence")),
              "evidence": f"SHOULD census hits {sh.get('hits')} · site sentence 'airtight = MUST …' present {bool(inp.get('rubric_sentence'))}"}
    # 10 review
    pend = [b.get("batch") for b in (inp.get("signoffs") or []) if b.get("kind") == "sample" or str(b.get("batch", "")).startswith("expiry-clock-seed")
            if ((b.get("sample") or {}).get("human_review") or {}).get("status") != "recorded"]
    out[10] = {"pass": not pend, "evidence": f"sample batches with human review PENDING: {pend or 'none'}"}
    # 11 converting state
    gbt = e.get("gap_by_testability") or {}
    open_t = sum(gbt.get(t, 0) for t in ("testable", "needs-receiver", "needs-oauth"))
    out[11] = {"pass": st == "converting", "evidence": f"{V} state {st} · open testable-tier rows {open_t}"}
    # 12 attribution
    ah, fl = inp.get("attribution_rc"), inp.get("filing_lint_rc")
    out[12] = {"pass": _rc_ok(ah, (2,)) and fl == 0,
               "evidence": f"attribution-hook rc {ah} (2 = ops not mounted) · filing_lint --branches rc {fl}"}
    return out


def _run(argv, timeout=120):
    try:
        r = subprocess.run([sys.executable] + [str(a) for a in argv], cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return None, ""
    except Exception:
        return None, ""


def collect(quick=False):
    """Gather the inputs from the tree (exports, registers) and the gates' own outputs."""
    import datetime, importlib, re
    inp = {"today": datetime.date.today()}
    def jload(p):
        try:
            return json.load(open(p))
        except Exception:
            return None
    inp["export"] = jload(ROOT / "public" / "coverage.json")
    inp["agent_axis"] = jload(CONF / "agent" / "agent_coverage.json")
    rk = CONF / "selfcheck" / "validate_req_kills.py"
    inp["req_kills_rc"] = _run([rk, "--version", V])[0] if rk.exists() else None
    inp["killset_rc"] = _run([CONF / "selfcheck" / "validate_killset_lock.py"])[0]
    inp["dormancy_rc"] = _run([CONF / "selfcheck" / "validate_dormancy.py"])[0]
    if quick:
        inp["dual_oracle_rc"] = inp["pydantic_rc"] = None
    else:
        inp["dual_oracle_rc"] = _run([CONF / "selfcheck" / "validate_dual_oracle.py", "--version", V], timeout=420)[0]
        inp["pydantic_rc"] = _run([CONF / "selfcheck" / "validate_dual_oracle.py", "--version", V, "--pydantic"], timeout=420)[0]
    inp["agent_evidence"] = jload(CONF / "agent" / "agent_run_evidence.json") or {}
    lock = jload(CONF / "SOURCES.lock.json") or {}
    inp["spec_pin"] = ((lock.get("spec") or {}).get("versions") or {}).get(V, {}).get("commit")
    try:
        sys.path.insert(0, str(CONF / "agent"))
        ac = importlib.import_module("agent_checks")
        inp["agent_checks_at_v"] = [c.id for c in ac.CHECKS if not c.versions or V in c.versions]
    except Exception:
        inp["agent_checks_at_v"] = []
    inp["ratchet"] = {k: v for k, v in (jload(CONF / "coverage" / "ratchet.json") or {}).items() if not k.startswith("_")}
    inp["agent_ratchet"] = {k: v for k, v in (jload(CONF / "agent" / "agent_ratchet.json") or {}).items() if not k.startswith("_")}
    inp["coverage_lock"] = (jload(CONF / "coverage" / "coverage_lock.json") or {}).get("versions", {})
    acl = jload(CONF / "agent" / "agent_coverage_lock.json") or {}
    inp["agent_coverage_lock"] = acl.get("versions", {})
    inp["agent_lock_suspensions"] = acl.get("suspensions", {})
    rc, out = _run([CONF / "selfcheck" / "validate_expiry_clocks.py"])
    m = re.search(r"(\d+) expired · (\d+) pin-drift · (\d+) missing-clock", out or "")
    findings = []
    if m:
        for kind, n in zip(("expired", "pin-drift", "missing-clock"), m.groups()):
            findings += [{"kind": kind, "entry": "see validate_expiry_clocks.py", "detail": f"{n} {kind}"}] * int(n)
    elif rc != 0:
        findings.append({"kind": "missing-clock", "entry": "validate_expiry_clocks.py", "detail": f"rc {rc}"})
    inp["expiry_findings"] = findings
    inp["known_issues_rc"] = _run([CONF / "ci" / "validate_known_issues.py"])[0]
    rc, out = _run([CONF / "selfcheck" / "verify_should_census.py", "--json"])
    try:
        inp["should"] = json.loads(out).get("per_version", {})
    except Exception:
        inp["should"] = {}
    inp["rubric_sentence"] = any("airtight = MUST" in p.read_text(errors="replace")
                                 for p in (ROOT / "public").glob("*.html"))
    inp["signoffs"] = (jload(CONF / "coverage" / "review_signoffs.json") or {}).get("signoffs", [])
    inp["attribution_rc"] = _run([CONF / "ci" / "attribution_hook_gate.py"])[0]
    fl = ROOT / "ops" / "tools" / "filing_lint.py"
    inp["filing_lint_rc"] = _run([fl, "--branches"], timeout=300)[0] if fl.exists() else None
    return inp


def main(argv):
    quick = "--quick" in argv
    res = evaluate(collect(quick=quick))
    if "--json" in argv:
        print(json.dumps({str(k): v for k, v in sorted(res.items())}, indent=1))
        return 0
    for k in sorted(res):
        print(f"item {k:2}: {'PASS' if res[k]['pass'] else 'FAIL'} — {res[k]['evidence']}")
    n = sum(1 for v in res.values() if v["pass"])
    print(f"\nDONE-2 status: {n}/{ITEMS} items hold" + (" [--quick: oracle items not run]" if quick else ""))
    return 0


def selftest():
    """Scratch inputs: an export whose merchant lane has 3 GAPs -> item 2 false; a
    register with an expired review_by -> item 8 false; the same inputs with those two
    fixed -> items 2 and 8 true (so the function can pass, not only fail)."""
    import datetime
    bad = 0

    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1

    def scratch(merchant_gap, expired):
        export = {"versions": {V: {
            "state": "converting", "musts": 10, "check": 7, "exempt": 0, "gap": 3,
            "roles": {"summary": {"merchant": 8, "agent": 4, "both": 2, "other": 0},
                      "merchant": {"musts": 8, "check": 8 - merchant_gap, "exempt": 0, "gap": merchant_gap},
                      "agent": {"musts": 4, "check": 4, "exempt": 0, "gap": 0}},
            "by_transport": {"rest": {"musts": 10, "check": 10 - merchant_gap, "exempt": 0, "gap": merchant_gap}},
            "surface": {"prose": {"missed": 0, "census_mode": "gate"}, "schema": {"enforce": True, "atoms_unaccounted": 0},
                        "should": {"hits": 274, "scope": "report-only"}},
            "evidence_classes": ["live-wire", "fixture-schema", "fixture-crypto", "self-referenced",
                                 "register-selfcheck", "reference-impl", "discovery-live"],
            "gap_by_testability": {"testable": 3},
        }}}
        today = datetime.date(2026, 9, 11)
        rb = "2026-09-01" if expired else "2026-12-01"
        return {
            "export": export, "today": today,
            "agent_axis": {V: {"agent_musts": 4, "check": 4, "exempt": 0, "gap": 0}},
            "req_kills_rc": 0, "killset_rc": 0, "dormancy_rc": 0,
            "dual_oracle_rc": 0, "pydantic_rc": 0,
            "agent_evidence": {"unrun_versions": {}, "evidence": {"agent.x": {V: {"date": "2026-09-11", "spec_pin": "cd78fb38"}}}},
            "agent_checks_at_v": ["agent.x"], "spec_pin": "cd78fb38",
            "ratchet": {V: 7}, "agent_ratchet": {V: {"accounted": 4}},
            "coverage_lock": {V: {"check": ["A-1"], "exempt": []}}, "agent_coverage_lock": {V: {"check": ["B-1"]}},
            "expiry_findings": [{"kind": "expired", "entry": "scratch.json x[0]", "detail": f"review_by {rb}"}] if expired else [],
            "known_issues_rc": 0,
            "should": {V: {"hits": 274}}, "rubric_sentence": True,
            "signoffs": [{"batch": "s1", "kind": "sample", "sample": {"human_review": {"status": "recorded"}}}],
            "attribution_rc": 0, "filing_lint_rc": 0,
        }

    try:
        bad_in = evaluate(scratch(merchant_gap=3, expired=True))
        good_in = evaluate(scratch(merchant_gap=0, expired=False))
    except NameError as e:
        print(f"  ✗ evaluate absent: {e}")
    # W1 integration (D2-16 x D5-13): item 9's site sentence is the REGISTERED CLAIM-RUB-016 text
    # on its page ("Airtight means the MUST / MUST NOT / REQUIRED / SHALL obligations …"), not the
    # plan's placeholder literal 'airtight = MUST' (kept as the pre-D5-13 fallback).
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        pub = pathlib.Path(td); (pub / "rubric.html").write_text("<p>Airtight means the MUST obligations; SHOULD-class report-only</p>")
        claims = {"claims": [{"id": "CLAIM-RUB-016", "page": "rubric.html", "text": "Airtight means the MUST obligations; SHOULD-class report-only"}]}
        try:
            present = rubric_sentence_present(pub, claims)
            absent = rubric_sentence_present(pub, {"claims": [{"id": "CLAIM-RUB-016", "page": "rubric.html", "text": "some other sentence"}]})
            (pub / "old.html").write_text("airtight = MUST only")
            legacy = rubric_sentence_present(pub, {"claims": []})
            case("item 9 detects the registered CLAIM-RUB-016 text on its page (absent -> False; legacy literal -> True)",
                 present is True and absent is False and legacy is True, f"present={present} absent={absent} legacy={legacy}")
        except NameError as e:
            case("item 9 detects the registered CLAIM-RUB-016 text on its page (absent -> False; legacy literal -> True)", False, str(e))
        print("\ndone2-status selftest: FAIL (1 case(s))")
        return 1
    bad += case("scratch export with roles.merchant.gap 3 -> item 2 FAIL",
                bad_in[2]["pass"] is False and "3" in bad_in[2]["evidence"], repr(bad_in[2]))
    bad += case("scratch register with an expired review_by -> item 8 FAIL",
                bad_in[8]["pass"] is False and "expired" in bad_in[8]["evidence"], repr(bad_in[8]))
    bad += case("same inputs fixed -> items 2 and 8 PASS",
                good_in[2]["pass"] is True and good_in[8]["pass"] is True, repr((good_in[2], good_in[8])))
    bad += case("twelve items reported", sorted(good_in) == list(range(1, ITEMS + 1)), repr(sorted(good_in)))
    print(f"\ndone2-status selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

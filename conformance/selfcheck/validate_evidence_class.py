#!/usr/bin/env python3
"""
validate_evidence_class.py — the kill-tested gate for the EVIDENCE-CLASS layer
(conformance/coverage/evidence.py) and the published honest split.

The evidence classifier is itself a claim ("this check's verdict rests on THIS kind
of evidence"), so it gets the same treatment as every other claim in this repo: a
selftest that proves it can FAIL. Four families:

  1. REPRESENTATIVE CHECKS — real shipped check objects must classify to the class
     their mechanics dictate: a wire-probing receiver MCheck, the AP2 _MERCHANT_SEED
     crypto self-tests, a schema-oracle fixture_check, a schema-tier namedtuple
     check, a live 01-era engine check that reaches engine.fetch only through module
     helpers (kills a non-transitive classifier), and an MCheck that reads only
     ctx.profile (wire by runner construction — kills a fetch-introspection-only
     classifier).

  2. CLASSIFIER KILL-TESTS — synthetic checks built so that each PLAUSIBLE broken
     classifier yields the wrong class: fixture+plain predicate must NOT inherit
     fixture-schema (kills a "fixture defaults to schema" mislabel), a crypto
     predicate must not classify schema, a wire check with reach RECORDED but never
     GRADED must not become live-wire (kills "recorded = corroborated"), a missing
     reach report must demote every wire check (fail-closed), and per-id
     aggregation must take the strongest class.

  3. PUBLISHED-TOTALS INVARIANTS — the evidence layer is REPORTING ONLY: the fresh
     export's check/exempt/gap totals must be internally consistent, the evidence
     breakdown must partition the CHECK bucket exactly (no id dropped or double
     counted), and the accounted numbers must still clear the committed ratchet
     floors (no count regression from this layer).

  4. PUBLISHED-SPLIT SYNC — the honest split published in public/site_claims.json
     ($.evidence.per_version) must equal a fresh matrix export's breakdown, and the
     committed reach report must be structurally sound (keys "version:module:
     check_id", targets only from ci/differential_targets.json — the independent-
     target register).

  5. VERSION-AXIS KILL-TESTS (R4) — reach evidence must never cross a spec version:
     a check graded at version V must NOT classify live-wire when evaluated at a
     DIFFERENT version it's also attributed to (negative control — this is the
     leak that let a single 2026-04-08 differential run silently "corroborate"
     2026-01-11/2026-01-23, and would have handed 2026-08-25 live-wire credit for
     a version with zero independently-authored implementation), and MUST classify
     live-wire when evaluated at the SAME version it was graded at (positive
     control — proves the version-keyed path isn't simply demoting everything to
     self-referenced "safely" while silently zeroing real corroboration).

Exit 0 = all hold; 1 = a classification lie, a totals drift, or a stale published
split. Hermetic: no server, no network — the reach report is committed data.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.dirname(HERE)
ROOT = os.path.dirname(CONF)
sys.path.insert(0, os.path.join(CONF, "coverage"))
sys.path.insert(0, os.path.join(CONF, "checks"))
sys.path.insert(0, HERE)

import evidence                                     # noqa: E402
import matrix                                       # noqa: E402


def _find(checks, cid):
    for c in checks:
        if c.id == cid:
            return c
    raise AssertionError(f"representative check {cid} not found")


def representative_checks():
    """Family 1: real shipped checks classify to the class their mechanics dictate."""
    fails = []
    import engine
    import v2026_01_23
    import area_04_08_ap2
    import area_04_08_cart
    import schema_check_04_08_payment
    import merchant_checks
    import merchant_checks_04_08_receiver as recv

    def expect(chk, stem, version, reach, want, name):
        got, _targets = evidence.classify_check(chk, stem, version, reach)
        if got != want:
            fails.append(f"{name}: classified '{got}', mechanics dictate '{want}'")

    # A wire-probing receiver check: wire by construction; live-wire ONLY with
    # graded independent reach AT THE SAME VERSION, self-referenced without.
    esc = _find(recv.CHECKS_04_08_RECEIVER, "checkout.escalation_continue_url")
    if evidence.acquisition(esc) != "wire":
        fails.append("receiver escalation check must be acquisition 'wire' "
                     f"(got {evidence.acquisition(esc)})")
    expect(esc, "merchant_checks_04_08_receiver", "2026-04-08", {}, "self-referenced",
           "wire receiver check with NO independent reach")
    synth_reach = {"2026-04-08:merchant_checks_04_08_receiver:checkout.escalation_continue_url":
                   ["flower-shop-official-sample"]}
    expect(esc, "merchant_checks_04_08_receiver", "2026-04-08", synth_reach, "live-wire",
           "wire receiver check WITH graded independent reach (positive control, R4 H3)")
    expect(esc, "merchant_checks_04_08_receiver", "2026-01-23", synth_reach, "self-referenced",
           "wire receiver check graded at 2026-04-08 must NOT credit a sibling "
           "version (negative control, R4)")

    # The AP2 _MERCHANT_SEED self-tests: fixture acquisition, crypto oracle.
    ap2 = _find(area_04_08_ap2.CHECKS, "payment.ap2_authorization_authentic")
    expect(ap2, "area_04_08_ap2", "2026-04-08", {}, "fixture-crypto", "AP2 seed-key self-test")
    ap2n = _find(area_04_08_ap2.CHECKS, "payment.ap2_mandate_nested_binding")
    expect(ap2n, "area_04_08_ap2", "2026-04-08", {}, "fixture-crypto",
           "AP2 nested-binding self-test")

    # A schema-oracle fixture_check (the schema_check.py factory whose fetch lambda
    # ignores `base`): fixture acquisition, official-oracle verdict.
    cart = _find(area_04_08_cart.CHECKS, "cart.entity_required_fields")
    if evidence.acquisition(cart) != "fixture":
        fails.append("fixture_check must be acquisition 'fixture' "
                     f"(got {evidence.acquisition(cart)}) — its fetch ignores base")
    expect(cart, "area_04_08_cart", "2026-04-08", {}, "fixture-schema",
           "schema-oracle fixture_check")

    # A schema-tier namedtuple check (valid+negatives run through the oracle).
    st = schema_check_04_08_payment.CHECKS[0]
    expect(st, "schema_check_04_08_payment", "2026-04-08", {}, "fixture-schema",
           "schema-tier namedtuple check")

    # A live 01-era engine check reaches engine.fetch only through module helpers —
    # a NON-TRANSITIVE classifier calls this 'fixture' and lies.
    core = v2026_01_23.CHECKS[0]
    if evidence.acquisition(core) != "wire":
        fails.append("v2026_01_23 core check must be 'wire' (fetch via helpers) — "
                     "a non-transitive classifier mislabels it "
                     f"(got {evidence.acquisition(core)})")

    # An MCheck that reads only ctx.profile (fetched from the live server by
    # discover()): wire by RUNNER construction, not by fetch_fn introspection.
    prof = next(c for c in merchant_checks.CHECKS
                if c.fetch_fn is merchant_checks.profile_resp)
    if evidence.acquisition(prof) != "wire":
        fails.append("profile_resp MCheck must be 'wire' (MerchantCtx is built from "
                     f"a live server) — got {evidence.acquisition(prof)}")

    # Under the COMMITTED reach report, the mechanism must actually corroborate:
    # at least one wire check is live-wire (a reach layer that can never grant
    # live-wire proves nothing).
    real = evidence.load_reach()
    if not real:
        fails.append("committed reach_report.json missing/empty — the live-wire "
                     "class would be unreachable")
    else:
        lw = [k for k in real
              if evidence.classify_check(
                  _find_by_key(k), _stem_of_key(k), _version_of_key(k), real)[0]
              == "live-wire"]
        if len(lw) < 20:
            fails.append(f"only {len(lw)} wire checks classify live-wire under the "
                         "committed reach report — corroboration layer looks broken")
    return fails


_KEY_CACHE = {}


def _version_of_key(key):
    return key.split(":", 1)[0]


def _stem_of_key(key):
    return key.split(":", 2)[1]


def _find_by_key(key):
    """Resolve a reach key 'version:module_stem:check_id' to the live check
    object (version-independent lookup — the same check object can be attributed
    to several versions; the CACHE is keyed by module:check_id only, but the
    caller separately extracts and passes the version)."""
    if not _KEY_CACHE:
        for path in matrix.check_files():
            checks, _mod = matrix._module_checks(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            for c in checks or []:
                _KEY_CACHE[f"{stem}:{c.id}"] = c
    _, stem, cid = key.split(":", 2)
    return _KEY_CACHE[f"{stem}:{cid}"]


def classifier_kill_tests():
    """Family 2: synthetic checks on which every plausible-broken classifier lies."""
    fails = []
    import engine
    from engine import Check
    import schema_check
    import crypto

    fixture_fetch = lambda base: engine.Resp(200, {}, b'{"x": 1}')          # noqa: E731
    plain_pred = lambda r: "clean-pass"                                     # noqa: E731

    def expect(chk, stem, version, reach, want, name):
        got, _t = evidence.classify_check(chk, stem, version, reach)
        if got != want:
            fails.append(f"KILL {name}: classified '{got}', must be '{want}'")

    # fixture + hand-written predicate = self-referenced. A classifier that defaults
    # the fixture tier to fixture-schema mislabels THIS and reds here.
    expect(Check("k.fixture_plain", ["ZZZ-001"], "MUST", fixture_fetch, plain_pred, []),
           "synthetic", "2026-04-08", {}, "self-referenced", "fixture + plain predicate")

    # crypto-routed predicate = fixture-crypto, never fixture-schema.
    def crypto_pred(r):
        return crypto.b64url_decode("AA") and "clean-pass"
    expect(Check("k.fixture_crypto", ["ZZZ-002"], "MUST", fixture_fetch, crypto_pred, []),
           "synthetic", "2026-04-08", {}, "fixture-crypto", "fixture + crypto predicate")

    # schema_predicate-built predicate = fixture-schema.
    expect(Check("k.fixture_schema", ["ZZZ-003"], "MUST", fixture_fetch,
                 schema_check.schema_predicate("create_checkout", "response"), []),
           "synthetic", "2026-04-08", {}, "fixture-schema", "fixture + schema-oracle predicate")

    # wire reached only through a local helper chain: transitivity required.
    def _helper(base):
        return engine.fetch(base, "/x")

    def indirect_wire(base):
        return _helper(base)
    wire_chk = Check("k.wire_indirect", ["ZZZ-004"], "MUST", indirect_wire, plain_pred, [])
    if evidence.acquisition(wire_chk) != "wire":
        fails.append("KILL indirect wire fetch: a non-transitive classifier "
                     "labels it 'fixture'")

    # RECORDED but never GRADED on an independent target is NOT corroboration:
    # only clean-pass/deviation statuses may grant live-wire.
    reach_ungraded = {"2026-04-08:synthetic:k.wire_indirect": []}
    expect(wire_chk, "synthetic", "2026-04-08", reach_ungraded, "self-referenced",
           "wire check recorded but never graded")
    reach_graded = {"2026-04-08:synthetic:k.wire_indirect": ["nodejs-reference-sample"]}
    expect(wire_chk, "synthetic", "2026-04-08", reach_graded,
           "live-wire", "wire check graded on an independent target (positive control)")

    # R4 (H1/negative control): the SAME reach report, evaluated at a DIFFERENT
    # version the check object is also attributed to, must NOT inherit the grade —
    # this is the exact leak that let a single 04-08 run "corroborate" 01-11/01-23,
    # and would have handed 2026-08-25 live-wire credit with zero independently-
    # authored implementation. A classifier that ignores the version parameter
    # (falls back to a bare module:check_id lookup) reds here.
    expect(wire_chk, "synthetic", "2026-01-23", reach_graded, "self-referenced",
           "wire check graded at 2026-04-08 must not credit 2026-01-23 (R4 negative control)")
    expect(wire_chk, "synthetic", "2026-08-25", reach_graded, "self-referenced",
           "wire check graded at 2026-04-08 must not credit 2026-08-25 (R4 negative control)")

    # load_reach: a skip/not-tested status must not count as graded ...
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump({"checks": {
            "2026-04-08:m:a": {"version": "2026-04-08",
                               "targets": {"flower-shop-official-sample":
                                          "not-tested (needs config: x)"}},
            "2026-04-08:m:b": {"version": "2026-04-08",
                               "targets": {"flower-shop-official-sample": "deviation"}},
            "2026-01-23:m:c": {"version": "2026-01-23",
                               "targets": {"nodejs-reference-sample": "clean-pass"}}}}, tf)
        path = tf.name
    got = evidence.load_reach(path)
    os.unlink(path)
    if "2026-04-08:m:a" in got:
        fails.append("KILL load_reach: a not-tested status was counted as graded")
    if got.get("2026-04-08:m:b") != ["flower-shop-official-sample"] or \
       got.get("2026-01-23:m:c") != ["nodejs-reference-sample"]:
        fails.append(f"KILL load_reach: graded statuses mis-loaded ({got})")
    # ... and a MISSING report fails closed (every wire check demotes).
    if evidence.load_reach("/nonexistent/reach.json") != {}:
        fails.append("KILL load_reach: missing report must yield {} (fail-closed)")

    # per-id aggregation takes the STRONGEST class; text-scan rows fail closed.
    rows = [("2026-04-08", "ZZZ-009", "synthetic.py", wire_chk),
            ("2026-04-08", "ZZZ-009", "other.py", None)]
    ev = evidence.evidence_by_id(rows, {"2026-04-08:synthetic:k.wire_indirect": ["x"]})
    if ev["2026-04-08"]["ZZZ-009"]["evidence"] != "live-wire":
        fails.append("KILL aggregation: strongest class must win per id")
    ev2 = evidence.evidence_by_id([("2026-04-08", "ZZZ-010", "ghost.py", None)], {})
    if ev2["2026-04-08"]["ZZZ-010"]["evidence"] != "self-referenced":
        fails.append("KILL text-scan fallback: must classify self-referenced")
    if evidence.strongest(["self-referenced", "fixture-crypto", "fixture-schema"]) \
            != "fixture-schema":
        fails.append("KILL strongest(): rank order broken")

    # R4 (evidence_by_id cross-version aggregation): the SAME check object attributed
    # to TWO versions, with reach graded at only ONE of them, must classify live-wire
    # at that version and self-referenced at the sibling — proving the (id(chk),
    # version) cache key (not id(chk) alone) actually takes effect end-to-end.
    rows_multiver = [("2026-04-08", "ZZZ-011", "synthetic.py", wire_chk),
                      ("2026-01-11", "ZZZ-011", "synthetic.py", wire_chk)]
    ev3 = evidence.evidence_by_id(rows_multiver,
                                   {"2026-04-08:synthetic:k.wire_indirect": ["x"]})
    if ev3["2026-04-08"]["ZZZ-011"]["evidence"] != "live-wire":
        fails.append("KILL cross-version cache: graded version must classify live-wire")
    if ev3["2026-01-11"]["ZZZ-011"]["evidence"] != "self-referenced":
        fails.append("KILL cross-version cache: ungraded sibling version must NOT "
                     "inherit live-wire from the same check object's other-version grade")
    return fails


def totals_invariants(export):
    """Family 3: the layer is reporting-only — totals stay whole and above floors."""
    fails = []
    floors = {k: v for k, v in
              json.load(open(os.path.join(CONF, "coverage", "ratchet.json"))).items()
              if not k.startswith("_")}
    for ver, d in export["versions"].items():
        if d["check"] + d["exempt"] + d["gap"] != d["musts"]:
            fails.append(f"{ver}: check+exempt+gap != musts (accounting hole)")
        ebd = d.get("evidence_breakdown")
        if ebd is None:
            fails.append(f"{ver}: evidence_breakdown missing from the export")
            continue
        if set(ebd) != set(evidence.CLASSES):
            fails.append(f"{ver}: evidence_breakdown classes {sorted(ebd)} != "
                         f"{sorted(evidence.CLASSES)}")
        if sum(ebd.values()) != d["check"]:
            fails.append(f"{ver}: evidence classes sum to {sum(ebd.values())} but "
                         f"CHECK bucket is {d['check']} — the split must PARTITION "
                         f"the bucket, never change it")
        n_rows = sum(1 for r in d["rows"] if r["status"] == "check"
                     and "evidence" in r)
        if n_rows != d["check"]:
            fails.append(f"{ver}: {n_rows} check rows carry an evidence field, "
                         f"expected {d['check']}")
        floor = floors.get(ver)
        if floor is not None and d["check"] + d["exempt"] < floor:
            fails.append(f"{ver}: accounted {d['check'] + d['exempt']} fell below "
                         f"ratchet floor {floor}")
    return fails


def published_sync(export):
    """Family 4: the split published in site_claims.json equals a fresh export, and
    the committed reach report is structurally sound."""
    fails = []
    sc = json.load(open(os.path.join(ROOT, "public", "site_claims.json")))
    ev = (sc.get("evidence") or {}).get("per_version")
    if not ev:
        fails.append("public/site_claims.json: $.evidence.per_version missing — "
                     "the honest split is not published")
        return fails
    for ver, d in export["versions"].items():
        want = {"check": d["check"], **d["evidence_breakdown"]}
        got = ev.get(ver)
        if got != want:
            fails.append(f"site_claims.json evidence split for {ver} is STALE: "
                         f"published {got}, fresh export says {want} — regenerate "
                         f"(matrix --json) and update $.evidence.per_version")

    rr_path = os.path.join(CONF, "coverage", "reach_report.json")
    if not os.path.exists(rr_path):
        fails.append("coverage/reach_report.json missing — wire corroboration "
                     "would silently fail closed for every check")
        return fails
    rr = json.load(open(rr_path))
    registered = {t["name"] for t in json.load(open(os.path.join(
        CONF, "ci", "differential_targets.json")))["targets"]}
    known_versions = set(matrix.VERSIONS)
    for key, entry in (rr.get("checks") or {}).items():
        parts = key.split(":", 2)
        if len(parts) != 3:
            fails.append(f"reach report key '{key}' is not 'version:module:check_id'")
            continue
        ver, _mod, _cid = parts
        if ver not in known_versions:
            fails.append(f"reach report key '{key}': version '{ver}' is not a "
                         f"known spec version {sorted(known_versions)}")
        if entry.get("version") != ver:
            fails.append(f"reach report {key}: entry.version={entry.get('version')!r} "
                         f"disagrees with its own key prefix {ver!r}")
        rogue = set(entry.get("targets") or {}) - registered
        if rogue:
            fails.append(f"reach report {key}: target(s) {sorted(rogue)} are not in "
                         f"the independent-target register (differential_targets"
                         f".json) — corroboration only counts servers we did not "
                         f"author")
    rogue_meta = set(rr.get("targets") or {}) - registered
    if rogue_meta:
        fails.append(f"reach report metadata names unregistered target(s) "
                     f"{sorted(rogue_meta)}")
    return fails


def main():
    fails = []
    print("evidence-class gate — classifier kill-tests + published-split sync\n")
    fams = [("representative checks", representative_checks),
            ("classifier kill-tests", classifier_kill_tests)]
    for name, fn in fams:
        f = fn()
        print(f"  {'✓' if not f else '✗'} {name}" + (f" ({len(f)} failure(s))" if f else ""))
        fails += f
    export = matrix.export_json()
    for name, fn in [("published-totals invariants", totals_invariants),
                     ("published-split sync", published_sync)]:
        f = fn(export)
        print(f"  {'✓' if not f else '✗'} {name}" + (f" ({len(f)} failure(s))" if f else ""))
        fails += f
    if fails:
        print("\nevidence-class gate: FAIL")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    ebd = {v: d["evidence_breakdown"] for v, d in export["versions"].items()}
    print("\nevidence-class gate: PASS — split published honestly:")
    for v, d in ebd.items():
        print(f"    {v}: " + " · ".join(f"{k} {d[k]}" for k in evidence.CLASSES))
    return 0


if __name__ == "__main__":
    # --selftest is an alias for the full run: everything here is hermetic
    sys.exit(main())

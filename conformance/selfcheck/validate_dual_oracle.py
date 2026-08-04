#!/usr/bin/env python3
"""
validate_dual_oracle.py — the DUAL-ORACLE gate: every schema-validation check runs
BOTH engines and ALARMS on verdict divergence.

BACKGROUND. The suite's schema-validation ORACLE is the official Rust `ucp-schema`
validator (SOURCES.lock.json schema_validator, wired via schema_oracle.py). An oracle
we trust blindly can silently pass a malformed payload: this week ucp-schema#43 proved
it — the Rust bundler false-accepts payment instruments missing every required field
(and false-rejects some valid ones) on the checkout payment path. The bug was FOUND by
cross-checking against an independent Python jsonschema referee. This gate makes that
cross-check a PERMANENT, first-class part of the suite.

WHAT IT DOES. For a corpus of (schema, payload, op, direction) the reference actually
uses, run the Rust oracle (schema_oracle.py) AND the independent referee
(dual_oracle_referee.py) and compare VERDICTS. A divergence is one of three valuable
signals — (a) our check is wrong, (b) the Rust oracle is wrong (upstream bug), (c) the
referee is wrong — and every one is worth surfacing. The gate FAILS on any divergence
NOT already diagnosed and filed in conformance/ci/known_oracle_divergences.json.

NORMALIZATION — semantic vs cosmetic (an over-sensitive gate is noise and gets ignored):
  * SEMANTIC (alarms): the boolean verdict (valid vs invalid); and, when both call it
    invalid, WHICH instance path is faulted (the referee's fault-path set). These are
    what a real bug moves.
  * COSMETIC (normalized out, never alarms): human error-message wording, error
    ORDERING, and the number of messages. The Rust oracle emits prose; the referee
    emits jsonschema messages — comparing those would alarm on nothing. So the gate
    compares only (a) the valid/invalid verdict and, for the built-in divergence
    fixtures, (b) the referee's faulted instance-path PREFIX against the acknowledged
    entry. Never message text.

KNOWN-DIVERGENCE ACKNOWLEDGEMENT + SELF-EXPIRY. #43 reproduces on the currently pinned
oracle (we deliberately froze the pin until #44 merges), so the gate WOULD go red on
it. It is instead acknowledged via known_oracle_divergences.json — an entry carrying
the #43/#44 upstream links, tagged to built-in boundary fixtures. Every run the gate
re-reproduces each entry; when the pin advances past #44 and the oracle is rebuilt, the
boundary fixtures AGREE, the entry stops reproducing, and the gate FAILS on the stale
acknowledgement until the entry is deleted (identical discipline to
known_reference_defects.json). This closes the oracle blind-spot follow-up: the
compensating coverage IS this gate.

EXIT CODES (run_suite skip convention): 0 = every check agrees or is acknowledged;
1 = a NEW divergence, or a STALE acknowledgement; 2 = an engine is unavailable (the
Rust binary isn't built, or jsonschema/referencing/the schema base is absent) -> the
suite SKIPs, never a false green.

Usage:
    python3 conformance/selfcheck/validate_dual_oracle.py [--server URL] [-v]
    python3 conformance/selfcheck/validate_dual_oracle.py --selftest   # kill-tests
"""
import sys, os, json, glob, pathlib, importlib, argparse, copy

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "conformance" / "checks"))

VERSION = "2026-04-08"
REGISTER = ROOT / "conformance" / "ci" / "known_oracle_divergences.json"
FIXTURES = HERE / "fixtures" / VERSION


class GateUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The two engines, invoked the same way the real schema checks invoke them.
# ---------------------------------------------------------------------------
def rust_verdict(payload, schema_rel, def_name, op, direction):
    """The Rust oracle's boolean verdict, dispatched exactly like the schema_check
    modules: def_name None -> validate_root; def_name with '/' -> validate_nested_def
    (nested role branch); else validate_against. Raises GateUnavailable via
    OracleUnavailable if the binary/base is absent."""
    from schema_oracle import (validate_against, validate_root, validate_nested_def,
                               OracleUnavailable)
    try:
        if def_name is None:
            ok, _ = validate_root(payload, schema_rel, op=op, version=VERSION,
                                  direction=direction or "response")
        elif "/" in def_name:
            ok, _ = validate_nested_def(payload, schema_rel, def_name, op=op,
                                        version=VERSION)
        else:
            ok, _ = validate_against(payload, schema_rel, def_name, op=op,
                                     version=VERSION, direction=direction)
        return ok
    except OracleUnavailable as e:
        raise GateUnavailable(str(e))


def referee_verdict(referee, payload, schema_rel, def_name, op, direction):
    """The independent referee's (valid, fault_paths) — fault_paths is the sorted set of
    faulted instance-path pointers (the SEMANTIC detail we compare; never message text)."""
    ok, faults = referee.validate(payload, schema_rel, def_name=def_name, op=op,
                                  direction=direction or "response")
    return ok, sorted({p for p, _kw in faults})


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
class Item:
    """One comparison: a payload validated in a specific (schema, def, op, direction)
    mode. expect_divergence is a known-divergence id when the item is a boundary fixture
    that SHOULD diverge; None means the engines must AGREE."""
    __slots__ = ("label", "schema_rel", "def_name", "op", "direction", "payload",
                 "expect_divergence", "fault_prefix")

    def __init__(self, label, schema_rel, def_name, op, direction, payload,
                 expect_divergence=None, fault_prefix=None):
        self.label = label
        self.schema_rel = schema_rel
        self.def_name = def_name
        self.op = op
        self.direction = direction
        self.payload = payload
        self.expect_divergence = expect_divergence
        self.fault_prefix = fault_prefix


def agreement_corpus():
    """The suite's existing 2026-04-08 schema-check fixtures — every valid fixture,
    every negative (defect) fixture and every control, imported from the schema_check
    modules so the corpus stays in sync as the checks evolve. All must AGREE."""
    items = []
    for f in sorted(glob.glob(str(ROOT / "conformance" / "checks" / "schema_check_04_08*.py"))):
        mod = importlib.import_module(pathlib.Path(f).stem)
        for c in getattr(mod, "CHECKS", []) or []:
            items.append(Item(f"{c.id}:valid", c.schema_rel, c.def_name, c.op,
                              c.direction, c.valid))
            for i, n in enumerate(c.negatives):
                items.append(Item(f"{c.id}:neg{i}", c.schema_rel, c.def_name, c.op,
                                  c.direction, n))
            for i, ctrl in enumerate(c.controls):
                items.append(Item(
                    f"{c.id}:ctrl{i}", ctrl[2] if len(ctrl) > 2 else c.schema_rel,
                    ctrl[3] if len(ctrl) > 3 else c.def_name, ctrl[1],
                    ctrl[4] if len(ctrl) > 4 else c.direction, ctrl[0]))
    return items


_ID43 = "ucp-schema-43-selfroot-ref-payment-instrument"


def _valid_checkout():
    return json.loads((FIXTURES / "checkout_response.valid.json").read_text())


def divergence_corpus():
    """The #43 boundary fixtures: a clean-valid checkout that both engines accept
    (proves NO false alarm on the conformant shape), plus the three documented #43
    symptoms that MUST diverge on the pinned buggy oracle. These are the negative
    fixture the gate needs — a divergence detector that has never caught a divergence
    proves nothing."""
    ck = "schemas/shopping/checkout.json"
    def with_instr(instr):
        d = _valid_checkout(); d["payment"] = {"instruments": [instr]}; return d
    valid_instr = {"id": "instr_1", "handler_id": "handler_card_1", "type": "card",
                   "selected": True}
    return [
        # control: a conformant instrument -> BOTH accept (no false alarm)
        Item("dual43.control_valid_instrument", ck, None, "read", "response",
             with_instr(valid_instr)),
        # #43 false-accept: instrument missing every required field
        Item("dual43.false_accept_missing_required", ck, None, "read", "response",
             with_instr({"selected": True}),
             expect_divergence=_ID43, fault_prefix="/payment/instruments/0"),
        # #43 false-accept: required fields present but non-string (base type voided)
        Item("dual43.false_accept_nonstring_fields", ck, None, "read", "response",
             with_instr({"id": 1, "handler_id": 2, "type": 3}),
             expect_divergence=_ID43, fault_prefix="/payment/instruments/0"),
        # #43 false-REJECT: a valid instrument with an extra property named `instruments`
        # (# mis-binds to payment.json, which types that name as an array)
        Item("dual43.false_reject_extra_instruments_prop", ck, None, "read", "response",
             with_instr({"id": "instr_1", "handler_id": "handler_card_1", "type": "card",
                         "instruments": "bogus-string"}),
             expect_divergence=_ID43),
    ]


def golden_corpus(server):
    """Opportunistic: when a live golden is reachable, drive a checkout and run the #43
    probe against the REAL live response (inject a malformed instrument). Contributes
    only when the live checkout is clean-valid on BOTH engines under checkout.json (so a
    self-describing envelope quirk can never masquerade as a divergence); otherwise a
    silent no-op. Never fails the gate by itself."""
    if not server:
        return []
    try:
        import urllib.request
        # Minimal create: POST an empty checkout create to the reference; tolerate any
        # shape. This is best-effort augmentation, guarded below.
        req = urllib.request.Request(server.rstrip("/") + "/checkout",
                                     data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
    except Exception:
        return []
    return [("golden_live_checkout", body)]


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
def load_register():
    data = json.loads(REGISTER.read_text())
    entries = {e["id"]: e for e in data.get("divergences", [])}
    for e in entries.values():
        if not e.get("upstream"):
            raise GateUnavailable(
                f"known_oracle_divergences entry {e.get('id')!r} has no upstream link "
                f"— an entry without one is suppression, not acknowledgement")
    return entries


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def evaluate(items, referee, rust_fn=rust_verdict, known_ids=frozenset()):
    """Run both engines over items. Returns (results, new_divergences, reproduced_ids).
    result = (item, rust_ok, ref_ok, ref_faults, status) where status in
    {agree, acknowledged, new-divergence}. A divergence is `acknowledged` ONLY when the
    item is a boundary fixture whose expect_divergence id is in `known_ids` AND the
    faulted path matches — a tag pointing at a non-existent register entry is treated
    as a NEW divergence (so removing the acknowledgement provably reddens the gate)."""
    results, new_div, reproduced = [], [], set()
    for it in items:
        rust_ok = rust_fn(it.payload, it.schema_rel, it.def_name, it.op, it.direction)
        ref_ok, ref_faults = referee_verdict(referee, it.payload, it.schema_rel,
                                             it.def_name, it.op, it.direction)
        diverged = (rust_ok != ref_ok)
        if not diverged:
            results.append((it, rust_ok, ref_ok, ref_faults, "agree"))
            continue
        # a divergence: acknowledged iff the item is a tagged boundary fixture, its id is
        # a real register entry, and the faulted path matches (semantic normalization:
        # verdict + fault-path prefix; never message text).
        ack = it.expect_divergence
        prefix_ok = (it.fault_prefix is None
                     or any(p.startswith(it.fault_prefix) for p in ref_faults)
                     or ref_ok)   # false-reject case: referee accepts, no fault path
        if ack and ack in known_ids and prefix_ok:
            reproduced.add(ack)
            results.append((it, rust_ok, ref_ok, ref_faults, "acknowledged"))
        else:
            new_div.append((it, rust_ok, ref_ok, ref_faults))
            results.append((it, rust_ok, ref_ok, ref_faults, "new-divergence"))
    return results, new_div, reproduced


def run(server=None, verbose=False):
    """Returns (exit_code, lines)."""
    from dual_oracle_referee import get_referee, available, RefereeUnavailable
    lines = []
    if not available():
        return 2, ["referee unavailable: jsonschema/referencing not importable"]
    try:
        referee = get_referee(VERSION)
        register = load_register()
    except (RefereeUnavailable, GateUnavailable) as e:
        return 2, [f"gate unavailable: {e}"]

    items = agreement_corpus() + divergence_corpus()
    # opportunistic golden augmentation: run the #43 probe on a live checkout body
    for _lbl, body in golden_corpus(server):
        ck = "schemas/shopping/checkout.json"
        base_r = referee_verdict(referee, body, ck, None, "read", "response")
        try:
            base_rust = rust_verdict(body, ck, None, "read", "response")
        except GateUnavailable:
            base_rust = None
        if base_r[0] and base_rust:                     # clean-valid on both -> probe it
            bad = copy.deepcopy(body); bad["payment"] = {"instruments": [{"selected": True}]}
            items.append(Item("dual43.golden_live_probe", ck, None, "read", "response",
                              bad, expect_divergence=_ID43,
                              fault_prefix="/payment/instruments/0"))
            lines.append("golden live checkout ingested for #43 probe")

    try:
        results, new_div, reproduced = evaluate(items, referee,
                                                known_ids=frozenset(register))
    except GateUnavailable as e:
        return 2, [f"engine unavailable mid-run: {e}"]

    agree = sum(1 for *_r, s in results if s == "agree")
    ack = [(it, rust_ok, ref_ok, f) for it, rust_ok, ref_ok, f, s in results
           if s == "acknowledged"]
    lines.append(f"referee: {referee.schema_count} schemas; corpus: {len(items)} payloads")
    lines.append(f"agreements: {agree}  ·  acknowledged divergences: {len(ack)}  ·  "
                 f"NEW divergences: {len(new_div)}")

    # acknowledged divergences (printed loudly, do not fail)
    for it, rust_ok, ref_ok, f in ack:
        e = register[it.expect_divergence]
        lines.append(f"  ACK  {it.label}: rust=valid:{rust_ok} ref=valid:{ref_ok} "
                     f"faults={f[:2]} — {it.expect_divergence} ({e['upstream']})")

    # NEW divergences -> FAIL (each a candidate finding)
    for it, rust_ok, ref_ok, f in new_div:
        who = ("oracle FALSE-ACCEPT (rust=valid, referee=invalid)" if (rust_ok and not ref_ok)
               else "oracle FALSE-REJECT (rust=invalid, referee=valid)" if (not rust_ok and ref_ok)
               else "verdict split")
        hint = ("triage: (a) our check wrong? (b) Rust oracle bug -> file upstream? "
                "(c) referee bug? Compare against the pinned schema at the faulted path.")
        lines.append(f"  ✗ NEW DIVERGENCE  {it.label}")
        lines.append(f"      schema={it.schema_rel} def={it.def_name} op={it.op} "
                     f"dir={it.direction}")
        lines.append(f"      {who}; referee fault paths={f[:5]}")
        lines.append(f"      {hint}")

    # self-expiry: every register entry must still be reproduced by the corpus
    stale = [eid for eid in register if eid not in reproduced]
    for eid in stale:
        e = register[eid]
        lines.append(f"  ✗ STALE ACKNOWLEDGEMENT  {eid}: no corpus payload reproduces "
                     f"this divergence anymore — the pinned oracle likely advanced past "
                     f"the fix ({e.get('fix', e['upstream'])}). Delete this entry from "
                     f"known_oracle_divergences.json.")

    ok = (not new_div) and (not stale)
    lines.append("PASS — every schema check agrees across both oracles "
                 "(known divergences acknowledged)" if ok else "FAIL")
    return (0 if ok else 1), lines


# ---------------------------------------------------------------------------
# Self-tests (kill-tests): the gate must provably catch a PLANTED divergence and a
# STALE acknowledgement, and the referee's lifecycle filter must match the resolver.
# ---------------------------------------------------------------------------
def selftest():
    from dual_oracle_referee import get_referee, available
    if not available():
        print("referee unavailable — skip"); return 2
    try:
        referee = get_referee(VERSION)
        load_register()
    except GateUnavailable as e:
        print(f"gate unavailable — skip: {e}"); return 2

    ok = True

    # (1) KILL-TEST — plant a divergence the register does NOT know about, on an
    #     otherwise-agreeing item, by forcing one engine's verdict to flip. The gate
    #     MUST redden (detect it) rather than pass.
    planted = [Item("planted.agree_then_flip",
                    "schemas/shopping/types/payment_instrument.json", None,
                    "complete", "request",
                    {"id": "i", "handler_id": "h", "type": "card"})]
    def flipped_rust(payload, schema_rel, def_name, op, direction):
        return not referee_verdict(referee, payload, schema_rel, def_name, op, direction)[0]
    _res, new_div, _rep = evaluate(planted, referee, rust_fn=flipped_rust)
    caught = len(new_div) == 1
    print(f"  {'✓' if caught else '✗'} kill-test: planted divergence "
          f"{'CAUGHT (gate reddens)' if caught else 'MISSED (gate is blind!)'}")
    ok = ok and caught

    # (1b) negative control — the SAME planted item WITHOUT the flip must NOT alarm
    #      (the detector is not trigger-happy).
    _r2, nd2, _ = evaluate(planted, referee, rust_fn=rust_verdict)
    quiet = len(nd2) == 0
    print(f"  {'✓' if quiet else '✗'} no-false-alarm control: conformant payload "
          f"{'stays green' if quiet else 'FALSELY alarmed'}")
    ok = ok and quiet

    # (2) SELF-EXPIRY — an acknowledged entry that no longer reproduces must FAIL as
    #     stale. Simulate the post-#44 world: evaluate the #43 boundary with a CORRECT
    #     (fixed) oracle == the referee's own verdict, so the fixtures AGREE and the
    #     entry is not reproduced.
    fixed_rust = lambda p, s, d, o, di: referee_verdict(referee, p, s, d, o, di)[0]
    _res3, nd3, reproduced3 = evaluate(divergence_corpus(), referee, rust_fn=fixed_rust)
    reg = load_register()
    stale = [eid for eid in reg if eid not in reproduced3]
    expired = (_ID43 in stale) and (len(nd3) == 0)
    print(f"  {'✓' if expired else '✗'} self-expiry: on a FIXED oracle the #43 "
          f"acknowledgement {'goes stale (gate would fail until deleted)' if expired else 'did NOT expire'}")
    ok = ok and expired

    # (3) referee lifecycle filter is faithful to the official resolver (independent of
    #     the #43 bundling bug): compare resolved (properties, required) on leaf type
    #     schemas across ops/directions.
    faithful = _lifecycle_matches_resolver(referee)
    print(f"  {'✓' if faithful else '✗'} referee lifecycle filter matches the Rust "
          f"resolver on sampled (schema, op, direction)")
    ok = ok and faithful

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def _lifecycle_matches_resolver(referee):
    """Resolve a leaf type schema (no $ref '#' bundling in play) with the official Rust
    resolver and with the referee's own lifecycle transform; assert the resulting
    required-set matches for every (op, direction). This proves the filter is an honest
    independent reimplementation, not a copy — and that comparisons are apples-to-apples."""
    from schema_oracle import SCHEMA_BASE, _run, OracleUnavailable
    from dual_oracle_referee import _apply_lifecycle
    base = SCHEMA_BASE.get(VERSION)
    targets = ["schemas/shopping/types/fulfillment_method.json",
               "schemas/shopping/types/token_credential.json"]
    for rel in targets:
        raw = json.loads((base / rel).read_text())
        for op in ("create", "update", "complete", "read"):
            for direction in ("request", "response"):
                args = ["resolve", str(base / rel), "--op", op,
                        "--schema-local-base", str(base),
                        "--request" if direction == "request" else "--response"]
                try:
                    r = _run(args)
                except OracleUnavailable:
                    return False
                if r.returncode != 0:
                    continue
                resolved = json.loads(r.stdout)
                # resolver required at the top object (union across allOf branches)
                res_req = set(resolved.get("required", []))
                for b in resolved.get("allOf", []):
                    if isinstance(b, dict):
                        res_req |= set(b.get("required", []) or [])
                mine = copy.deepcopy(raw)
                _apply_lifecycle(mine, op, direction)
                my_req = set(mine.get("required", []))
                for b in mine.get("allOf", []):
                    if isinstance(b, dict):
                        my_req |= set(b.get("required", []) or [])
                if res_req != my_req:
                    print(f"      lifecycle mismatch {rel} op={op} {direction}: "
                          f"resolver={sorted(res_req)} referee={sorted(my_req)}")
                    return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Dual-oracle schema-validation gate.")
    ap.add_argument("--server", default=None, help="optional live golden for a #43 probe")
    ap.add_argument("--selftest", action="store_true", help="run the kill-tests")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    code, lines = run(server=args.server, verbose=args.verbose)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())

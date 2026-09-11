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

VERSIONS (D4-01, B5a). `--version 2026-04-08` (default; output byte-stable) runs the schema_check
fixture corpus over the 78-schema base. `--version 2026-08-25` runs the 116-schema base over
responses CAPTURED in-process from the pinned golden-0825 (selfcheck/fixtures/2026-08-25/, see
capture_golden_0825.py) plus the #43 boundary fixtures rebuilt on the captured completed
checkout, plus the `--def selected_payment_instrument` path where the PINNED Rust build ABORTS
(stack overflow on the `$ref: "#"` self-root, ucp-schema#45): rust_verdict carries a THIRD state,
"crash" (any rc not in {0,1}), a divergence class of its own. PER-VERSION ORACLE (D4-04,
decision 7b): schema_oracle.bin_for(version) routes the 2026-08-25 layout to the merged-main
b52518f5 build (conformance/ci/oracle_manifest.json; contains #66), on which that path
validates and the #43 boundary agrees — both 08-25 acknowledgements were retired and the
items stay as regression watches. Register entries carry `versions` (which corpora must
reproduce them) and `expires_on.schema_validator_pin_not` (the entry FAILS as `STALE
ACKNOWLEDGEMENT (pin moved)` the moment the build serving a listed version is no longer that
pin, independently of the reproduction test).

Usage:
    python3 conformance/selfcheck/validate_dual_oracle.py [--server URL] [-v] [--version V]
    python3 conformance/selfcheck/validate_dual_oracle.py --selftest [--version V]   # kill-tests
"""
import sys, os, json, glob, pathlib, importlib, argparse, copy

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "conformance" / "checks"))

VERSION = "2026-04-08"
REGISTER = ROOT / "conformance" / "ci" / "known_oracle_divergences.json"
LOCK = ROOT / "conformance" / "SOURCES.lock.json"
FIXTURES = HERE / "fixtures" / VERSION
DEFAULT_VERSION = "2026-04-08"


def set_version(version):
    """Thread one spec version through every engine call (the 04-08 code path stays exactly
    as before when version == DEFAULT_VERSION)."""
    global VERSION, FIXTURES
    VERSION = version
    FIXTURES = HERE / "fixtures" / VERSION


class GateUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The two engines, invoked the same way the real schema checks invoke them.
# ---------------------------------------------------------------------------
def rust_verdict(payload, schema_rel, def_name, op, direction):
    """The Rust oracle's boolean verdict, dispatched exactly like the schema_check
    modules: def_name None -> validate_root; def_name with '/' -> validate_nested_def
    (nested role branch); else validate_against. Returns True (valid), False (invalid) or
    "crash" — the oracle exited with a code outside {0,1} (rc 2 resolution error, rc 134 /
    -6 abort on the `$ref: "#"` self-root blind spot, ucp-schema#45): not a verdict, a
    divergence class of its own. Raises GateUnavailable via OracleUnavailable if the
    binary/base is absent."""
    import schema_oracle
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
        if schema_oracle.LAST_RC not in (0, 1):
            return "crash"
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


def _captured_corpus():
    """2026-08-25: responses captured IN-PROCESS from the pinned golden-0825 (fixtures/
    2026-08-25/manifest.json items; capture_golden_0825.py). Each is validated in the mode
    the manifest names (root = --schema without --def). All must AGREE (and be valid)."""
    man = json.loads((FIXTURES / "manifest.json").read_text())
    items = []
    for it in man["items"]:
        payload = json.loads((FIXTURES / it["file"]).read_text())
        def_name = None if it.get("mode", "root") == "root" else it["def"]
        items.append(Item(f"captured:{it['file']}", it["schema_rel"], def_name, it["op"],
                          it["direction"], payload))
    return items


def agreement_corpus():
    """The suite's existing 2026-04-08 schema-check fixtures — every valid fixture,
    every negative (defect) fixture and every control, imported from the schema_check
    modules so the corpus stays in sync as the checks evolve. All must AGREE. At other
    versions: the captured golden responses (see _captured_corpus)."""
    if VERSION != DEFAULT_VERSION:
        return _captured_corpus()
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
_ID45 = "ucp-schema-45-selfroot-def-crash"
_PI_REL = {"2026-04-08": "schemas/shopping/types/payment_instrument.json",
           "2026-08-25": "schemas/common/types/payment_instrument.json"}


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
    ] + crash_corpus()


def crash_corpus():
    """The `--def` self-root blind spot (ucp-schema#45/#46, fix #66): on the PINNED build,
    `--def selected_payment_instrument` on the 08-25 payment_instrument.json (allOf[0] is
    {"$ref": "#"}) ABORTS with a stack overflow while the referee validates it. Since D4-04
    the 2026-08-25 layout runs the per-version b52518f5 build (contains #66): this item now
    AGREES (valid on both engines) and stays in the corpus as the REGRESSION WATCH for that
    fix — a crash here is a NEW divergence (the #45 acknowledgement was retired). Empty at
    04-08: no 04-08 check calls --def on that file (the pinned build aborts there too; the
    pointer is the manifest's known_blind_spots entry, asserted unreached by
    validate_schema_oracle_manifest.py)."""
    if VERSION != "2026-08-25":
        return []
    return [Item("dual45.def_selected_payment_instrument_regression_watch", _PI_REL[VERSION],
                 "selected_payment_instrument", "complete", "request",
                 {"id": "instr_1", "handler_id": "handler_card_1", "type": "card",
                  "selected": True})]


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
# PYDANTIC third leg (D4-05 / B5b): the python-sdk generated models, per SDK cut, in their
# own venvs (pydantic_leg.py; conformance/ci/make_sdk_venvs.sh). The TAG cut (PyPI latest)
# is GATED: a tag-vs-referee disagreement must be an acknowledged entry of
# conformance/ci/known_sdk_drops.json (self-expiring on `pypi_ucp_sdk_gt`); the MAIN cut is
# REPORT-ONLY. The zod leg (conformance/ci/zod_feed.mjs) is report-only too.
# ---------------------------------------------------------------------------
DROPS = ROOT / "conformance" / "ci" / "known_sdk_drops.json"
ZOD_FEED = ROOT / "conformance" / "ci" / "zod_feed.mjs"
ZOD_MAP = {
    ("schemas/shopping/checkout.json", None): "CheckoutResponseSchema",
    ("schemas/shopping/order.json", None): "OrderSchema",
    ("schemas/profile.json", "jwk_public_key"): "SigningKeySchema",
    ("schemas/common/types/unit.json", None): "UnitSchema",
    ("schemas/common/types/time_interval.json", None): "TimeIntervalSchema",
    ("schemas/common/types/location_serves.json", None): "LocationServesSchema",
    ("schemas/shopping/types/fulfillment_method.json", None): "FulfillmentMethodSchema",
    ("schemas/common/types/payment_instrument.json", "selected_payment_instrument"): "SelectedPaymentInstrumentSchema",
}


def drops_corpus():
    """The L4 N24/N25 probe families at 2026-08-25: each drop payload (the referee rejects it;
    the tag SDK accepts it — tagged with its known_sdk_drops id) next to a CONTROL the same
    model must accept (no false alarm). Empty at other versions."""
    if VERSION != "2026-08-25":
        return []
    P, U, T, L, F = ("schemas/profile.json", "schemas/common/types/unit.json",
                     "schemas/common/types/time_interval.json", "schemas/common/types/location_serves.json",
                     "schemas/shopping/types/fulfillment_method.json")
    fm = {"id": "m1", "type": "shipping", "line_item_ids": []}
    return [
        Item("drop.jwk_no_crv", P, "jwk_public_key", "read", "response",
             {"kty": "EC", "kid": "k1", "x": "AAAA", "y": "BBBB"}, expect_divergence="sdk-drop-jwk-no-crv"),
        Item("drop.jwk_alg_mismatch", P, "jwk_public_key", "read", "response",
             {"kty": "EC", "kid": "k1", "crv": "P-256", "x": "AAAA", "y": "BBBB", "alg": "ES384"},
             expect_divergence="sdk-drop-jwk-alg-mismatch"),
        Item("control.jwk_valid", P, "jwk_public_key", "read", "response",
             {"kty": "EC", "kid": "k1", "crv": "P-256", "x": "AAAA", "y": "BBBB", "alg": "ES256"}),
        Item("drop.unit_c62_scale2", U, None, "read", "response",
             {"unit": "C62", "scale": 2, "display_text": "each"}, expect_divergence="sdk-drop-unit-c62-scale"),
        Item("control.unit_valid", U, None, "read", "response", {"unit": "C62", "scale": 0, "display_text": "each"}),
        Item("drop.ti_opens_only", T, None, "read", "response", {"opens": "09:00"},
             expect_divergence="sdk-drop-time-interval-dependent-required"),
        Item("control.ti_valid", T, None, "read", "response", {"opens": "09:00", "closes": "17:00"}),
        Item("drop.ls_two_props", L, None, "read", "response",
             {"address": {"address_country": "US"}, "point": {"latitude": 1.0, "longitude": 2.0}},
             expect_divergence="sdk-drop-location-serves-max-properties"),
        Item("control.ls_valid", L, None, "read", "response", {"address": {"address_country": "US"}}),
        Item("drop.fm_dest_unknown_type", F, None, "read", "response",
             dict(fm, destinations=[{"id": "d1", "type": "teleport"}]),
             expect_divergence="sdk-drop-fulfillment-destination-unknown-type"),
        Item("drop.fm_dest_wrong_shape", F, None, "read", "response",
             dict(fm, destinations=[{"id": "d1", "type": "shipping_address", "address_country": 5}]),
             expect_divergence="sdk-drop-fulfillment-destination-nested-retyping"),
        Item("control.fm_dest_valid", F, None, "read", "response",
             dict(fm, destinations=[{"id": "d1", "type": "shipping_address", "address_country": "US"}])),
    ]


def load_drops():
    d = json.loads(DROPS.read_text())
    entries = {e["id"]: e for e in d.get("drops", []) if VERSION in (e.get("versions") or [VERSION])}
    for e in entries.values():
        if not e.get("upstream"):
            raise GateUnavailable(f"known_sdk_drops entry {e['id']!r} has no upstream link — suppression, not acknowledgement")
    return d.get("sdk") or {}, entries


def _ver(v):
    return tuple(int(x) for x in str(v).split("."))


def drops_expired(entries, tag_version):
    """[(id, threshold)] — entries whose `expires_on.pypi_ucp_sdk_gt` the tag venv's ucp-sdk
    version already exceeds: the next PyPI cut carries the fix, re-derive or delete."""
    out = []
    for eid, e in entries.items():
        thr = (e.get("expires_on") or {}).get("pypi_ucp_sdk_gt")
        if thr and _ver(tag_version) > _ver(thr):
            out.append((eid, thr))
    return out


def evaluate_leg(items, referee, leg_verdicts, known_ids):
    """Compare one SDK leg's verdicts ({label: (ok, faults)}) to the referee. Returns
    (results, new_divergences, reproduced_ids, not_judged) with result =
    (item, leg_ok, ref_ok, ref_faults, status) status in {agree, acknowledged, new-divergence,
    not-judged}. Acknowledged iff the item carries a known drop id present in known_ids."""
    results, new_div, reproduced, nj = [], [], set(), 0
    for it in items:
        if it.label not in leg_verdicts:
            nj += 1
            results.append((it, None, None, [], "not-judged")); continue
        leg_ok, _f = leg_verdicts[it.label]
        ref_ok, ref_faults = referee_verdict(referee, it.payload, it.schema_rel, it.def_name, it.op, it.direction)
        if leg_ok == ref_ok:
            results.append((it, leg_ok, ref_ok, ref_faults, "agree")); continue
        ack = it.expect_divergence
        if ack and ack in known_ids:
            reproduced.add(ack)
            results.append((it, leg_ok, ref_ok, ref_faults, "acknowledged"))
        else:
            new_div.append((it, leg_ok, ref_ok, ref_faults))
            results.append((it, leg_ok, ref_ok, ref_faults, "new-divergence"))
    return results, new_div, reproduced, nj


def _leg_requests(items):
    import pydantic_leg as pl
    reqs = []
    for it in items:
        m = pl.model_for(it.schema_rel, it.def_name, it.op, it.direction)
        if m:
            reqs.append((it.label, m, it.payload))
    return reqs


def zod_report(items, referee):
    """Run the zod leg (node + pinned @ucp-js/sdk) over the mapped items; returns
    (rows_written, path) or (None, reason). Report-only; never fails the gate."""
    import shutil, subprocess, tempfile
    node = shutil.which("node")
    pkg = ROOT / "conformance" / "ci" / "zod_feed" / "node_modules" / "@ucp-js" / "sdk"
    if not node or not pkg.exists() or not ZOD_FEED.exists():
        return None, "not run (node + `npm install` in conformance/ci/zod_feed required)"
    payload = []
    for it in items:
        z = ZOD_MAP.get((it.schema_rel, it.def_name))
        if not z:
            continue
        ref_ok, _ = referee_verdict(referee, it.payload, it.schema_rel, it.def_name, it.op, it.direction)
        payload.append({"label": it.label, "zod": z, "payload": it.payload, "referee_ok": ref_ok})
    ops_feeds = ROOT / "ops" / "feeds"
    if (ROOT / "ops").is_dir():
        ops_feeds.mkdir(parents=True, exist_ok=True)
        out = ops_feeds / "zod_divergences.json"
    else:
        rd = os.environ.get("RUN_SUITE_RECORD_DIR") or tempfile.mkdtemp(prefix="zod_feed_")
        out = pathlib.Path(rd) / "zod_divergences.json"
    fd, inp = tempfile.mkstemp(suffix=".json"); os.close(fd)
    pathlib.Path(inp).write_text(json.dumps(payload))
    try:
        r = subprocess.run([node, str(ZOD_FEED), "--in", inp, "--out", str(out)],
                           capture_output=True, text=True, cwd=str(ROOT / "conformance" / "ci" / "zod_feed"))
    finally:
        os.unlink(inp)
    if r.returncode != 0:
        return None, f"zod leg failed: {(r.stderr or r.stdout)[-160:]}"
    doc = json.loads(out.read_text())
    return len(doc["rows"]), str(out.relative_to(ROOT) if str(out).startswith(str(ROOT)) else out)


def run_pydantic(referee, items, tag_leg=None, main_leg=None, drops=None, with_zod=True):
    """The third column. Returns (rc, lines): 0 all agree/acknowledged, 1 a NEW tag drop,
    a STALE or EXPIRED acknowledgement, 2 a leg unavailable (never green)."""
    import pydantic_leg as pl
    tag_leg = tag_leg or pl.PydanticLeg(pl.TAG, "pydantic-tag")
    main_leg = main_leg or pl.PydanticLeg(pl.MAIN, "pydantic-main")
    lines = []
    try:
        sdk, entries = (drops if drops is not None else load_drops())
        tag_v = tag_leg.version()
        if sdk.get("pypi_pin") and tag_v != sdk["pypi_pin"]:
            return 2, [f"pydantic-tag: venv resolves ucp-sdk {tag_v} but known_sdk_drops.json pins "
                       f"{sdk['pypi_pin']} — SDK PIN DRIFT, rebuild (make_sdk_venvs.sh)"]
        reqs = _leg_requests(items)
        tag_res = tag_leg.validate_many(reqs)
        try:
            main_v = main_leg.version(); main_res = main_leg.validate_many(reqs)
        except pl.LegUnavailable as e:
            main_v, main_res = None, {}
            lines.append(f"  · pydantic-main unavailable ({e}) — report-only leg skipped")
    except (pl.LegUnavailable, GateUnavailable) as e:
        return 2, [f"pydantic leg unavailable: {e}"]
    res, new_div, reproduced, nj = evaluate_leg(items, referee, tag_res, entries)
    for it, leg_ok, ref_ok, f, st in res:
        if st == "acknowledged":
            lines.append(f"  ACK  pydantic-tag {it.label}: sdk=valid:{leg_ok} ref=valid:{ref_ok} faults={f[:2]} — {it.expect_divergence}")
    for it, leg_ok, ref_ok, f in new_div:
        lines.append(f"  ✗ NEW SDK DIVERGENCE  pydantic-tag {it.label}: sdk=valid:{leg_ok} ref=valid:{ref_ok} faults={f[:3]} — "
                     f"acknowledge in known_sdk_drops.json with an upstream link, or fix the model map")
    stale = [eid for eid in entries if eid not in reproduced]
    for eid in stale:
        lines.append(f"  ✗ STALE SDK DROP  {eid}: the tag SDK no longer reproduces it — delete or re-derive")
    expired = drops_expired(entries, tag_v)
    for eid, thr in expired:
        lines.append(f"  ✗ EXPIRED SDK DROP  {eid}: acknowledged for ucp-sdk <= {thr} but the tag venv is {tag_v} — re-derive on the new cut")
    main_div = 0
    if main_res:
        mres, mnew, _r, _nj = evaluate_leg(items, referee, main_res, set())
        for it, leg_ok, ref_ok, f in mnew:
            main_div += 1
            lines.append(f"  REPORT pydantic-main {it.label}: sdk=valid:{leg_ok} ref=valid:{ref_ok} faults={f[:3]} (report-only, ledger candidate)")
    zod_rows, zod_where = zod_report(items, referee) if with_zod else (None, "skipped")
    ok = not new_div and not stale and not expired
    agree = sum(1 for r in res if r[4] == "agree")
    lines.append(f"pydantic-tag {tag_v}: agree {agree} · acknowledged drops {len(reproduced)} · NEW {len(new_div)} · "
                 f"stale {len(stale)} · expired {len(expired)} · not-judged {nj}")
    lines.append(f"3 engines · {len(new_div)} unexplained divergence · pydantic-tag: {len(reproduced)} acknowledged drops "
                 f"(expire on PyPI > {sdk.get('pypi_pin', '?')}) · pydantic-main: {main_div}"
                 + (f" ({main_v}, report-only)" if main_v else "")
                 + (f" · zod: report-only, {zod_rows} rows written to {zod_where}" if zod_rows is not None
                    else f" · zod: {zod_where}"))
    return (0 if ok else 1), lines


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
def load_register(version=None):
    """Entries that apply at `version` (an entry's `versions` list; absent = every version)."""
    version = version or VERSION
    data = json.loads(REGISTER.read_text())
    entries = {e["id"]: e for e in data.get("divergences", [])
               if version in (e.get("versions") or [version])}
    for e in entries.values():
        if not e.get("upstream"):
            raise GateUnavailable(
                f"known_oracle_divergences entry {e.get('id')!r} has no upstream link "
                f"— an entry without one is suppression, not acknowledgement")
    return entries


def oracle_pin(lock=LOCK, version=None):
    """The commit of the ucp-schema build serving `version` (default: the gate's VERSION):
    conformance/ci/oracle_manifest.json per_version (D4-04, decision 7b) — whose 2026-04-08
    entry must equal SOURCES.lock schema_validator.commit (validate_schema_oracle_manifest.py
    asserts it) — with the lock as the fallback when the manifest is absent."""
    version = version or VERSION
    try:
        import schema_oracle
        return schema_oracle.manifest_entry(version)["commit"]
    except Exception:  # noqa: BLE001 — manifest absent/unknown version: the lock's pinned build
        pass
    try:
        return json.loads(pathlib.Path(lock).read_text())["schema_validator"]["commit"]
    except Exception as e:  # noqa: BLE001
        raise GateUnavailable(f"SOURCES.lock schema_validator.commit unreadable: {e}")


def stale_by_pin(register, pin):
    """[(id, expected_pin, actual_pin)] for entries whose `expires_on.schema_validator_pin_not`
    no longer equals the pinned oracle commit: the acknowledgement was made against THAT
    buggy pin and must be re-derived (deleted or re-pinned) the moment the pin moves —
    independently of whether the corpus still reproduces the divergence."""
    out = []
    for eid, e in register.items():
        want = (e.get("expires_on") or {}).get("schema_validator_pin_not")
        if want and want != pin:
            out.append((eid, want, pin))
    return out


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
        diverged = (rust_ok != ref_ok)          # "crash" never equals a bool: always a divergence
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
        if rust_ok == "crash":
            # a crash carries no fault path; the class must match the acknowledgement
            prefix_ok = ack in known_ids and known_ids_class(known_ids, ack) == "oracle-crash"
        if ack and ack in known_ids and prefix_ok:
            reproduced.add(ack)
            results.append((it, rust_ok, ref_ok, ref_faults, "acknowledged"))
        else:
            new_div.append((it, rust_ok, ref_ok, ref_faults))
            results.append((it, rust_ok, ref_ok, ref_faults, "new-divergence"))
    return results, new_div, reproduced


def known_ids_class(known_ids, ack):
    """The `class` of a register entry (default "verdict"); known_ids may be a plain set of
    ids (class unknown -> "verdict") or a dict id -> entry."""
    if isinstance(known_ids, dict):
        return (known_ids.get(ack) or {}).get("class", "verdict")
    return "verdict"


def run(server=None, verbose=False, pydantic=False):
    """Returns (exit_code, lines). pydantic=True adds the third column (08-25 only)."""
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
        results, new_div, reproduced = evaluate(items, referee, known_ids=register)
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
        who = ("oracle CRASH (rc not in {0,1}; referee=valid:%s)" % ref_ok if rust_ok == "crash"
               else "oracle FALSE-ACCEPT (rust=valid, referee=invalid)" if (rust_ok and not ref_ok)
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

    # pin-expiry: an acknowledgement made against a specific buggy pin dies with that pin
    try:
        moved = stale_by_pin(register, oracle_pin())
    except GateUnavailable as e:
        return 2, lines + [f"gate unavailable: {e}"]
    for eid, want, have in moved:
        lines.append(f"  ✗ STALE ACKNOWLEDGEMENT (pin moved)  {eid}: acknowledged against "
                     f"schema_validator {want[:12]} but SOURCES.lock now pins {have[:12]} — "
                     f"re-derive on the new oracle, then delete or re-pin this entry.")

    ok = (not new_div) and (not stale) and (not moved)
    if pydantic:
        prc, plines = run_pydantic(referee, items + drops_corpus())
        lines += plines
        if prc == 2:
            return 2, lines
        ok = ok and prc == 0
    if VERSION != DEFAULT_VERSION:
        lines.append(f"referee base {VERSION}: {referee.schema_count} schemas · corpus: "
                     f"{len(items)} payloads · agreements {agree} · acknowledged divergences: "
                     f"{len(reproduced)} ({', '.join(sorted(reproduced)) or 'none'}; expire on "
                     f"oracle re-pin) · NEW divergences: {len(new_div)} · "
                     f"{'PASS' if ok else 'FAIL'}")
    lines.append("PASS — every schema check agrees across both oracles "
                 "(known divergences acknowledged)" if ok else "FAIL")
    return (0 if ok else 1), lines


# ---------------------------------------------------------------------------
# Self-tests (kill-tests): the gate must provably catch a PLANTED divergence and a
# STALE acknowledgement, and the referee's lifecycle filter must match the resolver.
# ---------------------------------------------------------------------------
def selftest(pydantic=False):
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
                    _PI_REL[VERSION], None,
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
    if VERSION == DEFAULT_VERSION:
        stale = [eid for eid in reg if eid not in reproduced3]
        expired = (_ID43 in stale) and (len(nd3) == 0)
        print(f"  {'✓' if expired else '✗'} self-expiry: on a FIXED oracle the #43 "
              f"acknowledgement {'goes stale (gate would fail until deleted)' if expired else 'did NOT expire'}")
    else:
        # no acknowledgement is live at this version (D4-04 retired them on the per-version
        # build): plant one and prove the corpus does not reproduce it -> STALE
        planted_reg = dict(reg, **{"planted-ack": {"id": "planted-ack", "upstream": "x", "class": "verdict"}})
        stale = [eid for eid in planted_reg if eid not in reproduced3]
        expired = ("planted-ack" in stale) and (len(nd3) == 0)
        print(f"  {'✓' if expired else '✗'} self-expiry: a planted acknowledgement no corpus item "
              f"reproduces {'goes stale (gate would fail until deleted)' if expired else 'did NOT expire'}")
    ok = ok and expired

    # (3) referee lifecycle filter is faithful to the official resolver (independent of
    #     the #43 bundling bug): compare resolved (properties, required) on leaf type
    #     schemas across ops/directions.
    faithful = _lifecycle_matches_resolver(referee)
    print(f"  {'✓' if faithful else '✗'} referee lifecycle filter matches the Rust "
          f"resolver on sampled (schema, op, direction)")
    ok = ok and faithful

    if VERSION != DEFAULT_VERSION:
        # (5) the versioned base + the PER-VERSION oracle (D4-04: the merged-main b52518f5
        #     build, which contains #66) over the boundary fixtures: the #43 boundary AGREES
        #     (three verdict items), the --def self-root path VALIDATES (no crash); nothing
        #     NEW and no acknowledgement live at this version (both retired).
        res5, nd5, rep5 = evaluate(divergence_corpus(), referee, known_ids=reg)
        crash_items = [r for r in res5 if r[1] == "crash"]
        agreed = [r for r in res5 if r[4] == "agree"]
        watch = [r for r in res5 if r[0].label.startswith("dual45.") and r[4] == "agree" and r[1] is True]
        case5 = (referee.schema_count == 116 and len(nd5) == 0 and len(crash_items) == 0
                 and len(agreed) == len(res5) == 5 and len(watch) == 1 and len(reg) == 0)
        print(f"  {'✓' if case5 else '✗'} case 5: referee base {VERSION} loads "
              f"{referee.schema_count} schemas; per-version oracle {oracle_pin()[:8]}: #43 boundary "
              f"+ control agree ×{len(agreed) - len(watch)} (fixed by #66), --def selected_payment_instrument "
              f"validates (#45 fixed, crash ×{len(crash_items)}); NEW {len(nd5)}; "
              f"acknowledged divergences at {VERSION}: {len(reg)}")
        ok = ok and case5
        # (6) pin-expiry kill-test over the WHOLE register (every version): a moved pin must
        #     flag every pinned entry STALE; the real per-version pins flag none.
        allreg = {e["id"]: e for e in json.loads(REGISTER.read_text())["divergences"]}
        moved = stale_by_pin(allreg, "0000000000000000000000000000000000000000")
        pinned = {eid for eid in allreg if (allreg[eid].get("expires_on") or {}).get("schema_validator_pin_not")}
        real_ok = all(not stale_by_pin({eid: e}, oracle_pin(version=v))
                      for eid, e in allreg.items() for v in (e.get("versions") or [DEFAULT_VERSION]))
        case6 = {m[0] for m in moved} == pinned and pinned == set(allreg) and len(allreg) >= 1 and real_ok
        print(f"  {'✓' if case6 else '✗'} case 6: pin-expiry — every register entry ({len(allreg)}) carries "
              f"expires_on.schema_validator_pin_not; a moved pin flags all {len(moved)} as "
              f"STALE (pin moved); the real per-version pins flag none")
        ok = ok and case6

    if pydantic and VERSION == "2026-08-25":
        import pydantic_leg as pl
        tag = pl.PydanticLeg(pl.TAG, "pydantic-tag")
        if not tag.available():
            print("pydantic-tag venv not built — skip (conformance/ci/make_sdk_venvs.sh)"); return 2
        sdk, entries = load_drops()
        drops = drops_corpus()
        # (7) the #43 false-accept payload, judged by the pydantic-tag leg ALONE (no referee):
        #     the SDK model rejects what the pinned Rust oracle false-accepted
        ck = "schemas/shopping/checkout.json"
        bad = _valid_checkout(); bad["payment"] = {"instruments": [{"selected": True}]}
        t_ok, t_f = tag.validate(bad, pl.model_for(ck, None, "read", "response"))
        case7 = (t_ok is False) and any(f.startswith("/payment/instruments/0") for f in t_f)
        print(f"  {'✓' if case7 else '✗'} case 7: #43 payload, referee disabled — the pydantic-tag leg alone "
              f"{'reds' if case7 else 'did NOT red'} (faults {t_f[:2]})")
        ok = ok and case7
        # (8) JWK without crv: tag accepts, referee rejects -> acknowledged by its drop entry
        jwk = [it for it in drops if it.label == "drop.jwk_no_crv"]
        res8, nd8, rep8, _ = evaluate_leg(jwk, referee, tag.validate_many(_leg_requests(jwk)), entries)
        case8 = res8 and res8[0][4] == "acknowledged" and res8[0][1] is True and res8[0][2] is False
        print(f"  {'✓' if case8 else '✗'} case 8: JWK without crv — tag accepts, referee rejects -> "
              f"{'acknowledged (sdk-drop-jwk-no-crv)' if case8 else 'NOT acknowledged'}")
        ok = ok and case8
        # (9) delete the entry -> the same item is a NEW divergence (gate reds)
        fewer = {k: v for k, v in entries.items() if k != "sdk-drop-jwk-no-crv"}
        _r9, nd9, _rep9, _ = evaluate_leg(jwk, referee, tag.validate_many(_leg_requests(jwk)), fewer)
        case9 = len(nd9) == 1
        print(f"  {'✓' if case9 else '✗'} case 9: entry deleted -> {'NEW divergence (gate reds)' if case9 else 'still quiet (blind!)'}")
        ok = ok and case9
        # (10) offline venv -> rc 2, never green
        rc10, l10 = run_pydantic(referee, jwk, tag_leg=pl.PydanticLeg(ROOT / "nope-venv", "pydantic-tag"),
                                 main_leg=pl.PydanticLeg(ROOT / "nope-venv", "pydantic-main"), drops=(sdk, entries), with_zod=False)
        case10 = rc10 == 2
        print(f"  {'✓' if case10 else '✗'} case 10: offline venv -> rc {rc10} ({'skip, never green' if case10 else 'WRONG'})")
        ok = ok and case10
        # (11) expiry kill-test: threshold 0.4.9 -> every entry EXPIRED; the real threshold -> none
        low = {k: dict(v, expires_on={"pypi_ucp_sdk_gt": "0.4.9"}) for k, v in entries.items()}
        case11 = len(drops_expired(low, tag.version())) == len(entries) >= 1 and not drops_expired(entries, tag.version())
        print(f"  {'✓' if case11 else '✗'} case 11: pypi_ucp_sdk_gt 0.4.9 -> {len(drops_expired(low, tag.version()))}/{len(entries)} "
              f"EXPIRED; real threshold -> {len(drops_expired(entries, tag.version()))}")
        ok = ok and case11
        # (12) controls agree on the tag leg (no false alarm) + an unmapped item is not-judged
        ctrl = [it for it in drops if it.label.startswith("control.")]
        res12, nd12, _r, _ = evaluate_leg(ctrl, referee, tag.validate_many(_leg_requests(ctrl)), entries)
        unm = [Item("unmapped", "schemas/nope.json", None, "read", "response", {})]
        _r13, _n13, _rr, nj13 = evaluate_leg(unm, referee, {}, entries)
        case12 = all(r[4] == "agree" for r in res12) and len(nd12) == 0 and nj13 == 1
        print(f"  {'✓' if case12 else '✗'} case 12: {len(ctrl)} controls agree on the tag leg · unmapped item -> not-judged {nj13}")
        ok = ok and case12
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
               ("schemas/common/types/token_credential.json" if VERSION == "2026-08-25"
                else "schemas/shopping/types/token_credential.json")]   # moved at 08-25 (#723)
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
    ap.add_argument("--version", default=DEFAULT_VERSION, choices=["2026-04-08", "2026-08-25"],
                    help="spec version / schema base to run (default 2026-04-08)")
    ap.add_argument("--pydantic", action="store_true",
                    help="add the python-sdk pydantic third leg (2026-08-25 only; D4-05): tag cut gated via "
                         "known_sdk_drops.json, main cut report-only, zod feed report-only")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    set_version(args.version)
    if args.pydantic and args.version != "2026-08-25":
        ap.error("--pydantic requires --version 2026-08-25 (ucp-sdk 0.5.0 models are the 08-25 cut)")
    if args.selftest:
        return selftest(pydantic=args.pydantic)
    code, lines = run(server=args.server, verbose=args.verbose, pydantic=args.pydantic)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())

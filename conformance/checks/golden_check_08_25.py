#!/usr/bin/env python3
"""
golden_check_08_25.py — P3 wave 2: 2026-08-25 register rows that can ONLY be graded
against a LIVE golden-0825 server (signatures/keys, payment-credential shapes,
fulfillment open-vocabulary + destinations, buyer-consent Purpose/Segment shape) —
unblocked by the R11 defect-injection battery landing (server/defects_config.json,
conformance/selfcheck/validate_golden_0825_battery.py).

This is a SIBLING of struct_check_08_25.py, not an extension of it: struct_check
covers rows checkable WITHOUT the golden and WITHOUT the schema-oracle CLI (pure
algorithms); this file covers rows whose only honest evidence is the golden's
actual served wire behavior, corroborated by the same official ucp-schema oracle
the R11 battery uses wherever the schema itself enforces the rule.

EVIDENCE CLASS (PLAN-0825 §B/§E; no 08-25 check may claim live-wire — the golden
is OUR OWN adaptation, not an independently-authored server, so the honest ceiling
is fixture-schema, and anything the oracle cannot see is self-referenced):

  * fixture-schema — the check's CLEAN/DEVIATION verdict is the official ucp-schema
    oracle's own verdict on the golden's real wire response (schema_oracle.py,
    same binary + pin the R11 battery and every other selfcheck gate use). SIG-007,
    FUL-003, FUL-007, FUL-008, FUL-030, CNST-001, CNST-003.
  * self-referenced — the violated rule is NOT a JSON-Schema constraint the
    released corpus can express (the schema permits the shape either way; the MUST
    is conditional/behavioral prose), so THIS check's own predicate is the only
    judge. Both cases here were empirically verified against the pinned oracle
    before being classed this way (not assumed): SIG-008 (profile.json's `keys` is
    OPTIONAL, so an empty/absent keys[] is schema-valid even though the prose MUST
    requires it once a profile actually publishes a key — see R14/R8 in
    ops/GAP-LEDGER-0825.md) and PAY-011/PAY-012 (injecting a leaked credential.token
    into a real, otherwise-valid complete_checkout response still validates clean —
    `ucp_response: omit` is a codegen/docs directive, not a keyword that becomes
    `additionalProperties: false` anywhere in this corpus).

KILL-PROOF: every row below arms a NAMED mutant from
server/defects_config.json (either the shared "mutants" array, oracle-graded and
also exercised by the standing R11 report gate, or the new "self_referenced_mutants"
array, graded only here — see that file's own $comment) against a SINGLE hot-reloaded
golden-0825 instance (own port, 8198 — battery owns 8199, selftest.sh's main golden
owns 8182; never touches either), proving CLEAN -> arm -> DEVIATION -> disarm ->
CLEAN again (RESTORED) per row, exactly the R11 battery's FIRED/CAUGHT/RESTORED
discipline, just judged by this file's predicates instead of validate_golden_0825_
battery.py's generic run_oracle() dispatch.

Rows converted (register: conformance/requirements/2026-08-25/):
  SIG-007  Public keys MUST be RFC 7517 JWK              (fixture-schema)
  SIG-008  Public keys published at the canonical         (self-referenced)
           top-level keys[] location
  PAY-011  Credentials MUST NOT be echoed in responses     (self-referenced)
  PAY-012  Token credential value is omit-on-response      (self-referenced)
  FUL-003  fulfillment_method MUST include id/type/        (fixture-schema)
           line_item_ids (open vocabulary on `type` —
           positively demonstrated, not just un-asserted)
  FUL-007  fulfillment_group MUST include id/              (fixture-schema)
           line_item_ids
  FUL-008  fulfillment_option MUST include id/title/       (fixture-schema)
           totals
  FUL-030  shipping_destination MUST include id and a      (fixture-schema)
           const "shipping_address" type discriminator
  CNST-001 consent_purpose MUST include granted/source/    (fixture-schema)
           description (an OBJECT, never a bare boolean)
  CNST-003 consent_purpose.source MUST be exactly          (fixture-schema)
           "business" or "platform"

Rows explicitly LEFT BLOCKED this wave (one-line reasons; never a vacuous check —
see the module-level BLOCKED list at the bottom and the lane report for the
full evidence trail):
  SIG-044..049 (WBA-shape), SIG-050 (EdDSA signature encoding),
  PAY-020/021/022/024 + pan_credential/network_token_credential successors.

Wiring: run_suite.py gate "golden-check-08-25" (hermetic — boots its own
golden-0825, own port, no dependency on an already-running --server or on 8182).
Deliberately NOT wired into checkset_manifest.py / matrix.py / coverage export /
REGISTER_ONLY_VERSIONS: per the lane brief, unattributed pending the owner-visible
coverage/site flip, exactly struct_check_08_25.py's precedent.

Run:  python3 conformance/checks/golden_check_08_25.py
Exit 0 = every row clean-pass + kill-safe; 1 = a row failed or a mutant survived;
2 = the golden could not boot / the oracle binary or vendor tree is unavailable
(honest skip, mirrors every other oracle-backed gate in this suite).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from collections import namedtuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
SELF = ROOT / "conformance" / "selfcheck"
GOLDEN_DIR = ROOT / "conformance" / "testbed" / "golden-0825"
SERVER_DIR = GOLDEN_DIR / "server"
DEFECTS_CONFIG = SERVER_DIR / "defects_config.json"

sys.path.insert(0, str(SELF))
import schema_oracle as so  # noqa: E402

sys.path.insert(0, str(SERVER_DIR))
import defects  # noqa: E402  (arm/disarm via defects.write_state — same primitive
                              # validate_golden_0825_battery.py uses, not reimplemented)

VERSION = "2026-08-25"
VERSIONS = ("2026-08-25",)  # matrix.py / verify_citations.py scope marker — see
                            # struct_check_08_25.py's own docstring for why this
                            # matters (a filename-only version-token fallback would
                            # otherwise default this file to ALL four pinned versions)

PORT = int(os.environ.get("GOLDEN_CHECK_08_25_PORT", "8198"))
BASE = f"http://localhost:{PORT}"
SIM_SECRET = "golden-check-08-25-secret"


class OracleUnavailable(RuntimeError):
    pass


def _require_oracle():
    base = so.SCHEMA_BASE.get(VERSION)
    if base is None or not base.exists():
        raise OracleUnavailable(f"conformance/.vendor/ucp-{VERSION} not fetched")
    if not so.BIN.exists():
        raise OracleUnavailable(f"ucp-schema validator not built at {so.BIN}")


# ---------------------------------------------------------------------------
# server lifecycle (mirrors validate_golden_0825_battery.py's Golden class)
# ---------------------------------------------------------------------------


class Golden:
    def __init__(self, db_dir, defects_config, state_file):
        self.db_dir = pathlib.Path(db_dir)
        self.defects_config = defects_config
        self.state_file = state_file

    def start(self):
        env = dict(os.environ)
        env["PORT"] = str(PORT)
        env["DB_DIR"] = str(self.db_dir)
        env["SIM_SECRET"] = SIM_SECRET
        env["DEFECTS_CONFIG"] = str(self.defects_config)
        env["DEFECTS_STATE_FILE"] = str(self.state_file)
        serve = GOLDEN_DIR / "serve_golden_0825.sh"
        result = subprocess.run([str(serve)], env=env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"serve_golden_0825.sh failed (exit {result.returncode}):\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )

    def stop(self):
        env = dict(os.environ)
        env["PORT"] = str(PORT)
        env["DB_DIR"] = str(self.db_dir)
        stop = GOLDEN_DIR / "stop_golden_0825.sh"
        subprocess.run([str(stop)], env=env, capture_output=True, text=True, timeout=60)


def arm(state_file, name):
    defects.write_state(str(state_file), name)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def ucp_headers():
    suffix = uuid.uuid4().hex[:8]
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": f"idem-{suffix}",
        "Request-Id": f"req-{suffix}",
        "Request-Signature": f"sig-{suffix}",
        "UCP-Agent": 'profile="http://localhost:9/.well-known/ucp"; version="2026-08-25"',
        "Simulation-Secret": SIM_SECRET,
    }


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=ucp_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _fulfillment_block():
    """A shipping method whose group selects a REAL shipping-rate option
    (std-ship, from test_data/flower_shop/shipping_rates.csv) — this is what
    makes groups[0].options[] genuinely populated in the response (verified
    empirically, not assumed), which FUL-008's check needs."""
    return {
        "methods": [
            {
                "id": "method_1", "type": "shipping", "line_item_ids": [],
                "destinations": [{"id": "dest_1", "type": "shipping_address", "address_country": "US"}],
                "selected_destination_id": "dest_1",
                "groups": [{"id": "group_1", "line_item_ids": [], "selected_option_id": "std-ship"}],
            }
        ]
    }


def _create_checkout(extra_fields=None):
    body = {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": _fulfillment_block(),
    }
    if extra_fields:
        body.update(extra_fields)
    return http("POST", "/checkout-sessions", body)


def _create_checkout_open_vocab_fulfillment_type():
    """FUL-003's positive open-vocabulary proof: a fulfillment_method.type value
    OUTSIDE the retired 01-23-era closed {shipping, pickup} enum (fulfillment_
    method.json's `type` carries no `enum` at 08-25 — verified at the artifact,
    conformance/.vendor/ucp-2026-08-25 — only a description noting `shipping`/
    `pickup` as well-known and "Businesses MAY use additional values"). No
    destinations/groups needed for a non-shipping type."""
    body = {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": {"methods": [{"id": "method_open", "type": "digital_download",
                                      "line_item_ids": ["placeholder"]}]},
    }
    return http("POST", "/checkout-sessions", body)


def _create_checkout_with_consent(purpose_fields):
    """A checkout whose buyer.consent carries ONE purpose
    (com.example.marketing — the same key the pre-existing consent-invalid-
    source mutant uses, so the shared route's clean/armed cases line up).

    NOTE (recorded honestly, not worked around silently): this golden's
    request-side model currently requires `description` on a submitted
    consent purpose even though buyer_consent.json marks it
    `"ucp_request": "omit"` (the platform should not need to send it) —
    confirmed live 2026-08-31 (lane/p3-wave2): omitting it crashes
    create_checkout with an unhandled pydantic ValidationError -> bare 500,
    the same exception-handling gap class STATUS.md already documents for
    buyer_consent (R10a) and explicitly rules out of scope for a schema-
    migration pass. Sending `description` anyway is a valid input (the spec
    never forbids it) and is NOT what this check grades, so it works around
    the gap rather than silently depending on it; see the module docstring's
    BLOCKED-rows section — this specific finding is recorded in
    GAP-LEDGER-0825.md, not fixed here.
    """
    body = {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": _fulfillment_block(),
        "buyer": {"consent": {"com.example.marketing": purpose_fields}},
    }
    return http("POST", "/checkout-sessions", body)


def _complete_checkout():
    """A full create -> complete round trip with a real (token) credential —
    the same mock_payment_handler success path validate_golden_0825_battery.py
    uses. Returns the complete_checkout response, or None if the fixture
    itself failed (an ERROR, not a row verdict)."""
    st, checkout = _create_checkout()
    if st != 201 or not isinstance(checkout, dict):
        return None
    st2, completed = http("POST", f"/checkout-sessions/{checkout['id']}/complete", {
        "payment": {"instruments": [{
            "id": "instr_1", "handler_id": "mock_payment_handler", "type": "token",
            "display": {"brand": "Visa", "last_digits": "1234"},
            "credential": {"type": "token", "token": "success_token"},
        }]},
        "risk_signals": {},
    })
    if st2 != 200 or not isinstance(completed, dict):
        return None
    return completed


# ---------------------------------------------------------------------------
# predicates (self-referenced rows — SIG-008, PAY-011/012)
# ---------------------------------------------------------------------------


def p_keys_published(status, body):
    """SIG-008: keys[] MUST be published at the TOP LEVEL of the profile
    document (a sibling of `ucp`, never nested inside it — R14/R8). The
    oracle cannot judge this (profile.json marks `keys` optional), so this
    predicate reads the field directly."""
    if status != 200 or not isinstance(body, dict):
        return False
    keys = body.get("keys")
    return isinstance(keys, list) and len(keys) > 0


def p_no_credential_leak(status, body):
    """PAY-011/PAY-012: a complete_checkout response MUST NOT carry a
    `payment` field that echoes a credential (this golden's clean design
    omits `payment` from the response entirely — the strongest compliant
    posture — but the check only requires that no credential-shaped leak
    is present, matching the actual normative text). Empirically confirmed
    the oracle does not enforce this (see module docstring), so this
    predicate scans the response itself."""
    if status != 200 or not isinstance(body, dict):
        return False
    payment = body.get("payment")
    if payment is None:
        return True
    text = json.dumps(payment)
    return "credential" not in text and "token" not in text


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------

Row = namedtuple("Row", "id req_ids evidence doc make_request judge mutant")


def _oracle_profile(status, body):
    if status != 200 or not isinstance(body, dict):
        return False, f"discovery fetch failed: status {status}"
    return so.validate_profile(body, version=VERSION, role="business")


def _oracle_against(schema_rel, def_name, op):
    def judge(status, body):
        if status not in (200, 201) or not isinstance(body, dict):
            return False, f"request failed: status {status}"
        return so.validate_against(body, schema_rel, def_name, op=op, version=VERSION,
                                    direction="response")
    return judge


ROWS = [
    Row("SIG-007", ["SIG-007"], "fixture-schema",
        "Public keys MUST be represented using JWK (RFC 7517).",
        lambda: http("GET", "/.well-known/ucp"),
        _oracle_profile,
        "sdkdrop-jwk-missing-crv"),
    Row("SIG-008", ["SIG-008"], "self-referenced",
        "Public keys are published in the signer's profile at the canonical "
        "top-level keys[] location.",
        lambda: http("GET", "/.well-known/ucp"),
        lambda status, body: (p_keys_published(status, body),
                               "keys[] present at top level" if p_keys_published(status, body)
                               else "top-level keys[] missing or empty"),
        "sig-keys-not-published"),
    Row("PAY-011", ["PAY-011"], "self-referenced",
        "Credentials flow Platform -> Business only; businesses MUST NOT "
        "echo credentials back in responses.",
        lambda: _status_body(_complete_checkout()),
        lambda status, body: (p_no_credential_leak(status, body),
                               "no credential leak" if p_no_credential_leak(status, body)
                               else "credential-shaped data leaked into the response"),
        "pay-credential-leaked-in-response"),
    Row("PAY-012", ["PAY-012"], "self-referenced",
        "Token credential value MUST NOT be echoed in responses "
        "(token field is omit-on-response).",
        lambda: _status_body(_complete_checkout()),
        lambda status, body: (p_no_credential_leak(status, body),
                               "no token leak" if p_no_credential_leak(status, body)
                               else "token value leaked into the response"),
        "pay-credential-leaked-in-response"),
    Row("FUL-003", ["FUL-003"], "fixture-schema",
        "A fulfillment_method MUST include id, type, and line_item_ids "
        "(type is OPEN vocabulary at 08-25 -- no enum -- positively proven "
        "below, not just left unasserted).",
        _create_checkout,
        _oracle_against("schemas/shopping/fulfillment.json", "dev.ucp.shopping.checkout", "create"),
        "fulfillment-drop-method-id"),
    Row("FUL-007", ["FUL-007"], "fixture-schema",
        "A fulfillment_group MUST include id and line_item_ids.",
        _create_checkout,
        _oracle_against("schemas/shopping/fulfillment.json", "dev.ucp.shopping.checkout", "create"),
        "fulfillment-group-drop-id"),
    Row("FUL-008", ["FUL-008"], "fixture-schema",
        "A fulfillment_option MUST include id, title, and totals.",
        _create_checkout,
        _oracle_against("schemas/shopping/fulfillment.json", "dev.ucp.shopping.checkout", "create"),
        "fulfillment-option-drop-title"),
    Row("FUL-030", ["FUL-030"], "fixture-schema",
        "A shipping_destination MUST include id and type (type is a "
        "required const discriminator \"shipping_address\"). NOTE: this row "
        "shares its mutant with the R11 battery's own SDK-drop-family entry "
        "(sdkdrop-discriminator-array-retype), which drops `type` only -- it "
        "kill-tests the discriminator half of the MUST directly. The `id` "
        "half is asserted on every clean baseline above (the fixture always "
        "carries destinations[0].id) but has no dedicated mutant of its own "
        "this wave (P-7: reusing a proven mutant over minting a near-"
        "duplicate one for the row's other half).",
        _create_checkout,
        _oracle_against("schemas/shopping/fulfillment.json", "dev.ucp.shopping.checkout", "create"),
        "sdkdrop-discriminator-array-retype"),
    Row("CNST-001", ["CNST-001"], "fixture-schema",
        "A consent_purpose object MUST include granted, source, and "
        "description (an OBJECT, never a bare boolean).",
        lambda: _create_checkout_with_consent(
            {"granted": True, "source": "platform", "description": "x"}),
        _oracle_against("schemas/shopping/buyer_consent.json", "dev.ucp.shopping.checkout", "create"),
        "consent-purpose-missing-granted"),
    Row("CNST-003", ["CNST-003"], "fixture-schema",
        "consent_purpose.source MUST be exactly \"business\" or \"platform\".",
        lambda: _create_checkout_with_consent(
            {"granted": True, "source": "platform", "description": "x"}),
        _oracle_against("schemas/shopping/buyer_consent.json", "dev.ucp.shopping.checkout", "create"),
        "consent-invalid-source"),
]


def _status_body(resp):
    """Adapt _complete_checkout()'s Optional[dict] return (None on fixture
    failure) into the (status, body) shape every judge() expects."""
    if resp is None:
        return 0, None
    return 200, resp


# FUL-003 positively proves open vocabulary as a SEPARATE, non-kill-tested
# assertion layered onto the row above (there is no schema `enum` to mutate
# against, so this has no battery mutant of its own -- it is evidence FOR the
# row, not a second row). Run inline in run() rather than the ROWS table.


def run():
    """Boot ONE golden-0825 instance (defects mode on, own port/state), run
    every row's clean -> arm -> armed -> disarm -> restored cycle, tear down.
    Returns (results: list[(Row, verdict, detail)], open_vocab_ok: bool|None)."""
    with tempfile.TemporaryDirectory(prefix="ucp_golden_check_08_25_") as tmp:
        tmp = pathlib.Path(tmp)
        db_dir = tmp / "db"
        state_file = tmp / "defects_state.json"
        g = Golden(db_dir, defects_config=DEFECTS_CONFIG, state_file=state_file)
        g.start()
        try:
            results = []
            for row in ROWS:
                arm(state_file, None)
                c_status, c_body = row.make_request()
                c_ok, c_detail = row.judge(c_status, c_body)
                if not c_ok:
                    results.append((row, "CLEAN-FAILED", f"unmutated golden already fails: {c_detail}"))
                    continue

                arm(state_file, row.mutant)
                a_status, a_body = row.make_request()
                a_ok, a_detail = row.judge(a_status, a_body)
                arm(state_file, None)

                if a_ok:
                    results.append((row, "SURVIVED", f"mutant {row.mutant!r} did not deviate: {a_detail}"))
                    continue

                r_status, r_body = row.make_request()
                r_ok, r_detail = row.judge(r_status, r_body)
                if not r_ok:
                    results.append((row, "RESTORE-FAILED", f"disarmed but still red: {r_detail}"))
                    continue

                results.append((row, "KILLED", a_detail))

            # FUL-003's open-vocabulary positive proof (no mutant: nothing to
            # kill-test against an absent `enum` -- this demonstrates the row's
            # OWN text ("Businesses MAY use additional values"), corroborated
            # by the oracle accepting a non-well-known type value verbatim.
            arm(state_file, None)
            ov_status, ov_body = _create_checkout_open_vocab_fulfillment_type()
            ov_ok, ov_detail = _oracle_against(
                "schemas/shopping/fulfillment.json", "dev.ucp.shopping.checkout", "create"
            )(ov_status, ov_body)
            method_type_echoed = (
                isinstance(ov_body, dict)
                and (ov_body.get("fulfillment") or {}).get("methods", [{}])[0].get("type") == "digital_download"
            )
            open_vocab_ok = ov_ok and method_type_echoed
            open_vocab_detail = (
                "non-well-known type 'digital_download' accepted and echoed verbatim"
                if open_vocab_ok else f"open-vocabulary proof failed: {ov_detail}"
            )
        finally:
            g.stop()
    return results, (open_vocab_ok, open_vocab_detail)


BLOCKED = [
    ("SIG-044..049 (WBA-shape)",
     "golden's ucp_signing.py explicitly implements the default-UCP signing "
     "regime only (ucp_signing.py:318-323: 'does not parse WBA-shape component "
     "parameters'); no Signature-Agent header, no tag=web-bot-auth, no member-"
     "keyed keyid thumbprint binding anywhere in the golden. Building WBA-shape "
     "support is a new signing MODE, not minimal wiring -- out of this wave's "
     "budget."),
    ("SIG-050 (EdDSA signature encoding)",
     "the golden's only live signing path is outbound webhook delivery "
     "(services/checkout_service.py _notify_webhook), which CAN use an "
     "Ed25519 key (webhook_signer.py already supports it via "
     "--webhook_signing_key) -- but (a) capturing it needs a receiver harness "
     "plus platform.webhook_url plumbing (routes/ucp_implementation.py's "
     "extract_webhook_url fetches a caller-hosted platform profile), and (b) "
     "more fundamentally, defects.py's middleware patches served RESPONSE "
     "bodies only, never outbound client requests the server itself signs -- "
     "no battery mutant can arm/disarm a defect on this signing path without "
     "a second engine hook point, a real feature addition beyond 'minimal "
     "server-side support'."),
    ("PAY-020/021/024 (payment_credential/card_credential/token_credential "
     "required-field shape)",
     "these are REQUEST-side obligations (the credential the platform sends), "
     "which has no natural fit in the R11 battery's response-mutation "
     "arm/disarm idiom (defects.py mutates what the server SERVES, never what "
     "a caller SENDS). Empirically found while probing (2026-08-31): a "
     "credential missing `type` crashes complete_checkout with an unhandled "
     "pydantic ValidationError -> bare 500 (routes/ucp_implementation.py:225, "
     "PaymentCreateRequest(**payment) re-validates an already-FastAPI-parsed "
     "body a second time) instead of a clean 4xx -- the SAME exception-"
     "handling gap class STATUS.md already documents for buyer_consent (R10a) "
     "and rules out of scope for a schema-migration pass; recorded as a new "
     "finding in GAP-LEDGER-0825.md, not fixed here."),
    ("PAY-022 (card_credential MUST NOT be used for checkout) + "
     "pan_credential/network_token_credential successors",
     "PAY-022 is testability=manual in the register (not testable) and R9 "
     "already flags it a retirement candidate. No register rows exist yet for "
     "the successor schemas (pan_credential.json/network_token_credential.json "
     "-- PAY-021's own notes recommend a follow-up completeness pass, not "
     "added here per the carry-forward-only scope of that pass). Authoring "
     "NEW register rows is an L2/newsurface-lane task, out of this convert-"
     "existing-rows wave; empirically, the golden's payment logic "
     "(_process_payment) also only recognizes the RETIRED card/token "
     "discriminators, zero support for pan/network_token, so even a "
     "successor-schema check would have nothing real to grade yet."),
]


def main():
    try:
        _require_oracle()
    except OracleUnavailable as e:
        print(f"golden_check_08_25: SKIP -- {e}")
        return 2

    results, (open_vocab_ok, open_vocab_detail) = run()
    allok = True
    for row, verdict, detail in results:
        ok = verdict == "KILLED"
        allok = allok and ok
        print(f"  {'✓' if ok else '✗'} {row.id} ({row.evidence}): {verdict} — {detail[:100]}")
    print(f"  {'✓' if open_vocab_ok else '✗'} FUL-003 open-vocabulary proof: {open_vocab_detail}")
    allok = allok and open_vocab_ok

    print("\nBLOCKED this wave (never a vacuous check — reasons, not silence):")
    for row_ids, reason in BLOCKED:
        print(f"  - {row_ids}: {reason}")

    print("\nPASS" if allok else "\nFAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())

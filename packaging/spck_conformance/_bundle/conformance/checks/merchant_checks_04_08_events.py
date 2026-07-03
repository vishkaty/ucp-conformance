#!/usr/bin/env python3
"""
merchant_checks_04_08_events.py — 2026-04-08-scoped WEBHOOK/EVENTS area checks.

Two conversion tracks for the needs-receiver tier:

  * ORDER-EVENT WEBHOOKS (ORD-026..031, SIG-014/015/017/027): the business POSTs
    the FULL order entity (current-state snapshot) to the webhook URL the platform
    provides in its order capability's config (order.md "Events" / "Webhook URL
    Configuration"; payload schema = order per rest.openapi.json
    webhooks.orderEvent). The suite IS the receiving platform: each check boots a
    local receiver + platform-profile pair (webhook_harness.Harness0408, port 0),
    drives create -> complete (-> "Order created" event) and, config-permitting,
    the post-order adjustment hook (-> update event), then grades the CAPTURED
    deliveries — including full RFC 9421 signature verification against the JWK
    the merchant publishes in its profile's signing_keys[] (order.md "Webhook
    Signature Verification": the platform verifies; we are the platform).
    Config-gated on `webhooks.simulate`: a remote merchant cannot reach a local
    receiver, so without the config assertion these skip honestly.

  * REQUEST-VERIFICATION ERROR CODES (SIG-031..034 + the matching
    verify_rest_request pseudocode rows SIG-036/037/038): signatures.md "Error
    Handling" pins each signature error code to an HTTP status. Config-gated on
    `signature.request_private_jwk` (same assertion as SIG-002: the merchant
    verifies ES256-signed requests against the supplied test key). Each probe
    sends ONE precisely-defective signed request — Signature header absent /
    tampered signature / unknown keyid / body-digest mismatch — and requires the
    spec-mapped status + error code. SIG-035 (algorithm_unsupported) has NO
    spec-defined wire trigger (the verification pseudocode never returns it and
    signers MUST NOT declare an alg parameter), so it is NOT covered here.

  * SIG-021 (response signed components include content-digest + content-type
    when the response has a body): the suite is the receiving party for merchant
    RESPONSES, so this is directly observable on a signed create response
    (config-gated on `signature.responses`, like the wave-1 response checks).

Version-locked: signatures.md and the 04-08 order-events register rows are new at
2026-04-08 (the 01-era ORD-026.. ids do not exist; the 01-era webhook rows
ORD-012.. describe a DIFFERENT wire format — event_type envelope + detached-JWT
Request-Signature). versions=("2026-04-08",) on every check.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr/_create_payload from there, and the
RFC 9421 parsing/crypto helpers from merchant_checks_04_08_signatures).
"""
import sys, pathlib, json, uuid, base64, hashlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION, INCONCLUSIVE          # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload               # noqa: E402
from merchant_checks_04_08_signatures import (                          # noqa: E402
    ecdsa_p256_verify, parse_signature_input, parse_signature,
    _digest_value, _jwk_point, _profile_keys, _b64u_dec, _matched, _signed_headers)

V0408 = ("2026-04-08",)

# ---- SIG-031..034/036..038: request-verification error-code mapping -------------

def _wh_wait(ctx):
    """Delivery/retry wait window. order.md pins NO delivery timing, so a fixed
    window can false-deviate a conformant queued-delivery merchant (W2-F2):
    config webhooks.wait_seconds widens it; webhooks.simulate therefore asserts
    'delivers (and first-retries) within this window', not just reachability."""
    return float((ctx.config.get("webhooks") or {}).get("wait_seconds", 8.0))

def _sig_key(ctx):
    """(private scalar d, kid) from config signature.request_private_jwk, or None."""
    jwk = (ctx.config.get("signature") or {}).get("request_private_jwk") or {}
    try:
        d = int.from_bytes(_b64u_dec(jwk.get("d")), "big")
    except Exception:
        return None
    return d, (jwk.get("kid") or "")

def _sig_probe(ctx, *, drop_signature=False, tamper=False, kid=None,
               digest_of=None):
    """One precisely-defective ES256-signed create request; returns the merchant's
    rejection response. `digest_of` signs/derives Content-Digest from DIFFERENT
    bytes than the transmitted body (a digest mismatch with a consistent
    signature, so the digest check — verify_rest_request step 3, which precedes
    signature verification — is the one that must fire)."""
    key = _sig_key(ctx)
    if key is None:
        return Resp(0, {}, b'{"probe":"signature.request_private_jwk has no valid d"}')
    d, own_kid = key
    payload = _create_payload(ctx)
    raw = json.dumps(payload).encode()       # engine.fetch serializes identically
    hdrs = _signed_headers(ctx, "POST", "/checkout-sessions",
                           digest_of if digest_of is not None else raw,
                           d, kid if kid is not None else own_kid, tamper=tamper)
    if drop_signature:
        hdrs.pop("Signature", None)
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", payload, hdrs)

def sigerr_missing_resp(ctx):
    """Signature-Input present but the Signature header ABSENT — a required
    signature header not present (signatures.md: signature_missing -> 401)."""
    return _sig_probe(ctx, drop_signature=True)

def sigerr_invalid_resp(ctx):
    """A correctly-built ES256 signature with one flipped bit — verification fails
    (verify_rest_request step 5: error signature_invalid -> 401)."""
    return _sig_probe(ctx, tamper=True)

def sigerr_key_not_found_resp(ctx):
    """keyid names a key that is NOT in the signer's signing_keys
    (verify_rest_request step 2: error key_not_found -> 401)."""
    return _sig_probe(ctx, kid="spck-e2e-unknown-key-" + uuid.uuid4().hex[:8])

def sigerr_digest_mismatch_resp(ctx):
    """Content-Digest computed over DIFFERENT bytes than the transmitted body,
    signature consistent with the header (verify_rest_request step 3 fires first:
    error digest_mismatch -> 400)."""
    return _sig_probe(ctx, digest_of=b'{"tampered":"body bytes"}')

def _err_codes(r):
    """Every error-code string the response body carries (top-level `code`,
    `error.code`, or messages[].code — placement-permissive, value-strict)."""
    j = r.json if isinstance(r.json, dict) else {}
    codes = set()
    if isinstance(j.get("code"), str):
        codes.add(j["code"])
    err = j.get("error")
    if isinstance(err, dict) and isinstance(err.get("code"), str):
        codes.add(err["code"])
    for m in j.get("messages") or []:
        if isinstance(m, dict) and isinstance(m.get("code"), str):
            codes.add(m["code"])
    return codes

def p_sig_missing(r):
    return CLEAN if r.status == 401 and "signature_missing" in _err_codes(r) \
        else DEVIATION

def p_sig_invalid(r):
    return CLEAN if r.status == 401 and "signature_invalid" in _err_codes(r) \
        else DEVIATION

def p_key_not_found(r):
    return CLEAN if r.status == 401 and "key_not_found" in _err_codes(r) \
        else DEVIATION

def p_digest_mismatch(r):
    return CLEAN if r.status == 400 and "digest_mismatch" in _err_codes(r) \
        else DEVIATION

# ---- SIG-025: duplicate idempotency key -> cached response, no re-execution ------
def idem_replay_resp(ctx):
    """The SAME create request (identical body AND Idempotency-Key) sent twice;
    returns both results so the predicate can prove the second is the CACHED
    response (same resource id — a fresh id would prove re-execution)."""
    import uuid as _uuid
    k = str(_uuid.uuid4())
    p = _create_payload(ctx)                 # reused verbatim: identical body bytes
    r1 = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr(k))
    r2 = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr(k))
    body = {"first": r1.json, "first_status": r1.status,
            "second": r2.json, "second_status": r2.status}
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

def p_idem_replay(r):
    """SIG-025: the duplicate returns the cached result — same status, same
    checkout id, never a second execution (which would mint a new id)."""
    j = r.json if isinstance(r.json, dict) else {}
    f, s = j.get("first"), j.get("second")
    if not (isinstance(f, dict) and isinstance(s, dict)):
        return DEVIATION
    if j.get("first_status") not in (200, 201):
        return DEVIATION
    if j.get("second_status") != j.get("first_status"):
        return DEVIATION                    # a cached response replays the status
    fid = f.get("id")
    return CLEAN if fid and s.get("id") == fid else DEVIATION

# ---- SIG-021: response signed components (body present) --------------------------
def signed_create_resp(ctx):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx), _hdr())

def p_body_components(r):
    """SIG-021: a signed response WITH a body covers content-digest AND
    content-type in its signed components."""
    if r.status not in (200, 201) or not r.body:
        return DEVIATION
    m = _matched(r)
    if not m:
        return DEVIATION
    if m[0].get("unsupported"):
        return INCONCLUSIVE                 # RFC-legal component parameters (F4)
    return CLEAN if {"content-digest", "content-type"} <= set(m[0]["components"]) \
        else DEVIATION

_SI_NO_CTYPE = 'sig1=("@status" "content-digest");keyid="k1"'
_SI_NO_DIGEST = 'sig1=("@status" "content-type");keyid="k1"'

# ---- order-event webhook flows (the suite is the receiving platform) -------------
def _drive_webhook_flow(ctx, fail_first=0, adjust=False):
    """create -> complete (-> 'Order created' delivery) [-> adjust (-> update
    delivery)] with the platform profile's order config.webhook_url pointing at a
    local capturing receiver. Returns a Resp whose .json carries the captured
    deliveries + the ids the events must reference."""
    from webhook_harness import Harness0408
    with Harness0408(fail_first=fail_first) as h:
        hd = _hdr()
        hd["UCP-Agent"] = f'profile="{h.profile_url}"'
        p = _create_payload(ctx, with_fulfillment=True)
        opt = ctx.config.get("fulfillment_option_id")
        if opt:
            p["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"] = opt
        r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, hd)
        cid = (r.json or {}).get("id")
        li_id = ((r.json or {}).get("line_items") or [{}])[0].get("id")
        c = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                  "POST", ctx.config.get("complete_payment"), hd)
        oid = ((c.json or {}).get("order") or {}).get("id")
        events = h.wait_events(timeout=_wh_wait(ctx), n=1 + fail_first)
        if adjust and oid:
            fetch(ctx.shopping_endpoint, f"/testing/orders/{oid}/adjust", "POST",
                  {"line_item_id": li_id, "quantity": 1, "type": "refund"}, hd)
            events = h.wait_events(timeout=_wh_wait(ctx), n=len(events) + 1)
        body = {"events": events, "checkout_id": cid, "order_id": oid,
                "webhook_query": h.webhook_url.partition("?")[2]}
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

def wh_created_flow(ctx):
    return _drive_webhook_flow(ctx)

def wh_adjust_flow(ctx):
    return _drive_webhook_flow(ctx, adjust=True)

def wh_retry_flow(ctx):
    return _drive_webhook_flow(ctx, fail_first=1)

def _flow(r):
    """(events, checkout_id, order_id) from a flow Resp; None when malformed."""
    if not isinstance(r.json, dict):
        return None
    ev = r.json.get("events")
    oid = r.json.get("order_id")
    if not isinstance(ev, list) or not oid:
        return None
    return [e for e in ev if isinstance(e, dict)], r.json.get("checkout_id"), oid

# order.json required properties — "fully populated order entity" (ORD-029/030)
_ORDER_REQUIRED = ("ucp", "id", "checkout_id", "permalink_url", "line_items",
                   "fulfillment", "currency", "totals")

def _fully_populated(payload, cid, oid):
    """The event payload is the full order entity for OUR order: every
    order.json-required property present and non-null, non-empty line_items,
    and the ids reconcile with the checkout that produced the order."""
    if not isinstance(payload, dict):
        return False
    if any(payload.get(k) is None for k in _ORDER_REQUIRED):
        return False
    if not (isinstance(payload.get("line_items"), list) and payload["line_items"]):
        return False
    return str(payload.get("id")) == str(oid) \
        and str(payload.get("checkout_id")) == str(cid)

def p_created_full_entity(r):
    """ORD-029: an 'Order created' event was delivered and EVERY captured event
    for the order carries the fully populated order entity."""
    f = _flow(r)
    if not f:
        return DEVIATION
    events, cid, oid = f
    ours = [e for e in events
            if isinstance(e.get("payload"), dict)
            and str(e["payload"].get("id")) == str(oid)]
    if not ours:
        return DEVIATION                    # no delivery referenced our order
    return CLEAN if all(_fully_populated(e["payload"], cid, oid) for e in ours) \
        else DEVIATION

def p_update_full_entity(r):
    """ORD-030: after a post-order adjustment, an UPDATE event was delivered
    carrying the FULL order entity (current-state snapshot reflecting the
    adjustment — non-empty adjustments[]), never an incremental delta."""
    f = _flow(r)
    if not f:
        return DEVIATION
    events, cid, oid = f
    ours = [e for e in events
            if isinstance(e.get("payload"), dict)
            and str(e["payload"].get("id")) == str(oid)]
    if len(ours) < 2:
        return DEVIATION                    # created-only: no update delivered
    if not all(_fully_populated(e["payload"], cid, oid) for e in ours):
        return DEVIATION                    # a delta payload is not the full entity
    return CLEAN if any(e["payload"].get("adjustments") for e in ours) \
        else DEVIATION                      # snapshot must reflect the new state

def _event_sig(e):
    """(signature-input entry, signature bytes, lowercased headers) of a captured
    delivery for the first label present in both headers; None when absent."""
    h = e.get("headers") or {}
    si = parse_signature_input(h.get("signature-input", ""))
    sigs = parse_signature(h.get("signature", ""))
    if not si or not sigs:
        return None
    label = next((l for l in si if l in sigs), None)
    if label is None:
        return None
    return si[label], sigs[label], h

def p_webhook_signed_verifies(r, ctx):
    """ORD-026/ORD-027/SIG-027: every delivery carries the RFC 9421 headers
    (Signature, Signature-Input, Content-Digest), the sha-256 Content-Digest
    matches the raw body bytes, and the signature VERIFIES (ES256) against the
    JWK the merchant publishes in its profile's signing_keys[] under the declared
    keyid — the platform-side verification, performed by this suite."""
    f = _flow(r)
    if not f:
        return DEVIATION
    events, _, oid = f
    ours = [e for e in events
            if isinstance(e.get("payload"), dict)
            and str(e["payload"].get("id")) == str(oid)]
    if not ours:
        return DEVIATION
    for e in ours:
        m = _event_sig(e)
        if not m:
            return DEVIATION
        entry, sig, h = m
        try:
            raw = base64.b64decode(e.get("body_b64") or "", validate=True)
        except Exception:
            return DEVIATION
        got = _digest_value(h.get("content-digest"))
        if got is None or got != hashlib.sha256(raw).digest():
            return DEVIATION                # Content-Digest absent or wrong
        jwk = next((k for k in _profile_keys(ctx) if isinstance(k, dict)
                    and k.get("kid") == entry["params"].get("keyid")), None)
        if not jwk:
            return DEVIATION                # keyid not published -> unverifiable
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
            return INCONCLUSIVE             # P-384 verification not implemented
        Q = _jwk_point(jwk)
        if Q is None:
            return DEVIATION
        if entry.get("unsupported"):
            return INCONCLUSIVE             # RFC-legal component parameters (F4)
        derived = {"@method": e.get("method", "POST"),
                   "@authority": e.get("authority", ""),
                   "@path": e.get("path", ""),
                   "@query": "?" + (e.get("query") or "")}
        lines = []
        for c in entry["components"]:
            if c.startswith("@"):
                if c not in derived:
                    return INCONCLUSIVE     # exotic derived component
                v = derived[c]
            else:
                if c not in h:
                    return DEVIATION        # signed header absent from the request
                v = h[c].strip()
            lines.append(f'"{c}": {v}')
        lines.append(f'"@signature-params": {entry["raw"]}')
        if not ecdsa_p256_verify("\n".join(lines).encode(), sig, Q):
            return DEVIATION
    return CLEAN

def p_webhook_components(r):
    """SIG-014/SIG-015 (merchant as REQUEST SIGNER — its webhooks): the signed
    components include @method, @authority, @path (always required) and
    content-digest, content-type (the delivery has a body)."""
    f = _flow(r)
    if not f:
        return DEVIATION
    events, _, oid = f
    ours = [e for e in events
            if isinstance(e.get("payload"), dict)
            and str(e["payload"].get("id")) == str(oid)]
    if not ours:
        return DEVIATION
    want = {"@method", "@authority", "@path", "content-digest", "content-type"}
    for e in ours:
        m = _event_sig(e)
        if not m:
            return DEVIATION
        if m[0].get("unsupported"):
            return INCONCLUSIVE
        if not want <= set(m[0]["components"]):
            return DEVIATION
    return CLEAN

def p_webhook_query_signed(r):
    """SIG-017 (merchant as request signer): the platform-provided webhook URL
    carries a query string (the harness URL does, deliberately — 'URL format is
    platform-specific'), so deliveries MUST reach that URL with the query intact
    and sign @query."""
    f = _flow(r)
    if not f:
        return DEVIATION
    events, _, oid = f
    want_query = (r.json or {}).get("webhook_query") or ""
    ours = [e for e in events
            if isinstance(e.get("payload"), dict)
            and str(e["payload"].get("id")) == str(oid)]
    if not ours:
        return DEVIATION
    for e in ours:
        if (e.get("query") or "") != want_query:
            return DEVIATION                # did not POST to the URL as provided
        m = _event_sig(e)
        if not m:
            return DEVIATION
        if m[0].get("unsupported"):
            return INCONCLUSIVE
        if "@query" not in m[0]["components"]:
            return DEVIATION
    return CLEAN

def p_webhook_ucp_agent(r):
    """ORD-028: every delivery carries a UCP-Agent header whose RFC 8941 profile
    member names the business profile (a /.well-known/ucp URL — signatures.md
    UCP-Agent parsing rule 4 for business profiles)."""
    import re
    f = _flow(r)
    if not f:
        return DEVIATION
    events, _, oid = f
    ours = [e for e in events
            if isinstance(e.get("payload"), dict)
            and str(e["payload"].get("id")) == str(oid)]
    if not ours:
        return DEVIATION
    for e in ours:
        agent = (e.get("headers") or {}).get("ucp-agent") or ""
        m = re.search(r'profile="([^"]+)"', agent)
        if not m:
            return DEVIATION
        if not m.group(1).split("?")[0].rstrip("/").endswith("/.well-known/ucp"):
            return DEVIATION
    return CLEAN

def p_webhook_retry(r):
    """ORD-031: with the receiver failing the FIRST delivery (HTTP 500), the
    business retries — at least two delivery attempts for the order arrive."""
    f = _flow(r)
    if not f:
        return DEVIATION
    events, _, oid = f
    attempts = [e for e in events
                if isinstance(e.get("payload"), dict)
                and str(e["payload"].get("id")) == str(oid)]
    return CLEAN if len(attempts) >= 2 else DEVIATION

# ---- mutation constants (deterministic defect injections on the captured data) --
_BAD_SIG = "sig1=:" + base64.b64encode(b"\x03" * 64).decode() + ":"
_WH_SI_NO_PATH = json.dumps(
    'sig1=("@method" "@authority" "content-digest" "content-type");keyid="k1"')
_WH_SI_NO_DIGEST = json.dumps(
    'sig1=("@method" "@authority" "@path" "content-type");keyid="k1"')
_WH_SI_NO_QUERY = json.dumps(
    'sig1=("@method" "@authority" "@path" "content-digest" "content-type");keyid="k1"')

_WH_GATES = ("webhooks.simulate", "complete_payment")

CHECKS_04_08_EVENTS = [
    # --- request-verification error codes (signatures.md Error Handling) ---
    MCheck("signature.err_signature_missing", ["SIG-031"], "MUST",
           sigerr_missing_resp, p_sig_missing,
           ["status:200", "status:400", "set:code=\"signature_invalid\"",
            "drop:code", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk",), transport="rest",
           versions=V0408),
    MCheck("signature.err_signature_invalid", ["SIG-032", "SIG-036"], "MUST",
           sigerr_invalid_resp, p_sig_invalid,
           ["status:200", "status:400", "set:code=\"key_not_found\"",
            "drop:code", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk",), transport="rest",
           versions=V0408),
    MCheck("signature.err_key_not_found", ["SIG-033", "SIG-037"], "MUST",
           sigerr_key_not_found_resp, p_key_not_found,
           ["status:200", "status:400", "set:code=\"signature_invalid\"",
            "drop:code", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk",), transport="rest",
           versions=V0408),
    MCheck("signature.err_digest_mismatch", ["SIG-034", "SIG-038"], "MUST",
           sigerr_digest_mismatch_resp, p_digest_mismatch,
           ["status:200", "status:401", "set:code=\"signature_invalid\"",
            "drop:code", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk",), transport="rest",
           versions=V0408),
    # --- duplicate idempotency key -> cached response (server-side, directly
    #     probe-able; 04-08-locked: SIG ids exist only in the 04-08 register).
    #     CHK-048/CART-026 bundle the same cached-result rule with a >=24h
    #     retention window this probe cannot observe, so they are NOT cited. ---
    MCheck("signature.idempotency_replay_cached", ["SIG-025"], "MUST",
           idem_replay_resp, p_idem_replay,
           ["set:second.id=\"chk_other\"", "drop:second", "drop:first",
            "set:second_status=500", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V0408),
    # --- response signed components with a body (the suite is the receiver) ---
    MCheck("signature.response_body_components", ["SIG-021"], "MUST",
           signed_create_resp, p_body_components,
           ["status:500", "hdrop:Signature-Input",
            f"hset:Signature-Input={_SI_NO_CTYPE}",
            f"hset:Signature-Input={_SI_NO_DIGEST}"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    # --- order-event webhooks (the suite is the receiving platform) ---
    MCheck("webhook.order_created_full_entity", ["ORD-029"], "MUST",
           wh_created_flow, p_created_full_entity,
           ["set:events=[]", "drop:events",
            "drop:events.0.payload.checkout_id",
            "drop:events.0.payload.fulfillment",
            "set:events.0.payload.line_items=[]",
            "set:events.0.payload.id=\"ord_other\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
    MCheck("webhook.update_full_entity", ["ORD-030"], "MUST",
           wh_adjust_flow, p_update_full_entity,
           ["set:events=[]", "drop:events.1",
            "drop:events.1.payload.line_items",
            "drop:events.1.payload.totals",
            "set:events.1.payload.adjustments=[]",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES + ("order.simulate_adjustment",),
           transport="rest", versions=V0408),
    MCheck("webhook.signed_rfc9421_verifies", ["ORD-026", "ORD-027", "SIG-027"],
           "MUST", wh_created_flow, p_webhook_signed_verifies,
           ["set:events=[]",
            f"set:events.0.headers.signature={json.dumps(_BAD_SIG)}",
            "drop:events.0.headers.signature",
            "drop:events.0.headers.signature-input",
            "drop:events.0.headers.content-digest",
            "set:events.0.body_b64=\"eyJ0YW1wZXJlZCI6dHJ1ZX0=\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
    MCheck("webhook.signed_components", ["SIG-014", "SIG-015"], "MUST",
           wh_created_flow, p_webhook_components,
           ["set:events=[]",
            f"set:events.0.headers.signature-input={_WH_SI_NO_PATH}",
            f"set:events.0.headers.signature-input={_WH_SI_NO_DIGEST}",
            "drop:events.0.headers.signature-input",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
    MCheck("webhook.query_component_signed", ["SIG-017"], "MUST",
           wh_created_flow, p_webhook_query_signed,
           ["set:events=[]",
            f"set:events.0.headers.signature-input={_WH_SI_NO_QUERY}",
            "set:events.0.query=\"\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
    MCheck("webhook.ucp_agent_header", ["ORD-028"], "MUST",
           wh_created_flow, p_webhook_ucp_agent,
           ["set:events=[]", "drop:events.0.headers.ucp-agent",
            "set:events.0.headers.ucp-agent=\"garbage\"",
            "set:events.0.headers.ucp-agent=\"profile=\\\"https://x.example/other\\\"\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
    MCheck("webhook.retry_failed_delivery", ["ORD-031"], "MUST",
           wh_retry_flow, p_webhook_retry,
           ["set:events=[]", "drop:events.1", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
]

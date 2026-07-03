#!/usr/bin/env python3
"""
merchant_checks_01_11_01_23_events.py — 01-era (2026-01-11/2026-01-23) webhook-signing
and retry checks (WEBHOOK/EVENTS area).

The 01-era order.md webhook contract differs from 2026-04-08 on the wire:
the signature is a DETACHED JWT (RFC 7797) over the request body, carried in
the `Request-Signature` header, signed with a key from the business's
`signing_keys` array published at /.well-known/ucp (kid in the JWT protected
header). The requirement text is verbatim-identical in the 2026-01-11 and
2026-01-23 registers (ORD-014/015/016), so these checks cover both 01-era
versions and are locked OUT of 2026-04-08 (whose ORD-014/015/016 ids name
different requirements — id-drift trap).

The suite is the receiving platform: each check boots a capture-full receiver +
the official platform-profile template (webhook_harness.Harness0123, port 0),
drives create -> complete (-> "Order created" delivery), then verifies the
captured Request-Signature end-to-end against the JWK published in the
merchant's signing_keys — accepting BOTH RFC 7797 signing-input conventions
(b64=false: header..raw-bytes, and detached-standard b64: header..b64url(body)),
since the spec pins "detached JWT (RFC 7797)" without fixing the b64 parameter.

Config-gated on `webhooks.simulate` (a remote merchant cannot reach a local
receiver) — the official Flower golden does NOT sign its webhooks, so these
gate on the controlled fixture in 2026-01-23 mode and skip honestly elsewhere.

NOTE: imported lazily by merchant_checks.all_checks() — do not import before
merchant_checks.
"""
import sys, pathlib, json, base64, hashlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION, INCONCLUSIVE          # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload               # noqa: E402
from merchant_checks_04_08_signatures import (                          # noqa: E402
    ecdsa_p256_verify, _jwk_point, _profile_keys, _b64u_dec)

# Citation scope: these encode the 2026-01-11/2026-01-23 registers' semantics
# (the 2026-04-08 registers renumbered these ids onto DIFFERENT requirements).
VERSIONS = ("2026-01-11", "2026-01-23")

def _wh_wait(ctx):
    """Delivery/retry wait window. order.md pins NO delivery timing, so a fixed
    window can false-deviate a conformant queued-delivery merchant (W2-F2):
    config webhooks.wait_seconds widens it; webhooks.simulate therefore asserts
    'delivers (and first-retries) within this window', not just reachability."""
    return float((ctx.config.get("webhooks") or {}).get("wait_seconds", 8.0))

def _drive_webhook_flow_0123(ctx, fail_first=0):
    """create -> complete with the official 01-era platform-profile template
    naming a local capture-full receiver as webhook_url; return the captured
    deliveries + ids as a Resp."""
    from webhook_harness import Harness0123
    with Harness0123(fail_first=fail_first) as h:
        hd = _hdr()
        hd["UCP-Agent"] = f'profile="{h.profile_url}"'
        p = _create_payload(ctx, with_fulfillment=True)
        opt = ctx.config.get("fulfillment_option_id")
        if opt:
            p["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"] = opt
        r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, hd)
        cid = (r.json or {}).get("id")
        c = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                  "POST", ctx.config.get("complete_payment"), hd)
        oid = ((c.json or {}).get("order") or {}).get("id")
        events = h.wait_events(timeout=_wh_wait(ctx), n=1 + fail_first)
        body = {"events": events, "checkout_id": cid, "order_id": oid}
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

def wh0123_created_flow(ctx):
    return _drive_webhook_flow_0123(ctx)

def wh0123_retry_flow(ctx):
    return _drive_webhook_flow_0123(ctx, fail_first=1)

def _ours(r):
    """The captured deliveries that reference OUR order (payload is the order
    entity + event fields, or the pre-final {order:{id}} envelope some
    implementations send), plus the order id. None on malformed flow data."""
    if not isinstance(r.json, dict):
        return None
    events, oid = r.json.get("events"), r.json.get("order_id")
    if not isinstance(events, list) or not oid:
        return None
    out = []
    for e in events:
        if not isinstance(e, dict) or not isinstance(e.get("payload"), dict):
            continue
        p = e["payload"]
        pid = p.get("id")
        if pid is None and isinstance(p.get("order"), dict):
            pid = p["order"].get("id")
        if str(pid) == str(oid):
            out.append(e)
    return out

def _detached_jws_ok(header_b64, sig_b64, raw_body, jwks):
    """True when the Request-Signature detached JWS verifies against a published
    JWK (kid-selected), under EITHER RFC 7797 signing-input convention."""
    try:
        hdr = json.loads(_b64u_dec(header_b64))
        sig = _b64u_dec(sig_b64)
    except Exception:
        return False
    if not isinstance(hdr, dict) or hdr.get("alg") != "ES256" or len(sig) != 64:
        return False
    jwk = next((k for k in jwks if isinstance(k, dict)
                and k.get("kid") == hdr.get("kid")), None)
    if not jwk or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return False
    Q = _jwk_point(jwk)
    if Q is None:
        return False
    hb = header_b64.encode()
    b64_payload = base64.urlsafe_b64encode(raw_body).rstrip(b"=")
    return ecdsa_p256_verify(hb + b"." + raw_body, sig, Q) \
        or ecdsa_p256_verify(hb + b"." + b64_payload, sig, Q)

def p_webhook_0123_signed(r, ctx):
    """ORD-014/ORD-015: every delivery carries Request-Signature — a detached
    JWT (RFC 7797) whose kid selects a key published in the business's
    signing_keys at /.well-known/ucp and whose ES256 signature VERIFIES over the
    raw request body (the platform-side verification, performed by this suite)."""
    ours = _ours(r)
    if not ours:
        return DEVIATION
    keys = _profile_keys(ctx)
    for e in ours:
        rs = (e.get("headers") or {}).get("request-signature") or ""
        parts = rs.split(".")
        if len(parts) != 3 or parts[1] != "":
            return DEVIATION                # not a DETACHED (empty-payload) JWS
        try:
            raw = base64.b64decode(e.get("body_b64") or "", validate=True)
        except Exception:
            return DEVIATION
        if not _detached_jws_ok(parts[0], parts[2], raw, keys):
            return DEVIATION
    return CLEAN

def p_webhook_0123_retry(r):
    """ORD-016: with the receiver failing the FIRST delivery (HTTP 500), the
    business retries — at least two delivery attempts for the order arrive."""
    ours = _ours(r)
    if ours is None:
        return DEVIATION
    return CLEAN if len(ours) >= 2 else DEVIATION

# a structurally-valid detached JWS that CANNOT verify (unknown kid)
_BOGUS_HDR = base64.urlsafe_b64encode(
    json.dumps({"alg": "ES256", "kid": "ucp-not-published"}).encode()
).rstrip(b"=").decode()
_BOGUS_RS = json.dumps(
    _BOGUS_HDR + ".." + base64.urlsafe_b64encode(b"\x03" * 64).rstrip(b"=").decode())

_WH_GATES = ("webhooks.simulate", "complete_payment")

CHECKS_01_23_EVENTS = [
    MCheck("webhook.signed_detached_jws", ["ORD-014", "ORD-015"], "MUST",
           wh0123_created_flow, p_webhook_0123_signed,
           ["set:events=[]", "drop:events",
            "drop:events.0.headers.request-signature",
            f"set:events.0.headers.request-signature={_BOGUS_RS}",
            "set:events.0.body_b64=\"eyJ0YW1wZXJlZCI6dHJ1ZX0=\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=VERSIONS),
    MCheck("webhook.retry_failed_delivery_01", ["ORD-016"], "MUST",
           wh0123_retry_flow, p_webhook_0123_retry,
           ["set:events=[]", "drop:events.1", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=VERSIONS),
]

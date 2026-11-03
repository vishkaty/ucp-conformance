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

  * PHASE 5 REGRESSION WATCHES on the reference implementations (2026-10-27):
    webhook.platform_webhook_url_validated (OVR-058, samples#264: a platform
    profile whose order config.webhook_url violates order.json
    $defs/platform_schema must never become the delivery target, nor be echoed
    raw) and signature.idempotency_concurrent_single_result (SIG-025,
    samples#245/#265: N concurrent creates under ONE Idempotency-Key, with the
    agent-profile fetch held open by a barrier so every handler sits in the
    read-execute-store window at once, must yield ONE stored checkout). Both are
    gated on `webhooks.simulate` because both rely on its item 3, the loopback
    platform-profile fetch convention. Both run on the Python golden and the
    controlled fixture (clean-pass + kill_safe) and on the Node reference through
    the differential gate, where each deviation is a self-expiring allowlist
    entry until the upstream fix is merged and re-pinned.

Version-locked: signatures.md and the 04-08 order-events register rows are new at
2026-04-08 (the 01-era ORD-026.. ids do not exist; the 01-era webhook rows
ORD-012.. describe a DIFFERENT wire format — event_type envelope + detached-JWT
Request-Signature). versions=("2026-04-08",) on every check.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr/_create_payload from there, and the
RFC 9421 parsing/crypto helpers from merchant_checks_04_08_signatures).
"""
import sys, pathlib, json, uuid, base64, hashlib, threading, time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION, INCONCLUSIVE          # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload               # noqa: E402
from merchant_checks_04_08_signatures import (                          # noqa: E402
    ecdsa_p256_verify, parse_signature_input, parse_signature,
    _digest_value, _jwk_point, _profile_keys, _b64u_dec, _matched, _signed_headers,
    _ec_mul, _EC_G)
from webhook_harness import platform_profile_0408                       # noqa: E402

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

# ---- samples#263 class: a MALFORMED SIGNER PROFILE must map to the spec code -------
# The verifier resolves the signer's keys from the UCP-Agent profile URL. Four
# places on that path let the standard library, httpx and idna raise past the
# SignatureError mapping, so a malformed profile URL or a malformed published key
# answered a bare 500 text/plain (python reference at samples e7c9c124; fix open as
# samples#263). Spec at v2026-04-08 (a25a4a24): signatures.md Error Handling L561
# `invalid_profile_url` 400; overview.md Error Codes L698 `profile_malformed` 422;
# signatures.md L553/L388 `key_not_found` 401; L552/L405 `signature_invalid` 401.
# Config-gated on signature.request_private_jwk AND signature.resolves_signer_profile
# (the merchant resolves signer profiles from UCP-Agent and will fetch a plain-HTTP
# loopback one for this test; the controlled fixture bakes its keys in and never
# fetches, so it omits the key and these skip). Graded on the real reference by
# validate_sig002_reference.py, which pins the defect as a tripwire at the samples
# pin that carries it and requires clean-pass + kill_safe at any other pin.
class _SignerProfile:
    """An ephemeral loopback server publishing ONE signer-profile document (any JSON)
    at every path; `url` is what UCP-Agent names."""
    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = self.server.doc
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    def __init__(self, doc):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._H)
        self.srv.doc = json.dumps(doc).encode()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/signer-profile.json"
    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self
    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

def _test_public_jwk(ctx):
    """The PUBLIC JWK of config signature.request_private_jwk (kid, x, y), derived
    from d so a served profile can publish real key material with ONE member
    deliberately malformed."""
    key = _sig_key(ctx)
    if key is None:
        return None
    d, kid = key
    x, y = _ec_mul(d, _EC_G)
    b64u = lambda n: base64.urlsafe_b64encode(n.to_bytes(32, "big")).decode().rstrip("=")
    return {"kid": kid, "kty": "EC", "crv": "P-256", "x": b64u(x), "y": b64u(y)}

def _signed_create_naming(ctx, profile_url):
    """A correctly ES256-signed create (correct Content-Digest, every required
    component covered) whose UCP-Agent names `profile_url` as the signer profile."""
    key = _sig_key(ctx)
    if key is None:
        return Resp(0, {}, b'{"probe":"signature.request_private_jwk has no valid d"}')
    d, kid = key
    payload = _create_payload(ctx)
    raw = json.dumps(payload).encode()       # engine.fetch serializes identically
    hdrs = _signed_headers(ctx, "POST", "/checkout-sessions", raw, d, kid,
                           profile_url=profile_url)
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", payload, hdrs)

def sigprofile_bad_url_resp(ctx):
    """UCP-Agent names a profile URL urlsplit itself rejects (`https://[::1`): the
    URL check precedes every fetch, so no key material is involved."""
    return _signed_create_naming(ctx, "https://[::1")

def sigprofile_nonobject_entry_resp(ctx):
    """The served profile's signing_keys[] holds a NON-OBJECT entry and nothing
    else: no usable key is published (overview.md: violates schema -> profile_malformed)."""
    with _SignerProfile({"ucp": {"version": "2026-04-08"}, "signing_keys": [42]}) as sp:
        return _signed_create_naming(ctx, sp.url)

def sigprofile_unhashable_kid_resp(ctx):
    """The served profile publishes the test key with a `kid` that is an ARRAY: a
    key the lookup cannot use is skipped, so the signature's keyid resolves to no
    key (signatures.md verify_rest_request: key_not_found)."""
    pub = _test_public_jwk(ctx)
    if pub is None:
        return Resp(0, {}, b'{"probe":"signature.request_private_jwk has no valid d"}')
    pub["kid"] = [pub["kid"]]
    with _SignerProfile({"ucp": {"version": "2026-04-08"}, "signing_keys": [pub]}) as sp:
        return _signed_create_naming(ctx, sp.url)

def sigprofile_bad_coordinate_resp(ctx):
    """The served profile publishes the test key under the right kid with the `x`
    coordinate as an INTEGER: the key is found but its material is malformed, so
    verification fails (signatures.md verify_rest_request: signature_invalid)."""
    pub = _test_public_jwk(ctx)
    if pub is None:
        return Resp(0, {}, b'{"probe":"signature.request_private_jwk has no valid d"}')
    pub["x"] = 12345
    with _SignerProfile({"ucp": {"version": "2026-04-08"}, "signing_keys": [pub]}) as sp:
        return _signed_create_naming(ctx, sp.url)

def _mapped(r, status, code):
    return CLEAN if r.status == status and code in _err_codes(r) else DEVIATION

def p_sigprofile_invalid_url(r):
    """NEG-005@04-08 (overview.md L696; signatures.md L561): a malformed signer
    profile URL -> HTTP 400 invalid_profile_url. A 5xx is the samples#263 defect."""
    return _mapped(r, 400, "invalid_profile_url")

def p_sigprofile_malformed(r):
    """NEG-004@04-08 (overview.md L698): a fetched profile that violates the schema
    and publishes nothing usable -> HTTP 422 profile_malformed."""
    return _mapped(r, 422, "profile_malformed")

def p_sigprofile_key_not_found(r):
    """SIG-033/SIG-037@04-08 (signatures.md L553, L387-388): the keyid resolves to
    no usable published key -> HTTP 401 key_not_found."""
    return _mapped(r, 401, "key_not_found")

def p_sigprofile_signature_invalid(r):
    """SIG-032/SIG-036@04-08 (signatures.md L552, L404-405): the published key's
    material is malformed so verification cannot succeed -> HTTP 401
    signature_invalid."""
    return _mapped(r, 401, "signature_invalid")


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

    def hd(h):
        # fresh headers PER REQUEST: _hdr() mints the Idempotency-Key, and reusing
        # one key across create -> complete is an idempotency conflict (409) on a
        # merchant with a global key store (the reference server enforces this;
        # the controlled fixture scopes keys per endpoint, which hid the reuse)
        d = _hdr()
        d["UCP-Agent"] = f'profile="{h.profile_url}"'
        return d

    with Harness0408(fail_first=fail_first) as h:
        p = _create_payload(ctx, with_fulfillment=True)
        opt = ctx.config.get("fulfillment_option_id")
        if opt:
            p["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"] = opt
        r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, hd(h))
        cid = (r.json or {}).get("id")
        li_id = ((r.json or {}).get("line_items") or [{}])[0].get("id")
        c = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                  "POST", ctx.config.get("complete_payment"), hd(h))
        oid = ((c.json or {}).get("order") or {}).get("id")
        events = h.wait_events(timeout=_wh_wait(ctx), n=1 + fail_first)
        if adjust and oid:
            fetch(ctx.shopping_endpoint, f"/testing/orders/{oid}/adjust", "POST",
                  {"line_item_id": li_id, "quantity": 1, "type": "refund"}, hd(h))
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

def p_webhook_idem_key_signed(r):
    """SIG-016 (merchant as request signer — its webhook POSTs are state-changing
    requests): every delivery carries an idempotency-key header AND signs it
    ('Required for POST, PUT, DELETE, PATCH' in the signed-component table)."""
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
        if not (e.get("headers") or {}).get("idempotency-key"):
            return DEVIATION
        m = _event_sig(e)
        if not m:
            return DEVIATION
        if m[0].get("unsupported"):
            return INCONCLUSIVE
        if "idempotency-key" not in m[0]["components"]:
            return DEVIATION
    return CLEAN

def p_webhook_agent_signed(r):
    """SIG-018 (merchant as request signer): the deliveries carry a UCP-Agent
    header, and whenever it is present `ucp-agent` MUST be a signed component
    (it binds the signer's identity to the signature)."""
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
        if not (e.get("headers") or {}).get("ucp-agent"):
            return DEVIATION                # ORD-028 requires the header on webhooks
        m = _event_sig(e)
        if not m:
            return DEVIATION
        if m[0].get("unsupported"):
            return INCONCLUSIVE
        if "ucp-agent" not in m[0]["components"]:
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
_WH_SI_NO_IDEM = json.dumps(
    'sig1=("@method" "@authority" "@path" "@query" "ucp-agent" "content-digest" '
    '"content-type");keyid="k1"')
_WH_SI_NO_AGENT = json.dumps(
    'sig1=("@method" "@authority" "@path" "@query" "idempotency-key" '
    '"content-digest" "content-type");keyid="k1"')

# ---- OVR-058: a schema-violating platform webhook_url is never acted on ---------
def wh_schema_violating_profile_flow(ctx):
    """The platform profile names, as its order config.webhook_url, a ONE-ELEMENT
    ARRAY containing the live receiver URL (violates order.json
    $defs/platform_schema `type: string`). create -> (complete -> wait) with that
    profile; returns the create status/code, the echoed `platform` member and the
    deliveries the receiver captured. The array is the probe value because it is
    the one shape a `new URL()`/`fetch` coercion makes DELIVERABLE: a business that
    skips validation not only echoes it raw but delivers to the coerced target,
    which the receiver observes (samples#264; the typeof guard in that fix is the
    only line that catches it)."""
    from webhook_harness import Harness0408

    def hd(h):
        d = _hdr()
        d["UCP-Agent"] = f'profile="{h.profile_url}"'
        return d

    with Harness0408(profile_webhook_url=lambda url: [url]) as h:
        p = _create_payload(ctx, with_fulfillment=True)
        opt = ctx.config.get("fulfillment_option_id")
        if opt:
            p["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"] = opt
        r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, hd(h))
        j = r.json if isinstance(r.json, dict) else {}
        codes = [m.get("code") for m in (j.get("messages") or []) if isinstance(m, dict)]
        rec = {"create_status": r.status,
               "create_code": j.get("code") or (codes[0] if codes else None),
               "platform": j.get("platform"), "events": [], "complete_status": None}
        cid = j.get("id")
        if r.status == 201 and cid:
            c = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                      "POST", ctx.config.get("complete_payment"), hd(h))
            rec["complete_status"] = c.status
            rec["events"] = h.wait_events(n=1, timeout=_wh_wait(ctx))
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(rec).encode())

def p_platform_webhook_url_validated(r):
    """OVR-058: the business validates the platform profile it fetched. A profile
    whose order config.webhook_url violates platform_schema is either REJECTED
    (422 profile_malformed, the overview.md Error Codes mapping NEG-004 pins) or
    DROPPED (the posture both references adopt: samples#257 python, samples#264
    nodejs); in neither case is the value acted on. Deviation: the create crashes
    or refuses in any other shape, the raw non-string value is echoed back under
    platform.webhook_url, an echoed string is not an http(s) URL, or an order
    event is DELIVERED to the coerced target."""
    j = r.json if isinstance(r.json, dict) else None
    if j is None:
        return DEVIATION
    st = j.get("create_status")
    if st == 422 and j.get("create_code") == "profile_malformed":
        return CLEAN                        # rejected the schema-violating profile
    if st != 201:
        return DEVIATION                    # 5xx crash, or an unmapped refusal
    plat = j.get("platform")
    if isinstance(plat, dict) and "webhook_url" in plat:
        wu = plat["webhook_url"]
        if not isinstance(wu, str):
            return DEVIATION                # the raw schema-violating value, echoed
        u = urlsplit(wu)
        if u.scheme not in ("http", "https") or not u.netloc:
            return DEVIATION                # a string, but not a webhook target
    ev = j.get("events")
    if not isinstance(ev, list):
        return DEVIATION
    return DEVIATION if ev else CLEAN       # delivered to the coerced target

# ---- SIG-025 under CONCURRENCY: one Idempotency-Key, N simultaneous creates -------
class _BarrierProfile(BaseHTTPRequestHandler):
    """Serves the platform profile only once `n` fetches have ARRIVED (or `hold`
    seconds elapsed): every concurrent create is parked inside its own
    read-execute-store window at the same moment, so the race is reproduced by
    construction rather than by timing. A business that single-flights or caches
    the fetch still parks every handler on the one fetch, and the hold timeout
    releases it; the measurement is then still valid (fewer fetches, same window)."""
    def log_message(self, *a): pass
    def do_GET(self):
        srv = self.server
        with srv.cv:
            srv.arrived += 1
            srv.cv.notify_all()
            deadline = time.monotonic() + srv.hold
            while srv.arrived < srv.n and time.monotonic() < deadline:
                srv.cv.wait(timeout=0.05)
        body = json.dumps({"ucp": platform_profile_0408("http://127.0.0.1:1/never")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

_IDEM_RACERS = 8

def idem_concurrent_resp(ctx):
    """N identical creates (same body bytes, same Idempotency-Key) issued at once
    from N threads, each naming a barrier-served platform profile in UCP-Agent (the
    `webhooks.simulate` loopback-fetch convention). Returns every status and the
    checkout id each response named, so the predicate can prove the key names ONE
    stored result (samples#245 python, samples#265 nodejs: read, await the profile
    fetch, then INSERT OR REPLACE, so every racer stored its own checkout)."""
    n = _IDEM_RACERS
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BarrierProfile)
    srv.cv, srv.arrived, srv.n, srv.hold = threading.Condition(), 0, n, 5.0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/profiles/platform-profile.json"
    key = str(uuid.uuid4())
    payload = _create_payload(ctx)           # reused verbatim: identical body bytes

    def one(_):
        h = _hdr(key)
        h["UCP-Agent"] = f'profile="{url}"'
        return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", payload, h)
    try:
        with ThreadPoolExecutor(max_workers=n) as ex:
            rs = list(ex.map(one, range(n)))
    finally:
        srv.shutdown()
        srv.server_close()
    body = {"n": n, "fetches_arrived": srv.arrived,
            "statuses": [r.status for r in rs],
            "ids": [(r.json or {}).get("id") if isinstance(r.json, dict) else None
                    for r in rs]}
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

def p_idem_concurrent_single_result(r):
    """SIG-025 ("On duplicate: return cached response, do not re-execute"), with
    the duplicates arriving TOGETHER: every racer is answered 2xx with the SAME
    checkout id (the one stored result). Deviation: more than one id (each racer
    executed and stored its own), a 409 on an identical payload (a duplicate is a
    cached-response case, never a conflict), a 5xx, or a transport failure."""
    j = r.json if isinstance(r.json, dict) else None
    if j is None:
        return DEVIATION
    n, sts, ids = j.get("n"), j.get("statuses"), j.get("ids")
    if not (isinstance(n, int) and isinstance(sts, list) and isinstance(ids, list)):
        return DEVIATION
    if len(sts) != n or len(ids) != n or n < 2:
        return DEVIATION
    if any(s not in (200, 201) for s in sts):
        return DEVIATION                    # 409 on identical bodies, 5xx, status 0
    if any(not isinstance(i, str) or not i for i in ids):
        return DEVIATION
    return CLEAN if len(set(ids)) == 1 else DEVIATION

_WH_GATES = ("webhooks.simulate", "complete_payment")
# The SIGNING cluster (ORD-026/027/028, SIG-014/015/017/027 — order.md "Webhook
# Signature Verification") and delivery RETRY (ORD-031) are gated on their own
# config assertions, exactly like `signature.responses` gates the response-signing
# checks: the reference server delivers order webhooks (samples#140) but does NOT
# yet sign them, send UCP-Agent on them, or retry failures — a capability the
# golden does not have, so forcing these on `webhooks.simulate` alone would
# false-deviate it (the audit's "correctly dormant, do not force" rule).
# validate_webhook_reference.py pins those gaps as tripwires so the moment
# upstream implements them we get a red prompt to flip the flower config.
_WH_SIGNED = _WH_GATES + ("webhooks.signed",)
_WH_RETRY = _WH_GATES + ("webhooks.retries",)

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
    # --- samples#263 class (Phase 5 watch, 2026-10-27): malformed SIGNER PROFILE ->
    #     the spec code, never a bare 500. The reference's envelope carries the code
    #     at messages[0].code (error_response.json), which is where the kills bite.
    MCheck("signature.signer_profile_url_malformed_400", ["NEG-005"], "MUST",
           sigprofile_bad_url_resp, p_sigprofile_invalid_url,
           ["status:500", "status:201", "status:422",
            "set:messages.0.code=\"profile_unreachable\"", "drop:messages",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk", "signature.resolves_signer_profile"), transport="rest", versions=V0408),
    MCheck("signature.signer_profile_nonobject_key_422", ["NEG-004"], "MUST",
           sigprofile_nonobject_entry_resp, p_sigprofile_malformed,
           ["status:500", "status:201", "status:401",
            "set:messages.0.code=\"key_not_found\"", "drop:messages",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk", "signature.resolves_signer_profile"), transport="rest", versions=V0408),
    MCheck("signature.signer_profile_unhashable_kid_401", ["SIG-033", "SIG-037"], "MUST",
           sigprofile_unhashable_kid_resp, p_sigprofile_key_not_found,
           ["status:500", "status:201", "status:400",
            "set:messages.0.code=\"signature_invalid\"", "drop:messages",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk", "signature.resolves_signer_profile"), transport="rest", versions=V0408,
           kills={"SIG-033": ["status:500", "status:201", "set:messages.0.code=\"signature_invalid\""],
                  "SIG-037": ["status:400", "drop:messages", "corrupt-json", "empty"]}),
    MCheck("signature.signer_profile_bad_coordinate_401", ["SIG-032", "SIG-036"], "MUST",
           sigprofile_bad_coordinate_resp, p_sigprofile_signature_invalid,
           ["status:500", "status:201", "status:400",
            "set:messages.0.code=\"key_not_found\"", "drop:messages",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk", "signature.resolves_signer_profile"), transport="rest", versions=V0408,
           kills={"SIG-032": ["status:500", "status:201", "set:messages.0.code=\"key_not_found\""],
                  "SIG-036": ["status:400", "drop:messages", "corrupt-json", "empty"]}),
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
           cfg_needs=_WH_SIGNED, transport="rest", versions=V0408),
    MCheck("webhook.signed_components", ["SIG-014", "SIG-015"], "MUST",
           wh_created_flow, p_webhook_components,
           ["set:events=[]",
            f"set:events.0.headers.signature-input={_WH_SI_NO_PATH}",
            f"set:events.0.headers.signature-input={_WH_SI_NO_DIGEST}",
            "drop:events.0.headers.signature-input",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_SIGNED, transport="rest", versions=V0408),
    MCheck("webhook.query_component_signed", ["SIG-017"], "MUST",
           wh_created_flow, p_webhook_query_signed,
           ["set:events=[]",
            f"set:events.0.headers.signature-input={_WH_SI_NO_QUERY}",
            "set:events.0.query=\"\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_SIGNED, transport="rest", versions=V0408),
    MCheck("webhook.idempotency_key_signed", ["SIG-016"], "MUST",
           wh_created_flow, p_webhook_idem_key_signed,
           ["set:events=[]",
            f"set:events.0.headers.signature-input={_WH_SI_NO_IDEM}",
            "drop:events.0.headers.idempotency-key",
            "drop:events.0.headers.signature-input",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_SIGNED, transport="rest", versions=V0408),
    MCheck("webhook.ucp_agent_signed", ["SIG-018"], "MUST",
           wh_created_flow, p_webhook_agent_signed,
           ["set:events=[]",
            f"set:events.0.headers.signature-input={_WH_SI_NO_AGENT}",
            "drop:events.0.headers.ucp-agent",
            "drop:events.0.headers.signature-input",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_SIGNED, transport="rest", versions=V0408),
    # SIG-041 rides the ORD-028 predicate deliberately: p_webhook_ucp_agent's
    # second assertion IS the SIG-041 rule — the business's UCP-Agent profile URL
    # on its own signed requests MUST point to /.well-known/ucp (signatures.md
    # UCP-Agent parsing rule 4 for business profiles).
    MCheck("webhook.ucp_agent_header", ["ORD-028", "SIG-041"], "MUST",
           wh_created_flow, p_webhook_ucp_agent,
           ["set:events=[]", "drop:events.0.headers.ucp-agent",
            "set:events.0.headers.ucp-agent=\"garbage\"",
            "set:events.0.headers.ucp-agent=\"profile=\\\"https://x.example/other\\\"\"",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_SIGNED, transport="rest", versions=V0408),
    MCheck("webhook.retry_failed_delivery", ["ORD-031"], "MUST",
           wh_retry_flow, p_webhook_retry,
           ["set:events=[]", "drop:events.1", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_RETRY, transport="rest", versions=V0408),
    # --- Phase 5 regression watches (2026-10-27) ---
    # samples#264: the Node reference stores, echoes and DELIVERS TO whatever the
    # platform profile publishes as webhook_url (an array coerces to a deliverable
    # string; a data: URL "succeeds" without leaving the process). Python gates it
    # since samples#257; the controlled fixture drops a non-string. On the Node
    # target this DEVIATES at pin e7c9c124 and is a self-expiring entry in
    # differential_allowlist.json until the fix merges and the samples pin moves.
    MCheck("webhook.platform_webhook_url_validated", ["OVR-058"], "MUST",
           wh_schema_violating_profile_flow, p_platform_webhook_url_validated,
           ["set:create_status=500",
            "set:platform={\"webhook_url\":[\"http://127.0.0.1:1/hook\"]}",
            "set:platform={\"webhook_url\":42}",
            "set:platform={\"webhook_url\":\"xts-not-a-url-2030\"}",
            "set:events=[{\"path\":\"/webhooks/ucp/orders\"}]",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_WH_GATES, transport="rest", versions=V0408),
    # samples#245 (python, merged) / samples#265 (nodejs, open): concurrent creates
    # under one key each persisted a checkout. The barrier profile makes the window
    # deterministic (every handler parked at its profile await before any write).
    # Our own controlled fixture had the same read-execute-store shape and was
    # fixed alongside this check (fixtures/merchant/server.py create_checkout).
    MCheck("signature.idempotency_concurrent_single_result", ["SIG-025"], "MUST",
           idem_concurrent_resp, p_idem_concurrent_single_result,
           ["set:ids=[\"chk_a\",\"chk_b\",\"chk_a\",\"chk_a\",\"chk_a\",\"chk_a\",\"chk_a\",\"chk_a\"]",
            "set:statuses=[500,201,201,201,201,201,201,201]",
            "set:statuses=[201,409,409,409,409,409,409,409]",
            "drop:ids", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("webhooks.simulate",), transport="rest", versions=V0408),
]

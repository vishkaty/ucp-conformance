#!/usr/bin/env python3
"""
merchant_checks_08_25_envelope.py — 2026-08-25 error envelope, negotiation, discovery and
idempotency MChecks (D1-16a, C4-B W1 half), graded on golden-0825 by the merchant-0825
gate against its pinned skip population.

Every id below was re-read against the pinned 08-25 register (SOURCES.lock cd78fb38):
  ERR-001/002/003/005/031  message_error.json / message.json — the error message shape on
                           a business error response (an unknown route: 404 not_found
                           envelope, D3-04's hardening; kill direction on the golden:
                           behavior mutant `unknown_route_plain_404`)
  ERR-009/010              message_warning.json — the rejected-discount-code warning
                           (discount.md#L138-L141; golden kill: `discount_reject_silent`)
  NEG-001                  overview/index.md#L1862,L3553 — an unadvertised platform
                           version in UCP-Agent -> 422 version_unsupported (golden kill:
                           `negotiation_accept_any`)
  DISC-003                 overview/index.md#L2240-L2242 — Cache-Control on the business's
                           published artifacts (its profile + any artifact it serves itself)
  CHK-048 item 2           checkout/rest.md#L1309-L1312 — same-key retry of Complete (CHK-078, the platform half, is agent-lane)
                           returns the cached result (seq_invariants I9 replay_cached, the
                           single implementation)
CHK-048 is graded by merchant_checks.idempotency.conflict_409 (versions=V_0825).
Not here (blocked by task id in coverage/wave1b_targets_0825.json): ERR-025/026 (no info
message on the golden, D3-28), DISC-001 (TLS-fronted golden, D1-20), CHK-079 (I9
fresh-key model, D4-07).
"""
import json
import sys
import uuid
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ci"))
from engine import Resp, fetch, CLEAN, DEVIATION                       # noqa: E402
from merchant_checks import (MCheck, _hdr, _create_payload, _create_for_complete,   # noqa: E402
                             disc_reject_resp, _dinvalid)
from merchant_checks_04_08_discovery import fetch_noredirect, _profile_url, _cache_control   # noqa: E402
from wire_shapes import shapes_for                                    # noqa: E402
import seq_invariants                                                 # noqa: E402

VERSIONS = ("2026-08-25",)
V0825 = ("2026-08-25",)

ERROR_SEVERITIES = ("recoverable", "requires_buyer_input", "requires_buyer_review", "unrecoverable")
MESSAGE_KINDS = ("error", "warning", "info")


# ---- fetches --------------------------------------------------------------------
def unknown_route_error_resp(ctx):
    """A route the business cannot have: its error response (404 not_found envelope)."""
    return fetch(ctx.shopping_endpoint, f"/nope-{uuid.uuid4().hex[:8]}", "GET", None, _hdr())


def _messages(r):
    msgs = (r.json or {}).get("messages") if isinstance(r.json, dict) else None
    return msgs if isinstance(msgs, list) else None


def _first_error(r):
    msgs = _messages(r)
    if not msgs or not isinstance(msgs[0], dict):
        return None
    return msgs[0]


def _rejected_warning(r, ctx):
    """The message that communicates the rejected code (found by content, so a dropped
    `code`/`type` field still locates it and fails the field check)."""
    for m in _messages(r) or []:
        if isinstance(m, dict) and _dinvalid(ctx) in str(m.get("content") or ""):
            return m
    return None


# ---- ERR-001/002/003/005/031: error message shape ---------------------------------
def p_error_fields(r):
    """ERR-001: the error message carries type, code, content and severity."""
    if not (400 <= r.status < 500):
        return DEVIATION
    m = _first_error(r)
    if m is None:
        return DEVIATION
    return CLEAN if all(m.get(k) not in (None, "") for k in ("type", "code", "content", "severity")) else DEVIATION


def p_error_type_const(r):
    """ERR-002: the error message's type discriminator is the constant "error"."""
    if not (400 <= r.status < 500):
        return DEVIATION
    m = _first_error(r)
    return CLEAN if m is not None and m.get("type") == "error" else DEVIATION


def p_error_severity_enum(r):
    """ERR-003: severity is one of the four spec values."""
    if not (400 <= r.status < 500):
        return DEVIATION
    m = _first_error(r)
    return CLEAN if m is not None and m.get("severity") in ERROR_SEVERITIES else DEVIATION


def p_error_content_type_enum(r):
    """ERR-005: content_type, when present, is plain or markdown."""
    if not (400 <= r.status < 500):
        return DEVIATION
    m = _first_error(r)
    if m is None:
        return DEVIATION
    ct = m.get("content_type")
    return CLEAN if ct is None or ct in ("plain", "markdown") else DEVIATION


def p_message_kinds(r):
    """ERR-031: every message is exactly one of error / warning / info by its type."""
    if not (400 <= r.status < 500):
        return DEVIATION
    msgs = _messages(r)
    if not msgs:
        return DEVIATION
    return CLEAN if all(isinstance(m, dict) and m.get("type") in MESSAGE_KINDS for m in msgs) else DEVIATION


# ---- ERR-009/010: warning message shape (rejected discount code) ------------------
def p_warning_fields(r, ctx):
    """ERR-009: the warning message carries type, code and content."""
    if r.status != 200:
        return DEVIATION
    m = _rejected_warning(r, ctx)
    if m is None:
        return DEVIATION
    return CLEAN if all(m.get(k) not in (None, "") for k in ("type", "code", "content")) else DEVIATION


def p_warning_type_const(r, ctx):
    """ERR-010: the warning message's type discriminator is the constant "warning"."""
    if r.status != 200:
        return DEVIATION
    m = _rejected_warning(r, ctx)
    return CLEAN if m is not None and m.get("type") == "warning" else DEVIATION


# ---- NEG-001: unadvertised platform version -> 422 version_unsupported ------------
UNADVERTISED_VERSION = "2099-01-01"


def neg_unsupported_version_resp(ctx):
    """Otherwise-valid create whose UCP-Agent declares a protocol version the business
    advertises nowhere ({its version} ∪ supported_versions) — the 08-25 header form
    (wire_shapes.headers version_param), not the 04-08 fetched-profile form."""
    h = shapes_for(ctx.version).headers(version_param=UNADVERTISED_VERSION)
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", _create_payload(ctx), h)


def p_version_unsupported_0825(r):
    """NEG-001@08-25: HTTP 422 with a UCP error envelope whose message code is
    version_unsupported (the 08-25 envelope carries the code in messages[], not at the
    root as the 04-08 transport-error body did)."""
    if r.status != 422:
        return DEVIATION
    m = _first_error(r)
    return CLEAN if m is not None and m.get("code") == "version_unsupported" else DEVIATION


# ---- DISC-003: Cache-Control on the business's published artifacts ---------------
def _self_hosted_artifacts(ctx):
    """URLs of artifacts the business itself publishes: its profile, plus every
    spec/schema URL in the profile on the profile's own host (an artifact hosted by a
    third party — ucp.dev — is that publisher's duty, not this business's)."""
    from urllib.parse import urlsplit
    prof = _profile_url(ctx)
    host = urlsplit(prof).netloc
    urls = [prof]
    doc = ctx.profile.get("ucp", ctx.profile) if isinstance(ctx.profile, dict) else {}
    for section in ("capabilities", "services", "payment_handlers"):
        entries = (doc.get(section) or {}) if isinstance(doc, dict) else {}
        for v in (entries.values() if isinstance(entries, dict) else []):
            for e in (v if isinstance(v, list) else [v]):
                if not isinstance(e, dict):
                    continue
                for k in ("spec", "schema", "config_schema"):
                    u = e.get(k)
                    if isinstance(u, str) and urlsplit(u).netloc == host and u not in urls:
                        urls.append(u)
    return urls


def published_artifacts_resp(ctx):
    """One synthetic Resp aggregating each self-published artifact's status and
    Cache-Control, so engine.mutate can inject a defect into the graded fields."""
    arts = []
    for u in _self_hosted_artifacts(ctx):
        r = fetch_noredirect(u)
        arts.append({"url": u, "status": r.status, "cache_control": _cache_control(r)})
    return Resp(200, {"Content-Type": "application/json"}, json.dumps({"artifacts": arts}).encode())


def _cc_ok(cc):
    if not cc:
        return False
    directives = [d.strip().lower() for d in str(cc).split(",")]
    if "public" not in directives or any(b in directives for b in ("private", "no-store", "no-cache")):
        return False
    ages = [d for d in directives if d.startswith("max-age=")]
    try:
        return bool(ages) and int(ages[0].split("=", 1)[1]) >= 60
    except ValueError:
        return False


def p_published_artifacts_cache_control(r):
    """DISC-003@08-25: every published artifact response (the profile and any artifact
    the business serves itself) is 200 with Cache-Control public, max-age>=60 and none
    of private/no-store/no-cache."""
    arts = (r.json or {}).get("artifacts") if isinstance(r.json, dict) else None
    if not isinstance(arts, list) or not arts:
        return DEVIATION
    for a in arts:
        if not isinstance(a, dict) or a.get("status") != 200 or not _cc_ok(a.get("cache_control")):
            return DEVIATION
    return CLEAN


# ---- CHK-048 item 2: same-key retry of Complete returns the cached result (I9) -----
def idem_replay_resp(ctx):
    """create -> complete (key K) -> Get -> complete again with K and the SAME body; one
    synthetic Resp {first, get, replay} so the I9 predicate and the mutations see both."""
    cid = (_create_for_complete(ctx).json or {}).get("id")
    body = ctx.config.get("complete_payment")
    k = str(uuid.uuid4())
    first = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST", body, _hdr(k))
    got = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "GET", None, _hdr())
    replay = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST", body, _hdr(k))
    doc = {"first": {"status": first.status, "body": first.json},
           "get": {"status": got.status},
           "replay": {"status": replay.status, "body": replay.json}}
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(doc).encode())


def p_replay_cached(r):
    """CHK-078 via seq_invariants.replay_cached (the single I9 implementation)."""
    d = r.json if isinstance(r.json, dict) else None
    if not d or not isinstance(d.get("first"), dict) or not isinstance(d.get("replay"), dict):
        return DEVIATION
    ok, _detail = seq_invariants.replay_cached(d["first"].get("status"), d["first"].get("body"),
                                               d["replay"].get("status"), d["replay"].get("body"))
    return CLEAN if ok else DEVIATION


CHECKS_08_25_ENVELOPE = [
    MCheck("envelope.error_message_fields", ["ERR-001"], "MUST", unknown_route_error_resp, p_error_fields,
           ["drop:messages.0.severity", "drop:messages.0.code", "drop:messages.0.content",
            "set:messages=[]", "corrupt-json"], transport="rest", versions=V0825),
    MCheck("envelope.error_type_const", ["ERR-002"], "MUST", unknown_route_error_resp, p_error_type_const,
           ['set:messages.0.type="warning"', "drop:messages.0.type", "corrupt-json"],
           transport="rest", versions=V0825),
    MCheck("envelope.error_severity_enum", ["ERR-003"], "MUST", unknown_route_error_resp, p_error_severity_enum,
           ['set:messages.0.severity="critical"', "drop:messages.0.severity", "corrupt-json"],
           transport="rest", versions=V0825),
    MCheck("envelope.error_content_type_enum", ["ERR-005"], "MUST", unknown_route_error_resp,
           p_error_content_type_enum,
           ['set:messages.0.content_type="html"', 'set:messages.0.content_type=""', "corrupt-json"],
           transport="rest", versions=V0825),
    MCheck("envelope.message_kinds_discriminated", ["ERR-031"], "MUST", unknown_route_error_resp, p_message_kinds,
           ['set:messages.0.type="fatal"', "drop:messages.0.type", "set:messages=[]", "corrupt-json"],
           transport="rest", versions=V0825),
    MCheck("envelope.warning_message_fields", ["ERR-009"], "MUST", disc_reject_resp, p_warning_fields,
           ["drop:messages.0.code", "drop:messages.0.type", "set:messages=[]", "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount", "discount.rejected_messages"), transport="rest", versions=V0825),
    MCheck("envelope.warning_type_const", ["ERR-010"], "MUST", disc_reject_resp, p_warning_type_const,
           ['set:messages.0.type="info"', "drop:messages.0.type", "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount", "discount.rejected_messages"), transport="rest", versions=V0825),
    MCheck("negotiation.version_unsupported_422_0825", ["NEG-001"], "MUST", neg_unsupported_version_resp,
           p_version_unsupported_0825,
           ["status:400", "status:201", 'set:messages.0.code="unsupported_version"', "drop:messages",
            "corrupt-json"], needs=("product",), transport="rest", versions=V0825),
    MCheck("discovery.published_artifacts_cache_control", ["DISC-003"], "MUST", published_artifacts_resp,
           p_published_artifacts_cache_control,
           ['set:artifacts.0.cache_control="private, max-age=300"', 'set:artifacts.0.cache_control="no-store"',
            'set:artifacts.0.cache_control="public, no-cache, max-age=300"',
            'set:artifacts.0.cache_control="public, max-age=30"', "drop:artifacts.0.cache_control",
            "set:artifacts=[]", "corrupt-json"], transport="rest", versions=V0825),
    # W1 integration (D2-08 lane rule): CHK-078 binds the PLATFORM alone (MAY retry identically,
    # MUST NOT switch keys) — a merchant check may not cover it; the business's half of the same
    # protocol is CHK-048 item 2 ("Return the cached result for duplicate keys whose request body
    # matches the original", rest.md#L1309-L1312), which is what this check grades.
    MCheck("idempotency.replay_cached", ["CHK-048"], "MUST", idem_replay_resp, p_replay_cached,
           ["set:replay.status=409", "set:replay.status=500", 'set:replay.body.id="other"',
            "drop:replay.body.order", 'set:replay.body.status="incomplete"', "corrupt-json"],
           capability="dev.ucp.shopping.order", needs=("product",), cfg_needs=("complete_payment",),
           transport="rest", versions=V0825),
]

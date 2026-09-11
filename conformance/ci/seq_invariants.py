#!/usr/bin/env python3
"""
seq_invariants.py — sequence invariants over a UCP business, as PURE functions with spec
anchors (PLAN-v3 §2.10). OWNER: D4-07 (I1..I10, seqfuzz_gate.py, replay_mode.json). This
file was created by D1-16a with ONLY the I9 idempotency triad, against the §2.10 interface,
so the CHK-048/CHK-078 MChecks (merchant_checks_08_25_envelope.py) import the single
implementation rather than restating the invariant text (RV1 C14). D4-07 extends it in
place; nothing here may be duplicated elsewhere.

I9 — idempotency (checkout/rest.md#L1309-L1312 CHK-048, checkout/index.md#L1109-L1118
CHK-078/079): for a Complete Checkout carrying an Idempotency-Key,
  replay_cached      same key + byte-equal body -> the CACHED result (same status, same
                     checkout id, same order id), never a second order;
  mismatch_409       same key + different body -> 409 with a UCP error envelope;
  terminal_rejected  a genuinely new Complete after the checkout reached a terminal state
                     (completed / canceled) -> a 4xx error envelope, never a second order.
Each returns (ok: bool, detail: str). Inputs are plain status ints and decoded JSON bodies.
"""

I9_ANCHORS = {
    "replay_cached": "docs/specification/shopping/checkout/rest.md#L1309-L1312 (CHK-048); "
                     "checkout/index.md#L1113-L1115 (CHK-078)",
    "mismatch_409": "docs/specification/shopping/checkout/rest.md#L1309-L1312 (CHK-048)",
    "terminal_rejected": "docs/specification/shopping/checkout/index.md#L1028-L1029 (CHK-017); "
                         "#L1116-L1118 (CHK-079)",
}


def _order_id(body):
    order = body.get("order") if isinstance(body, dict) else None
    return order.get("id") if isinstance(order, dict) else None


def _is_error_envelope(body):
    if not isinstance(body, dict):
        return False
    ucp, msgs = body.get("ucp"), body.get("messages")
    return isinstance(ucp, dict) and ucp.get("status") == "error" \
        and isinstance(msgs, list) and len(msgs) >= 1


def replay_cached(first_status, first_body, replay_status, replay_body):
    """I9a: a same-key, same-body retry returns the cached result."""
    if first_status != 200 or not isinstance(first_body, dict):
        return False, f"first completion not 200/object (status {first_status})"
    if replay_status != first_status:
        return False, f"replay status {replay_status} != cached {first_status}"
    if not isinstance(replay_body, dict):
        return False, "replay body is not an object"
    if replay_body.get("id") != first_body.get("id"):
        return False, f"replay checkout id {replay_body.get('id')!r} != cached {first_body.get('id')!r}"
    if replay_body.get("status") != first_body.get("status"):
        return False, f"replay status field {replay_body.get('status')!r} != cached {first_body.get('status')!r}"
    if _order_id(replay_body) != _order_id(first_body):
        return False, f"replay order {_order_id(replay_body)!r} != cached {_order_id(first_body)!r} (a second order?)"
    return True, "cached result replayed"


def mismatch_409(status, body):
    """I9b: a same-key, different-body request is rejected with 409 + UCP error envelope."""
    if status != 409:
        return False, f"expected 409 on a mismatched replay, got {status}"
    if not _is_error_envelope(body):
        return False, "409 without a UCP error envelope (ucp.status error + messages[])"
    return True, "409 with error envelope"


def terminal_rejected(status, body):
    """I9c: a new operation on a terminal checkout is a 4xx error envelope, never a 2xx."""
    if not (400 <= int(status) < 500):
        return False, f"expected 4xx after a terminal state, got {status}"
    if not _is_error_envelope(body):
        return False, f"{status} without a UCP error envelope"
    return True, f"{status} with error envelope"

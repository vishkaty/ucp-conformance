#!/usr/bin/env python3
"""
seq_invariants.py — the checkout SEQUENCE invariants (D4-07 / E3), each a PURE function over
one observed step with its spec anchor. Consumed by seqfuzz_gate.py (random sequences against
a disposable golden-0825), by the E4 hypothesis state machine (D4-11) and — for I10 — by
D1-16b's REPLAY-002 Row and D3-23's `idem_hash_json_equal` mutant: I10 `replay_reserialized`
is THE single implementation of REPLAY-002 (RV2 #13); nobody re-implements it.

A step is a plain dict:
  {"op": create|get|update|complete|cancel|replay_same|replay_mismatch|replay_reserialized,
   "status": int, "body": dict|None, "prev": {"status": str|None, "id": str|None, "order_id": ...},
   "orig": {"status": int, "id": str|None}   # for replay_* steps: the ORIGINAL response
   "timed_out": bool}
Each invariant returns None (holds) or a short violation string. `check(step, mode)` runs all
ten; I10's outcome depends on `mode` ("enforce" -> violation, "report" -> a report line):
conformance/ci/replay_mode.json decides the mode per context (decision 1).
"""
import datetime, json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
REPLAY_MODE = HERE / "replay_mode.json"

# checkout.json `status` enum at 2026-08-25
STATUS_ENUM = ("incomplete", "requires_escalation", "ready_for_complete",
               "complete_in_progress", "completed", "canceled")
TERMINAL = ("completed", "canceled")
MUTATING = ("update", "complete", "cancel")


def _status(step):
    b = step.get("body")
    return b.get("status") if isinstance(b, dict) else None


def _ok(step):
    return 200 <= int(step.get("status", 0)) < 300


def status_in_enum(step):
    """I1 — checkout.json `status` enum (schemas/shopping/checkout.json#/properties/status)."""
    if not _ok(step) or step["op"] in ("replay_mismatch",):
        return None
    st = _status(step)
    if st is not None and st not in STATUS_ENUM:
        return f"status {st!r} not in the checkout status enum"
    return None


def terminal_never_left(step):
    """I2 — a completed/canceled checkout never changes status (checkout/index.md#L1028-L1029, CHK-017)."""
    prev = (step.get("prev") or {}).get("status")
    if prev in TERMINAL and _ok(step) and step["op"] in MUTATING + ("get",):
        st = _status(step)
        if st is not None and st != prev:
            return f"terminal state {prev} left: {step['op']} -> {st}"
    return None


def in_progress_has_no_order(step):
    """I3 — `complete_in_progress` carries no `order` (checkout/index.md#L397-L398)."""
    if _ok(step) and _status(step) == "complete_in_progress" and (step.get("body") or {}).get("order"):
        return "complete_in_progress response carries an order"
    return None


def completed_has_order(step):
    """I4 — `completed` carries `order` with an id (checkout/index.md#L397-L398)."""
    if _ok(step) and _status(step) == "completed":
        order = (step.get("body") or {}).get("order")
        if not isinstance(order, dict) or not order.get("id"):
            return "completed checkout without order.id"
    return None


def totals_consistent(step):
    """I5 — exactly one `subtotal` and one `total`; total == sum of every other entry
    (discounts carry a negative amount; checkout/index.md#L1332 class, AMB-004)."""
    if not _ok(step) or step["op"] == "replay_mismatch":
        return None
    totals = (step.get("body") or {}).get("totals")
    if totals is None:
        return None
    kinds = [t.get("type") for t in totals if isinstance(t, dict)]
    if kinds.count("subtotal") != 1 or kinds.count("total") != 1:
        return f"totals need exactly one subtotal and one total, got {kinds}"
    try:
        total = next(int(t["amount"]) for t in totals if t.get("type") == "total")
        others = sum(int(t["amount"]) for t in totals if t.get("type") != "total")
    except (KeyError, TypeError, ValueError):
        return "totals amounts are not integers"
    if total < 0:
        return f"negative total {total}"
    if total != others:
        return f"total {total} != sum of the other entries {others}"
    return None


def escalation_has_continue_url(step):
    """I6 — `requires_escalation` carries `continue_url` (checkout/index.md#L1110-L1118)."""
    if _ok(step) and _status(step) == "requires_escalation" and not (step.get("body") or {}).get("continue_url"):
        return "requires_escalation without continue_url"
    return None


def client_error_envelope(step):
    """I7 — every 4xx carries the UCP error envelope: ucp.status == error and messages[]
    of type error with a code (common/types/error_response.json#L7-L20; ERR-028..031, #L489-L493)."""
    st = int(step.get("status", 0))
    if 400 <= st < 500:
        b = step.get("body")
        if not isinstance(b, dict):
            return f"{st} without a JSON body"
        ucp = b.get("ucp") or {}
        msgs = b.get("messages") or []
        if ucp.get("status") != "error":
            return f"{st} envelope ucp.status is {ucp.get('status')!r}, not 'error'"
        if not msgs or any(not (isinstance(m, dict) and m.get("type") == "error" and m.get("code")) for m in msgs):
            return f"{st} envelope messages[] missing a type=error entry with a code"
    return None


def no_server_error(step):
    """I8 — never a 5xx, never a hang (checkout/index.md#L489-L493 class: recoverable outcomes)."""
    if step.get("timed_out"):
        return f"{step['op']} timed out (hang)"
    if int(step.get("status", 0)) >= 500:
        return f"{step['op']} returned {step['status']}"
    if int(step.get("status", 0)) == 0:
        err = (step.get("body") or {}).get("_transport_error") if isinstance(step.get("body"), dict) else None
        return f"{step['op']} transport failure: {err or 'no HTTP response'}"
    return None


def replay_semantics(step):
    """I9 — same key + same bytes -> the cached response (same status, same id; SIG-025);
    same key + mismatched payload -> 409 (REPLAY-001, signatures.md#L826-L845); update/cancel
    after a terminal state -> rejected 4xx (CHK-017)."""
    op = step["op"]
    orig = step.get("orig") or {}
    if op == "replay_same":
        if int(step["status"]) != int(orig.get("status", -1)):
            return f"same-bytes replay answered {step['status']} but the original was {orig.get('status')}"
        bid = (step.get("body") or {}).get("id") if isinstance(step.get("body"), dict) else None
        if orig.get("id") and bid != orig["id"]:
            return f"same-bytes replay returned id {bid!r} != original {orig['id']!r} (executed twice)"
    elif op == "replay_mismatch":
        if int(step["status"]) != 409:
            return f"mismatched-payload replay answered {step['status']}, REPLAY-001 requires 409"
    elif op in ("update", "cancel") and (step.get("prev") or {}).get("status") in TERMINAL:
        if _ok(step):
            return f"{op} after {step['prev']['status']} was accepted ({step['status']})"
    return None


def replay_reserialized(step, mode="enforce"):
    """I10 — REPLAY-002 (signatures.md#L837-L845): payload matching compares the SHA-256 of the
    RAW BODY BYTES, so a byte-different, JSON-equal retry under the same key MUST get 409.
    mode "enforce": a cached 2xx is a violation; mode "report": the same observation is a
    REPORT line (decision 1; citation + KI-009), never a violation. THE single implementation."""
    if step["op"] != "replay_reserialized":
        return None
    st = int(step["status"])
    if st == 409:
        return None
    if 200 <= st < 300:
        msg = (f"re-serialized (JSON-equal, byte-different) replay was CACHED ({st}) — REPLAY-002 "
               f"requires 409 (signatures.md#L837-L845; KI-009)")
        return msg if mode == "enforce" else f"REPORT: {msg}"
    return f"re-serialized replay answered {st}, expected 409"


INVARIANTS = {
    "I1": (status_in_enum, "schemas/shopping/checkout.json#/properties/status"),
    "I2": (terminal_never_left, "shopping/checkout/index.md#L1028-L1029 (CHK-017)"),
    "I3": (in_progress_has_no_order, "shopping/checkout/index.md#L397-L398"),
    "I4": (completed_has_order, "shopping/checkout/index.md#L397-L398"),
    "I5": (totals_consistent, "shopping/checkout/index.md#L1332 (AMB-004)"),
    "I6": (escalation_has_continue_url, "shopping/checkout/index.md#L1110-L1118"),
    "I7": (client_error_envelope, "common/types/error_response.json#L7-L20 (ERR-028..031)"),
    "I8": (no_server_error, "shopping/checkout/index.md#L489-L493"),
    "I9": (replay_semantics, "signatures.md#L826-L845 (REPLAY-001, SIG-025, CHK-017)"),
    "I10": (replay_reserialized, "signatures.md#L837-L845 (REPLAY-002)"),
}


def load_replay_mode(path=REPLAY_MODE):
    return json.loads(pathlib.Path(path).read_text())


def resolve_mode(context, today=None, mode=None):
    """The I10 mode for a context: `golden_gates` -> its value; `user_facing` -> its value
    until `report_until` (inclusive), then `enforce` (self-expiring; decision 1)."""
    m = mode or load_replay_mode()
    today = today or datetime.date.today()
    if context == "user_facing":
        until = datetime.date.fromisoformat(m["report_until"])
        return m["user_facing"] if today <= until else "enforce"
    return m.get(context, "enforce")


def check(step, mode="enforce"):
    """[(id, message)] — every invariant that does not hold on this step; I10 under `mode`.
    A message starting with 'REPORT:' is a report line, not a violation."""
    out = []
    for iid, (fn, _anchor) in INVARIANTS.items():
        msg = fn(step, mode) if iid == "I10" else fn(step)
        if msg:
            out.append((iid, msg))
    return out

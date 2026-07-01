#!/usr/bin/env python3
"""
area_lifecycle2.py — additional checkout-lifecycle + idempotency checks for spec
2026-01-23, verified against the LIVE reference server.

Scope: behaviors NOT covered by core (v2026_01_23.py). Core already covers create
(CHK-001), get (CHK-002), cancel (CHK-005), complete (CHK-004/008), completed-
immutable-cancel (CHK-012) and idempotency-409 (IDM-004). This module adds:

  * CHK-003 / CHK-006 / CHK-007 — Update via PUT is a FULL REPLACEMENT of the
    checkout resource. Stateful: create a session, PUT a full resource with a
    changed line-item quantity, then GET; the observed quantity MUST be the PUT
    value (the write replaced session state).
  * IDM-003 — cached replay: same Idempotency-Key + same body returns the cached
    result (identical status + body). Exercised on create (POST) and cancel.
  * CHK-013 — status field is present and constrained to the six-value lifecycle
    enum on a real response.
  * CHK-012 — completed is immutable to UPDATE (distinct verb from core's cancel).
  * CHK-010 — a canceled session cannot be canceled again / updated (SHOULD -> the
    reference server rejects with a conflict, matching the oracle's non-200).

Each check is self-validated by the engine (clean-pass on the live response AND
every declared mutation must break it). Only clean + kill_safe checks are shipped.
"""
import sys, pathlib, uuid  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check, fetch                       # noqa: E402
import v2026_01_23 as core                            # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION             # noqa: E402

STATUS_ENUM = core.STATUS_ENUM
_PUT_QTY = 5   # replacement quantity, distinct from the create default of 1


# ---- fetch_fns (stateful sequences returning the final response under test) ----
def _full_replace_get(base):
    """Create -> PUT full resource with quantity=_PUT_QTY -> GET. Returns GET."""
    r = core._create(base)
    j = r.json or {}
    cid = j.get("id")
    li = (j.get("line_items") or [{}])[0]
    put_body = {
        "id": cid, "currency": j.get("currency", "USD"),
        "line_items": [{"id": li.get("id"), "quantity": _PUT_QTY,
                        "item": {"id": (li.get("item") or {}).get("id")}}],
        "payment": {"instruments": (j.get("payment") or {}).get("instruments", []),
                    "handlers": (j.get("payment") or {}).get("handlers", [])},
    }
    fetch(base, f"/checkout-sessions/{cid}", "PUT", put_body, core._ucp_headers())
    return fetch(base, f"/checkout-sessions/{cid}", "GET", None, core._ucp_headers())


def _idem_replay_create(base):
    """Same key + same body, twice. Returns the replay wrapped so the predicate can
    compare it to the first response (both bodies embedded)."""
    k = str(uuid.uuid4())
    body = core._create_payload()
    r1 = fetch(base, "/checkout-sessions", "POST", body, core._ucp_headers(k))
    r2 = fetch(base, "/checkout-sessions", "POST", body, core._ucp_headers(k))
    # Embed the first result inside the replay's json so a single-response predicate
    # (and the mutation engine, which mutates only this response) can evaluate it.
    r2.json = {"_first": r1.json, "_first_status": r1.status, "_replay": r2.json}
    r2.body = None
    return r2


def _idem_replay_cancel(base):
    k = str(uuid.uuid4())
    cid = (core._create(base).json or {}).get("id")
    c1 = fetch(base, f"/checkout-sessions/{cid}/cancel", "POST", None, core._ucp_headers(k))
    c2 = fetch(base, f"/checkout-sessions/{cid}/cancel", "POST", None, core._ucp_headers(k))
    c2.json = {"_first": c1.json, "_first_status": c1.status, "_replay": c2.json}
    c2.body = None
    return c2


def _completed_then_update(base):
    """Create -> complete -> PUT update. Returns the update response (must be rejected)."""
    cid = (core._create(base).json or {}).get("id")
    core._complete(base, cid)
    return fetch(base, f"/checkout-sessions/{cid}", "PUT",
                 {"id": cid, "currency": "USD",
                  "line_items": [{"id": "line_item_123", "quantity": 2,
                                  "item": {"id": "bouquet_roses"}}]},
                 core._ucp_headers())


def _canceled_then_cancel(base):
    """Create -> cancel -> cancel again. Returns the second cancel (must be rejected)."""
    cid = (core._create(base).json or {}).get("id")
    fetch(base, f"/checkout-sessions/{cid}/cancel", "POST", None, core._ucp_headers())
    return fetch(base, f"/checkout-sessions/{cid}/cancel", "POST", None, core._ucp_headers())


# ---- predicates -------------------------------------------------------------
def chk_full_replace(r):    # CHK-003 / CHK-006 / CHK-007
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    li = (r.json.get("line_items") or [{}])[0]
    return CLEAN if li.get("quantity") == _PUT_QTY else DEVIATION


def chk_idem_replay(r):     # IDM-003
    d = r.json if isinstance(r.json, dict) else {}
    first, replay = d.get("_first"), d.get("_replay")
    if not isinstance(first, dict) or not isinstance(replay, dict):
        return DEVIATION
    if d.get("_first_status") != r.status:   # replay status must match first
        return DEVIATION
    return CLEAN if first == replay else DEVIATION


def chk_status_enum(r):     # CHK-013
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("status") in STATUS_ENUM else DEVIATION


def chk_rejected_4xx(r):    # CHK-010 / CHK-012 terminal-state mutation rejected
    return CLEAN if 400 <= r.status < 500 else DEVIATION


CHECKS = [
    # Full replacement: PUT writes the entire resource; the changed quantity is
    # observable on a subsequent GET. Mutations break the observed quantity or the
    # 200 status.
    Check("checkout.update_full_replace", ["CHK-003", "CHK-006", "CHK-007"], "MUST",
          _full_replace_get, chk_full_replace,
          ["status:404", "status:500",
           "set:line_items=[{\"id\":\"line_item_123\",\"quantity\":1}]",
           "drop:line_items.0.quantity", "drop:line_items", "empty", "corrupt-json"]),

    # Idempotent cached replay on create: same key + same body returns identical
    # result. Mutating the replay body or status desyncs it from the first.
    Check("idempotency.replay_create", ["IDM-003"], "MUST",
          _idem_replay_create, chk_idem_replay,
          ["status:409", "drop:_replay.id", "set:_replay={}",
           "drop:_replay", "drop:_first"]),

    # Idempotent cached replay on cancel: duplicate cancel with the same key
    # returns the identical cached body.
    Check("idempotency.replay_cancel", ["IDM-003"], "MUST",
          _idem_replay_cancel, chk_idem_replay,
          ["status:409", "drop:_replay.status", "set:_replay={}",
           "drop:_replay", "drop:_first"]),

    # status field present and within the six-value lifecycle enum.
    Check("checkout.status_enum", ["CHK-013"], "MUST",
          core.f_create, chk_status_enum,
          ["drop:status", "set:status=\"bogus\"", "set:status=null",
           "status:500", "empty"]),

    # completed is immutable to UPDATE (PUT) — distinct verb from core's cancel path.
    Check("checkout.completed_immutable_update", ["CHK-012"], "MUST",
          _completed_then_update, chk_rejected_4xx,
          ["status:200", "status:201", "status:500"]),

    # a canceled session cannot be canceled again (terminal-state conflict).
    Check("checkout.canceled_not_cancelable", ["CHK-010"], "MUST",
          _canceled_then_cancel, chk_rejected_4xx,
          ["status:200", "status:201", "status:500"]),
]

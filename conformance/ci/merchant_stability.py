#!/usr/bin/env python3
"""
merchant_stability.py — the ISOLATION safety net.

The agent-conformance workstream lives in its own tree (conformance/agent/) and is
structurally invisible to the merchant machinery. This gate is the belt-and-suspenders
on top of that: it snapshots the merchant golden fixture's canonical responses and
fails the build if ANYTHING agent work touches changes a merchant-visible byte.

It captures a representative set of merchant responses (discovery, checkout, catalog,
cart, MCP, A2A), normalizes per-request volatility (generated ids, timestamps, nonces,
signatures), and diffs against a committed snapshot.

  merchant_stability.py --server URL --record   # (re)record the baseline (deliberate)
  merchant_stability.py --server URL             # gate: exit 1 on any drift

Recording is a DELIBERATE act — a diff means "you changed a merchant response"; only
re-record when that change is intended and reviewed.
"""
import argparse, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "merchant_golden_snapshot.json")

# keys whose values are per-request volatile — normalized to a placeholder so the
# snapshot is stable across runs while still catching any STRUCTURAL/value change.
VOLATILE_KEY = re.compile(
    r"(^id$|_id$|^expires|^created|^updated|time$|timestamp|nonce|"
    r"messageid|contextid|permalink|continue_url|^keyid$|^tag$)", re.I)
VOLATILE_HEADERS = {"signature", "signature-input", "content-digest", "date",
                    "content-length"}


def _norm(o):
    if isinstance(o, dict):
        return {k: ("<VOL>" if VOLATILE_KEY.search(k) else _norm(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_norm(v) for v in o]
    if isinstance(o, str) and (o.startswith(("chk_", "ord_", "cart_")) or
                               re.fullmatch(r"[0-9a-f-]{8,}", o)):
        return "<VOL>"
    return o


def _req(server, path, method="GET", body=None, headers=None):
    url = server.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:
        return {"error": str(e)}
    try:
        j = json.loads(raw)
    except Exception:
        return {"status": status, "raw": raw[:200]}
    return {"status": status, "body": _norm(j)}


def _agent_hdr():
    import uuid
    return {"UCP-Agent": 'profile="https://spck.dev/agent"', "request-signature": "test",
            "request-id": str(uuid.uuid4()), "idempotency-key": str(uuid.uuid4())}


def capture(server):
    """The canonical merchant-response surface. Deterministic after normalization."""
    import uuid
    ck = {"id": str(uuid.uuid4()), "currency": "USD",
          "line_items": [{"id": "li_1", "quantity": 1,
                          "item": {"id": "teapot_ceramic", "price": 1000}, "totals": []}],
          "payment": {"instruments": [], "handlers": []}, "status": "incomplete",
          "ucp": {"version": "2026-04-08"}, "totals": [], "links": []}
    meta = {"ucp-agent": {"profile": "https://spck.dev/agent"}}
    return {
        "discovery": _req(server, "/.well-known/ucp"),
        "checkout_create": _req(server, "/checkout-sessions", "POST", ck, _agent_hdr()),
        "catalog_search": _req(server, "/catalog/search", "POST",
                               {"query": "*"}, _agent_hdr()),
        "catalog_product": _req(server, "/catalog/product", "POST",
                                {"id": "teapot_ceramic"}, _agent_hdr()),
        "cart_create": _req(server, "/carts", "POST",
                            {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 1}]},
                            _agent_hdr()),
        "mcp_search": _req(server, "/ucp/mcp", "POST",
                          {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "search_catalog",
                                      "arguments": {"meta": meta, "catalog": {"query": "*"}}}}),
        "a2a_checkout": _req(server, "/ucp/a2a", "POST",
                            {"jsonrpc": "2.0", "id": 1, "method": "message/send",
                             "params": {"message": {"role": "user", "kind": "message",
                                        "parts": [{"kind": "data",
                                                   "data": {"action": "create_checkout"}}]}}}),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default="http://localhost:8184")
    ap.add_argument("--record", action="store_true", help="(re)record the baseline snapshot")
    args = ap.parse_args()

    cur = capture(args.server)
    if args.record:
        json.dump(cur, open(SNAPSHOT, "w"), indent=1, sort_keys=True)
        print(f"merchant-stability: recorded baseline ({len(cur)} surfaces) -> {SNAPSHOT}")
        return 0
    if not os.path.exists(SNAPSHOT):
        print("merchant-stability: SKIP — no baseline yet (run --record once).")
        return 2
    base = json.load(open(SNAPSHOT))
    drift = []
    for k in sorted(set(base) | set(cur)):
        if base.get(k) != cur.get(k):
            drift.append(k)
    if drift:
        print("merchant-stability gate: FAIL — merchant golden responses drifted "
              f"(agent work must not change merchant output): {drift}")
        for k in drift:
            print(f"  --- {k} ---")
            print(f"    baseline: {json.dumps(base.get(k))[:300]}")
            print(f"    current : {json.dumps(cur.get(k))[:300]}")
        return 1
    print(f"merchant-stability gate: PASS — all {len(base)} merchant golden surfaces "
          f"byte-identical (normalized).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

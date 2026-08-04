#!/usr/bin/env python3
"""
fuzz_gate.py — the SCHEMA-GUIDED FUZZ GATE: fire fuzz_corpus.py at a reference server,
classify every response, and FAIL on any NEW server-side crash (5xx / connection reset /
hang) that is not in the self-expiring known_fuzz_defects.json register.

This is the bug-discovery engine that pairs with the dual-oracle referee. Where the
merchant checks fire hand-authored payloads (and so can only find the bugs someone
thought to write), this gate enumerates the CONSTRAINT POINTS of the pinned 2026-04-08
request schemas and fires one boundary payload per point — the #156 currency-omit 500
was found by luck; this finds the whole #156 CLASS systematically.

CLASSIFICATION (credibility is the product — no false alarms).
  Every generated payload gets an EXPECTED-VALIDITY verdict from the INDEPENDENT Python
  referee (dual_oracle_referee.Referee), the same second oracle the dual-oracle gate
  trusts. Then the server's response is bucketed:

    * 5xx / status 0 (connection reset / crash) / timeout (hang)   -> CRASH   (FINDING)
    * 2xx on a referee-INVALID payload                             -> ACCEPT  (spec-
        contradicting acceptance — a soft finding; reported for human triage, NOT gate-
        failing, because schema-stricter-than-contract and language type-coercion
        (e.g. bool-as-int, null-as-absent) are defensible and would otherwise cry wolf)
    * 4xx with a JSON envelope body                                -> CONFORMANT rejection
    * 4xx with no/again-non-JSON body                              -> NO-ENVELOPE (soft)
    * 2xx on a referee-VALID payload                               -> conformant accept

  ONLY the CRASH bucket fails the gate — an unambiguous, reproducible server defect —
  and only when the crash is NEW (matches no register entry). A 4xx with an envelope is
  a conformant rejection of a malformed probe and is never a finding.

REGISTER + SELF-EXPIRY (known_fuzz_defects.json).
  A diagnosed, upstream-tracked crash is reported loudly but does not fail the gate.
  Each entry's `signature` names the corpus cases that must STILL crash; if none of them
  reproduce, the entry is STALE and the gate FAILS until it is deleted — so an
  acknowledgement can never outlive the bug (exactly like known_reference_defects.json).

KILL-TEST (--selftest, hermetic).  A fuzzer that never caught a crash proves nothing.
  --selftest stands up an in-process stub server that 500s on a PLANTED boundary input
  and asserts: (1) with an empty register the gate REDDENS on the planted crash; (2) with
  the planted signature registered the gate PASSES; (3) with the crash removed but the
  entry still registered the gate REDDENS on the stale entry; (4) the corpus is
  byte-deterministic across builds. No network, no golden.

USAGE
  python3 conformance/ci/fuzz_gate.py --server http://localhost:8182 [--product ID] [--json]
  python3 conformance/ci/fuzz_gate.py --selftest
Exit: 0 = clean (or only acknowledged/soft findings); 1 = a NEW crash or a STALE entry;
2 = SKIP (server down and not --require-server, or the referee is unavailable).
"""
import argparse, json, os, pathlib, sys, urllib.request, urllib.error, socket

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SELF = ROOT / "conformance" / "selfcheck"
REGISTER = HERE / "known_fuzz_defects.json"
sys.path.insert(0, str(SELF))
import fuzz_corpus as fc                                          # noqa: E402

try:
    import dual_oracle_referee as dor
    _HAVE_REFEREE = dor.available()
except Exception:
    _HAVE_REFEREE = False

FIRE_TIMEOUT = 20            # seconds; a response slower than this is treated as a HANG


# ---- firing + classification ---------------------------------------------------------
def _headers(i):
    # deterministic, unique-per-case idempotency/request ids so cases never collide
    return {"UCP-Agent": 'profile="https://spck.dev/agent"', "request-signature": "test",
            "idempotency-key": f"fuzz-{i}", "request-id": f"fuzz-req-{i}",
            "Content-Type": "application/json"}


def fire(base, case, i, session_id="co_nonexistent"):
    """Send one case. Return (status, body_bytes, kind) where kind is
    'http'|'crash'|'hang'. status 0 == connection error/reset."""
    path = case.path_template.replace("{id}", session_id)
    url = base.rstrip("/") + path
    data = json.dumps(case.body).encode() if case.body is not None else b"null"
    req = urllib.request.Request(url, data=data, method=case.method, headers=_headers(i))
    try:
        with urllib.request.urlopen(req, timeout=FIRE_TIMEOUT) as r:
            return r.status, r.read(), "http"
    except urllib.error.HTTPError as e:
        return e.code, e.read(), "http"
    except socket.timeout:
        return 0, b"TIMEOUT", "hang"
    except Exception as e:
        return 0, str(e).encode(), "crash"


def _expected_valid(referee, case):
    """Referee verdict: is this payload schema-VALID as a <op> request? None => the
    referee cannot judge (non-object whole-body) -> structurally invalid."""
    if not isinstance(case.body, dict):
        return False
    try:
        ok, _ = referee.validate(case.body, "schemas/shopping/checkout.json",
                                 op=case.op, direction="request")
        return ok
    except Exception:
        return None


def classify(base, corpus, referee, session_id="co_nonexistent"):
    """Fire the whole corpus; return per-case records with a classification bucket."""
    records = []
    for i, case in enumerate(corpus):
        status, body, kind = fire(base, case, i, session_id)
        try:
            j = json.loads(body)
            is_json_obj = isinstance(j, dict)
        except Exception:
            is_json_obj = False
        exp_valid = _expected_valid(referee, case) if referee else None
        if kind in ("crash", "hang") or status == 0 or status >= 500:
            bucket = "crash"
        elif 200 <= status < 300:
            bucket = "accept-invalid" if exp_valid is False else "accept-ok"
        elif 400 <= status < 500:
            bucket = "reject-envelope" if is_json_obj else "reject-noenvelope"
        else:
            bucket = f"other-{status}"
        records.append({"cid": case.cid, "op": case.op, "category": case.category,
                        "field": _field_of(case.cid), "status": status, "kind": kind,
                        "bucket": bucket, "tags": case.tags,
                        "mutation": case.mutation, "body_head": body[:120].decode(errors="replace")})
    return records


def _field_of(cid):
    """The mutated field path from a cid '<op>/<category>/<field...>[/detail]'."""
    parts = cid.split("/")
    return parts[2] if len(parts) >= 3 else ""


# ---- register ------------------------------------------------------------------------
def load_register(path=REGISTER):
    if not os.path.exists(path):
        return [], []
    d = json.load(open(path))
    entries, errs = [], []
    for e in d.get("defects", []):
        if not e.get("minimal_repro"):
            errs.append(f"register entry {e.get('id')}: missing minimal_repro")
        if not e.get("upstream"):
            errs.append(f"register entry {e.get('id')}: missing upstream reference "
                        f"(a linkless entry is suppression, not acknowledgement)")
        if not e.get("signature"):
            errs.append(f"register entry {e.get('id')}: missing signature")
        entries.append(e)
    return entries, errs


def _match(entry, rec):
    """Does a crashing record match this register entry's signature?"""
    sig = entry.get("signature", {})
    if sig.get("op") and sig["op"] != rec["op"]:
        return False
    if sig.get("category") and sig["category"] != rec["category"]:
        return False
    fp = sig.get("field_prefix")
    if fp and not rec["field"].startswith(fp):
        return False
    cids = sig.get("cids")
    if cids and rec["cid"] not in cids:
        return False
    return True


def adjudicate(records, entries):
    """Split crashes into acknowledged vs NEW; find STALE entries (no crash reproduces).
    Returns (new_crashes, acknowledged, stale_entries)."""
    crashes = [r for r in records if r["bucket"] == "crash"]
    acknowledged, new_crashes = [], []
    reproduced_ids = set()
    for r in crashes:
        hit = next((e for e in entries if _match(e, r)), None)
        if hit:
            acknowledged.append((r, hit["id"]))
            reproduced_ids.add(hit["id"])
        else:
            new_crashes.append(r)
    stale = [e for e in entries if e["id"] not in reproduced_ids]
    return new_crashes, acknowledged, stale


# ---- live gate -----------------------------------------------------------------------
def server_up(url, timeout=3):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/.well-known/ucp", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _live_session_id(base):
    """Create a real checkout so the UPDATE lane targets an existing session (a 404 on a
    bogus id would mask an update-path crash). Best-effort; falls back to a bogus id."""
    try:
        prod = fc.DEFAULT_PRODUCT
        body = fc._create_baseline(prod, "2026-04-08")
        req = urllib.request.Request(base.rstrip("/") + "/checkout-sessions",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers=_headers("session-seed"))
        with urllib.request.urlopen(req, timeout=FIRE_TIMEOUT) as r:
            return (json.loads(r.read()) or {}).get("id") or "co_nonexistent"
    except Exception:
        return "co_nonexistent"


def run_live(base, product, require_server, as_json):
    if not _HAVE_REFEREE:
        print("fuzz gate: SKIP — the independent referee (jsonschema/referencing) is "
              "unavailable; cannot assign expected-validity.")
        return 2
    if not server_up(base):
        if require_server:
            print(f"fuzz gate: FAIL — server {base} required but DOWN.")
            return 1
        print(f"fuzz gate: SKIP — no server at {base}.")
        return 2

    referee = dor.get_referee("2026-04-08")
    corpus = fc.build_corpus(product_id=product)
    session_id = _live_session_id(base)
    records = classify(base, corpus, referee, session_id)
    entries, reg_errs = load_register()
    if reg_errs:
        print("fuzz gate: FAIL — invalid register:")
        for e in reg_errs:
            print(f"  ✗ {e}")
        return 1
    new_crashes, acknowledged, stale = adjudicate(records, entries)

    from collections import Counter
    by_bucket = Counter(r["bucket"] for r in records)
    soft_accept = [r for r in records if r["bucket"] == "accept-invalid"]
    soft_noenv = [r for r in records if r["bucket"] == "reject-noenvelope"]

    if as_json:
        print(json.dumps({"server": base, "corpus": len(corpus),
                          "digest": fc.corpus_digest(corpus), "buckets": dict(by_bucket),
                          "new_crashes": new_crashes, "acknowledged": [a[1] for a in acknowledged],
                          "stale_entries": [e["id"] for e in stale],
                          "spec_contradicting_accepts": soft_accept,
                          "reject_no_envelope": soft_noenv}, indent=2, default=str))

    print(f"fuzz corpus: {len(corpus)} cases (digest {fc.corpus_digest(corpus)[:16]}) "
          f"fired at {base}")
    print(f"  buckets: {dict(sorted(by_bucket.items()))}")
    if acknowledged:
        seen = {}
        for r, eid in acknowledged:
            seen.setdefault(eid, []).append(r["cid"])
        for eid, cids in seen.items():
            print(f"  · ACKNOWLEDGED crash [{eid}]: {len(cids)} case(s) reproduce "
                  f"(e.g. {cids[0]}) — see known_fuzz_defects.json")
    if soft_accept:
        print(f"  · SOFT: {len(soft_accept)} spec-contradicting accept(s) "
              f"(referee-invalid but 2xx; triage, not gate-failing):")
        for r in soft_accept[:8]:
            print(f"      {r['cid']}  -> {r['status']}  ({r['mutation']})")
    if soft_noenv:
        print(f"  · SOFT: {len(soft_noenv)} 4xx without a JSON envelope (probe-hygiene).")

    failed = False
    if new_crashes:
        failed = True
        print(f"\nfuzz gate: FAIL — {len(new_crashes)} NEW server crash(es) "
              f"(5xx/reset/hang) not in known_fuzz_defects.json:")
        for r in new_crashes[:20]:
            print(f"  ✗ {r['cid']}  status={r['status']} kind={r['kind']}  ({r['mutation']})")
            print(f"      body: {r['body_head']}")
    if stale:
        failed = True
        print(f"\nfuzz gate: FAIL — {len(stale)} STALE register entr(y/ies) no longer "
              f"reproduce (delete them; the bug they described is gone):")
        for e in stale:
            print(f"  ✗ {e['id']}")
    if failed:
        return 1
    print("\nfuzz gate: PASS — no NEW server crash; every registered defect still "
          "reproduces (self-expiry holds). Conformant 4xx rejections are not findings.")
    return 0


# ---- hermetic kill-test (--selftest) -------------------------------------------------
def _selftest():
    """Prove the gate catches a PLANTED crash and a STALE entry, with no network/golden."""
    import http.server, threading, tempfile

    # 1) corpus determinism
    d1 = fc.corpus_digest(fc.build_corpus())
    d2 = fc.corpus_digest(fc.build_corpus())
    assert d1 == d2, "corpus is non-deterministic"
    assert any("#156" in c.tags for c in fc.build_corpus()), "the #156 currency-omit positive control is missing from the corpus"
    print("  ✓ corpus deterministic (digest stable) and contains the #156 positive control")

    # A stub server that 500s ONLY when a PLANTED boundary input is seen: a create whose
    # top-level `id` is not a string (the exact shape of a real #156-class crash). Every
    # other request gets a conformant 4xx envelope. `plant` toggles the crash off to
    # exercise self-expiry.
    state = {"plant": True}

    class Stub(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/.well-known/ucp":
                self._send(200, {"ucp": {"version": "2026-04-08"}})
            else:
                self._send(404, {"error": "not found"})
        def do_POST(self): self._handle()
        def do_PUT(self): self._handle()
        def _handle(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
            # Mirror the real golden's #156-class crash EXACTLY: a top-level `id` that
            # is present AND non-null AND not a string crashes (null is treated as
            # absent -> 201), so the planted crashes are precisely the create/wrongtype/id
            # cases the register signature names.
            planted = (isinstance(payload, dict) and payload.get("id") is not None
                       and not isinstance(payload["id"], str))
            if state["plant"] and planted:
                self.send_response(500); self.end_headers()
                self.wfile.write(b"Internal Server Error")     # the planted crash
                return
            if isinstance(payload, dict) and payload.get("line_items"):
                self._send(201, {"id": "co_1", "status": "incomplete",
                                 "line_items": payload["line_items"]})
            else:
                self._send(400, {"error": "bad request", "envelope": True})
        def _send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    referee = dor.get_referee("2026-04-08") if _HAVE_REFEREE else None
    corpus = fc.build_corpus()

    try:
        # (1) empty register + planted crash => gate must REDDEN on a NEW crash
        records = classify(base, corpus, referee)
        crashes = [r for r in records if r["bucket"] == "crash"]
        assert crashes, "kill-test FAILED: the planted crash was not detected at all"
        new, ack, stale = adjudicate(records, [])
        assert new and not stale, "kill-test FAILED: planted crash not reported as NEW"
        assert any(r["field"] == "id" and r["op"] == "create" for r in new), \
            "kill-test FAILED: the create/id crash was not among the new findings"
        print(f"  ✓ PLANTED crash caught: {len(new)} new crash(es) with an empty register "
              f"(the gate reddens)")

        # (2) register the planted signature => gate must PASS (acknowledged)
        entry = [{"id": "planted", "upstream": "selftest", "minimal_repro": {"x": 1},
                  "signature": {"op": "create", "category": "wrongtype", "field_prefix": "id"}}]
        new, ack, stale = adjudicate(records, entry)
        assert not new and ack and not stale, \
            "kill-test FAILED: a registered crash was not acknowledged"
        print(f"  ✓ registered crash ACKNOWLEDGED ({len(ack)} case(s)); gate would pass")

        # (3) turn the crash OFF, keep the entry => STALE => gate must REDDEN
        state["plant"] = False
        records2 = classify(base, corpus, referee)
        assert not [r for r in records2 if r["bucket"] == "crash"], \
            "kill-test setup error: crash still reproduces after disabling the plant"
        new, ack, stale = adjudicate(records2, entry)
        assert not new and stale and stale[0]["id"] == "planted", \
            "kill-test FAILED: a stale register entry was not caught (self-expiry broken)"
        print(f"  ✓ STALE entry caught after the crash stopped reproducing (self-expiry holds)")

        # (4) register-hygiene: a linkless entry is rejected
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({"defects": [{"id": "x", "signature": {"op": "create"},
                                    "minimal_repro": {"a": 1}}]}, tf)
            tfp = tf.name
        _, errs = load_register(tfp)
        os.unlink(tfp)
        assert any("upstream" in e for e in errs), \
            "kill-test FAILED: a linkless register entry was not rejected"
        print("  ✓ register hygiene: a linkless entry is rejected (no silent suppression)")
    finally:
        srv.shutdown()

    print("\nfuzz gate --selftest: PASS — the gate provably catches a planted crash, "
          "acknowledges a registered one, self-expires a stale entry, and rejects a "
          "linkless register.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://localhost:8182")
    ap.add_argument("--product", default=fc.DEFAULT_PRODUCT,
                    help="a real in-stock product id on the target (drives the valid baseline)")
    ap.add_argument("--require-server", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="hermetic kill-test (planted crash + self-expiry); no network")
    args = ap.parse_args()
    if args.selftest:
        if not _HAVE_REFEREE:
            print("fuzz gate --selftest: SKIP — referee unavailable.")
            return 2
        return _selftest()
    return run_live(args.server, args.product, args.require_server, args.json)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
seqfuzz_gate.py — stateful SEQUENCE fuzz over the checkout lifecycle (D4-07 / E3).

Random create/get/update/complete/cancel/replay sequences (+ concurrent idempotency races)
against a DISPOSABLE golden-0825 (free port + mktemp DB_DIR, re-seeded per run, deleted on exit;
never :8182/:8197/:8198/:8199), every response checked by conformance/ci/seq_invariants.py
(I1..I9 + I10 REPLAY-002 — the single implementation; mode from replay_mode.json, decision 1).

    --selftest                 planted-violation kill-tests on an IN-PROCESS stub (hermetic, no
                               sockets): in_progress+order (I3), terminal left (I2), 500 on a
                               quantity change (I8), re-serialized replay cached (I10 enforce);
                               a control stub with nothing planted stays quiet; I10 reports in
                               report mode; replay_mode past report_until resolves to enforce
    --boot                     boot a disposable golden-0825 for this run (or --server URL)
    --seconds N --races N --seed S --context golden_gates|user_facing
    --out PATH                 write this run's LAST_RUN.json there (default: RECORD_DIR / tmp)
    --record                   ALSO rewrite the tracked conformance/ci/seqfuzz/LAST_RUN.json
                               (decision 24: the owner commits it at release time)

I10 on OUR golden: golden-0825 hashes the parsed body today (services/checkout_service.py
_compute_hash, sort_keys), so a re-serialized replay is CACHED. replay_mode.json
`golden_pending` names the fix (D3-23) with a date: while it is pending (and never past
`until`) the I10 hits on the golden are printed as PENDING D3-23 and not counted as
violations — dated and named, never silent. The stub selftest never uses that exemption.
Exit: 0 no violations · 1 violations · 2 golden unavailable (never green).
"""
import argparse, datetime, hashlib, http.client, json, os, pathlib, random, shutil, socket, subprocess, sys
import tempfile, threading, time, urllib.error, urllib.request, uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import seq_invariants as si  # noqa: E402

GOLDEN_DIR = ROOT / "conformance" / "testbed" / "golden-0825"
TRACKED = HERE / "seqfuzz" / "LAST_RUN.json"
STALE_DAYS = 14
PRODUCTS = ["bouquet_roses", "pot_ceramic", "bouquet_sunflowers"]
VERSION = "2026-08-25"


def fulfillment_block():
    return {"methods": [{
        "id": "method_1", "type": "shipping", "line_item_ids": [],
        "destinations": [{"id": "dest_1", "type": "shipping_address", "address_country": "US"}],
        "selected_destination_id": "dest_1",
        "groups": [{"id": "group_1", "line_item_ids": [], "selected_option_id": "std-ship"}],
    }]}


def create_body(rng):
    n = rng.randint(1, 2)
    items = [{"item": {"id": rng.choice(PRODUCTS)}, "quantity": rng.randint(1, 2)} for _ in range(n)]
    return {"line_items": items, "fulfillment": fulfillment_block()}


def complete_body():
    return {"payment": {"instruments": [{"id": "instr_1", "handler_id": "mock_payment_handler", "type": "card",
                                         "display": {"brand": "Visa", "last_digits": "1234"},
                                         "credential": {"type": "token", "token": "success_token"}}]},
            "risk_signals": {}}


def headers(key=None):
    s = uuid.uuid4().hex[:8]
    return {"Content-Type": "application/json", "Idempotency-Key": key or f"idem-{s}", "Request-Id": f"req-{s}",
            "Request-Signature": f"sig-{s}",
            "UCP-Agent": 'profile="http://localhost:9/.well-known/ucp"; version="2026-08-25"'}


def mismatch_of(raw):
    """A MATERIALLY different payload under the same key (a real field changes — quantity or
    the credential token — never an unknown key a server may legitimately ignore). None when
    the body has no material field."""
    obj = json.loads(raw or b"{}")
    if obj.get("line_items"):
        obj["line_items"][0]["quantity"] = int(obj["line_items"][0].get("quantity", 1)) + 1
        return compact(obj)
    if obj.get("payment"):
        obj["payment"]["instruments"][0]["credential"]["token"] = "success_token_" + uuid.uuid4().hex[:6]
        return compact(obj)
    return None


def compact(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


def reserialize(raw):
    """Byte-different, JSON-equal bytes (key order + whitespace changed; an empty object
    becomes `{ }`). Asserts the bytes differ — a same-bytes 'replay' is I9's case, not I10's."""
    obj = json.loads(raw)
    out = json.dumps(obj, sort_keys=True, indent=1).encode()
    if out == raw:
        out = json.dumps(obj, sort_keys=True, separators=(", ", ": ")).encode()
    if out == raw:
        out = raw.replace(b"{", b"{ ", 1)
    assert out != raw and json.loads(out) == obj
    return out


# ---------------------------------------------------------------------------
# clients: (status, body, timed_out) = client.request(method, path, raw_bytes|None, headers)
# ---------------------------------------------------------------------------
class HttpClient:
    def __init__(self, base, timeout=10.0):
        self.base, self.timeout = base.rstrip("/"), timeout

    def request(self, method, path, raw, hdrs):
        req = urllib.request.Request(self.base + path, data=raw, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                st, data = r.status, r.read()
        except urllib.error.HTTPError as e:
            st, data = e.code, e.read()
        except (TimeoutError, socket.timeout):
            return 0, None, True
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)):
                return 0, None, True
            return 0, {"_transport_error": f"{type(e).__name__}: {e}"}, False
        except (http.client.HTTPException, ConnectionError, OSError) as e:
            # a malformed status line / dropped connection is a TRANSPORT failure the I8
            # invariant records (status 0) — never an exception that loses the whole run
            return 0, {"_transport_error": f"{type(e).__name__}: {str(e)[:120]}"}, False
        try:
            body = json.loads(data) if data else None
        except ValueError:
            body = None
        return st, body, False


def _envelope(code, content, status):
    """(status, UCP error envelope body)."""
    return status, {"ucp": {"version": VERSION, "status": "error"},
                    "messages": [{"type": "error", "code": code, "content": content, "severity": "unrecoverable"}]}


class StubGolden:
    """In-process checkout state machine with PLANTABLE defects (the selftest oracle):
    plant in {None, "in_progress_order", "terminal_left", "500_on_quantity", "reserialized_cached"}.
    Byte-exact idempotency by default (REPLAY-002 conformant)."""

    def __init__(self, plant=None):
        self.plant = plant
        self.checkouts, self.idem = {}, {}
        self.lock = threading.Lock()

    def _key_hash(self, raw):
        if self.plant == "reserialized_cached":
            return hashlib.sha256(json.dumps(json.loads(raw or b"{}"), sort_keys=True).encode()).hexdigest()
        return hashlib.sha256(raw or b"").hexdigest()

    def _checkout(self, cid, body, status):
        items = body.get("line_items") or []
        sub = sum(1000 * int(li.get("quantity", 1)) for li in items)
        return {"id": cid, "status": status, "currency": "USD", "line_items": items,
                "totals": [{"type": "subtotal", "amount": sub}, {"type": "total", "amount": sub}],
                "ucp": {"version": VERSION}}

    def request(self, method, path, raw, hdrs):
        with self.lock:
            key = hdrs.get("Idempotency-Key")
            h = self._key_hash(raw)
            if key in self.idem:
                prev_h, prev_resp = self.idem[key]
                if prev_h != h:
                    st, body = _envelope("idempotency_conflict", "payload mismatch", 409)
                    return st, body, False
                return prev_resp[0], json.loads(json.dumps(prev_resp[1])), False
            st, body = self._handle(method, path, raw)
            if method != "GET":
                self.idem[key] = (h, (st, body))
            return st, body, False

    def _handle(self, method, path, raw):
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return _envelope("invalid_request", "bad json", 400)
        parts = path.strip("/").split("/")
        if parts[0] != "checkout-sessions":
            return _envelope("not_found", "Not Found", 404)
        if method == "POST" and len(parts) == 1:
            if not data.get("line_items"):
                return _envelope("invalid_request", "line_items required", 422)
            cid = uuid.uuid4().hex
            ck = self._checkout(cid, data, "ready_for_complete")
            self.checkouts[cid] = ck
            return 201, json.loads(json.dumps(ck))
        cid = parts[1] if len(parts) > 1 else None
        ck = self.checkouts.get(cid)
        if ck is None:
            return _envelope("not_found", "checkout not found", 404)
        action = parts[2] if len(parts) > 2 else None
        if method == "GET" and action is None:
            return 200, json.loads(json.dumps(ck))
        if method == "PUT" and action is None:
            if ck["status"] in si.TERMINAL:
                if self.plant == "terminal_left":
                    ck = self._checkout(cid, data, "incomplete"); self.checkouts[cid] = ck
                    return 200, json.loads(json.dumps(ck))
                return _envelope("invalid_state", "checkout is terminal", 409)
            if self.plant == "500_on_quantity" and any(int(li.get("quantity", 1)) >= 2 for li in data.get("line_items") or []):
                return 500, {"detail": "Internal Server Error"}
            ck = self._checkout(cid, data, "ready_for_complete"); self.checkouts[cid] = ck
            return 200, json.loads(json.dumps(ck))
        if method == "POST" and action == "complete":
            if ck["status"] in si.TERMINAL:
                return _envelope("invalid_state", "checkout is terminal", 409)
            if self.plant == "in_progress_order":
                ck["status"] = "complete_in_progress"; ck["order"] = {"id": "ord-" + cid[:8]}
                return 200, json.loads(json.dumps(ck))
            ck["status"] = "completed"; ck["order"] = {"id": "ord-" + cid[:8], "permalink_url": f"http://stub/orders/{cid[:8]}"}
            return 200, json.loads(json.dumps(ck))
        if method == "POST" and action == "cancel":
            if ck["status"] in si.TERMINAL:
                return _envelope("invalid_state", "checkout is terminal", 409)
            ck["status"] = "canceled"
            return 200, json.loads(json.dumps(ck))
        return _envelope("not_found", "Not Found", 404)


# ---------------------------------------------------------------------------
# the fuzzer
# ---------------------------------------------------------------------------
class Run:
    def __init__(self, client, mode, pending=None):
        self.c, self.mode, self.pending = client, mode, pending
        self.requests = self.sequences = 0
        self.violations, self.examples, self.reports, self.pending_hits = {}, {}, {}, {}
        self.last = None      # (method, path, raw, key, status, id)

    def _step(self, op, method, path, raw, key, prev, orig=None):
        st, body, to = self.c.request(method, path, raw, headers(key))
        self.requests += 1
        step = {"op": op, "status": st, "body": body, "prev": prev, "orig": orig or {}, "timed_out": to}
        for iid, msg in si.check(step, self.mode):
            if msg.startswith("REPORT:"):
                self.reports[iid] = self.reports.get(iid, 0) + 1
            elif iid == "I10" and self.pending:
                self.pending_hits[iid] = self.pending_hits.get(iid, 0) + 1
            else:
                self.violations[iid] = self.violations.get(iid, 0) + 1
                self.examples.setdefault(iid, f"{op} {method} {path} -> {st}: {msg}")
        bid = body.get("id") if isinstance(body, dict) else None
        if method != "GET" and not op.startswith("replay"):
            # the ORIGINAL a later replay re-sends: never a replay itself (a re-serialization of
            # a re-serialization can land back on the original bytes = a legitimate cache hit)
            self.last = (method, path, raw, key, st, bid)
        return st, body

    def sequence(self, rng, ops=None):
        """One sequence: create, then `ops` (scripted) or 3-7 random ops."""
        self.sequences += 1
        prev = {"status": None, "id": None}
        raw = compact(create_body(rng)); key = f"idem-{uuid.uuid4().hex[:10]}"
        st, body = self._step("create", "POST", "/checkout-sessions", raw, key, prev)
        if st != 201 or not isinstance(body, dict):
            self.failed_creates = getattr(self, "failed_creates", 0) + 1
            return
        self.failed_creates = 0
        cid = body["id"]; prev = {"status": body.get("status"), "id": cid}
        # `complete` consumes seeded stock (L5 N19: an unbounded fuzz exhausts the inventory and
        # every later create is a 4xx that says nothing about the lifecycle) — keep it rare.
        menu = ["get", "update", "update", "cancel", "replay_same", "replay_mismatch", "replay_reserialized"]
        menu += ["complete"] if rng.random() < 0.25 else []
        plan = ops or [rng.choice(menu) for _ in range(rng.randint(3, 7))]
        for op in plan:
            if op == "get":
                st, body = self._step("get", "GET", f"/checkout-sessions/{cid}", None, None, prev)
            elif op == "update":
                b = create_body(rng); b["line_items"][0]["quantity"] = rng.randint(1, 3)
                st, body = self._step("update", "PUT", f"/checkout-sessions/{cid}", compact(b), f"idem-{uuid.uuid4().hex[:10]}", prev)
            elif op == "complete":
                st, body = self._step("complete", "POST", f"/checkout-sessions/{cid}/complete", compact(complete_body()), f"idem-{uuid.uuid4().hex[:10]}", prev)
            elif op == "cancel":
                st, body = self._step("cancel", "POST", f"/checkout-sessions/{cid}/cancel", b"{}", f"idem-{uuid.uuid4().hex[:10]}", prev)
            elif op.startswith("replay") and self.last:
                m, p, r, k, ost, oid = self.last
                orig = {"status": ost, "id": oid}
                if op == "replay_same":
                    st, body = self._step(op, m, p, r, k, prev, orig)
                elif op == "replay_mismatch":
                    alt = mismatch_of(r)
                    if alt is None:          # a bodiless op (cancel) has no material field to change
                        continue
                    st, body = self._step(op, m, p, alt, k, prev, orig)
                else:
                    st, body = self._step(op, m, p, reserialize(r or b"{}"), k, prev, orig)
            else:
                continue
            if 200 <= st < 300 and isinstance(body, dict) and body.get("status") and op not in ("replay_mismatch",):
                prev = {"status": body["status"], "id": cid}

    PROBE = ["update", "replay_reserialized", "replay_same", "replay_mismatch", "get", "complete", "get",
             "update", "cancel"]
    PROBE2 = ["cancel", "update", "complete", "replay_same"]
    PROBE3 = ["update", "update", "complete", "replay_same", "replay_reserialized"]

    def fuzz(self, seconds, seed):
        rng = random.Random(seed)
        for plan in (self.PROBE, self.PROBE2, self.PROBE3):     # scripted coverage of every path
            self.sequence(rng, plan)
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            self.sequence(rng)
            if getattr(self, "failed_creates", 0) >= 20:
                # 20 consecutive failed creates = the seeded stock is gone (or the golden is
                # down): stop, and say so — a fuzz over an empty shop proves nothing
                self.stopped = f"stopped early: 20 consecutive creates failed after {self.sequences} sequences (stock exhausted?)"
                break

    def races(self, n):
        """Concurrent same-key requests: (a) same bytes -> one id; (b) mismatched -> exactly one 2xx,
        the rest 409; (c) CHK-078 lost-response retry of complete -> same order id; (d) CHK-079
        fresh key after ready_for_complete -> executed."""
        out = {}
        rng = random.Random(1)

        def fire(fn, count):
            res = [None] * count
            def w(i):
                res[i] = fn(i)
            ts = [threading.Thread(target=w, args=(i,)) for i in range(count)]
            [t.start() for t in ts]; [t.join() for t in ts]
            self.requests += count
            return res
        def hist(rs):
            h = {}
            for st, _b, _t in rs:
                h[str(st)] = h.get(str(st), 0) + 1
            return h
        raw = compact(create_body(rng)); key = f"race-{uuid.uuid4().hex[:8]}"
        rs = fire(lambda i: self.c.request("POST", "/checkout-sessions", raw, headers(key)), n)
        ids = {b.get("id") for st, b, _ in rs if isinstance(b, dict) and b.get("id")}
        out["same_bytes"] = {"n": n, "status_2xx": sum(1 for st, _b, _t in rs if 200 <= st < 300),
                             "distinct_ids": len(ids), "statuses": hist(rs)}
        key = f"race-{uuid.uuid4().hex[:8]}"
        base_body = create_body(rng)
        bodies = []
        for i in range(n):
            b = json.loads(json.dumps(base_body)); b["line_items"][0]["quantity"] = i + 1
            bodies.append(compact(b))
        rs = fire(lambda i: self.c.request("POST", "/checkout-sessions", bodies[i], headers(key)), n)
        out["mismatch"] = {"n": n, "status_2xx": sum(1 for st, _b, _t in rs if 200 <= st < 300),
                           "status_409": sum(1 for st, _b, _t in rs if st == 409), "statuses": hist(rs)}
        st, b, _ = self.c.request("POST", "/checkout-sessions", compact(create_body(rng)), headers()); self.requests += 1
        cid = b.get("id") if isinstance(b, dict) else None
        key = f"race-{uuid.uuid4().hex[:8]}"; cb = compact(complete_body())
        r1 = self.c.request("POST", f"/checkout-sessions/{cid}/complete", cb, headers(key))
        r2 = self.c.request("POST", f"/checkout-sessions/{cid}/complete", cb, headers(key)); self.requests += 2
        o1 = ((r1[1] or {}).get("order") or {}).get("id"); o2 = ((r2[1] or {}).get("order") or {}).get("id")
        out["lost_response_retry"] = {"status": [r1[0], r2[0]], "same_order_id": bool(o1) and o1 == o2}
        st, b, _ = self.c.request("POST", "/checkout-sessions", compact(create_body(rng)), headers()); self.requests += 1
        cid = b.get("id") if isinstance(b, dict) else None
        r3 = self.c.request("POST", f"/checkout-sessions/{cid}/complete", cb, headers()); self.requests += 1
        out["fresh_key_after_ready"] = {"status": r3[0], "completed": ((r3[1] or {}).get("status") == "completed")}
        ok = (out["same_bytes"]["distinct_ids"] == 1 and out["same_bytes"]["status_2xx"] == n
              and out["mismatch"]["status_2xx"] == 1 and out["mismatch"]["status_409"] == n - 1
              and out["lost_response_retry"]["same_order_id"] and out["fresh_key_after_ready"]["completed"])
        out["ok"] = ok
        if not ok:
            self.violations["races"] = self.violations.get("races", 0) + 1
            self.examples.setdefault("races", json.dumps(out))
        return out


def summary_line(run, races):
    mode_note = (f"I10 {run.mode}: {run.reports.get('I10', 0)} re-serialized replays cached (report)" if run.mode == "report"
                 else f"I10 enforce: {run.pending_hits.get('I10', 0)} re-serialized replays cached PENDING {run.pending}"
                 if run.pending else f"I10 enforce: {run.violations.get('I10', 0)} violations")
    rl = ""
    if races:
        rl = (f" / races: same-bytes {races['same_bytes']['status_2xx']}/{races['same_bytes']['n']} "
              f"{'one id' if races['same_bytes']['distinct_ids'] == 1 else str(races['same_bytes']['distinct_ids']) + ' ids'}, "
              f"mismatch {races['mismatch']['status_2xx']}x2xx+{races['mismatch']['status_409']}x409"
              f"{'' if races['ok'] else ' (RACE FAIL)'}")
    v = sum(run.violations.values())
    return (f"{v} violations / {len(si.INVARIANTS) - 1} invariants ({mode_note}) / {run.requests} requests"
            f" / {run.sequences} sequences{rl}")


# ---------------------------------------------------------------------------
# disposable golden
# ---------------------------------------------------------------------------
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class Disposable:
    def __init__(self):
        self.port = free_port()
        self.db = pathlib.Path(tempfile.mkdtemp(prefix="seqfuzz_golden_"))
        self.env = dict(os.environ, PORT=str(self.port), DB_DIR=str(self.db), SIM_SECRET="seqfuzz-secret")
        for k in ("DEFECTS_CONFIG", "DEFECTS_STATE_FILE", "REQUIRE_SIGNATURES"):
            self.env.pop(k, None)

    def __enter__(self):
        r = subprocess.run([str(GOLDEN_DIR / "serve_golden_0825.sh")], env=self.env, capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            raise RuntimeError(f"golden boot failed on :{self.port}: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
        return f"http://localhost:{self.port}"

    def __exit__(self, *a):
        subprocess.run([str(GOLDEN_DIR / "stop_golden_0825.sh")], env=self.env, capture_output=True, text=True, timeout=60)
        shutil.rmtree(self.db, ignore_errors=True)


def golden_sha():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


def pending_task(mode_doc, today=None):
    gp = mode_doc.get("golden_pending") or {}
    if not gp:
        return None
    today = today or datetime.date.today()
    return gp["task"] if today <= datetime.date.fromisoformat(gp["until"]) else None


def report_line(tracked=TRACKED):
    """The 14-day report line run_suite prints (battery idiom; decision 24)."""
    if not tracked.exists():
        return "seqfuzz          · not yet run — python3 conformance/ci/seqfuzz_gate.py --boot --record"
    try:
        d = json.loads(tracked.read_text())
    except (OSError, ValueError) as e:
        return f"seqfuzz          ✗ LAST_RUN.json unreadable ({e})"
    age = (time.time() - d.get("ran_at_epoch", 0)) / 86400
    v = sum((d.get("violations") or {}).values())
    return (f"seqfuzz          {'✓' if v == 0 else '✗'} {v} violations · {d.get('requests')} requests · "
            f"{d.get('sequences')} sequences · I10 {d.get('invariants_mode', {}).get('I10')}"
            f"{' (pending ' + d['pending']['task'] + ': ' + str(d['pending'].get('I10', 0)) + ' hits)' if d.get('pending') else ''}"
            f", {age:.1f}d ago{' [STALE — re-run]' if age > STALE_DAYS else ''}")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
def selftest():
    ok = True
    caught = 0
    plants = [("in_progress_order", "I3"), ("terminal_left", "I2"), ("500_on_quantity", "I8"), ("reserialized_cached", "I10")]
    for plant, iid in plants:
        run = Run(StubGolden(plant), mode="enforce", pending=None)
        run.fuzz(seconds=0.0, seed=7)
        for _ in range(30):
            run.sequence(random.Random(len(run.examples) + run.sequences))
        hit = iid in run.violations
        caught += hit
        print(f"  {'✓' if hit else '✗'} planted {plant:20} -> {iid} {'CAUGHT' if hit else 'MISSED'}: "
              f"{run.examples.get(iid, '(no ' + iid + ' violation recorded; got ' + str(sorted(run.violations)) + ')')[:110]}")
        ok = ok and hit
    ctrl = Run(StubGolden(None), mode="enforce", pending=None)
    ctrl.fuzz(seconds=0.5, seed=11)
    races = ctrl.races(6)
    quiet = not ctrl.violations
    print(f"  {'✓' if quiet else '✗'} control stub: {sum(ctrl.violations.values())} violations over "
          f"{ctrl.requests} requests / {ctrl.sequences} sequences; races ok={races['ok']}"
          + ("" if quiet else f" — {ctrl.examples}"))
    ok = ok and quiet and races["ok"]
    rep = Run(StubGolden("reserialized_cached"), mode="report", pending=None)
    rep.fuzz(seconds=0.0, seed=7)
    rmode = "I10" not in rep.violations and rep.reports.get("I10", 0) >= 1
    print(f"  {'✓' if rmode else '✗'} I10 in report mode: {rep.reports.get('I10', 0)} REPORT line(s), "
          f"{rep.violations.get('I10', 0)} violation(s)")
    ok = ok and rmode
    past = si.resolve_mode("user_facing", today=datetime.date(2026, 11, 10)) == "enforce"
    now = si.resolve_mode("user_facing", today=datetime.date(2026, 9, 11)) == "report"
    gg = si.resolve_mode("golden_gates") == "enforce"
    print(f"  {'✓' if past and now and gg else '✗'} replay_mode.json: golden_gates=enforce, user_facing=report until "
          f"{si.load_replay_mode()['report_until']}, then enforce (self-expiring)")
    ok = ok and past and now and gg
    print(f"{caught}/4 planted violations caught · {'control quiet' if quiet else 'control NOISY'} · {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="stateful sequence fuzz gate (D4-07)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--server", default=None)
    ap.add_argument("--boot", action="store_true", help="boot a disposable golden-0825 (free port, mktemp DB_DIR)")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--races", type=int, default=0)
    ap.add_argument("--seed", type=int, default=int(time.time()))
    ap.add_argument("--context", default="golden_gates", choices=["golden_gates", "user_facing"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.server and not a.boot:
        ap.error("--server URL or --boot")
    mode_doc = si.load_replay_mode()
    mode = si.resolve_mode(a.context, mode=mode_doc)
    pending = pending_task(mode_doc) if (a.context == "golden_gates" and mode == "enforce") else None
    try:
        ctx = Disposable() if a.boot else None
        base = ctx.__enter__() if ctx else a.server
    except Exception as e:  # noqa: BLE001
        print(f"seqfuzz: SKIP — {e}"); return 2
    try:
        run = Run(HttpClient(base), mode=mode, pending=pending)
        t0 = time.monotonic()
        races = run.races(a.races) if a.races else None     # races FIRST, on a fresh stock
        run.fuzz(a.seconds, a.seed)
        dt = time.monotonic() - t0
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
    line = summary_line(run, races)
    if getattr(run, "stopped", None):
        print(f"  · {run.stopped}")
    for iid, ex in run.examples.items():
        print(f"  ✗ {iid}: {ex}")
    doc = {"ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "ran_at_epoch": time.time(), "golden_sha": golden_sha(), "seconds": round(dt, 1), "seed": a.seed,
           "context": a.context, "sequences": run.sequences, "requests": run.requests,
           "violations": run.violations, "examples": run.examples, "reports": run.reports,
           "pending": ({"task": pending, **run.pending_hits} if pending else None),
           "stopped": getattr(run, "stopped", None),
           "races": races, "invariants_mode": {"I10": mode}, "summary": line}
    out = pathlib.Path(a.out) if a.out else pathlib.Path(os.environ.get("RUN_SUITE_RECORD_DIR") or tempfile.mkdtemp(prefix="seqfuzz_")) / "seqfuzz_LAST_RUN.json"
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(doc, indent=1) + "\n")
    if a.record:
        TRACKED.parent.mkdir(parents=True, exist_ok=True); TRACKED.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"recorded -> {TRACKED.relative_to(ROOT)}")
    print(line + (f" [seed {a.seed}, {dt:.0f}s, {base}]"))
    return 0 if not run.violations else 1


if __name__ == "__main__":
    sys.exit(main())

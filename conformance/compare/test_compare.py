#!/usr/bin/env python3
"""
test_compare.py — the test-of-the-comparison (P2-13 TDD gates).

Hermetic gates (no golden needed):
  1. catch-rate math: shared-surface head-to-head vs spck-only addendum are
     computed separately; an spck-only mutant can NEVER move the head-to-head.
  2. catch criteria: baseline-relative, strict (crash is never a catch;
     official tests unstable on the clean golden earn no catch credit).
  3. citations: every mutant cites a register row that exists AND is a
     MUST/MUST NOT (a mutant grounded in a SHOULD would overclaim).
  4. sticky proxy behavior against a stub upstream:
       - mutation applies with NO client cooperation (no X-Mutate header),
       - client-sent X-Mutate is ignored (no suite can steer the defect),
       - passthrough mode is byte-identical,
       - determinism: same request twice -> identical mutated bytes,
       - match scoping: non-matching paths/statuses pass through untouched.

Results-calibration gates (run AFTER run_compare.py; skip with exit 2 if
results/results.json is absent):
  5. the calibration mutant expected to be caught by BOTH suites was;
  6. the spck-only calibration mutant was caught by us, excluded from the
     head-to-head, and NOT silently counted against the official suite;
  7. every mutant is flagged deterministic (same mutant -> same verdict).

Exit 0 = all gates green; 1 = a gate failed; 2 = hermetic green, results absent.
"""
import json, pathlib, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_compare import (compute_rates, decide_catch_official,          # noqa: E402
                         decide_catch_spck, validate_citations)
import sticky_proxy                                                     # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'XX '} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- gate 1+2 --
def test_rate_math():
    print("gate 1/2: catch-rate math + catch criteria")
    mutants = [
        {"id": "a", "shared_surface": True}, {"id": "b", "shared_surface": True},
        {"id": "c", "shared_surface": True}, {"id": "d", "shared_surface": False},
    ]
    records = {"a": {"spck_catch": True, "official_catch": True},
               "b": {"spck_catch": True, "official_catch": False},
               "c": {"spck_catch": False, "official_catch": True},
               "d": {"spck_catch": True, "official_catch": False}}
    r = compute_rates(mutants, records)
    check("shared head-to-head counts only shared mutants (n=3)",
          r["shared_surface"]["n"] == 3)
    check("spck 2/3 on shared", r["shared_surface"]["spck_caught"] == 2
          and abs(r["shared_surface"]["spck_rate"] - 2 / 3) < 1e-3)
    check("official 2/3 on shared (spck-only miss NOT charged to official)",
          r["shared_surface"]["official_caught"] == 2)
    check("spck-only mutant lands in the addendum, not the head-to-head",
          r["spck_only_surface"]["n"] == 1 and r["spck_only_surface"]["spck_caught"] == 1)

    # a diagnostic mutant is counted NOWHERE — neither bucket moves
    mutants_d = mutants + [{"id": "e", "shared_surface": False, "diagnostic": True}]
    records_d = dict(records, e={"spck_catch": False, "official_catch": True})
    rd = compute_rates(mutants_d, records_d)
    check("diagnostic mutant counted in NEITHER bucket",
          rd["shared_surface"]["n"] == 3 and rd["spck_only_surface"]["n"] == 1
          and rd["shared_surface"]["official_caught"] == 2)

    c, new = decide_catch_official(["t.flaky"], ["t.base"], ["t.base", "t.flaky", "t.new"], False)
    check("official catch = NEW failures only; unstable tests earn no credit",
          c and new == ["t.new"])
    c, _ = decide_catch_official([], ["t.base"], ["t.base"], False)
    check("official no new failure -> miss", not c)
    c, _ = decide_catch_official([], [], ["t.x"], True)
    check("official crash is never a catch", not c)
    c, new = decide_catch_spck([], ["checkout.response_fields"], False)
    check("spck catch = new deviation", c and new == ["checkout.response_fields"])
    c, _ = decide_catch_spck(["pre.existing"], ["pre.existing"], False)
    check("spck pre-existing deviation is not a catch", not c)
    c, _ = decide_catch_spck([], ["x"], True)
    check("spck crash is never a catch", not c)


# ------------------------------------------------------------------ gate 3 --
def test_citations():
    print("gate 3: every mutant cites a real MUST in the pinned register")
    spec = json.loads((HERE / "mutants.json").read_text())
    problems, _ = validate_citations(
        spec["mutants"], HERE.parents[1] / "conformance" / "requirements" / "2026-04-08")
    check("all citations resolve to MUST/MUST NOT register rows",
          not problems, "; ".join(problems))
    ids = [m["id"] for m in spec["mutants"]]
    check("mutant ids unique", len(ids) == len(set(ids)))
    cal = spec["calibration"]
    check("calibration mutants exist and are classified as designed",
          any(m["id"] == cal["expected_both_catch"] and m["shared_surface"]
              for m in spec["mutants"])
          and any(m["id"] == cal["expected_spck_only_catch"] and not m["shared_surface"]
                  for m in spec["mutants"]))


# ------------------------------------------------------------------ gate 4 --
class _Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/err":
            body = json.dumps({"ucp": {"status": "error"}, "messages": [{"type": "error"}]}).encode()
            code = 400
        else:
            body = json.dumps({
                "endpoint": "http://upstream.example",
                "totals": [{"type": "subtotal", "amount": 100},
                           {"type": "discount", "amount": -10},
                           {"type": "total", "amount": 90}]}).encode()
            code = 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _get(port, path, hdrs=None):
    req = urllib.request.Request(f"http://localhost:{port}{path}", headers=hdrs or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_proxy():
    print("gate 4: sticky proxy semantics (stub upstream)")
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    up_port = up.server_address[1]
    threading.Thread(target=up.serve_forever, daemon=True).start()

    def with_proxy(mutate, match_status, fn, match_path="^/ok"):
        srv = sticky_proxy.serve(f"http://localhost:{up_port}", 0, mutate,
                                 match_path, "GET,POST,PUT", match_status,
                                 [(f"http://upstream.example", "http://proxy.example")])
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            return fn(port)
        finally:
            srv.shutdown()

    # passthrough is byte-identical (modulo the declared URL rewrite)
    def f(port):
        _, direct = _get(up_port, "/ok")
        _, proxied = _get(port, "/ok")
        return direct.replace(b"upstream.example", b"proxy.example") == proxied
    check("passthrough byte-identical (with declared endpoint rewrite)",
          with_proxy("", "all", f))

    # mutation applies WITHOUT any client cooperation header
    def f(port):
        _, b = _get(port, "/ok")
        d = json.loads(b)
        return [t["amount"] for t in d["totals"] if t["type"] == "discount"] == [10]
    check("mutation applies with no X-Mutate from the client",
          with_proxy("tot-negate:discount", "2xx", f))

    # client-sent X-Mutate is ignored (suites cannot steer the defect)
    def f(port):
        _, b = _get(port, "/ok", {"X-Mutate": "drop:totals"})
        return "totals" in json.loads(b)
    check("client X-Mutate ignored in passthrough mode", with_proxy("", "all", f))

    def f(port):
        _, b = _get(port, "/ok", {"X-Mutate": "empty"})
        d = json.loads(b)
        return [t["amount"] for t in d["totals"] if t["type"] == "discount"] == [10]
    check("client X-Mutate ignored while a fixed mutation is active",
          with_proxy("tot-negate:discount", "2xx", f))

    # determinism: same request twice -> identical bytes
    def f(port):
        return _get(port, "/ok") == _get(port, "/ok")
    check("same mutant + same request -> identical response (determinism)",
          with_proxy("tot-del:subtotal", "2xx", f))

    # match scoping: a 4xx-only mutation leaves 2xx untouched, and vice versa
    def f(port):
        _, ok = _get(port, "/ok")
        st, err = _get(port, "/err")
        return "totals" in json.loads(ok) and st == 400 and "messages" not in json.loads(err)
    check("status matcher scopes the defect (4xx mutated, 2xx untouched)",
          with_proxy("drop:messages", "4xx", f, match_path="^/"))

    def f(port):
        st, err = _get(port, "/err")
        return st == 400 and "messages" in json.loads(err)
    check("2xx-scoped mutation leaves error responses untouched",
          with_proxy("drop:totals", "2xx", f, match_path="^/"))

    # comparison-specific tokens
    def f(port):
        _, b = _get(port, "/ok")
        d = json.loads(b)
        types = [t["type"] for t in d["totals"]]
        return "subtotal" not in types and "total" in types
    check("tot-del removes exactly the targeted type", with_proxy("tot-del:subtotal", "2xx", f))

    def f(port):
        _, b = _get(port, "/ok")
        subs = [t for t in json.loads(b)["totals"] if t["type"] == "subtotal"]
        return subs and all("amount" not in t for t in subs)
    check("tot-strip-amount removes only the amount", with_proxy("tot-strip-amount:subtotal", "2xx", f))
    up.shutdown()


# -------------------------------------------------------------- gates 5-7 ---
def test_results_calibration():
    print("gates 5-7: calibration against the recorded run")
    rp = HERE / "results" / "results.json"
    if not rp.exists():
        print("  (results/results.json absent — run run_compare.py first; skipping)")
        return None
    res = json.loads(rp.read_text())
    cal = json.loads((HERE / "mutants.json").read_text())["calibration"]
    by_id = {m["id"]: m for m in res["mutants"]}

    both = by_id.get(cal["expected_both_catch"])
    check("calibration: known mutant caught by BOTH suites",
          bool(both) and both["spck_catch"] and both["official_catch"])
    only = by_id.get(cal["expected_spck_only_catch"])
    check("calibration: spck-only-surface mutant caught by spck",
          bool(only) and only["spck_catch"])
    check("calibration: spck-only mutant excluded from the head-to-head",
          bool(only) and not only["shared_surface"])
    n_shared_recorded = res["rates"]["shared_surface"]["n"]
    n_shared_declared = sum(1 for m in res["mutants"] if m["shared_surface"])
    check("head-to-head n == declared shared mutants", n_shared_recorded == n_shared_declared)
    check("every mutant deterministic (same mutant -> same verdict on repeat)",
          all(m["deterministic"] for m in res["mutants"]),
          ", ".join(m["id"] for m in res["mutants"] if not m["deterministic"]))
    check("no harness crashes counted as catches",
          all(not (m.get("spck_crashed") and m["spck_catch"])
              and not (m.get("official_crashed") and m["official_catch"])
              for m in res["mutants"]))
    return True


def main():
    test_rate_math()
    test_citations()
    test_proxy()
    had_results = test_results_calibration()
    print(f"\ncompare self-test: {'FAIL' if FAILS else 'PASS'}"
          + ("" if had_results else " (hermetic gates only; no recorded results yet)"))
    if FAILS:
        return 1
    return 0 if had_results else 2


if __name__ == "__main__":
    sys.exit(main())

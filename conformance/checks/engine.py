#!/usr/bin/env python3
"""
engine.py — Phase 1 register-driven conformance check engine.

A check cites the register requirement(s) it verifies, evaluates a real server
response, and declares the mutations that MUST break it. Before any check counts
toward a verdict, the engine self-validates it: the check must pass on the clean
response AND fail on every declared mutant (kill-rate). A check that isn't
mutation-safe is reported kill_safe=False, which the verdict gate downgrades to
inconclusive (so it can never produce a green). Results feed verdict_gate for an
honest, coverage-gated report.

Mutations here operate on the CAPTURED response (deterministic golden+mutation),
which works for stateful checks too — we mutate the final response under test.
The live mutation_proxy remains for integration/timing/replay cases.
"""
import json, sys, urllib.request, urllib.error, pathlib, glob
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CheckResult, aggregate, CLEAN, DEVIATION, INCONCLUSIVE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQ_DIR = ROOT / "conformance" / "requirements"

# ---- response capture -------------------------------------------------------
class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, dict(headers), body
        try: self.json = json.loads(body)
        except Exception: self.json = None
    def clone(self):
        return Resp(self.status, dict(self.headers), self.body)

def fetch(base, path, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return Resp(r.status, r.getheaders(), r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.headers.items(), e.read())
    except Exception as e:
        return Resp(0, {}, str(e).encode())

# ---- mutations on a captured response (defect injection) --------------------
def _reparse(r):
    r.body = json.dumps(r.json).encode() if r.json is not None else r.body
    return r
def mutate(resp, mut):
    r = resp.clone()
    name, _, arg = mut.partition(":")
    if name == "status":         r.status = int(arg); return r
    if name == "empty":          r.body = b""; r.json = None; return r
    if name == "corrupt-json":   r.body = (r.body or b"{}")[:-1]; r.json = None; return r
    if name == "drop" and r.json is not None:
        cur = r.json; parts = arg.split(".")
        for p in parts[:-1]:
            if isinstance(cur, list) and p.isdigit() and int(p) < len(cur): cur = cur[int(p)]
            elif isinstance(cur, dict): cur = cur.get(p)
            else: cur = None
            if cur is None: break
        last = parts[-1]
        if isinstance(cur, dict): cur.pop(last, None)
        elif isinstance(cur, list) and last.isdigit() and int(last) < len(cur): cur.pop(int(last))
        return _reparse(r)
    if name == "set" and isinstance(r.json, dict):
        k, _, v = arg.partition("="); r.json[k] = json.loads(v); return _reparse(r)
    return r  # no-op (mutation not applicable)

# ---- check spec -------------------------------------------------------------
class Check:
    def __init__(self, cid, req_ids, keyword, fetch_fn, predicate, mutations):
        self.id, self.req_ids, self.keyword = cid, req_ids, keyword
        self.fetch_fn, self.predicate, self.mutations = fetch_fn, predicate, mutations

def run_check(chk, base):
    """Returns (results: list[CheckResult], detail: dict). kill_safe is computed by
    mutation: clean must pass, every declared mutant must deviate."""
    try:
        golden = chk.fetch_fn(base)
    except Exception as e:
        kill_safe = False
        res = [CheckResult(rid, chk.keyword, INCONCLUSIVE, kill_safe) for rid in chk.req_ids]
        return res, {"clean": "error:" + str(e), "kills": "", "kill_safe": kill_safe}
    clean = chk.predicate(golden)
    kills, survivors = 0, []
    for m in chk.mutations:
        if chk.predicate(mutate(golden, m)) == DEVIATION: kills += 1
        else: survivors.append(m)
    kill_safe = (clean == CLEAN and not survivors)
    status = clean if kill_safe else (clean if clean == DEVIATION else INCONCLUSIVE)
    res = [CheckResult(rid, chk.keyword, status, kill_safe) for rid in chk.req_ids]
    return res, {"clean": clean, "kills": f"{kills}/{len(chk.mutations)}",
                 "kill_safe": kill_safe, "survivors": survivors}

# ---- inscope MUSTs from the register (coverage denominator) -----------------
def inscope_musts(version, transports=("rest", "any")):
    ids = set()
    for f in glob.glob(str(REQ_DIR / version / "*.json")):
        for r in json.load(open(f)).get("rows", []):
            if r["keyword"] in ("MUST", "MUST NOT") and r["testability"] == "testable" \
               and any(t in transports for t in r.get("transport", [])):
                ids.add(r["id"])
    return ids

def run_report(checks, base, version, scope_stamp, disclaimer, transports=("rest", "any")):
    all_results, details = [], []
    for chk in checks:
        res, det = run_check(chk, base)
        all_results += res
        details.append((chk, det))
    rep = aggregate(all_results, inscope_musts(version, transports), scope_stamp, disclaimer)
    return rep, details

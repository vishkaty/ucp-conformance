#!/usr/bin/env python3
"""
preview_parity.py — the JS preview parity gate (PLAN-v3 §2.18 H3, D5-11, SITE-R-034).

The instant check (/api/conformance, /check) and the badge are a PREVIEW of the engine. This
gate proves, on a FROZEN capture set, that both graders say the same thing about the same
bytes and that neither drifts silently:

  · the id map conformance/web/preview_parity.json is add-only and its projection
    functions/api/preview_ids.js is byte-fresh (gen_preview_ids.py --check)
  · JS side: conformance/web/preview_parity/parity_runner.mjs runs the REAL preview module
    over every capture with fetch stubbed to the captured responses (no network)
  · engine side: every capture is served from an ephemeral loopback stub (the capture host
    mapped back to the stub's own origin) and graded by the REAL CLI
    (conformance/checks/merchant.py --json); the mapped engine twin(s) give the verdict
  · both columns are compared with conformance/web/preview_parity/expected.json (the
    expected-verdict file, frozen by --record and reviewed like code). A divergence is: a
    JS verdict drift, an engine verdict drift, a JS/engine disagreement that is not
    tolerated, a preview id the map does not carry, a capture the expected file does not
    carry, an expired or stale `tolerated_divergences` entry, or a stale id-map projection.

    python3 conformance/ci/preview_parity.py            # `13 captures · 7 preview ids mapped · 0 divergences (N tolerated) · PASS`
    python3 conformance/ci/preview_parity.py --record   # freeze expected.json from the current run (review the diff!)
    python3 conformance/ci/preview_parity.py --selftest # `3/3 planted divergences RED · unmodified GREEN` (JS side, hermetic)
    python3 conformance/ci/preview_parity.py --js-only  # skip the engine runs (engine column from expected.json)
Exit 0 pass · 1 fail · 2 honest skip (node absent). Ports: ephemeral loopback stubs only.
"""
import copy
import datetime
import http.server
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAP = ROOT / "conformance" / "web" / "preview_parity.json"
PAR = ROOT / "conformance" / "web" / "preview_parity"
CAPS = PAR / "captures"
EXPECTED = PAR / "expected.json"
RUNNER = PAR / "parity_runner.mjs"
MERCHANT = ROOT / "conformance" / "checks" / "merchant.py"
PREVIEW_JS = ROOT / "functions" / "api" / "conformance.js"
DECIDED = ("pass", "deviation")


def load_map(path=MAP):
    return json.load(open(path, encoding="utf-8"))


def mapped_ids(m):
    return [i for i, e in m["preview"].items() if not e.get("marker")]


def load_captures(d=CAPS):
    caps = {}
    for f in sorted(pathlib.Path(d).glob("*.json")):
        c = json.load(open(f, encoding="utf-8"))
        caps[c["name"]] = c
    return caps


# ── JS side ───────────────────────────────────────────────────────────────────
def js_verdicts(captures_dir=CAPS, preview_js=PREVIEW_JS, map_path=MAP):
    env = dict(os.environ, PARITY_MAP=str(map_path))
    r = subprocess.run(["node", str(RUNNER), str(captures_dir), str(preview_js)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"parity_runner.mjs failed: {r.stderr[-400:]}")
    return json.loads(r.stdout)


# ── engine side: ephemeral stub per capture + the real CLI ────────────────────
def _stub(cap, host):
    """A loopback HTTP server that serves the capture: the profile at /.well-known/ucp with
    the capture host mapped back to this stub's origin, the captured catalog probe answers,
    a UCP 404 envelope for everything else."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    origin = f"http://127.0.0.1:{srv.server_address[1]}"
    ver = cap.get("spec_version") or "2026-04-08"

    def body_of(entry):
        b = entry["body"]
        text = b if isinstance(b, str) else json.dumps(b)
        return text.replace(host, origin).encode()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, ctype, raw):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _404(self):
            env = {"ucp": {"version": ver, "status": "error"},
                   "messages": [{"type": "error", "code": "not_found", "content": "Not Found", "severity": "unrecoverable"}]}
            self._send(404, "application/json", json.dumps(env).encode())

        def do_GET(self):
            if self.path.split("?")[0] == "/.well-known/ucp":
                wk = cap["well_known"]
                self._send(wk["status"], wk.get("content_type") or "application/json", body_of(wk))
            else:
                self._404()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            cat = cap.get("catalog") or {}
            p = self.path.split("?")[0]
            entry = None
            if p.endswith("/catalog/search"):
                entry = cat.get("empty_search") if body.get("query") == "zzz_no_such_product_zzz" else cat.get("search")
            elif p.endswith("/catalog/lookup"):
                entry = cat.get("lookup")
            if entry:
                self._send(entry["status"], entry.get("content_type") or "application/json", body_of(entry))
            else:
                self._404()

    srv.RequestHandlerClass = H
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, origin


def _norm_engine(status):
    s = str(status)
    if s == "clean-pass":
        return "pass"
    if s == "deviation":
        return "deviation"
    if s.startswith("not-tested") or s.startswith("not-applicable"):
        return "not-tested"
    return s


def engine_verdicts_for(cap, m, host):
    """{js id: verdict} for one capture: the mapped engine twin(s) for the capture's version,
    read from the REAL CLI's JSON. `absent` = no such engine row; `error` = the CLI did not
    produce a report (frozen in expected.json like any other verdict)."""
    srv, origin = _stub(cap, host)
    try:
        r = subprocess.run([sys.executable, str(MERCHANT), "--server", origin, "--json"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=240)
        try:
            rep = json.loads(r.stdout)
        except ValueError:
            rep = None
    finally:
        srv.shutdown(); srv.server_close()
    by = {c["id"]: _norm_engine(c["status"]) for c in (rep or {}).get("checks", [])}
    ver = cap.get("spec_version")
    out = {}
    for jid in mapped_ids(m):
        twins = (m["preview"][jid].get("engine") or {}).get(ver) or []
        if rep is None:
            out[jid] = "error" if twins else "absent"
        elif not twins:
            out[jid] = "absent"
        else:
            # decided twins (pass/deviation) must agree; an undecided twin (version-scoped
            # away, not-tested, absent) never masks a decided one
            vs = [by.get(t, "absent") for t in twins]
            dec = sorted({v for v in vs if v in DECIDED})
            out[jid] = dec[0] if len(dec) == 1 else ("mixed:" + "/".join(dec) if dec else vs[0])
    return out


def engine_verdicts(caps, m):
    return {name: engine_verdicts_for(cap, m, m["capture_host"]) for name, cap in caps.items()}


# ── compare ───────────────────────────────────────────────────────────────────
def compare(js, eng, expected, m, today, id_map_fails=()):
    ids = mapped_ids(m)
    exp = expected.get("captures") or {}
    tol = expected.get("tolerated_divergences") or []
    div = [f"id-map projection: {f}" for f in id_map_fails]
    used = set()
    for name in sorted(set(js) | set(exp)):
        if name not in exp:
            div.append(f"{name}: capture not in expected.json — freeze it with --record after review"); continue
        if name not in js:
            div.append(f"{name}: expected.json names a capture that no longer exists"); continue
        jv, ev = js[name]["verdicts"], eng.get(name, {})
        for jid in sorted(jv):
            if jid not in m["preview"]:
                div.append(f"{name}: preview id {jid!r} is not in the id map (add-only map: register it)")
        for jid in ids:
            e = exp[name].get(jid) or {}
            j, g = jv.get(jid, "absent"), ev.get(jid, "absent")
            if j != e.get("js"):
                div.append(f"{name}/{jid}: JS verdict {j!r} != expected {e.get('js')!r}")
            if g != e.get("engine"):
                div.append(f"{name}/{jid}: engine verdict {g!r} != expected {e.get('engine')!r}")
            if j in DECIDED and g in DECIDED and j != g:
                t = next((x for x in tol if x.get("capture") == name and x.get("id") == jid), None)
                if t is None:
                    div.append(f"{name}/{jid}: JS says {j}, engine says {g} — untolerated disagreement")
                else:
                    used.add((name, jid))
    for t in tol:
        key = (t.get("capture"), t.get("id"))
        exp_on = t.get("expires_on") or ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp_on) or exp_on < today:
            div.append(f"tolerated divergence {key[0]}/{key[1]} expired on {exp_on or '(none)'} — re-decide it ({t.get('reason', '')[:60]})")
        if key not in used:
            div.append(f"tolerated divergence {key[0]}/{key[1]} no longer matches a disagreement — retire the entry")
    return div


def record(js, eng, m):
    out = {"_about": "Expected verdicts of the preview-parity gate (D5-11): per capture, per preview id, the JS "
                     "preview verdict and the engine twin's verdict, frozen by `preview_parity.py --record` "
                     "and reviewed like code. `tolerated_divergences` = JS/engine disagreements accepted "
                     "with a reason and an expiry (never open-ended).",
           "captures": {}, "tolerated_divergences": []}
    if EXPECTED.exists():
        out["tolerated_divergences"] = json.load(open(EXPECTED)).get("tolerated_divergences") or []
    for name in sorted(js):
        out["captures"][name] = {jid: {"js": js[name]["verdicts"].get(jid, "absent"),
                                       "engine": eng.get(name, {}).get(jid, "absent")} for jid in mapped_ids(m)}
    EXPECTED.write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n")


def _summary_line(ncaps, m, div, tolerated):
    return (f"preview-parity: {ncaps} captures · {len(mapped_ids(m))} preview ids mapped · "
            f"{len(div)} divergences ({tolerated} tolerated) · {'PASS' if not div else 'FAIL'}")


def main(argv):
    if not shutil.which("node"):
        print("preview-parity: SKIP — node not available"); return 2
    if "--selftest" in argv:
        return selftest()
    sys.path.insert(0, str(ROOT / "conformance" / "web"))
    import gen_preview_ids
    m = load_map()
    caps = load_captures()
    js = js_verdicts()
    if "--js-only" in argv and EXPECTED.exists():
        e = json.load(open(EXPECTED))
        eng = {n: {jid: v.get("engine") for jid, v in c.items()} for n, c in e.get("captures", {}).items()}
    else:
        eng = engine_verdicts(caps, m)
    if "--record" in argv:
        record(js, eng, m)
        print(f"preview-parity: recorded {len(caps)} captures x {len(mapped_ids(m))} ids -> {EXPECTED.relative_to(ROOT)}")
    expected = json.load(open(EXPECTED)) if EXPECTED.exists() else {}
    today = datetime.date.today().isoformat()
    div = compare(js, eng, expected, m, today, gen_preview_ids.check_failures())
    for d in div:
        print(f"  x {d}")
    print(_summary_line(len(caps), m, div, len(expected.get("tolerated_divergences") or [])))
    return 0 if not div else 1


def selftest():
    """Three planted divergences must RED; the unmodified tree must stay GREEN. JS side +
    compare logic only (engine column from expected.json) — hermetic and fast."""
    m = load_map()
    expected = json.load(open(EXPECTED))
    eng = {n: {jid: v.get("engine") for jid, v in c.items()} for n, c in expected["captures"].items()}
    today = datetime.date.today().isoformat()
    bad = 0

    def case(name, div, want_red, must_name=None):
        nonlocal bad
        red = bool(div)
        ok = red == want_red and (not must_name or any(must_name in d for d in div))
        print(f"  {'✓' if ok else '✗'} {name}: {'RED' if red else 'GREEN'}"
              + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}"
                               + (f" naming {must_name!r}" if must_name else ""))
              + (("\n      " + "\n      ".join(div[:4])) if div and not ok else ""))
        bad += 0 if ok else 1

    # unmodified
    js = js_verdicts()
    case("unmodified tree", compare(js, eng, expected, m, today), want_red=False)
    # 1. capsOk forced true in a scratch copy of the preview module
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(ROOT / "functions" / "api" / "preview_ids.js", tmp)
        src = PREVIEW_JS.read_text()
        assert "const capsOk = legacy0111" in src
        (pathlib.Path(tmp) / "conformance.js").write_text(src.replace("const capsOk = legacy0111", "const capsOk = true || legacy0111"))
        js1 = js_verdicts(preview_js=pathlib.Path(tmp) / "conformance.js")
        case("capsOk forced true", compare(js1, eng, expected, m, today), True, must_name="capabilities")
    # 2. an id-map row deleted (add-only map: the preview emits an id the map no longer carries)
    with tempfile.TemporaryDirectory() as tmp:
        m2 = copy.deepcopy(m); del m2["preview"]["discovery.services_array"]
        mp = pathlib.Path(tmp) / "map.json"; mp.write_text(json.dumps(m2))
        js2 = js_verdicts(map_path=mp)
        exp2 = copy.deepcopy(expected)
        for c in exp2["captures"].values():
            c.pop("discovery.services_array", None)
        case("id-map row deleted", compare(js2, eng, exp2, m2, today), True, must_name="not in the id map")
    # 3. an expired tolerated_divergence
    exp3 = copy.deepcopy(expected)
    exp3["tolerated_divergences"] = list(exp3.get("tolerated_divergences") or []) + [
        {"capture": "controlled-2026-04-08", "id": "discovery.version", "reason": "planted", "expires_on": "2026-01-01"}]
    case("expired tolerated_divergence", compare(js, eng, exp3, m, today), True, must_name="expired")
    print("preview-parity selftest: " + ("3/3 planted divergences RED · unmodified GREEN" if not bad else f"FAIL ({bad} case(s))"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

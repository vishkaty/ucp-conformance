#!/usr/bin/env python3
"""
capture.py — (re)build the FROZEN capture set of the preview-parity gate (D5-11, SITE-R-034).

Boots each source on an EPHEMERAL loopback port (never a registered ports.json port), fetches
exactly what both graders fetch — GET /.well-known/ucp, then the read-only catalog probes
(search "*", the no-match search, lookup of the first product) with the ENGINE's request
headers — stops what it booted, and writes conformance/web/preview_parity/captures/<name>.json
with the served origin normalised to the id map's `capture_host` (the preview's SSRF guard
refuses loopback endpoints; the gate's stub server maps the host back to itself).

    python3 conformance/web/preview_parity/capture.py --controlled      # 3 fixture versions
    python3 conformance/web/preview_parity/capture.py --golden-0825     # our 08-25 golden (uv)
    python3 conformance/web/preview_parity/capture.py --flower          # the official Flower Shop sample (uv)
    python3 conformance/web/preview_parity/capture.py --synthesize      # 8 mutants from controlled-2026-04-08
Owner-side tool: the gate itself never boots anything. Stdlib only (+ the engine's headers).
"""
import argparse
import copy
import datetime
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
CAPS = HERE / "captures"
MAP = json.load(open(ROOT / "conformance" / "web" / "preview_parity.json"))
HOST = MAP["capture_host"]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
from wire_shapes import base_headers  # noqa: E402  (the engine's exact request headers)

TODAY = datetime.date.today().isoformat()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(base, path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **base_headers()}
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("content-type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read().decode("utf-8", "replace")


def wait_up(base, tries=80):
    for _ in range(tries):
        try:
            urllib.request.urlopen(base + "/.well-known/ucp", timeout=2)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _normalise(text, origins):
    for o in origins:
        text = text.replace(o, HOST)
    return text


def capture(base, name, source, origins):
    st, ct, body = _req(base, "/.well-known/ucp")
    try:
        profile = json.loads(_normalise(body, origins))
    except ValueError:
        profile = None
    ucp = (profile or {}).get("ucp", profile) if isinstance(profile, dict) else {}
    version = ucp.get("version") if isinstance(ucp, dict) else None
    cap = {"schema": "preview-capture/1", "name": name, "source": source, "captured_at": TODAY,
           "spec_version": version, "normalized": {"from": origins, "to": HOST},
           "well_known": {"status": st, "content_type": ct, "body": profile if profile is not None else body},
           "catalog": None}
    caps = ucp.get("capabilities") if isinstance(ucp, dict) else None
    names = set(caps.keys()) if isinstance(caps, dict) else {e.get("name") for e in caps if isinstance(e, dict)} if isinstance(caps, list) else set()
    svc = (ucp.get("services") or {}).get("dev.ucp.shopping") if isinstance(ucp, dict) else None
    rest = None
    if isinstance(svc, list):
        rest = next((s.get("endpoint") for s in svc if isinstance(s, dict) and s.get("transport") == "rest"), None)
    elif isinstance(svc, dict) and isinstance(svc.get("rest"), dict):
        rest = svc["rest"].get("endpoint")
    if rest and "dev.ucp.shopping.catalog.search" in names:
        ep = rest.replace(HOST, base)          # back to the live origin for the probe
        def probe(path, body):
            s2, c2, b2 = _req(ep, path, "POST", body)
            try:
                j = json.loads(_normalise(b2, origins))
            except ValueError:
                j = b2
            return {"status": s2, "content_type": c2, "body": j}
        search = probe("/catalog/search", {"query": "*"})
        empty = probe("/catalog/search", {"query": "zzz_no_such_product_zzz"})
        prods = (search["body"] or {}).get("products") if isinstance(search["body"], dict) else None
        lookup = None
        if "dev.ucp.shopping.catalog.lookup" in names and prods:
            lookup = probe("/catalog/lookup", {"ids": [prods[0].get("id")]})
        cap["catalog"] = {"search": search, "empty_search": empty, "lookup": lookup}
    CAPS.mkdir(parents=True, exist_ok=True)
    (CAPS / f"{name}.json").write_text(json.dumps(cap, indent=1, ensure_ascii=True, sort_keys=False) + "\n")
    print(f"captured {name} (spec {version}, catalog {'yes' if cap['catalog'] else 'no'}) -> captures/{name}.json")


def do_controlled():
    fixture = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
    for ver in ("2026-04-08", "2026-01-23", "2026-01-11"):
        port = free_port(); base = f"http://127.0.0.1:{port}"
        p = subprocess.Popen([sys.executable, str(fixture), "--port", str(port), "--spec-version", ver],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert wait_up(base), f"controlled {ver} did not come up on {port}"
            capture(base, f"controlled-{ver}", f"conformance/fixtures/merchant/server.py --spec-version {ver} (ephemeral port)",
                    [f"http://127.0.0.1:{port}", f"http://localhost:{port}"])
        finally:
            p.terminate(); p.wait(timeout=10)


def do_golden_0825():
    port = free_port(); base = f"http://localhost:{port}"
    db = tempfile.mkdtemp(prefix="parity_golden_0825_")
    env = dict(os.environ, PORT=str(port), DB_DIR=db, SIM_SECRET="parity-capture")
    env.pop("DEFECTS_CONFIG", None); env.pop("DEFECTS_STATE_FILE", None)
    d = ROOT / "conformance" / "testbed" / "golden-0825"
    r = subprocess.run([str(d / "serve_golden_0825.sh")], env=env, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        sys.exit(f"golden-0825 boot failed: {(r.stderr or r.stdout)[-400:]}")
    try:
        capture(base, "golden-0825", "conformance/testbed/golden-0825 (serve_golden_0825.sh, ephemeral port)",
                [f"http://localhost:{port}", f"http://127.0.0.1:{port}"])
    finally:
        subprocess.run([str(d / "stop_golden_0825.sh")], env=env, capture_output=True, text=True, timeout=60)


def do_flower():
    port = free_port(); base = f"http://localhost:{port}"
    db = tempfile.mkdtemp(prefix="parity_flower_")
    env = dict(os.environ, PORT=str(port), DB_DIR=db, SIM_SECRET="parity-capture")
    r = subprocess.run([str(ROOT / "conformance" / "ci" / "serve_golden.sh")], env=env, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        sys.exit(f"flower golden boot failed: {(r.stderr or r.stdout)[-400:]}")
    try:
        capture(base, "flower-golden", "official Flower Shop sample (conformance/ci/serve_golden.sh, vendored pin, ephemeral port)",
                [f"http://localhost:{port}", f"http://127.0.0.1:{port}"])
    finally:
        subprocess.run([str(ROOT / "conformance" / "ci" / "stop_golden.sh")], env=env, capture_output=True, text=True, timeout=60)


def _root(c):
    """The profile root both graders grade: `body.ucp` when wrapped, else the flat body."""
    b = c["well_known"]["body"]
    return b.get("ucp", b)


MUTANTS = [
    ("synthetic-capabilities-array", "capabilities as an ARRAY of names (04-08 expects a keyed object)",
     lambda c: _root(c).__setitem__("capabilities", list(_root(c)["capabilities"].keys()))),
    ("synthetic-capability-name-not-reverse-domain", "first capability key renamed to 'Checkout' (not reverse-domain)",
     lambda c: _rename_first_cap(c)),
    ("synthetic-services-object", "services.dev.ucp.shopping as an OBJECT {rest:{endpoint}} (04-08 expects an array)",
     lambda c: _root(c)["services"].__setitem__("dev.ucp.shopping", {"rest": {"endpoint": HOST}})),
    ("synthetic-version-undated", "version 'draft' (OVR-010: MUST be a dated YYYY-MM-DD release)",
     lambda c: _root(c).__setitem__("version", "draft")),
    ("synthetic-content-type-html", "profile served as text/html (SHOULD application/json — report-only)",
     lambda c: c["well_known"].__setitem__("content_type", "text/html; charset=utf-8")),
    ("synthetic-catalog-search-shape", "search products[0] without variants (CAT-012 shape)",
     lambda c: c["catalog"]["search"]["body"]["products"][0].pop("variants")),
    ("synthetic-catalog-empty-search-messages", "no-match search answers with an error message (CAT-012)",
     lambda c: c["catalog"]["empty_search"]["body"].__setitem__("messages", [{"type": "error", "code": "no_results", "content": "nothing", "severity": "recoverable"}])),
    ("synthetic-catalog-lookup-no-inputs", "lookup variants with an empty inputs array (CAT-017/018)",
     lambda c: [v.__setitem__("inputs", []) for p in c["catalog"]["lookup"]["body"]["products"] for v in p.get("variants", [])]),
]


def _rename_first_cap(c):
    caps = _root(c)["capabilities"]
    k = sorted(caps)[0]
    caps["Checkout"] = caps.pop(k)


def do_synthesize():
    base = json.load(open(CAPS / "controlled-2026-04-08.json"))
    assert base.get("catalog") and base["catalog"].get("lookup"), "controlled-2026-04-08 needs catalog captures first"
    for name, what, mut in MUTANTS:
        c = copy.deepcopy(base)
        c["name"] = name
        c["source"] = f"synthetic: controlled-2026-04-08 with {what}"
        c["mutation"] = what
        c["captured_at"] = base["captured_at"]
        mut(c)
        (CAPS / f"{name}.json").write_text(json.dumps(c, indent=1, ensure_ascii=True) + "\n")
        print(f"synthesized {name}: {what}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--controlled", action="store_true")
    ap.add_argument("--golden-0825", action="store_true")
    ap.add_argument("--flower", action="store_true")
    ap.add_argument("--synthesize", action="store_true")
    a = ap.parse_args()
    if a.controlled: do_controlled()
    if a.golden_0825: do_golden_0825()
    if a.flower: do_flower()
    if a.synthesize: do_synthesize()
    if not any(vars(a).values()):
        ap.print_help(); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

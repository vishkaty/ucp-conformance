#!/usr/bin/env python3
"""
validate_cli_summary.py — the CLI's headline math must be capability- AND transport-
aware, must refuse to print a coverage number it cannot stand behind, and must never
treat an unmapped register area as "core" (PLAN-v3 §2.3, D1-03).

As-is (main f9cc470): merchant.applicable_musts counted every testable MUST row whose
area was not in a hard-coded 04-08-named AREA_CAPABILITY map — so at 2026-08-25 the
denominator was 353 for golden-0825, including location (35), permalink (19), loyalty
(17), payment-terms (13), split-payments (12), payment-authentication (9) rows for
capabilities the server never declared; an MCP-only server got `coverage: 0.0` (a real
number about nothing); and the headline mixed MUST ids with check counts.

Target: `conformance/requirements/<v>/_area_capabilities.json` {area: capability|null}
for every reviewed version (null = core); a row counts only if its area's capability is
declared (or core) AND its transport intersects the server's; an area present in the
register but absent from the file FAILS CLOSED (rc 1 naming the area — never silently
"core"). Outside SUPPORTED_SERVED_VERSIONS or with no REST transport the JSON carries
`verdict.coverage: null`, `support`, a banner, and `checks_summary` (checks, never ids).

Cases (loopback stubs, hermetic):
  (a) golden-0825-shaped 6-capability REST profile → denominator 248 (pinned on first
      run 2026-09-10; strictly below the 353 that counted undeclared extensions)
  (b) MCP-only 08-25 profile → coverage None, support "rest-not-declared", banner,
      checks_summary.not_applicable totals 237 (54 transport · 181 version-scoped ·
      2 capability under the runner's precedence)
  (c) 2026-01-23 profile → denominator frozen to today's 108; 2026-04-08 → 125
  (d) a register area missing from the file → rc 1 naming the area
  (e) an unreviewed served version → coverage None, support "unreviewed-version"

    --selftest   exit 0 pass, 1 fail.
"""
import contextlib
import http.server
import io
import json
import pathlib
import shutil
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))
import merchant  # noqa: E402

CAPS6 = ("dev.ucp.shopping.buyer_consent", "dev.ucp.shopping.cart", "dev.ucp.shopping.checkout",
         "dev.ucp.shopping.discount", "dev.ucp.shopping.fulfillment", "dev.ucp.shopping.order")
CAPS_0123 = ("dev.ucp.shopping.checkout", "dev.ucp.shopping.fulfillment",
             "dev.ucp.shopping.discount", "dev.ucp.shopping.order")
PIN_0825_6CAPS = 255        # pinned 2026-09-10 (D1-03 first run: 248); re-pinned at the W0 integration
                            # merge the same day: D2-01 made 10 REQUIRED/SHALL rows mandatory
                            # (CHK-008/036/037/046, DISC-006, ORD-015, PAY-009/010/017, TOT-010) and
                            # D2-02 merged 3 away (CART-036, DSC-029, PAY-018) -> 248 + 10 - 3 = 255.
                            # Was 353 before the capability map.
PIN_0123 = 108              # today's value for CAPS_0123 over REST — frozen
PIN_0408 = 125              # today's value for CAPS6 over REST — frozen
PIN_MCP_ONLY_NA = {"transport": 54, "version_scoped": 181, "capability": 2}   # transport 43 -> 53 at D1-16a
                            # (10 REST-only 08-25 MChecks); 53 -> 54 on 2026-09-22 with
                            # discovery.profile_no_redirect_0825, DISC-002's live 08-25 grader


def _profile(version, caps, transports, base):
    return {"ucp": {"version": version, "capabilities": {c: {} for c in caps},
                    "services": {"dev.ucp.shopping": [
                        {"transport": t, "endpoint": f"{base}/{t}"} for t in transports]}}}


class _Stub(http.server.BaseHTTPRequestHandler):
    profile = {}

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/.well-known/ucp"):
            return self._send(200, self.profile)
        self._send(404, {"error": "not found"})

    do_POST = do_PUT = do_DELETE = do_GET

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def stub(profile_fn):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    _Stub.profile = profile_fn(base)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        yield base
    finally:
        srv.shutdown(); srv.server_close()


def _cli_json(base):
    """merchant.main() in-process with --json; returns (rc, parsed JSON, stdout)."""
    old = sys.argv; sys.argv = ["merchant.py", "--server", base, "--json"]
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                rc = merchant.main()
            except SystemExit as e:
                rc = e.code
    finally:
        sys.argv = old
    text = buf.getvalue() + err.getvalue()
    try:
        doc = json.loads(text[text.index("{"):])
    except ValueError:
        doc = None
    return rc, doc, text


def _denominator(base):
    profile, _ = merchant.discover(base)
    ctx = merchant.MerchantCtx(base, profile, {})
    return len(merchant.applicable_musts(ctx))


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    # (a) capability-aware denominator at 08-25
    with stub(lambda b: _profile("2026-08-25", CAPS6, ["rest"], b)) as base:
        try:
            n = _denominator(base)
            check("(a) 08-25 six-capability denominator pinned", n == PIN_0825_6CAPS,
                  f"got {n} (expected {PIN_0825_6CAPS})")
            check("(a) strictly below the undeclared-extensions count", n < 353, f"got {n}")
        except TypeError as e:                       # applicable_musts(ctx) signature
            check("(a) 08-25 six-capability denominator pinned", False, f"{e}")
        rc, doc, text = _cli_json(base)
        v = (doc or {}).get("verdict", {})
        check("(a) REST 08-25 keeps a numeric coverage", isinstance(v.get("coverage"), (int, float)),
              f"coverage={v.get('coverage')!r}")
        check("(a) support = supported", v.get("support") == "supported", f"{v.get('support')!r}")
        cs = (doc or {}).get("checks_summary")
        check("(a) checks_summary present with the 5 buckets",
              isinstance(cs, dict) and set(cs) >= {"run", "clean", "deviation", "not_tested", "not_applicable"}
              and set(cs.get("not_applicable", {})) >= {"version_scoped", "capability", "transport", "config"},
              f"{cs}")

    # (b) MCP-only 08-25: no number, a banner, transport bucket
    with stub(lambda b: _profile("2026-08-25", CAPS6, ["mcp"], b)) as base:
        rc, doc, text = _cli_json(base)
        v = (doc or {}).get("verdict", {})
        check("(b) MCP-only coverage is null", "coverage" in v and v["coverage"] is None,
              f"coverage={v.get('coverage')!r}")
        check("(b) support = rest-not-declared", v.get("support") == "rest-not-declared", f"{v.get('support')!r}")
        check("(b) banner present", "REST transport not declared" in ((doc or {}).get("banner") or ""),
              f"banner={(doc or {}).get('banner')!r}")
        na = ((doc or {}).get("checks_summary") or {}).get("not_applicable") or {}
        check("(b) not_applicable totals 237 (250 checks − 13 reachable over MCP: 4 run + 9 needs-product, W1 integration)",
              sum(na.values()) == 237 if na else False, f"{na}")
        check("(b) not_applicable split pinned",
              {k: na.get(k) for k in PIN_MCP_ONLY_NA} == PIN_MCP_ONLY_NA, f"{na}")

    # (c) old versions frozen
    with stub(lambda b: _profile("2026-01-23", CAPS_0123, ["rest"], b)) as base:
        try:
            n = _denominator(base)
        except TypeError as e:
            n = f"TypeError: {e}"
        check("(c) 2026-01-23 denominator frozen", n == PIN_0123, f"got {n}")
    with stub(lambda b: _profile("2026-04-08", CAPS6, ["rest"], b)) as base:
        try:
            n = _denominator(base)
        except TypeError as e:
            n = f"TypeError: {e}"
        check("(c) 2026-04-08 denominator frozen", n == PIN_0408, f"got {n}")

    # (d) fail closed on an unmapped register area
    with stub(lambda b: _profile("2026-08-25", CAPS6, ["rest"], b)) as base:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cli_summary_"))
        try:
            shutil.copytree(merchant.REQ_DIR, tmp / "requirements")
            unlisted = tmp / "requirements" / "2026-08-25" / "zz-unlisted-area.json"
            unlisted.write_text(json.dumps({"_area": "zz-unlisted", "rows": [
                {"id": "ZZ-001", "keyword": "MUST", "testability": "testable",
                 "transport": ["rest"], "requirement": "planted", "source": "planted"}]}))
            old_req = merchant.REQ_DIR
            merchant.REQ_DIR = tmp / "requirements"
            try:
                rc, doc, text = _cli_json(base)
            finally:
                merchant.REQ_DIR = old_req
            check("(d) unlisted register area → rc 1", rc == 1, f"rc={rc}")
            check("(d) names the area", "zz-unlisted" in text, text.strip()[-200:])
            # and a MISSING map file is the same failure, never "everything is core"
            (tmp / "requirements" / "2026-08-25" / "_area_capabilities.json").unlink(missing_ok=True)
            unlisted.unlink()
            merchant.REQ_DIR = tmp / "requirements"
            try:
                rc, doc, text = _cli_json(base)
            finally:
                merchant.REQ_DIR = old_req
            check("(d) missing _area_capabilities.json → rc 1", rc == 1, f"rc={rc}")
            check("(d) missing map names the file", "_area_capabilities.json" in text, text.strip()[-200:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # (e) an unreviewed served version never gets a number
    with stub(lambda b: _profile("2027-01-01", CAPS6, ["rest"], b)) as base:
        rc, doc, text = _cli_json(base)
        v = (doc or {}).get("verdict", {})
        check("(e) unreviewed version coverage is null", "coverage" in v and v["coverage"] is None,
              f"coverage={v.get('coverage')!r}")
        check("(e) support = unreviewed-version", v.get("support") == "unreviewed-version",
              f"{v.get('support')!r}")

    print(f"cli-summary: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--selftest" not in sys.argv:
        print("usage: validate_cli_summary.py --selftest"); sys.exit(2)
    sys.exit(selftest())

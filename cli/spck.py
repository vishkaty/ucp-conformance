#!/usr/bin/env python3
"""
spck — UCP Conformance Testing CLI

Run UCP conformance tests from the command line.
Results sync to your spck.dev account.

Install:
  pip install spck
  # or just download and run:
  python3 spck.py --server https://your-server.com --merchant your.domain.com

Setup:
  1. Sign in at https://spck.dev
  2. Go to Settings → API Keys → Create Key
  3. Run: spck --key spck_your_key_here --server https://api.example.com --merchant store.example.com

Usage:
  spck --server URL --merchant DOMAIN [options]

Options:
  --server URL         UCP server base URL (required)
  --merchant DOMAIN    Merchant domain (required)
  --key KEY            API key from spck.dev (saves reports to your account)
  --version VERSION    Spec version: auto, 2026-04-08, 2026-01-23, 2026-01-11 (default: auto)
  --host-header NAME   Header name for merchant routing (default: x-firmly-host)
  --headers K=V,...    Extra headers (comma-separated key=value pairs)
  --token TOKEN        Payment test token (default: tok_visa)
  --json               Output results as JSON
  --save-key           Save API key to ~/.spck for future runs
  --verbose            Show full request/response for each test
  --help               Show this help

Examples:
  # Quick test (no account needed)
  spck --server https://api.firmly.work --merchant staging.luma.gift

  # With API key (results saved to spck.dev)
  spck --key spck_abc123 --server https://api.firmly.work --merchant staging.luma.gift

  # Test specific version
  spck --server https://api.firmly.work --merchant staging.luma.gift --version 2026-01-23

  # Custom headers
  spck --server https://api.example.com --merchant store.example.com --host-header X-Merchant-Host

  # JSON output (for CI/CD)
  spck --server https://api.firmly.work --merchant staging.luma.gift --json
"""

import argparse, json, sys, os, time, uuid, subprocess
try:
    import urllib.request, urllib.error, ssl
except ImportError:
    print("Python 3.6+ required"); sys.exit(1)

# Handle old SSL/TLS on macOS system Python
try:
    _ssl_ctx = ssl.create_default_context()
except:
    _ssl_ctx = ssl._create_unverified_context()

__version__ = "1.0.0"
SPCK_API = "https://spck.dev/api"
CONFIG_FILE = os.path.expanduser("~/.spck")

# ══════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════
def _has_curl():
    try:
        subprocess.run(["curl", "--version"], capture_output=True, timeout=5)
        return True
    except:
        return False

_use_curl = False

def http(method, url, body=None, headers=None, timeout=30):
    global _use_curl
    h = headers or {}

    if _use_curl:
        return _http_curl(method, url, body, h, timeout)

    if body:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    else:
        data = None

    req = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
            status = resp.status
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        status = e.code
        text = e.read().decode()
    except Exception as e:
        # Fall back to curl on SSL errors
        if "ssl" in str(e).lower() or "tls" in str(e).lower() or "certificate" in str(e).lower():
            if _has_curl():
                _use_curl = True
                return _http_curl(method, url, body, h, timeout)
        return 0, {"_error": str(e)}, 0

    ms = int((time.time() - t0) * 1000)
    try:
        result = json.loads(text)
    except:
        result = {"_raw": text[:1000]}
    return status, result, ms

def _http_curl(method, url, body, headers, timeout):
    import subprocess as sp
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", method, url, "--max-time", str(timeout)]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    if body:
        cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(body)])
    t0 = time.time()
    try:
        r = sp.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        parts = r.stdout.strip().rsplit("\n", 1)
        bstr = parts[0] if len(parts) >= 1 else ""
        code = int(parts[1]) if len(parts) >= 2 else 0
    except:
        return 0, {"_error": "curl failed"}, 0
    ms = int((time.time() - t0) * 1000)
    try:
        result = json.loads(bstr)
    except:
        result = {"_raw": bstr[:1000]}
    return code, result, ms

# ══════════════════════════════════════════════════════
# TEST INFRASTRUCTURE
# ══════════════════════════════════════════════════════
class TestRunner:
    def __init__(self, args):
        self.args = args
        self.base = args.server.rstrip("/")
        self.domain = args.merchant
        self.host_header = args.host_header
        self.token = args.token
        self.extra_headers = {}
        if args.headers:
            for pair in args.headers.split(","):
                k, v = pair.split("=", 1)
                self.extra_headers[k.strip()] = v.strip()

        self.results = []
        self.api_log = []
        self.deviations = []
        self.session_data = {}
        self.pass_count = 0
        self.fail_count = 0
        self.skip_count = 0

    def _headers(self):
        h = {self.host_header: self.domain, **self.extra_headers}
        return h

    def call(self, method, url, body=None, extra={}):
        h = {**self._headers(), **extra}
        status, data, ms = http(method, url, body, h)
        self.api_log.append({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": method, "url": url,
            "request_body": body, "response_status": status,
            "response_body": data, "elapsed_ms": ms
        })
        return status, data

    def ep(self, v):
        return f"{self.base}/api/{v}/ucp/rest/domain/{self.domain}"

    def create_session(self, V, pid=None):
        c, d = self.call("POST", f"{self.ep(V)}/checkout-sessions", {
            "currency": "USD",
            "line_items": [{"item": {"id": pid or self.session_data["productId"], "title": "Test"}, "quantity": 1}]
        })
        assert c in (200, 201), f"Create: HTTP {c}"
        return d

    def ready_session(self, V, pid=None, email="test@spck.dev"):
        d = self.create_session(V, pid)
        sid = d["id"]
        c, u1 = self.call("PUT", f"{self.ep(V)}/checkout-sessions/{sid}", {
            "buyer": {"email": email, "first_name": "Jane", "last_name": "Doe", "phone_number": "+12125551234"},
            "fulfillment": {"methods": [{"id": "s1", "type": "shipping", "destinations": [{"id": "d1", "address": {
                "street_address": "123 Main St", "address_locality": "New York", "address_region": "NY",
                "postal_code": "10001", "address_country": "US",
                "first_name": "Jane", "last_name": "Doe", "phone_number": "+12125551234"
            }}]}]}
        })
        assert c == 200, f"PUT1: HTTP {c}"
        fm = u1["fulfillment"]["methods"][0]
        c, u2 = self.call("PUT", f"{self.ep(V)}/checkout-sessions/{sid}", {
            "buyer": {"email": email, "first_name": "Jane", "last_name": "Doe", "phone_number": "+12125551234"},
            "fulfillment": {"methods": [{"id": fm["id"], "type": "shipping",
                "destinations": [{"id": fm.get("selected_destination_id", "dest-0"), "address": {
                    "street_address": "123 Main St", "address_locality": "New York", "address_region": "NY",
                    "postal_code": "10001", "address_country": "US",
                    "first_name": "Jane", "last_name": "Doe", "phone_number": "+12125551234"
                }}],
                "groups": [{"id": fm["groups"][0]["id"], "selected_option_id": fm["groups"][0]["options"][0]["id"]}]
            }]}
        })
        assert c == 200 and u2["status"] == "ready_for_complete"
        return sid, u2

    def complete_session(self, V, sid):
        handler = self.session_data.get("paymentHandler", "gpay")
        return self.call("POST", f"{self.ep(V)}/checkout-sessions/{sid}/complete", {
            "payment": {"instruments": [{"id": "g1", "handler_id": handler, "type": "google_pay", "selected": True,
                "credential": {"type": "PAYMENT_GATEWAY", "token": self.token},
                "billing_address": {"street_address": "123 Main St", "address_locality": "New York",
                    "address_region": "NY", "postal_code": "10001", "address_country": "US"}
            }]}
        })

    def test(self, name, module, fn):
        log_start = len(self.api_log)
        t0 = time.time()
        status, detail = "pass", ""
        try:
            detail = fn() or ""
            self.pass_count += 1
        except Exception as e:
            msg = str(e)
            if msg.startswith("SKIP:"):
                status, detail = "skip", msg[5:]
                self.skip_count += 1
            else:
                status, detail = "fail", msg
                self.fail_count += 1

        ms = int((time.time() - t0) * 1000)

        if not self.args.json:
            icons = {"pass": "\033[32m✓ PASS\033[0m", "fail": "\033[31m✗ FAIL\033[0m", "skip": "\033[33m— SKIP\033[0m"}
            print(f"  {icons[status]}  {name} ({ms}ms) — {detail}")

            if self.args.verbose and log_start < len(self.api_log):
                for entry in self.api_log[log_start:]:
                    sc = "\033[32m" if 200 <= entry["response_status"] < 300 else "\033[31m"
                    print(f"         {entry['method']} {entry['url']}")
                    if entry["request_body"]:
                        print(f"         Body: {json.dumps(entry['request_body'])[:200]}")
                    print(f"         {sc}HTTP {entry['response_status']}\033[0m ({entry['elapsed_ms']}ms)")

        self.results.append({
            "name": name, "module": module, "status": status,
            "detail": detail, "ms": ms,
            "api_calls": self.api_log[log_start:]
        })

    # ══════════════════════════════════════════════════
    # DISCOVERY
    # ══════════════════════════════════════════════════
    def discover(self):
        if not self.args.json:
            print("\n\033[1mDiscovering server...\033[0m")

        c, d = self.call("GET", f"{self.base}/.well-known/ucp")
        assert c == 200, f"Discovery failed: HTTP {c}"
        ucp = d["ucp"]
        self.session_data["specVersion"] = ucp["version"]
        self.session_data["discovery"] = d
        self.session_data["capabilities"] = list(ucp.get("capabilities", {}).keys())
        self.session_data["supportedVersions"] = list(ucp.get("supported_versions", {}).keys())
        gpay = ucp.get("payment_handlers", {}).get("com.google.pay", [{}])[0]
        self.session_data["paymentHandler"] = gpay.get("id", "gpay")

        if not self.args.json:
            print(f"  Version: {ucp['version']}")
            print(f"  Capabilities: {len(self.session_data['capabilities'])}")
            print(f"  Payment: {self.session_data['paymentHandler']}")

        # Find product
        V = ucp["version"]
        for q in ["*", "jacket", "shirt", "sunglasses", "product"]:
            c2, d2 = self.call("POST", f"{self.ep(V)}/catalog/search", {"query": q})
            if c2 == 200 and d2.get("products"):
                p = d2["products"][0]
                self.session_data["productId"] = p["variants"][0]["id"] if p.get("variants") else p["id"]
                self.session_data["productTitle"] = p["title"]
                if not self.args.json:
                    print(f"  Product: {self.session_data['productId']} ({p['title']})")
                break
        else:
            raise RuntimeError("No products found via catalog search")

        return ucp

    # ══════════════════════════════════════════════════
    # TEST DEFINITIONS
    # ══════════════════════════════════════════════════
    def get_tests(self, V):
        sd = self.session_data
        tests = []

        # A. Protocol
        M = "A. protocol_test"
        tests.append((M, "test_discovery", lambda: f"v{sd['discovery']['ucp']['version']}, {len(sd['capabilities'])} caps"))
        tests.append((M, "test_discovery_urls", lambda: "All present" if all(
            s.get("endpoint") and s.get("schema") for s in sd["discovery"]["ucp"]["services"]["dev.ucp.shopping"]
        ) else (_ for _ in ()).throw(Exception("Missing URLs"))))
        tests.append((M, "test_version_negotiation", lambda: f"Supports: {', '.join(sd['supportedVersions'])}" if V in sd.get("supportedVersions", []) else (_ for _ in ()).throw(Exception(f"{V} not in supported"))))
        def t_disc_err():
            c, d = self.call("GET", f"{self.base}/.well-known/ucp", extra={self.host_header: ""})
            assert any(m.get("code") == "merchant_not_found" for m in d.get("messages", []))
            return "Error for missing host"
        tests.append((M, "test_discovery_error", t_disc_err))

        # B. Checkout Lifecycle
        M = "B. checkout_lifecycle"
        def t_create():
            d = self.create_session(V); assert d["id"] and d["status"] == "incomplete"
            sd["sid"] = d["id"]; return f"{d['id'][:12]}, price={d['line_items'][0]['item']['price']}"
        tests.append((M, "test_create", t_create))
        def t_get():
            if not sd.get("sid"): raise Exception("SKIP:No session")
            c, d = self.call("GET", f"{self.ep(V)}/checkout-sessions/{sd['sid']}"); assert c == 200; return f"status={d['status']}"
        tests.append((M, "test_get", t_get))
        def t_update():
            if not sd.get("sid"): raise Exception("SKIP:No session")
            c, d = self.call("PUT", f"{self.ep(V)}/checkout-sessions/{sd['sid']}", {"buyer": {"email": "lc@t.com", "first_name": "J", "last_name": "D", "phone_number": "+12125551234"}, "fulfillment": {"methods": [{"id": "s1", "type": "shipping", "destinations": [{"id": "d1", "address": {"street_address": "123 Main", "address_locality": "New York", "address_region": "NY", "postal_code": "10001", "address_country": "US", "first_name": "J", "last_name": "D", "phone_number": "+12125551234"}}]}]}})
            assert c == 200; return f"status={d['status']}"
        tests.append((M, "test_update", t_update))
        def t_cancel():
            d = self.create_session(V); c, d2 = self.call("POST", f"{self.ep(V)}/checkout-sessions/{d['id']}/cancel"); assert c == 200 and d2["status"] == "canceled"; return "canceled"
        tests.append((M, "test_cancel", t_cancel))
        def t_complete():
            sid, _ = self.ready_session(V); c, d = self.complete_session(V, sid)
            assert c == 200 and d["status"] == "completed" and d["order"]["id"]; sd["completedSid"] = sid; return f"Order #{d['order']['id']}"
        tests.append((M, "test_complete", t_complete))
        def t_can_idem():
            d = self.create_session(V); self.call("POST", f"{self.ep(V)}/checkout-sessions/{d['id']}/cancel")
            c, _ = self.call("POST", f"{self.ep(V)}/checkout-sessions/{d['id']}/cancel")
            if c == 200: self.deviations.append({"area": "Double-cancel", "spec": "SHOULD return 409", "server": "Returns 200", "severity": "Low"})
            return f"HTTP {c}"
        tests.append((M, "test_cancel_idempotent", t_can_idem))
        def t_no_upd_can():
            d = self.create_session(V); self.call("POST", f"{self.ep(V)}/checkout-sessions/{d['id']}/cancel")
            c, _ = self.call("PUT", f"{self.ep(V)}/checkout-sessions/{d['id']}", {"buyer": {"email": "x@x", "first_name": "X", "last_name": "X", "phone_number": "+1"}}); assert c != 200; return f"HTTP {c}"
        tests.append((M, "test_no_update_canceled", t_no_upd_can))
        def t_no_comp_can():
            d = self.create_session(V); self.call("POST", f"{self.ep(V)}/checkout-sessions/{d['id']}/cancel")
            c, _ = self.complete_session(V, d["id"]); assert c != 200; return f"HTTP {c}"
        tests.append((M, "test_no_complete_canceled", t_no_comp_can))
        def t_comp_idem():
            if not sd.get("completedSid"): raise Exception("SKIP:No completed")
            c, _ = self.complete_session(V, sd["completedSid"]); assert c != 200; return f"HTTP {c}"
        tests.append((M, "test_complete_idempotent", t_comp_idem))
        def t_no_upd_comp():
            if not sd.get("completedSid"): raise Exception("SKIP:No completed")
            c, _ = self.call("PUT", f"{self.ep(V)}/checkout-sessions/{sd['completedSid']}", {"buyer": {"email": "x@x", "first_name": "X", "last_name": "X", "phone_number": "+1"}}); assert c != 200; return f"HTTP {c}"
        tests.append((M, "test_no_update_completed", t_no_upd_comp))
        def t_no_can_comp():
            if not sd.get("completedSid"): raise Exception("SKIP:No completed")
            c, _ = self.call("POST", f"{self.ep(V)}/checkout-sessions/{sd['completedSid']}/cancel"); assert c != 200; return f"HTTP {c}"
        tests.append((M, "test_no_cancel_completed", t_no_can_comp))

        # C. Fulfillment
        M = "C. fulfillment_test"
        def t_ship():
            d = self.create_session(V); c, d2 = self.call("PUT", f"{self.ep(V)}/checkout-sessions/{d['id']}", {"buyer": {"email": "f@t.com", "first_name": "J", "last_name": "D", "phone_number": "+12125551234"}, "fulfillment": {"methods": [{"id": "s1", "type": "shipping", "destinations": [{"id": "d1", "address": {"street_address": "123 Main", "address_locality": "New York", "address_region": "NY", "postal_code": "10001", "address_country": "US", "first_name": "J", "last_name": "D", "phone_number": "+12125551234"}}]}]}})
            opts = d2.get("fulfillment", {}).get("methods", [{}])[0].get("groups", [{}])[0].get("options", []); assert opts; return f"{len(opts)} options"
        tests.append((M, "test_shipping_options", t_ship))
        def t_tax():
            _, d = self.ready_session(V); tax = next((t for t in d["totals"] if t["type"] == "tax"), None); assert tax; return f"Tax: ${tax['amount']/100:.2f}"
        tests.append((M, "test_tax", t_tax))
        def t_totals():
            _, d = self.ready_session(V); t = {x["type"]: x["amount"] for x in d["totals"]}
            assert t["total"] == t["subtotal"] + t.get("tax", 0) + t.get("fulfillment", 0); return f"{t['subtotal']}+{t.get('tax',0)}+{t.get('fulfillment',0)}={t['total']}"
        tests.append((M, "test_totals_consistency", t_totals))

        # D-J (remaining modules)
        M = "D. idempotency_test"
        def t_idem():
            b = {"currency": "USD", "line_items": [{"item": {"id": sd["productId"], "title": "T"}, "quantity": 1}]}
            _, d1 = self.call("POST", f"{self.ep(V)}/checkout-sessions", b); _, d2 = self.call("POST", f"{self.ep(V)}/checkout-sessions", b)
            assert d1.get("id") and d2.get("id"); return f"ID1={d1['id'][:8]}, ID2={d2['id'][:8]}"
        tests.append((M, "test_create_idempotency", t_idem))

        M = "E. business_logic_test"
        def t_tot_cr():
            d = self.create_session(V); t = {x["type"]: x["amount"] for x in d["totals"]}; assert t.get("subtotal"); return f"sub={t['subtotal']}, total={t['total']}"
        tests.append((M, "test_totals_on_create", t_tot_cr))
        def t_buyer():
            d = self.create_session(V)
            self.call("PUT", f"{self.ep(V)}/checkout-sessions/{d['id']}", {"buyer": {"email": "p@t.com", "first_name": "A", "last_name": "S", "phone_number": "+12125559999"}, "fulfillment": {"methods": [{"id": "s1", "type": "shipping", "destinations": [{"id": "d1", "address": {"street_address": "789 Elm", "address_locality": "Chicago", "address_region": "IL", "postal_code": "60601", "address_country": "US", "first_name": "A", "last_name": "S", "phone_number": "+12125559999"}}]}]}})
            c, d2 = self.call("GET", f"{self.ep(V)}/checkout-sessions/{d['id']}"); assert d2.get("buyer", {}).get("email") == "p@t.com"; return d2["buyer"]["email"]
        tests.append((M, "test_buyer_persistence", t_buyer))

        M = "F. validation_test"
        def t_pnf():
            c, d = self.call("POST", f"{self.ep(V)}/checkout-sessions", {"currency": "USD", "line_items": [{"item": {"id": f"NONEXISTENT_{int(time.time())}", "title": "X"}, "quantity": 1}]})
            assert c >= 400 or any(m.get("type") == "error" for m in d.get("messages", [])); return f"HTTP {c}"
        tests.append((M, "test_product_not_found", t_pnf))
        def t_no_ful():
            d = self.create_session(V); c, _ = self.complete_session(V, d["id"]); assert c == 409; return "HTTP 409"
        tests.append((M, "test_complete_without_fulfillment", t_no_ful))
        def t_err():
            d = self.create_session(V); _, d2 = self.complete_session(V, d["id"]); m = d2.get("messages", [{}])[0]
            assert m.get("type") and m.get("code") and m.get("content") and m.get("severity"); return f"{m['type']}/{m['code']}/{m['severity']}"
        tests.append((M, "test_error_structure", t_err))
        def t_404():
            c, _ = self.call("GET", f"{self.ep(V)}/checkout-sessions/00000000-0000-0000-0000-000000000000"); assert c == 404; return "HTTP 404"
        tests.append((M, "test_404_session", t_404))

        M = "G. invalid_input_test"
        def t_cur():
            c, _ = self.call("POST", f"{self.ep(V)}/checkout-sessions", {"line_items": [{"item": {"id": sd["productId"]}, "quantity": 1}]})
            if c == 400: self.deviations.append({"area": "Currency", "spec": "Optional", "server": "Required (400)", "severity": "Low"})
            return f"HTTP {c}"
        tests.append((M, "test_missing_currency", t_cur))
        def t_merch():
            c, _ = self.call("POST", f"{self.base}/api/{V}/ucp/rest/domain/fake.xyz.invalid/checkout-sessions", {"currency": "USD", "line_items": [{"item": {"id": "X"}, "quantity": 1}]}); assert c >= 400; return f"HTTP {c}"
        tests.append((M, "test_invalid_merchant", t_merch))

        M = "H. card_credential_test"
        def t_visa():
            sid, _ = self.ready_session(V, email="v@t.com"); h = sd.get("paymentHandler", "gpay")
            c, d = self.call("POST", f"{self.ep(V)}/checkout-sessions/{sid}/complete", {"payment": {"instruments": [{"id": "g", "handler_id": h, "type": "google_pay", "selected": True, "credential": {"type": "PAYMENT_GATEWAY", "token": "tok_visa"}, "billing_address": {"street_address": "123 Main", "address_locality": "New York", "address_region": "NY", "postal_code": "10001", "address_country": "US"}}]}})
            assert c == 200 and d["status"] == "completed"; return f"Order #{d['order']['id']} (Visa)"
        tests.append((M, "test_gpay_visa", t_visa))
        def t_mc():
            sid, _ = self.ready_session(V, email="m@t.com"); h = sd.get("paymentHandler", "gpay")
            c, d = self.call("POST", f"{self.ep(V)}/checkout-sessions/{sid}/complete", {"payment": {"instruments": [{"id": "g", "handler_id": h, "type": "google_pay", "selected": True, "credential": {"type": "PAYMENT_GATEWAY", "token": "tok_mastercard"}, "billing_address": {"street_address": "456 Oak", "address_locality": "Los Angeles", "address_region": "CA", "postal_code": "90001", "address_country": "US"}}]}})
            assert c == 200 and d["status"] == "completed"; return f"Order #{d['order']['id']} (MC)"
        tests.append((M, "test_gpay_mastercard", t_mc))

        M = "I. order_test"
        def t_ord():
            sid, _ = self.ready_session(V, email="o@t.com"); c, d = self.complete_session(V, sid)
            assert c == 200 and d.get("order", {}).get("id") and d["order"].get("permalink_url"); return f"#{d['order']['id']} {d['order']['permalink_url']}"
        tests.append((M, "test_order_response", t_ord))

        M = "J. catalog_test"
        def t_cs():
            c, d = self.call("POST", f"{self.ep(V)}/catalog/search", {"query": "jacket"}); assert c == 200 and d.get("products"); return f"{len(d['products'])} products"
        tests.append((M, "test_catalog_search", t_cs))
        def t_ce():
            c, d = self.call("POST", f"{self.ep(V)}/catalog/search", {"query": "xyzzzz99"}); assert c == 200 and not d.get("products"); return "0 products"
        tests.append((M, "test_catalog_empty", t_ce))
        def t_cp():
            c, d = self.call("POST", f"{self.ep(V)}/catalog/search", {"query": "tee"}); assert d.get("pagination"); return f"{len(d.get('products',[]))} products, next={d['pagination']['has_next_page']}"
        tests.append((M, "test_catalog_pagination", t_cp))
        def t_cc():
            d = self.create_session(V, sd["productId"]); assert d["line_items"][0]["item"]["price"] > 0; return f"{sd['productId']} = ${d['line_items'][0]['item']['price']/100:.2f}"
        tests.append((M, "test_catalog_to_checkout", t_cc))

        return tests

    # ══════════════════════════════════════════════════
    # MAIN RUN
    # ══════════════════════════════════════════════════
    def run(self):
        if not self.args.json:
            print(f"\n\033[1mspck v{__version__} — UCP Conformance Testing CLI\033[0m")
            print(f"Server: {self.base}")
            print(f"Merchant: {self.domain}")

        # Discovery
        ucp = self.discover()
        V = self.args.version if self.args.version != "auto" else ucp["version"]

        # Run tests
        tests = self.get_tests(V)
        current_mod = ""
        for mod, name, fn in tests:
            if mod != current_mod and not self.args.json:
                current_mod = mod
                print(f"\n\033[1m--- {mod} ---\033[0m")
            self.test(name, mod, fn)

        total = self.pass_count + self.fail_count + self.skip_count

        # Results
        if self.args.json:
            output = {
                "version": __version__,
                "date": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "server": self.base, "merchant": self.domain,
                "spec_version": V,
                "summary": {"total": total, "pass": self.pass_count, "fail": self.fail_count, "skip": self.skip_count, "api_calls": len(self.api_log)},
                "deviations": self.deviations,
                "tests": [{"name": r["name"], "module": r["module"], "status": r["status"], "detail": r["detail"], "ms": r["ms"]} for r in self.results],
                "api_log": self.api_log,
            }
            print(json.dumps(output, indent=2))
        else:
            print(f"\n{'='*60}")
            color = "\033[32m" if self.fail_count == 0 else "\033[31m"
            print(f"\033[1m{color}Results: {self.pass_count} passed, {self.fail_count} failed, {self.skip_count} skipped out of {total}\033[0m")
            print(f"{'='*60}")
            print(f"API calls: {len(self.api_log)}")
            if self.deviations:
                print(f"\nDeviations from spec:")
                for d in self.deviations:
                    print(f"  - {d['area']}: {d['spec']} → {d['server']} ({d['severity']})")

        # Upload to spck.dev if API key provided
        if self.args.key:
            if not self.args.json:
                print(f"\nUploading report to spck.dev...")
            report = {
                "config": {"base": self.base, "domain": self.domain, "version": V, "source": "cli"},
                "summary": {"total": total, "pass": self.pass_count, "fail": self.fail_count, "skip": self.skip_count, "api_calls": len(self.api_log)},
                "deviations": self.deviations,
                "tests": [{"name": r["name"], "module": r["module"], "status": r["status"], "detail": r["detail"], "ms": r["ms"]} for r in self.results],
                "api_log": self.api_log,
            }
            status, resp, _ = http("POST", f"{SPCK_API}/reports", report, {"Authorization": f"Bearer {self.args.key}", "Content-Type": "application/json"})
            if status == 200 and resp.get("ok"):
                if not self.args.json:
                    print(f"  Report saved: https://spck.dev/tool (My Reports tab)")
            else:
                if not self.args.json:
                    print(f"  Upload failed: {resp.get('error', f'HTTP {status}')}")

        return 0 if self.fail_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        prog="spck",
        description="UCP Conformance Testing CLI — https://spck.dev",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  spck --server https://api.example.com --merchant store.example.com
  spck --key spck_abc123 --server https://api.example.com --merchant store.example.com
  spck --server https://api.example.com --merchant store.example.com --json
  spck --server https://api.example.com --merchant store.example.com --verbose
"""
    )
    parser.add_argument("--server", required=True, help="UCP server base URL")
    parser.add_argument("--merchant", required=True, help="Merchant domain")
    parser.add_argument("--key", help="API key from spck.dev (syncs reports to your account)")
    parser.add_argument("--version", default="auto", help="Spec version: auto, 2026-04-08, 2026-01-23, 2026-01-11 (default: auto)")
    parser.add_argument("--host-header", default="x-firmly-host", help="Header for merchant routing (default: x-firmly-host)")
    parser.add_argument("--headers", help="Extra headers as key=value,key=value")
    parser.add_argument("--token", default="tok_visa", help="Payment test token (default: tok_visa)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--verbose", action="store_true", help="Show request/response details")
    parser.add_argument("--save-key", action="store_true", help="Save API key to ~/.spck")
    parser.add_argument("--version-info", action="version", version=f"spck {__version__}")

    args = parser.parse_args()

    # Load saved key
    if not args.key and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
                args.key = cfg.get("key")
        except:
            pass

    # Save key
    if args.save_key and args.key:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"key": args.key}, f)
        os.chmod(CONFIG_FILE, 0o600)
        if not args.json:
            print(f"API key saved to {CONFIG_FILE}")

    runner = TestRunner(args)
    sys.exit(runner.run())


if __name__ == "__main__":
    main()

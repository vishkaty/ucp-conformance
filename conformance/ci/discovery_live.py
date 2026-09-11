#!/usr/bin/env python3
"""
discovery_live.py — the DISCOVERY-LIVE sampler + offline grader (D4-08 / E2, decision 4).

What it does, and only this: GET https://<store>/.well-known/ucp for a small daily sample of
real stores, under conformance/ci/probe_policy.json (named UA, per-UA robots, <=1 req/s,
<=1 fetch/store/day, <=50/day, 10 s timeout, no redirects, GET only, no other path, no
signing/replay/idempotency headers ever — the DENYLIST is code, tested by --selftest), on the
OWNER'S MACHINE ONLY (refuses under GITHUB_ACTIONS; ops/tools/run_sampler.sh). Captures
(`discovery-capture/1`) are private (ops/feeds/discovery_captures/<date>/<domain>.json,
pruned after 90 days); only aggregates (conformance/coverage/discovery_reach.json) are public.

    --sample --frame hf|FILE --n 50 --out DIR [--seed]     the daily sample (local only)
    --grade DIR [--record]                                  OFFLINE: no network, no socket (RV2 T4)
    --prune DIR                                             delete captures older than retention
    --selftest                                              hermetic kill-tests (a)-(h)

Rows a capture may EARN (role-scoped, RV1 #11 — the business side of discovery only):
  OVR-001 reverse-domain names · OVR-010 dated version · DISC-001 https · DISC-002 (business
  half: no 3xx) · DISC-003 Cache-Control · DISC-005 https service endpoints · SIG-007 JWK shape ·
  SIG-008 top-level keys[] · CAP-001 schema declared · CAP-003 spec https.
NEVER: CAP-004/CAP-006 (platform-side URL parsing / authority), DISC-004/007/008 (verifier
side). Evidence class `discovery-live` (coverage/evidence.py): a check whose predicate reaches
load_capture AND whose discovery_reach entry shows >=3 distinct domains within 30 days; never
promoted to live-wire; differential_targets.json stays at 2.
"""
import argparse, datetime, hashlib, http.client, json, os, pathlib, random, re, socket, sys, time, urllib.error
import urllib.parse, urllib.request, urllib.robotparser

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
POLICY_FILE = HERE / "probe_policy.json"
POLICY = json.loads(POLICY_FILE.read_text())
REACH_FILE = ROOT / "conformance" / "coverage" / "discovery_reach.json"
SCHEMA = "discovery-capture/1"
EARNABLE = ("OVR-001", "OVR-010", "DISC-001", "DISC-002", "DISC-003", "DISC-005", "SIG-007", "SIG-008",
            "CAP-001", "CAP-003")
NEVER_EARNABLE = ("CAP-004", "CAP-006", "DISC-004", "DISC-007", "DISC-008")
REACH_MIN_DOMAINS = 3
REACH_MAX_AGE_DAYS = 30
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9_]+){2,}$")   # {reverse-domain}.{service}.{capability}


class DenylistViolation(RuntimeError):
    """A request the policy forbids: raised BEFORE any socket is opened."""


class ActionsRefused(RuntimeError):
    """The sampler never runs in GitHub Actions (decision 4)."""


# ---------------------------------------------------------------------------
# policy enforcement
# ---------------------------------------------------------------------------
def check_request(method, url, headers, sample_hosts):
    u = urllib.parse.urlsplit(url)
    if method.upper() not in POLICY["methods_allowed"]:
        raise DenylistViolation(f"method {method} (only {POLICY['methods_allowed']})")
    if u.scheme != "https":
        raise DenylistViolation(f"scheme {u.scheme!r} (https only)")
    if u.path not in POLICY["paths_allowed"] or u.query or u.fragment:
        raise DenylistViolation(f"path {u.path!r} (only {POLICY['paths_allowed']}, no query)")
    if u.username or u.password:
        raise DenylistViolation("userinfo in URL")
    denied = {h.lower() for h in POLICY["headers_denied"]}
    for h in headers or {}:
        if h.lower() in denied:
            raise DenylistViolation(f"header {h} is denied")
    host = (u.hostname or "").lower()
    if host not in {h.lower() for h in sample_hosts}:
        raise DenylistViolation(f"host {host!r} is not in today's sample")
    return True


class RateLimiter:
    def __init__(self, per_second=None, clock=time.monotonic, sleep=time.sleep):
        self.min_gap = 1.0 / float(per_second or POLICY["rate"]["per_second"])
        self.clock, self.sleep, self.last = clock, sleep, None

    def wait(self):
        now = self.clock()
        if self.last is not None and now - self.last < self.min_gap:
            self.sleep(self.min_gap - (now - self.last))
        self.last = self.clock()


def _no_redirect_opener(timeout):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    return urllib.request.build_opener(NoRedirect())


def http_get(url, ua, timeout):
    """(status, headers dict, body bytes, latency_ms) — GET without following redirects."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": ua, "Accept": "application/json"})
    t0 = time.monotonic()
    try:
        with _no_redirect_opener(timeout).open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(), int((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(), int((time.monotonic() - t0) * 1000)


def robots_verdict(host, ua, fetch=None, timeout=None):
    """'allow' | 'blocked' | 'unavailable' for GET /.well-known/ucp under our UA (then *)."""
    timeout = timeout or POLICY["timeout_s"]
    fetch = fetch or (lambda h: http_get(f"https://{h}/robots.txt", ua, timeout)[2].decode("utf-8", "replace"))
    try:
        text = fetch(host)
    except Exception:  # noqa: BLE001
        return "unavailable"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    token = ua.split("/")[0]
    return "allow" if rp.can_fetch(token, f"https://{host}/.well-known/ucp") else "blocked"


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------
def capture(domain, ua=None, timeout=None, getter=None, frame=None, robots=None):
    ua = ua or POLICY["user_agent"]; timeout = timeout or POLICY["timeout_s"]
    url = f"https://{domain}/.well-known/ucp"
    getter = getter or http_get
    fetched = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        status, headers, body, latency = getter(url, ua, timeout)
        err = None
    except Exception as e:  # noqa: BLE001
        status, headers, body, latency, err = 0, {}, b"", 0, f"{type(e).__name__}: {str(e)[:120]}"
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    return {"schema": SCHEMA, "domain": domain, "url": url, "fetched_at": fetched, "ua": ua,
            "robots_checked": robots is not None, "robots_verdict": robots,
            "http": {"status": status, "headers": headers, "redirects": 0,
                     "tls": {"scheme": "https"}, "latency_ms": latency, "error": err},
            "body_sha256": hashlib.sha256(body).hexdigest() if body else None,
            "body": parsed, "body_text": (None if parsed is not None else body[:2000].decode("utf-8", "replace") or None),
            "frame": frame or {"source": POLICY["frame"]["source"], "bucket": None}, "grade": None}


def load_capture(path):
    d = json.loads(pathlib.Path(path).read_text())
    if d.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} capture")
    return d


def frame_domains(source):
    """Domains of the sampling frame: a local file (one domain per line / CSV with a domain
    column) or 'hf' = the pinned dataset probe_policy.json names (fetched read-only)."""
    if source == "hf":
        import csv, io, subprocess
        f = POLICY["frame"]
        sha = f["source"].split("@")[1]
        url = f"https://huggingface.co/datasets/UCPChecker/ucp-merchants/resolve/{sha}/{f['file']}"
        p = subprocess.run(["curl", "-sSL", "--fail", "--max-time", "60", url], capture_output=True, text=True, timeout=70)
        if p.returncode != 0:
            raise RuntimeError(f"frame fetch failed: {p.stderr[:120]}")
        rows = list(csv.DictReader(io.StringIO(p.stdout)))
        col = next((c for c in rows[0] if c.lower() in ("domain", "host", "hostname", "shop", "url", "merchant")), None) if rows else None
        out = []
        for r in rows:
            v = (r.get(col) or "").strip()
            if v:
                v = urllib.parse.urlsplit(v if "://" in v else f"https://{v}").hostname or v
                out.append(v.lower())
        return sorted(set(out))
    text = pathlib.Path(source).read_text()
    doms = []
    for line in text.splitlines():
        line = line.strip().split(",")[0].strip()
        if line and not line.startswith("#"):
            doms.append((urllib.parse.urlsplit(line if "://" in line else f"https://{line}").hostname or line).lower())
    return sorted(set(doms))


def choose(domains, n, date):
    """Deterministic daily sample (seed = the date), <= per_day."""
    n = min(n, POLICY["rate"]["per_day"])
    rng = random.Random(f"spck-discovery-{date}")
    pool = list(domains)
    rng.shuffle(pool)
    return pool[:n]


def sample(domains, n, out_dir, date=None, env=None, getter=None, robots_fetch=None, limiter=None, ua=None):
    """Fetch <=n stores (<=per_day, <=1/store/day) into out_dir; refuses under GITHUB_ACTIONS."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") or POLICY.get("actions_forbidden") and env.get("CI") == "true" and env.get("GITHUB_RUN_ID"):
        raise ActionsRefused("the discovery-live sampler runs only on the owner's machine (decision 4) — refused under GITHUB_ACTIONS")
    date = date or datetime.date.today().isoformat()
    ua = ua or POLICY["user_agent"]
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    limiter = limiter or RateLimiter()
    chosen = choose(domains, n, date)
    hosts = set(chosen)
    written, skipped = [], []
    for dom in chosen:
        path = out_dir / f"{dom}.json"
        if path.exists():
            skipped.append((dom, "already fetched today")); continue   # <=1 fetch/store/day
        check_request("GET", f"https://{dom}/.well-known/ucp", {"User-Agent": ua, "Accept": "application/json"}, hosts)
        limiter.wait()
        rv = robots_verdict(dom, ua, fetch=robots_fetch) if POLICY["robots"] else "not-checked"
        if rv == "blocked":
            cap = {"schema": SCHEMA, "domain": dom, "url": f"https://{dom}/.well-known/ucp", "fetched_at": None, "ua": ua,
                   "robots_checked": True, "robots_verdict": "blocked", "http": None, "body_sha256": None, "body": None,
                   "frame": {"source": POLICY["frame"]["source"], "bucket": None}, "grade": None}
            path.write_text(json.dumps(cap, indent=1) + "\n"); skipped.append((dom, "robots_blocked")); continue
        limiter.wait()
        cap = capture(dom, ua=ua, getter=getter, robots=rv)
        path.write_text(json.dumps(cap, indent=1) + "\n")
        written.append(dom)
    return written, skipped


def prune(root, retention_days=None, today=None):
    retention_days = retention_days or POLICY["retention_days"]
    today = today or datetime.date.today()
    removed = []
    for d in sorted(pathlib.Path(root).glob("????-??-??")):
        try:
            when = datetime.date.fromisoformat(d.name)
        except ValueError:
            continue
        if (today - when).days > retention_days:
            for f in d.glob("*.json"):
                f.unlink()
            d.rmdir(); removed.append(d.name)
    return removed


# ---------------------------------------------------------------------------
# offline grader (no network, no socket)
# ---------------------------------------------------------------------------
def _entries(container):
    """[(name, entry)] over a UCP record `{name: entry | [entry, ...]}` (08-25: one list of
    versioned entries per capability/service name) or a legacy list of `{name, ...}` objects."""
    if isinstance(container, dict):
        out = []
        for k, v in container.items():
            if isinstance(v, list):
                out += [(k, e) for e in v if isinstance(e, dict)]
            else:
                out.append((k, v if isinstance(v, dict) else {}))
        return out
    if isinstance(container, list):
        return [((c or {}).get("name") or "", c or {}) for c in container if isinstance(c, dict)]
    return []


def _caps(body):
    """[(name, entry)] over ucp.capabilities (record of entry lists at 08-25; legacy list)."""
    return _entries(((body or {}).get("ucp") or {}).get("capabilities"))


def _services(body):
    return _entries(((body or {}).get("ucp") or {}).get("services"))


def _keys(body):
    b = body or {}
    if isinstance(b.get("keys"), list):
        return "keys", b["keys"]
    if isinstance(b.get("signing_keys"), list):
        return "signing_keys", b["signing_keys"]
    ucp = b.get("ucp") or {}
    if isinstance(ucp.get("keys"), list):
        return "ucp.keys", ucp["keys"]
    return None, []


def _cc_ok(cc):
    if not cc:
        return False
    parts = [p.strip().lower() for p in cc.split(",")]
    if any(p in ("private", "no-store", "no-cache") for p in parts):
        return False
    if "public" not in parts:
        return False
    m = next((p for p in parts if p.startswith("max-age=")), None)
    try:
        return m is not None and int(m.split("=", 1)[1]) >= 60
    except ValueError:
        return False


def grade_capture(cap):
    """{req_id: clean-pass | deviation | not-applicable} over the EARNABLE rows only."""
    g = {}
    http_ = cap.get("http") or {}
    st = int(http_.get("status") or 0)
    body = cap.get("body")
    if cap.get("robots_verdict") == "blocked" or st == 0:
        return {r: "not-applicable" for r in EARNABLE}
    g["DISC-001"] = "clean-pass" if cap["url"].startswith("https://") else "deviation"
    g["DISC-002"] = "deviation" if 300 <= st < 400 else "clean-pass"
    if not (200 <= st < 300) or not isinstance(body, dict):
        for r in EARNABLE:
            g.setdefault(r, "not-applicable")
        return g
    g["DISC-003"] = "clean-pass" if _cc_ok((http_.get("headers") or {}).get("cache-control")) else "deviation"
    ucp = body.get("ucp") or {}
    ver = ucp.get("version"); sv = ucp.get("supported_versions") or {}
    vals = [ver] + (list(sv.keys()) if isinstance(sv, dict) else [str(x) for x in sv] if isinstance(sv, list) else [])
    vals = [v for v in vals if v is not None]
    g["OVR-010"] = ("clean-pass" if vals and all(DATE_RE.match(str(v)) for v in vals) else
                    "deviation" if vals else "not-applicable")
    names = [n for n, _e in _caps(body)] + [n for n, _e in _services(body)]
    g["OVR-001"] = ("clean-pass" if names and all(NAME_RE.match(n) for n in names) else
                    "deviation" if names else "not-applicable")
    eps = [e.get("endpoint") for _n, e in _services(body) if e.get("endpoint")]
    g["DISC-005"] = ("clean-pass" if eps and all(str(e).startswith("https://") for e in eps) else
                     "deviation" if eps else "not-applicable")
    caps = _caps(body)
    g["CAP-001"] = ("clean-pass" if caps and all(e.get("schema") for _n, e in caps) else
                    "deviation" if caps else "not-applicable")
    specs = [e.get("spec") for _n, e in caps if e.get("spec")]
    g["CAP-003"] = ("clean-pass" if specs and all(str(s).startswith("https://") for s in specs) else
                    "deviation" if specs else "not-applicable")
    where, keys = _keys(body)
    if keys:
        def jwk_ok(k):
            if not isinstance(k, dict) or not k.get("kty"):
                return False
            kty = k["kty"]
            return {"EC": all(k.get(x) for x in ("crv", "x", "y")), "OKP": all(k.get(x) for x in ("crv", "x")),
                    "RSA": all(k.get(x) for x in ("n", "e"))}.get(kty, True)
        g["SIG-007"] = "clean-pass" if all(jwk_ok(k) for k in keys) else "deviation"
        g["SIG-008"] = "clean-pass" if where == "keys" else "deviation"
    else:
        g["SIG-007"] = g["SIG-008"] = "not-applicable"
    assert not any(r in g for r in NEVER_EARNABLE)
    return g


def schema_legs(cap):
    """Both schema oracles on the profile (offline; the per-version Rust build + referee)."""
    out = {}
    body = cap.get("body")
    if not isinstance(body, dict):
        return out
    try:
        import schema_oracle as so
        ok, _ = so.validate_profile(body, version="2026-08-25", role="business")
        out["oracle"] = "valid" if ok else ("crash" if so.LAST_RC not in (0, 1) else "invalid")
    except Exception as e:  # noqa: BLE001
        out["oracle"] = f"unavailable: {type(e).__name__}"
    try:
        import dual_oracle_referee as ref
        ok, faults = ref.get_referee("2026-08-25").validate(body, "schemas/profile.json", def_name="business_schema", op="read", direction="response")
        out["referee"] = "valid" if ok else "invalid"; out["referee_faults"] = [p for p, _k in faults][:5]
    except Exception as e:  # noqa: BLE001
        out["referee"] = f"unavailable: {type(e).__name__}"
    return out


def grade_dir(dir_, write=True):
    """Grade every capture in dir_ (writes `grade` + `schema` back when write=True)."""
    dir_ = pathlib.Path(dir_)
    caps = []
    for f in sorted(dir_.glob("*.json")):
        cap = load_capture(f)
        cap["grade"] = grade_capture(cap)
        cap["schema_legs"] = schema_legs(cap)
        if write:
            f.write_text(json.dumps(cap, indent=1) + "\n")
        caps.append(cap)
    return caps


def reach_from(captures_root, today=None):
    """Aggregate over every capture dir <= 30 days old: per row, the distinct domains graded
    clean-pass or deviation, and the latest date. Public output (no domains listed)."""
    today = today or datetime.date.today()
    rows = {r: {"domains": set(), "clean_pass": 0, "deviation": 0, "latest": None} for r in EARNABLE}
    stores, days = set(), []
    for d in sorted(pathlib.Path(captures_root).glob("????-??-??")):
        try:
            when = datetime.date.fromisoformat(d.name)
        except ValueError:
            continue
        if (today - when).days > REACH_MAX_AGE_DAYS:
            continue
        days.append(d.name)
        for f in d.glob("*.json"):
            cap = load_capture(f)
            g = cap.get("grade") or {}
            if not g:
                continue
            graded_here = False
            for r, v in g.items():
                if r in rows and v in ("clean-pass", "deviation"):
                    graded_here = True
                    rows[r]["domains"].add(cap["domain"]); rows[r][v.replace("-", "_")] += 1
                    rows[r]["latest"] = max(rows[r]["latest"] or d.name, d.name)
            # W1 integration: a store counts only when its DOCUMENT was graded on some row — an
            # unfetched capture (status 0, every row not-applicable) is not "a store's document
            # graded by this suite" (the public CLAIM-COV-007 sentence; D5-13's reader agrees).
            if graded_here:
                stores.add(cap["domain"])
    return {"as_of": today.isoformat(), "stores": len(stores), "days": days, "window_days": REACH_MAX_AGE_DAYS,
            "rows": {r: {"domains": len(v["domains"]), "clean_pass": v["clean_pass"], "deviation": v["deviation"], "latest": v["latest"]}
                     for r, v in rows.items()}}


def row_is_discovery_live(reach_row, today=None):
    """The class rule: >=3 distinct domains graded within 30 days."""
    if not reach_row or not reach_row.get("latest"):
        return False
    today = today or datetime.date.today()
    age = (today - datetime.date.fromisoformat(reach_row["latest"])).days
    return int(reach_row.get("domains", 0)) >= REACH_MIN_DOMAINS and age <= REACH_MAX_AGE_DAYS


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
def _synthetic_capture(domain, cc="public, max-age=300", status=200, keys=None, where="keys"):
    prof = json.loads((ROOT / "conformance" / "selfcheck" / "fixtures" / "2026-08-25" / "discovery_profile.json").read_text())
    body = {"ucp": prof["ucp"]} if "ucp" in prof else prof
    if keys is not None:
        body[where] = keys
    cap = capture(domain, getter=lambda url, ua, t: (status, {"cache-control": cc, "content-type": "application/json"},
                                                    json.dumps(body).encode(), 12), robots="allow")
    return cap


def selftest():
    import tempfile, shutil
    ok = True

    def case(tag, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'✓' if cond else '✗'} {tag}" + (f" — {detail}" if detail else ""))

    hosts = {"a.example", "b.example", "c.example"}
    # (a) denylist
    def raises(fn):
        try:
            fn(); return False
        except DenylistViolation:
            return True
    case("case a: POST -> DenylistViolation", raises(lambda: check_request("POST", "https://a.example/.well-known/ucp", {}, hosts)))
    case("case a: non-well-known path -> DenylistViolation", raises(lambda: check_request("GET", "https://a.example/catalog", {}, hosts)))
    case("case a: Signature header -> DenylistViolation", raises(lambda: check_request("GET", "https://a.example/.well-known/ucp", {"Signature": "x"}, hosts)))
    case("case a: host outside the sample -> DenylistViolation", raises(lambda: check_request("GET", "https://zzz.example/.well-known/ucp", {}, hosts)))
    case("case a: plain http -> DenylistViolation", raises(lambda: check_request("GET", "http://a.example/.well-known/ucp", {}, hosts)))
    case("case a: the allowed request passes", check_request("GET", "https://a.example/.well-known/ucp", {"User-Agent": POLICY["user_agent"]}, hosts))
    # (b) robots
    case("case b: robots Disallow on /.well-known/ucp -> blocked",
         robots_verdict("a.example", POLICY["user_agent"], fetch=lambda h: "User-agent: *\nDisallow: /.well-known/ucp\n") == "blocked")
    case("case b: robots allow -> allow", robots_verdict("a.example", POLICY["user_agent"], fetch=lambda h: "User-agent: *\nDisallow: /api/\n") == "allow")
    case("case b: our UA named -> blocked even when * is open",
         robots_verdict("a.example", POLICY["user_agent"], fetch=lambda h: "User-agent: spck-conformance-research\nDisallow: /\n\nUser-agent: *\nAllow: /\n") == "blocked")
    # (c) rate: 3 fetches >= 2 s (a fake clock proves the limiter sleeps; then one real pass)
    slept = []
    t = [0.0]
    rl = RateLimiter(per_second=1, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    for _ in range(3):
        rl.wait()
    case("case c: rate limiter — 3 fetches take >= 2 s", round(sum(slept), 3) >= 2.0, f"slept {sum(slept):.2f}s")
    # (d) grader
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dl_selftest_")); day = tmp / "2026-09-11"; day.mkdir()
    caps = [_synthetic_capture("a.example"), _synthetic_capture("b.example", keys=[{"kty": "EC", "kid": "k", "crv": "P-256", "x": "x", "y": "y"}]),
            _synthetic_capture("c.example", cc="no-store"), _synthetic_capture("d.example", status=301)]
    for c in caps:
        (day / f"{c['domain']}.json").write_text(json.dumps(c) + "\n")
    g = {c["domain"]: grade_capture(c) for c in caps}
    case("case d: DISC-001/003 clean, OVR-010 clean, SIG-007/008 n/a on a keyless capture",
         g["a.example"]["DISC-001"] == "clean-pass" and g["a.example"]["DISC-003"] == "clean-pass" and g["a.example"]["OVR-010"] == "clean-pass"
         and g["a.example"]["SIG-007"] == "not-applicable" and g["a.example"]["SIG-008"] == "not-applicable", str(g["a.example"]))
    case("case d: keys[] present -> SIG-007/SIG-008 clean", g["b.example"]["SIG-007"] == "clean-pass" and g["b.example"]["SIG-008"] == "clean-pass")
    case("case d: planted Cache-Control: no-store -> DISC-003 deviation", g["c.example"]["DISC-003"] == "deviation")
    case("case d: a 301 -> DISC-002 (business half) deviation", g["d.example"]["DISC-002"] == "deviation")
    case("case d: a capture never earns CAP-004 / DISC-007 (verifier/platform side)",
         all(r not in gg for gg in g.values() for r in NEVER_EARNABLE) and not set(EARNABLE) & set(NEVER_EARNABLE))
    # (e) evidence rule
    case("case e: 3 domains within 30 days -> discovery-live", row_is_discovery_live({"domains": 3, "latest": "2026-09-01"}, today=datetime.date(2026, 9, 11)))
    case("case e: 2 domains -> not discovery-live", not row_is_discovery_live({"domains": 2, "latest": "2026-09-01"}, today=datetime.date(2026, 9, 11)))
    case("case e: 31 days old -> not counted", not row_is_discovery_live({"domains": 5, "latest": "2026-08-11"}, today=datetime.date(2026, 9, 11)))
    sys.path.insert(0, str(ROOT / "conformance" / "coverage"))
    import evidence
    class _Chk:  # a check whose predicate reaches load_capture
        id = "disc.synthetic"; req_ids = ["DISC-003"]
        def predicate(self, r, _lc=load_capture):
            return _lc
    reach3 = {"disc.synthetic": {"domains": 3, "latest": datetime.date.today().isoformat()}}
    reach2 = {"disc.synthetic": {"domains": 2, "latest": datetime.date.today().isoformat()}}
    c3 = evidence.classify_check(_Chk(), "discovery_check_08_25", "2026-08-25", {}, discovery_reach=reach3)[0]
    c2 = evidence.classify_check(_Chk(), "discovery_check_08_25", "2026-08-25", {}, discovery_reach=reach2)[0]
    lw = evidence.classify_check(_Chk(), "discovery_check_08_25", "2026-08-25",
                                 {"2026-08-25:discovery_check_08_25:disc.synthetic": ["flower-shop-official-sample"]}, discovery_reach=reach3)[0]
    case("case e: evidence.classify_check: capture-reaching check + 3 domains -> discovery-live", c3 == "discovery-live", c3)
    case("case e: 2 domains -> self-referenced", c2 == "self-referenced", c2)
    case("case e: never promoted to live-wire even with a reach-report entry", lw == "discovery-live", lw)
    # (f) --grade opens no socket
    real_socket = socket.socket
    def boom(*a, **k):
        raise AssertionError("socket opened during --grade")
    socket.socket = boom
    try:
        graded = grade_dir(day, write=True)
        r = reach_from(tmp, today=datetime.date(2026, 9, 11))
        case("case f: --grade over 4 captures opened no socket (socket.socket raises)", len(graded) == 4 and r["stores"] == 4)
    except AssertionError as e:
        case("case f: --grade opened a socket", False, str(e))
    finally:
        socket.socket = real_socket
    # (g) Actions refusal
    try:
        sample(["a.example"], 1, tmp / "x", env={"GITHUB_ACTIONS": "true"}, getter=lambda u, ua, t: (200, {}, b"{}", 1))
        case("GITHUB_ACTIONS refusal", False)
    except ActionsRefused as e:
        case("GITHUB_ACTIONS refusal: sample() refuses under GITHUB_ACTIONS", True, str(e)[:60])
    # (h) sample: <=1/store/day + per_day cap + denylist wired, with stubbed network
    calls = []
    w1, s1 = sample(["a.example", "b.example"], 5, day, date="2026-09-11", env={}, getter=lambda u, ua, t: (calls.append(u), (200, {"cache-control": "public, max-age=100"}, b'{"ucp": {"version": "2026-08-25"}}', 1))[1],
                    robots_fetch=lambda h: "User-agent: *\nAllow: /\n", limiter=RateLimiter(per_second=1000))
    case("case h: already-fetched stores are skipped (<=1 fetch/store/day)", w1 == [] and len(s1) == 2 and calls == [], str((w1, s1)))
    w2, s2 = sample(["e.example", "f.example"], 5, day, date="2026-09-11", env={}, getter=lambda u, ua, t: (calls.append(u), (200, {"cache-control": "public, max-age=100"}, b'{"ucp": {"version": "2026-08-25"}}', 1))[1],
                    robots_fetch=lambda h: "User-agent: *\nAllow: /\n", limiter=RateLimiter(per_second=1000))
    case("case h: new stores fetched once each, GET /.well-known/ucp only", sorted(w2) == ["e.example", "f.example"] and all(u.endswith("/.well-known/ucp") for u in calls) and len(calls) == 2)
    case("case h: per-day cap from the policy", len(choose([f"{i}.example" for i in range(200)], 500, "2026-09-11")) == POLICY["rate"]["per_day"])
    targets = json.loads((HERE / "differential_targets.json").read_text())
    n_targets = len(targets.get("targets", targets)) if isinstance(targets, dict) else len(targets)
    case("W1-6: differential_targets.json stays at 2 (discovery-live never becomes a differential target)", n_targets == 2, str(n_targets))
    shutil.rmtree(tmp, ignore_errors=True)
    print("discovery-live selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="discovery-live sampler + offline grader (D4-08)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--frame", default="hf", help="'hf' (the pinned dataset) or a file of domains")
    ap.add_argument("--n", type=int, default=POLICY["rate"]["per_day"])
    ap.add_argument("--out", default=None, help="capture dir for --sample (default ops/feeds/discovery_captures/<today>)")
    ap.add_argument("--grade", metavar="DIR")
    ap.add_argument("--record", action="store_true", help="with --grade: write conformance/coverage/discovery_reach.json")
    ap.add_argument("--prune", metavar="ROOT")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.sample:
        today = datetime.date.today().isoformat()
        out = pathlib.Path(a.out or (ROOT / "ops" / "feeds" / "discovery_captures" / today))
        try:
            doms = frame_domains(a.frame)
            w, s = sample(doms, a.n, out, date=today)
        except ActionsRefused as e:
            print(f"discovery-live: REFUSED — {e}"); return 3
        except DenylistViolation as e:
            print(f"discovery-live: DENYLIST — {e}"); return 3
        print(f"{len(w)} fetched · {len(s)} skipped ({', '.join(f'{d}:{why}' for d, why in s) or '-'}) -> {out} · frame {len(doms)} domains")
        return 0
    if a.grade:
        caps = grade_dir(a.grade, write=True)
        root = pathlib.Path(a.grade).parent
        reach = reach_from(root)
        if a.record:
            REACH_FILE.write_text(json.dumps(reach, indent=1) + "\n")
        n = len(caps)
        earned = [r for r in EARNABLE if any((c.get("grade") or {}).get(r) in ("clean-pass", "deviation") for c in caps)]
        robots = sum(1 for c in caps if c.get("robots_checked"))
        live = [r for r in EARNABLE if row_is_discovery_live(reach["rows"].get(r))]
        print(f"{n}/{n} captures graded · rows {', '.join(earned) or '-'} evidence={'discovery-live' if live else 'self-referenced'} "
              f"(domains {reach['stores']}{' >=3' if reach['stores'] >= 3 else ' <3'}; discovery-live rows: {', '.join(live) or 'none'}) · "
              f"0 denylist events · robots checked {robots}/{n}" + (f" · recorded -> {REACH_FILE.relative_to(ROOT)}" if a.record else ""))
        return 0
    if a.prune:
        removed = prune(a.prune)
        print(f"pruned {len(removed)} capture day(s) older than {POLICY['retention_days']} d: {', '.join(removed) or '-'}")
        return 0
    ap.error("--selftest | --sample | --grade DIR | --prune ROOT")


if __name__ == "__main__":
    sys.exit(main())

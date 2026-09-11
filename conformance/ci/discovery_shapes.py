#!/usr/bin/env python3
"""
discovery_shapes.py — the PURE discovery-live aggregation (PLAN-v3 §2.11, D5-13 / H5) shared by
the State-of-UCP scanner (ops/tools/state_of_ucp_scan.py, owner-run) and the D4-08 sampler:

  · shape_bucket(profile)        rest_keys | rest_nokeys | mcp_only | embedded_only | other
                                 (from services.dev.ucp.shopping transports + published signing keys;
                                 01-11 service objects and 04-08/08-25 arrays both understood)
  · aggregate_shapes(profiles)   {version_2026_08_25, shape_rest_keys, shape_rest_nokeys, shape_mcp_only,
                                 shape_embedded_only, shape_other} — the scan-summary.json keys
  · discovery_reach(captures, today)
                                 over `discovery-capture/1` files: {stores, as_of, stale_excluded,
                                 malformed, class_threshold_met}; a capture older than 30 days NEVER
                                 counts (decision 4 retention; the `discovery-live` class needs >= 3
                                 distinct domains within 30 days)

    discovery_shapes.py --check <captures-dir>   # fail-noisy: rc 1 on any stale or malformed capture
    discovery_shapes.py --selftest               # bucketing fixtures + the planted 31-day-old capture
Aggregates only — no domain is ever printed. Stdlib only; no network.
"""
import datetime
import json
import pathlib
import sys

WINDOW_DAYS = 30
CLASS_MIN_DOMAINS = 3
SCHEMA = "discovery-capture/1"
BUCKETS = ("rest_keys", "rest_nokeys", "mcp_only", "embedded_only", "other")


def _root(profile):
    if not isinstance(profile, dict):
        return {}
    ucp = profile.get("ucp", profile)
    return ucp if isinstance(ucp, dict) else {}


def transports_of(profile):
    """The set of shopping transports a profile declares (array form at 04-08/08-25; the
    01-11 service OBJECT lists transports as members: rest / mcp / embedded)."""
    svc = (_root(profile).get("services") or {}).get("dev.ucp.shopping")
    if isinstance(svc, list):
        return {s.get("transport") for s in svc if isinstance(s, dict) and s.get("transport")}
    if isinstance(svc, dict):
        return {k for k in ("rest", "mcp", "embedded", "a2a") if isinstance(svc.get(k), dict)}
    return set()


def has_keys(profile):
    r = _root(profile)
    for k in ("keys", "signing_keys"):
        v = r.get(k) if k in r else (profile.get(k) if isinstance(profile, dict) else None)
        if isinstance(v, list) and v:
            return True
    return False


def shape_bucket(profile):
    t = transports_of(profile)
    if "rest" in t:
        return "rest_keys" if has_keys(profile) else "rest_nokeys"
    if t == {"mcp"}:
        return "mcp_only"
    if t == {"embedded"}:
        return "embedded_only"
    return "other"


def aggregate_shapes(profiles):
    out = {"version_2026_08_25": sum(1 for p in profiles if _root(p).get("version") == "2026-08-25")}
    for b in BUCKETS:
        out[f"shape_{b}"] = 0
    for p in profiles:
        out[f"shape_{shape_bucket(p)}"] += 1
    return out


def _date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def discovery_reach(captures, today):
    """{stores, as_of, stale_excluded, malformed, class_threshold_met} over discovery-capture/1
    documents. Only a well-formed, 200-status capture fetched within WINDOW_DAYS counts."""
    fresh, stale, malformed = {}, [], []
    for i, c in enumerate(captures):
        tag = f"#{i}"
        if not isinstance(c, dict) or c.get("schema") != SCHEMA or not c.get("domain") \
                or not isinstance(c.get("http"), dict) or not isinstance(c.get("body"), (dict, str)):
            malformed.append(f"{tag}: not a {SCHEMA} document"); continue
        d = _date(c.get("fetched_at"))
        if d is None:
            malformed.append(f"{tag}: fetched_at unparsable"); continue
        age = (today - d).days
        if age > WINDOW_DAYS:
            stale.append(f"{tag}: fetched_at {d.isoformat()} is {age} days old (max {WINDOW_DAYS}) — excluded"); continue
        if c["http"].get("status") != 200:
            continue                                   # a non-200 capture is evidence of nothing
        fresh[c["domain"]] = max(fresh.get(c["domain"], d), d)
    stores = len(fresh)
    return {"stores": stores, "as_of": max(fresh.values()).isoformat() if fresh else None,
            "stale_excluded": stale, "malformed": malformed,
            "class_threshold_met": stores >= CLASS_MIN_DOMAINS}


def check_dir(d, today=None):
    today = today or datetime.date.today()
    caps = []
    for f in sorted(pathlib.Path(d).glob("*.json")):
        try:
            caps.append(json.load(open(f, encoding="utf-8")))
        except ValueError:
            caps.append(None)
    r = discovery_reach(caps, today)
    for s in r["stale_excluded"] + r["malformed"]:
        print(f"  x capture {s}")
    print(f"discovery-reach: {len(caps)} captures · {r['stores']} stores · as_of {r['as_of']} · "
          f"{len(r['stale_excluded'])} stale excluded · {len(r['malformed'])} malformed · "
          f"class threshold {'met' if r['class_threshold_met'] else 'NOT met'} (>= {CLASS_MIN_DOMAINS} domains in {WINDOW_DAYS} d)"
          + ("" if not (r["stale_excluded"] or r["malformed"]) else " · FAIL"))
    return 0 if not (r["stale_excluded"] or r["malformed"]) else 1


def _cap(domain, fetched_at, transports=("rest",), keys=True, version="2026-08-25", status=200):
    svc = [{"transport": t, "endpoint": f"https://{domain}"} for t in transports]
    return {"schema": SCHEMA, "domain": domain, "url": f"https://{domain}/.well-known/ucp", "fetched_at": fetched_at,
            "ua": "spck-conformance-research/0.1", "robots_checked": True, "robots_verdict": "allowed",
            "http": {"status": status, "headers": {}, "redirects": 0, "tls": "TLSv1.3", "latency_ms": 10},
            "body_sha256": "0" * 64, "frame": {"source": "hf", "bucket": "rest"}, "grade": None,
            "body": {"ucp": {"version": version, "capabilities": {"dev.ucp.shopping.checkout": [{}]},
                             "services": {"dev.ucp.shopping": svc}}, "keys": [{"kid": "k1"}] if keys else []}}


def selftest():
    bad = 0

    def case(name, got, want):
        nonlocal bad
        ok = got == want
        print(f"  {'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"  <-- want {want!r}"))
        bad += 0 if ok else 1

    caps8 = {f"dev.ucp.shopping.{n}": [{}] for n in ("checkout", "order", "cart", "discount", "fulfillment", "buyer_consent", "catalog.search", "catalog.lookup")}
    p8 = {"ucp": {"version": "2026-08-25", "capabilities": caps8,
                  "services": {"dev.ucp.shopping": [{"transport": "mcp", "endpoint": "https://a.example/mcp"},
                                                    {"transport": "embedded", "endpoint": "https://a.example/embed"}]}}}
    caps14 = {f"dev.ucp.shopping.c{i}": [{}] for i in range(14)}
    p14 = {"ucp": {"version": "2026-08-25", "capabilities": caps14,
                   "services": {"dev.ucp.shopping": [{"transport": "rest", "endpoint": "https://b.example"},
                                                     {"transport": "mcp", "endpoint": "https://b.example/mcp"}]},
                   "keys": [{"kid": "k"}]}}
    p_mcp = {"ucp": {"version": "2026-04-08", "services": {"dev.ucp.shopping": [{"transport": "mcp", "endpoint": "x"}]}}}
    p_emb = {"ucp": {"version": "2026-04-08", "services": {"dev.ucp.shopping": [{"transport": "embedded", "endpoint": "x"}]}}}
    p_0111 = {"ucp": {"version": "2026-01-11", "services": {"dev.ucp.shopping": {"rest": {"endpoint": "x"}}}}, "signing_keys": []}
    # W1 integration (D4-08 writer x D5-13 reader): D4 records a FAILED fetch as a
    # discovery-capture/1 document with body null and http.status 0 (the 2026-09-11 smoke's
    # jlique.com, DNS failure). It is a non-200 capture — excluded from the count, never
    # "malformed" — and check_dir() must find captures under D4's YYYY-MM-DD subdirectories.
    import datetime as _dt, json as _json, tempfile as _tf
    dead = {"schema": "discovery-capture/1", "domain": "dead.example", "fetched_at": "2026-09-11T00:00:00Z",
            "http": {"status": 0, "headers": {}, "redirects": 0, "error": "URLError"}, "body": None, "body_text": None,
            "body_sha256": None, "frame": "hf", "robots_checked": True, "grade": {}}
    live = [{"schema": "discovery-capture/1", "domain": f"s{i}.example", "fetched_at": "2026-09-11T00:00:00Z",
             "http": {"status": 200, "headers": {}, "redirects": 0}, "body": {"ucp": {"version": "2026-08-25"}},
             "body_text": "{}", "body_sha256": "x", "frame": "hf", "robots_checked": True, "grade": {}} for i in range(3)]
    r = discovery_reach(live + [dead], _dt.date(2026, 9, 11))
    case("failed fetch (status 0, body null) is excluded, not malformed", (r["stores"], len(r["malformed"])), (3, 0))
    with _tf.TemporaryDirectory() as td:
        day = pathlib.Path(td) / "2026-09-11"; day.mkdir()
        for c in live + [dead]:
            (day / f"{c['domain']}.json").write_text(_json.dumps(c))
        rc, _line = check_dir(td, today=_dt.date(2026, 9, 11))
        case("check_dir finds captures under a YYYY-MM-DD subdirectory (D4's layout)", rc, 0)
    case("8-cap mcp+embedded (no rest) -> other", shape_bucket(p8), "other")
    case("14-cap rest+mcp with keys -> rest_keys", shape_bucket(p14), "rest_keys")
    case("mcp-only -> mcp_only", shape_bucket(p_mcp), "mcp_only")
    case("embedded-only -> embedded_only", shape_bucket(p_emb), "embedded_only")
    case("01-11 service object rest, no keys -> rest_nokeys", shape_bucket(p_0111), "rest_nokeys")
    case("aggregate_shapes counts + version_2026_08_25",
         aggregate_shapes([p8, p14, p_mcp, p_emb, p_0111]),
         {"version_2026_08_25": 2, "shape_rest_keys": 1, "shape_rest_nokeys": 1, "shape_mcp_only": 1,
          "shape_embedded_only": 1, "shape_other": 1})
    today = datetime.date(2026, 9, 11)
    fresh = [_cap("a.example", "2026-09-01"), _cap("b.example", "2026-09-05"), _cap("c.example", "2026-09-10"),
             _cap("a.example", "2026-09-09")]                          # same domain twice counts once
    r = discovery_reach(fresh, today)
    case("3 distinct domains within 30 d -> stores 3, threshold met, as_of newest",
         (r["stores"], r["as_of"], r["class_threshold_met"], r["stale_excluded"], r["malformed"]),
         (3, "2026-09-10", True, [], []))
    r2 = discovery_reach(fresh[:2] + [_cap("c.example", "2026-08-11")], today)   # 31 days old
    case("a 31-day-old capture never counts (stores 2, threshold NOT met, named stale)",
         (r2["stores"], r2["class_threshold_met"], len(r2["stale_excluded"]), "31 days old" in r2["stale_excluded"][0]),
         (2, False, 1, True))
    r3 = discovery_reach(fresh[:3] + [_cap("d.example", "2026-09-10", status=404), {"schema": "x"}], today)
    case("a non-200 capture counts nothing; a non-capture document is malformed",
         (r3["stores"], len(r3["malformed"])), (3, 1))
    print(f"discovery-shapes selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--selftest" in a:
        sys.exit(selftest())
    if len(a) == 2 and a[0] == "--check":
        sys.exit(check_dir(a[1]))
    print(__doc__); sys.exit(2)

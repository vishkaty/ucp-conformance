#!/usr/bin/env python3
"""
validate_mcp_checks.py — gate `mcp-check-0825`: the MCP conformance checks
(checks/merchant_checks_08_25_mcp.py, driven through checks/mcp_client.py) are SOUND on
golden-0825's MCP binding, their skip population is PINNED, and every defects_config row
that names one of them is KILLED (PLAN-v3 D3-10, §2.15).

Three proofs against one golden (booted with DEFECTS_CONFIG by run_suite on :8195):
  1. soundness  — every MCP check that runs is clean-pass AND kill_safe (the same rule
                  validate_merchant_checks.py applies to every other golden);
  2. skips      — the not-applicable / not-tested population equals
                  checks/expected_skips_golden_0825_mcp.json (PLAN-v3 §2.3: an unexplained
                  skip, a pinned id that ran, a class mismatch, a stale spec_pin or an
                  expired review_by is red);
  3. mutants    — with --defects-state FILE, each defects_config.json row whose checks[]
                  names an `mcp:<id>` check is armed in turn: the named checks must flip
                  CLEAN -> DEVIATION -> CLEAN (the battery's FIRED/CAUGHT/RESTORED rule,
                  judged here by the harness checks themselves).

    python3 conformance/selfcheck/validate_mcp_checks.py --server http://localhost:8195 \
        [--defects-state FILE] [--expected-skips FILE] [--record FILE]
    python3 conformance/selfcheck/validate_mcp_checks.py --selftest      # hermetic
Exit 0 = `mcp: N/N run checks sound · K/K mutants killed · 0 unexplained`; 1 = red (named);
2 = the golden is not reachable.
"""
import argparse
import ast
import datetime
import importlib
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "conformance" / "checks"))

EXPECTED_SKIPS = ROOT / "conformance" / "checks" / "expected_skips_golden_0825_mcp.json"
DEFECTS_CONFIG = ROOT / "conformance" / "testbed" / "golden-0825" / "server" / "defects_config.json"
SOURCES_LOCK = ROOT / "conformance" / "SOURCES.lock.json"
VERSION = "2026-08-25"
GOLDEN = "golden-0825-mcp"
SKIP_CLASSES = {"version-scoped", "transport-not-declared", "capability-not-declared",
                "needs-product", "needs-config", "oracle-unavailable"}


# ----------------------------------------------------------------------------- pieces
def assess_skips(expected, skipped, ran, pin, today):
    """PLAN-v3 §2.3 expected-skips semantics. `expected` is the pinned file's document,
    `skipped` {id: class} from this run, `ran` the ids that ran, `pin` the live 8-hex
    spec pin for the version, `today` an ISO date. Returns (fails: [str], n_unexplained)."""
    fails = []
    if expected.get("golden") != GOLDEN:
        fails.append(f"expected-skips file is for golden {expected.get('golden')!r}, not {GOLDEN!r}")
    if expected.get("spec_pin") != pin:
        fails.append(f"stale spec_pin {expected.get('spec_pin')!r} (lock says {pin!r}) — re-review and re-pin")
    if str(expected.get("review_by", "")) < today:
        fails.append(f"expired review_by {expected.get('review_by')!r} (today {today})")
    pinned = expected.get("expected_skips", {})
    for cid, cls in pinned.items():
        if cls not in SKIP_CLASSES:
            fails.append(f"pinned skip {cid}: unknown class {cls!r}")
        if cid in ran:
            fails.append(f"stale pin: {cid} is pinned as skipped ({cls}) but RAN — remove the pin")
        elif cid not in skipped:
            fails.append(f"stale pin: {cid} is pinned as skipped ({cls}) but is not a known skip of this run")
        elif skipped[cid] != cls:
            fails.append(f"class mismatch: {cid} pinned {cls!r}, observed {skipped[cid]!r}")
    unexplained = sorted(c for c in skipped if c not in pinned)
    for cid in unexplained:
        fails.append(f"unexplained skip: {cid} ({skipped[cid]}) is not pinned in {EXPECTED_SKIPS.name}")
    return fails, len(unexplained)


def mutant_rows(config):
    """Every defects_config.json row (behavior or check-graded patch) whose checks[] names
    an `mcp:<id>` check of the MCP module — the rows this gate must prove KILLED."""
    rows = []
    for row in config.get("mutants", []) + config.get("behavior_mutants", []):
        checks = [c for c in row.get("checks", []) if c.startswith("mcp:")]
        if checks:
            rows.append((row["name"], checks, "behavior" in row))
    return rows


def live_pin(lock_path=SOURCES_LOCK, version=VERSION):
    d = json.loads(pathlib.Path(lock_path).read_text())
    return (d["spec"]["versions"][version].get("commit") or "")[:8]


# ----------------------------------------------------------------------------- selftest
def selftest():
    """Hermetic kill-tests of the three proofs' logic and of the client contract."""
    import mcp_client
    import merchant_checks_08_25_mcp as mcpchk
    from engine import Resp, CLEAN, DEVIATION

    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    sc = {"ucp": {"version": VERSION, "status": "success"}, "id": "chk_1", "status": "incomplete"}
    both = {"jsonrpc": "2.0", "id": 1,
            "result": {"structuredContent": sc, "content": [{"type": "text", "text": json.dumps(sc)}]}}
    content_only = {"jsonrpc": "2.0", "id": 1,
                    "result": {"content": [{"type": "text", "text": json.dumps(sc)}]}}

    # (a) the client's envelope contract: the UCP payload is result.structuredContent
    check("client: payload_of reads result.structuredContent",
          mcp_client.payload_of(both) == sc)
    try:
        mcp_client.payload_of(content_only)
        check("client: a content[]-only result fails the envelope assertion", False,
              "payload_of accepted a result without structuredContent")
    except mcp_client.EnvelopeError:
        check("client: a content[]-only result fails the envelope assertion", True)

    # (b) a PLANTED client that reads content[0].text as the payload: the gate's
    # envelope assertion (the MCP check's predicate) is not fooled by it
    class _ContentReadingClient(mcp_client.McpClient):
        @staticmethod
        def payload_of(doc):
            return json.loads(doc["result"]["content"][0]["text"])

    planted = _ContentReadingClient("http://localhost:0/mcp")
    check("planted client: reads content[] as the payload (the wrong contract)",
          planted.payload_of(content_only) == sc)
    resp = Resp(200, {"Content-Type": "application/json"}, json.dumps(content_only).encode())
    check("planted client: the envelope assertion still reds a content[]-only result",
          mcpchk.p_structured_content(resp) == DEVIATION)
    resp_ok = Resp(200, {"Content-Type": "application/json"}, json.dumps(both).encode())
    check("envelope assertion: a structuredContent (+ matching content[]) result is CLEAN",
          mcpchk.p_structured_content(resp_ok) == CLEAN)
    mismatch = json.loads(json.dumps(both)); mismatch["result"]["content"][0]["text"] = "{}"
    check("envelope assertion: content[] text that does not parse to structuredContent is DEVIATION",
          mcpchk.p_structured_content(Resp(200, {}, json.dumps(mismatch).encode())) == DEVIATION)

    # (c) SSE decoding: one `message` event carries the same JSON-RPC document
    sse = ("event: message\ndata: " + json.dumps(both) + "\n\n").encode()
    check("client: an SSE message event decodes to the JSON-RPC document",
          mcp_client.decode_sse(sse) == both)
    check("client: decode_sse of a JSON body is that body",
          mcp_client.decode_sse(json.dumps(both).encode()) == both)

    # (d) expected-skips semantics (§2.3)
    pin, today = "cd78fb38", "2026-09-11"
    good = {"golden": GOLDEN, "spec_pin": pin, "review_by": "2026-10-11",
            "expected_skips": {"mcp.x": "needs-config"}}
    f, n = assess_skips(good, {"mcp.x": "needs-config"}, ["mcp.a"], pin, today)
    check("skips: exact match -> green", not f and n == 0, "; ".join(f))
    f, n = assess_skips(good, {"mcp.x": "needs-config", "mcp.y": "needs-product"}, ["mcp.a"], pin, today)
    check("skips: an unpinned skip -> red, counted unexplained",
          any("unexplained skip: mcp.y" in x for x in f) and n == 1)
    f, _ = assess_skips(good, {}, ["mcp.a", "mcp.x"], pin, today)
    check("skips: a pinned id that RAN -> stale pin red", any("stale pin: mcp.x" in x for x in f))
    f, _ = assess_skips(good, {"mcp.x": "needs-product"}, ["mcp.a"], pin, today)
    check("skips: class mismatch -> red", any("class mismatch: mcp.x" in x for x in f))
    f, _ = assess_skips({**good, "review_by": "2026-09-10"}, {"mcp.x": "needs-config"}, [], pin, today)
    check("skips: expired review_by -> red", any("expired review_by" in x for x in f))
    f, _ = assess_skips(good, {"mcp.x": "needs-config"}, [], "deadbeef", today)
    check("skips: stale spec_pin -> red", any("stale spec_pin" in x for x in f))

    # (e) mutant-row selection from the real catalog: the rows the gate must kill
    rows = mutant_rows(json.loads(DEFECTS_CONFIG.read_text()))
    names = {n for n, _, _ in rows}
    check("mutants: the real catalog yields the 7 MCP-graded rows (2 patch + 5 behavior)",
          len(rows) == 7 and {"mcp_result_not_structured", "mcp_transport_not_advertised",
                              "mcp_meta_not_required", "mcp_negotiation_error_code_-32000",
                              "mcp_error_http_status_200"} <= names, sorted(names))
    check("mutants: the battery-graded ordering row is NOT in this gate's set",
          "mcp_headers_after_verify" not in names)

    # (g) the deferred-import contract of run(): every name run() pulls from a
    # sibling module must exist TODAY. run() imports lazily (the gate is import-light
    # when the golden is unreachable), so a cross-lane rename in merchant_checks /
    # validate_merchant_checks cannot be caught by importing this module — it only
    # fires when the gate actually runs against a live server. That is exactly how
    # D1-10's `_skip_class` -> `merchant_checks.skip_class` move reached the merged
    # tree red (W1 integration): every hermetic sweep stayed green and the full
    # selftest failed with ImportError. This case pins the contract hermetically.
    # The names are read out of run()'s own source, so the case cannot drift away
    # from what the gate actually imports.
    src = ast.parse(pathlib.Path(__file__).read_text())
    run_fn = next(n for n in src.body
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    missing, deferred, unreachable = [], [], []
    for node in ast.walk(run_fn):
        mods = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
        for mod in mods:
            try:
                m = importlib.import_module(mod)
            except ModuleNotFoundError:
                # golden-side modules are not on the hermetic path; the live gate
                # covers them. Never let that hide a missing engine-side name.
                unreachable.append(mod)
                continue
            names = ([a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else [])
            deferred += [f"{mod}.{n}" for n in names] or [mod]
            missing += [f"{mod}.{n}" for n in names if not hasattr(m, n)]
    check(f"run()'s {len(deferred)} deferred imports all resolve",
          not missing, "missing: " + ", ".join(missing))

    # (f) the pinned file itself is well-formed against the live lock
    doc = json.loads(EXPECTED_SKIPS.read_text())
    f, _ = assess_skips(doc, dict(doc.get("expected_skips", {})), [], live_pin(),
                        datetime.date.today().isoformat())
    check("expected_skips_golden_0825_mcp.json: pin fresh, clock unexpired, classes known",
          not f, "; ".join(f))

    print(f"validate_mcp_checks selftest: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


# ----------------------------------------------------------------------------- the gate
def run(server, defects_state=None, expected_skips=EXPECTED_SKIPS, record=None):
    import merchant_checks
    import merchant_checks_08_25_mcp as mcpchk
    from engine import CLEAN, DEVIATION
    from merchant import MerchantCtx, discover
    from merchant_checks import skip_class
    from validate_merchant_checks import REF_CONFIG, _write_record
    try:
        profile, _ = discover(server)
    except SystemExit as e:
        print(f"mcp-check-0825: SKIP — golden-0825 not reachable at {server} ({e})")
        return 2
    ctx = MerchantCtx(server, profile, REF_CONFIG)
    if not ctx.has_mcp:
        print(f"mcp-check-0825: FAIL — {server} advertises no MCP transport")
        return 1
    checks = list(mcpchk.CHECKS_MCP)
    _, detail = merchant_checks.run_merchant_checks(ctx, checks)
    ok, broken, weak, skipped = [], [], [], []
    for chk, d in detail:
        st = d["status"]
        if isinstance(st, str) and st.startswith(("not-applicable", "not-tested")):
            skipped.append((chk.id, st)); continue
        if st != CLEAN:
            broken.append((chk.id, st, d.get("observed"))); continue
        if not d.get("kill_safe"):
            weak.append((chk.id, d.get("survivors"))); continue
        ok.append(chk.id)
    fails = []
    for cid, st, obs in broken:
        fails.append(f"BROKEN {cid}: {st} on the golden — observed {json.dumps(obs)[:200]}")
    for cid, surv in weak:
        fails.append(f"WEAK {cid}: survivors {surv}")

    # 2. skips vs the pinned population
    doc = json.loads(pathlib.Path(expected_skips).read_text())
    skip_map = {cid: skip_class(st) for cid, st in skipped}
    sfails, n_unexplained = assess_skips(doc, skip_map, ok + [b[0] for b in broken] + [w[0] for w in weak],
                                         live_pin(), datetime.date.today().isoformat())
    fails += sfails

    # 3. mutants: arm each MCP-graded row, the named checks must flip
    killed, mutant_lines = [], []
    rows = mutant_rows(json.loads(DEFECTS_CONFIG.read_text()))
    if defects_state:
        sys.path.insert(0, str(ROOT / "conformance" / "testbed" / "golden-0825" / "server"))
        import defects
        by_id = {c.id: c for c in checks}

        def verdict(cid):
            chk = by_id[cid]
            try:
                return merchant_checks._pred(chk, chk.fetch_fn(ctx), ctx)
            except Exception as e:                       # noqa: BLE001
                return f"error:{e}"

        for name, named, is_behavior in rows:
            ids = [c[len("mcp:"):] for c in named]
            unknown = [c for c in ids if c not in by_id]
            if unknown:
                fails.append(f"mutant {name}: names unknown MCP check(s) {unknown}"); continue
            defects.write_state(defects_state, None)
            clean = {c: verdict(c) for c in ids}
            defects.write_state(defects_state, name)
            armed = {c: verdict(c) for c in ids}
            defects.write_state(defects_state, None)
            restored = {c: verdict(c) for c in ids}
            bad_clean = {c: v for c, v in clean.items() if v != CLEAN}
            not_flipped = {c: v for c, v in armed.items() if v != DEVIATION}
            not_restored = {c: v for c, v in restored.items() if v != CLEAN}
            if bad_clean:
                fails.append(f"mutant {name}: clean baseline not CLEAN {bad_clean}")
            elif not_flipped:
                fails.append(f"mutant {name}: SURVIVED — armed, these checks did not deviate: {not_flipped}")
            elif not_restored:
                fails.append(f"mutant {name}: RESTORE-FAILED — disarmed but still red: {not_restored}")
            else:
                killed.append(name)
                mutant_lines.append(f"  ✓ {name:40} KILLED — {', '.join(ids)} flipped CLEAN->DEVIATION->CLEAN")

    for cid in ok:
        print(f"  ✓ {cid:36} sound (clean-pass + kill_safe)")
    for cid, st in skipped:
        print(f"  · {cid:36} skipped ({st})")
    for line in mutant_lines:
        print(line)
    for f in fails:
        print(f"  ✗ {f}")
    if record:
        _write_record(record, GOLDEN, server, ctx, ok, [(b[0], b[1]) for b in broken], weak, [], skipped)
    n_run = len(ok) + len(broken) + len(weak)
    mutants = f" · {len(killed)}/{len(rows)} mutants killed" if defects_state else " · mutants not armed (no --defects-state)"
    summary = f"mcp: {len(ok)}/{n_run} run checks sound{mutants} · {n_unexplained} unexplained"
    print(f"mcp-check-0825: {'PASS' if not fails else 'FAIL'} — {summary}")
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="mcp-check-0825 gate.")
    ap.add_argument("--server", default="http://localhost:8195")
    ap.add_argument("--defects-state", help="the booted golden's --defects_state_file (arms the rows)")
    ap.add_argument("--expected-skips", default=str(EXPECTED_SKIPS))
    ap.add_argument("--record", help="write the run record for validate_dormancy.py")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run(args.server, args.defects_state, args.expected_skips, args.record)


if __name__ == "__main__":
    sys.exit(main())

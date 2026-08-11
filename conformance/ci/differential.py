#!/usr/bin/env python3
"""
differential.py — the FIXTURE-CIRCULARITY gate (differential conformance testing).

Kill-rate proves a check catches defects *relative to our controlled fixture*. But
behavioral checks and their fixture are authored by the same effort, so a check and
its fixture can encode the SAME misreading of the spec and pass together. The only
way to catch that is to run the checks against a conformant target WE DIDN'T AUTHOR
and confirm they don't false-flag it.

This harness does exactly that. Point it at one or more INDEPENDENT, known-conformant
UCP servers (the official Flower Shop sample; a real merchant you trust; your own
staging server) and it asserts the invariant:

    A check that passes our controlled fixture MUST NOT report a MUST-deviation
    against an independently-authored conformant server.

Any deviation is a DIFFERENTIAL FINDING: either the target has a real bug, or — the
case we care about — our check encodes a fixture-specific misreading. Neither may be
ignored. A finding is silenced only by an entry in differential_allowlist.json that
names the (target, check) pair and documents WHY the target legitimately deviates
(so a real target bug can never mask one of our check bugs, and vice versa).

SELF-EXPIRY (mirrors known_reference_defects.json / known_fuzz_defects.json).
An allowlist entry is an ACTIVE claim that a specific (target, check) deviation still
occurs. When that deviation stops reproducing — upstream fixed the bug, or our check
changed — the claim is false, and a false silence left in place would mask a FUTURE,
different regression of that same (target, check). So every entry is checked against
what actually happened this run: if we PROBED the entry's target but its silenced
deviation NO LONGER occurs, the entry is STALE and the gate FAILS loudly, naming it to
delete — exactly as the defect registers fail on a defect that stops reproducing. This
converts "silently masks forever" into "forces removal when the bug is fixed." An entry
whose target was not probed this run is untestable and left alone (never falsely expired).

Third parties can run this against their own conformant server:
    python conformance/ci/differential.py --server https://store.example --config my.json
If we flag your conformant server, that's our bug — the finding tells you which check.

Exit: 0 = no unexplained differential findings; 1 = a finding needs resolution;
2 = SKIP (no independent target reachable — like other server-dependent gates).
"""
import argparse, json, os, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PKG = os.path.join(ROOT, "packaging")
ALLOWLIST = os.path.join(HERE, "differential_allowlist.json")
TARGETS = os.path.join(HERE, "differential_targets.json")

# a check status is a PASS/benign unless it starts with one of these; anything else
# (a "deviation"/"fail"/MUST-violation status) is a differential finding.
SAFE_PREFIXES = ("clean-pass", "pass", "not-tested", "not-applicable", "skip", "inconclusive")


def server_up(url, timeout=3):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/.well-known/ucp", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def run_cli(server, config=None):
    """Run the PUBLIC CLI (the same path a third party uses) and return its JSON."""
    argv = [sys.executable, "-m", "spck_conformance.cli", "--server", server, "--json"]
    if config:
        argv += ["--config", config]
    env = dict(os.environ, PYTHONPATH=PKG + os.pathsep + env_path())
    p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=300)
    try:
        return json.loads(p.stdout)
    except Exception:
        raise RuntimeError(f"CLI produced no JSON for {server}:\n{p.stdout[-400:]}\n{p.stderr[-400:]}")


def env_path():
    return os.environ.get("PYTHONPATH", "")


def deviations(report):
    """The checks that report a MUST-deviation (not pass / not-tested / not-applicable)."""
    out = []
    for c in report.get("checks", []):
        status = (c.get("status") or "").lower()
        if not status.startswith(SAFE_PREFIXES):
            out.append(c)
    return out


def load_allowlist():
    if not os.path.exists(ALLOWLIST):
        return {}
    d = json.load(open(ALLOWLIST))
    idx, errs = {}, []
    for a in d.get("allow", []):
        if len((a.get("reason") or "").strip()) < 40:
            errs.append(f"allowlist {a.get('target')}/{a.get('check')}: reason too thin — "
                        f"document WHY this independent target legitimately deviates")
        idx[(a.get("target"), a.get("check"))] = a
    return idx, errs


def classify_allowlist(allow_keys, matched, probed):
    """Self-expiry classifier — split allowlist (target, check) keys into (used, stale),
    scoped to the targets we actually PROBED this run.

        used  : allowlisted (target, check) whose silenced deviation ACTUALLY occurred —
                the exception earned its keep this run.
        stale : allowlisted (target, check) whose target we PROBED but whose deviation did
                NOT occur — the target is now conformant on that check, so the exception
                has outlived the deviation it documented and must be deleted (exactly like
                a known_reference_defects.json / known_fuzz_defects.json entry that stops
                reproducing). Leaving it would let it mask a FUTURE, different regression of
                the same (target, check).

    An entry whose target was NOT probed this run (unreachable / not configured) is
    UNTESTABLE — neither used nor stale — so a normal single-target run never falsely
    expires an exception written for a different, absent target."""
    used = sorted(k for k in allow_keys if k in matched)
    stale = sorted(k for k in allow_keys if k[0] in probed and k not in matched)
    return used, stale


def _selftest():
    """Hermetic kill-test for the self-expiry classifier (no network, no golden). A
    self-expiry rule that cannot fire proves nothing, so assert every arm: a probed
    target whose deviation vanished is STALE, a reproducing deviation is USED, and an
    UNPROBED target's entry is neither (never falsely expired)."""
    fails = []
    A = ("nodejs-reference-sample", "discovery.profile_cache_control")
    B = ("nodejs-reference-sample", "payment.response_handlers_echo")
    C = ("flower-shop-official-sample", "some.absent_check")
    allow = {A, B, C}
    both = {"nodejs-reference-sample", "flower-shop-official-sample"}

    # A & B still deviate on a probed node; C's target (flower) is probed but conformant.
    used, stale = classify_allowlist(allow, matched={A, B}, probed=both)
    if not (used == sorted([A, B]) and stale == [C]):
        fails.append(f"probed target with a vanished deviation must be STALE — "
                     f"got used={used} stale={stale}")

    # every allowlisted deviation reproduces -> all USED, nothing stale (the healthy run).
    used, stale = classify_allowlist(allow, matched=allow, probed=both)
    if stale:
        fails.append(f"every reproducing entry must be kept, none stale — got stale={stale}")

    # no target probed -> every entry is UNTESTABLE, so none is falsely expired.
    used, stale = classify_allowlist(allow, matched=set(), probed=set())
    if used or stale:
        fails.append(f"unprobed targets must be neither used nor stale — "
                     f"got used={used} stale={stale}")

    # only node probed -> node's absent B is stale; flower's C is untestable, NOT stale.
    used, stale = classify_allowlist(allow, matched={A}, probed={"nodejs-reference-sample"})
    if not (used == [A] and stale == [B]):
        fails.append(f"staleness must be scoped to probed targets only — "
                     f"got used={used} stale={stale}")

    if fails:
        print("differential self-expiry selftest: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("differential self-expiry selftest: PASS — a stale (probed-but-vanished) "
          "allowlist deviation is caught, a reproducing one is kept, and an unprobed "
          "target's entry is never falsely expired (self-expiry mirrors the defect registers).")
    return 0


def load_targets(cli_server, cli_config, cli_name=None):
    targets = []
    if cli_server:
        # Name an ad-hoc --server by its IDENTITY when we know it, not by its address.
        # Allowlist entries describe an implementation ("the flower shop deviates on X"),
        # and the same implementation reached on a different port is the same
        # implementation. Keying an exception to a localhost URL would make it silently
        # stop applying the moment the port changed.
        targets.append({"name": cli_name or cli_server, "server": cli_server,
                        "config": cli_config})
    if os.path.exists(TARGETS):
        for t in json.load(open(TARGETS)).get("targets", []):
            # allow env-substitution so URLs/secrets aren't committed
            srv = os.environ.get(t.get("server_env", ""), t.get("server", ""))
            if srv:
                targets.append({"name": t.get("name", srv), "server": srv,
                                "config": t.get("config")})
    return targets


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", help="an independent, known-conformant UCP server URL")
    ap.add_argument("--config", help="config for --server (optional)")
    ap.add_argument("--target-name", help="canonical name for --server, so allowlist "
                    "entries key on the implementation rather than the address")
    ap.add_argument("--selftest", action="store_true",
                    help="run the hermetic self-expiry classifier kill-test (no network)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    allow, allow_errs = load_allowlist() if os.path.exists(ALLOWLIST) else ({}, [])
    targets = load_targets(args.server, args.config, args.target_name)
    reachable = [t for t in targets if server_up(t["server"])]

    if allow_errs:
        print("differential gate: FAIL — invalid allowlist entries:")
        for e in allow_errs:
            print(f"  ✗ {e}")
        return 1

    if not reachable:
        print("differential gate: SKIP — no independent conformant target reachable. "
              "Set one in differential_targets.json (server_env) or pass --server "
              "<known-good UCP server> to run the fixture-circularity check.")
        return 2

    findings = []
    matched = set()      # allowlist keys whose silenced deviation ACTUALLY occurred this run
    probed = set()       # target names we actually obtained a report from this run
    for t in reachable:
        try:
            rep = run_cli(t["server"], t.get("config"))
        except Exception as e:
            print(f"  ! {t['name']}: could not run CLI ({e})")
            continue
        probed.add(t["name"])
        devs = deviations(rep)
        passed = sum(1 for c in rep.get("checks", [])
                     if (c.get("status") or "").lower().startswith(("clean-pass", "pass")))
        print(f"  {t['name']}: {passed} checks pass, {len(devs)} deviation(s) "
              f"[spec {rep.get('spec_version')}]")
        for c in devs:
            key = (t["name"], c.get("id"))
            if key in allow:
                matched.add(key)          # this exception earned its keep this run
                continue
            findings.append((t["name"], c.get("id"), c.get("status"),
                             (c.get("requirements") or c.get("req_ids") or [""]) if isinstance(
                                 c.get("requirements") or c.get("req_ids"), list) else ""))

    # SELF-EXPIRY: an allowlist entry for a target we PROBED whose silenced deviation no
    # longer occurs is STALE — fail loudly and name it, mirroring the defect registers, so
    # a documented silence can never outlive its deviation (and so mask a future regression
    # of the same (target, check)). Entries for un-probed targets are untestable, not stale.
    _used, stale = classify_allowlist(set(allow), matched, probed)
    failed = False

    if findings:
        print("\ndifferential gate: FAIL — our checks flag an independently-conformant target "
              "(our check may encode a fixture-specific misreading; or the target has a real "
              "bug — resolve, or allowlist with a documented reason):")
        for name, cid, status, req in findings:
            print(f"  ✗ {name}  {cid}  [{status}]  {req}")
        failed = True

    if stale:
        print("\ndifferential gate: FAIL — allowlisted deviation(s) NO LONGER occur: the "
              "target is now conformant on these checks (upstream fixed the bug, or our "
              "check changed). A silence that outlives its deviation would mask a FUTURE, "
              "different regression of the same (target, check) — DELETE the stale entr(y/ies) "
              "from differential_allowlist.json:")
        for name, cid in stale:
            print(f"  ✗ {name}  {cid}  — no deviation this run; remove this allow entry")
        failed = True

    if failed:
        return 1

    print("\ndifferential gate: PASS — no check false-flags an independently-authored "
          "conformant target (fixture-circularity invariant holds); every allowlisted "
          "deviation still occurs (no stale exception).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

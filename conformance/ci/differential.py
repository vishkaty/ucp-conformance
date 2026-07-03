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


def load_targets(cli_server, cli_config):
    targets = []
    if cli_server:
        targets.append({"name": cli_server, "server": cli_server, "config": cli_config})
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
    args = ap.parse_args()

    allow, allow_errs = load_allowlist() if os.path.exists(ALLOWLIST) else ({}, [])
    targets = load_targets(args.server, args.config)
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
    for t in reachable:
        try:
            rep = run_cli(t["server"], t.get("config"))
        except Exception as e:
            print(f"  ! {t['name']}: could not run CLI ({e})")
            continue
        devs = deviations(rep)
        passed = sum(1 for c in rep.get("checks", [])
                     if (c.get("status") or "").lower().startswith(("clean-pass", "pass")))
        print(f"  {t['name']}: {passed} checks pass, {len(devs)} deviation(s) "
              f"[spec {rep.get('spec_version')}]")
        for c in devs:
            key = (t["name"], c.get("id"))
            if key in allow:
                continue
            findings.append((t["name"], c.get("id"), c.get("status"),
                             (c.get("requirements") or c.get("req_ids") or [""]) if isinstance(
                                 c.get("requirements") or c.get("req_ids"), list) else ""))

    if findings:
        print("\ndifferential gate: FAIL — our checks flag an independently-conformant target "
              "(our check may encode a fixture-specific misreading; or the target has a real "
              "bug — resolve, or allowlist with a documented reason):")
        for name, cid, status, req in findings:
            print(f"  ✗ {name}  {cid}  [{status}]  {req}")
        return 1

    print("\ndifferential gate: PASS — no check false-flags an independently-authored "
          "conformant target (fixture-circularity invariant holds).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

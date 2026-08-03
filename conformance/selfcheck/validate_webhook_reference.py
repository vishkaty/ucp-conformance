#!/usr/bin/env python3
"""
validate_webhook_reference.py — order-webhook regression watch for samples#140/#146
(and the ucp#568 signing contract) on the REAL reference server, following the
validate_sig002_reference.py pattern: boot the VENDORED Flower Shop golden on a
private port and grade the WEBHOOK/EVENTS merchant checks against it.

samples#140 (python) and #146 (nodejs) fixed the order-event webhook body to be the
FULL order entity (rest.openapi.json webhooks.orderEvent: requestBody schema =
order), replacing the old {event_type, checkout_id, order} envelope. The checks
that cover this (webhook.order_created_full_entity et al.) were proven sound on the
controlled fixture (validate_sig_check.py) but sat dormant on the reference: the
flower config declared no `webhooks.simulate`, so an upstream revert would go
unnoticed. This gate is the thing in our repo that goes red if that happens.

Legs:
  * LIVE: vendored reference booted with --allow_insecure_profile_urls; the checks'
    own Harness0408 (loopback platform profile + capturing receiver, port 0) is the
    receiving platform; UCP-Agent names the loopback profile. Requires
    webhook.order_created_full_entity -> CLEAN-pass AND kill_safe (its declared
    payload mutations — envelope body, empty line_items, foreign order id — all
    caught).
  * MUTANT: the same flow with the harness platform profile advertising NO
    webhook_url (what a merchant that never delivers looks like): the SAME check
    must DEVIATE — the gate proves its own kill direction on every run.
  * TRIPWIRES (documented reference gaps, pinned so a change upstream is loud):
    the reference today does NOT sign webhook deliveries, does NOT send UCP-Agent
    on them (the ucp#568 contract: order.md "Webhook Signature Verification"),
    and does NOT retry a failed delivery (ORD-031). With the signing/retry config
    asserted anyway, those checks must NOT clean-pass. When upstream implements
    webhook signing/retry, this leg goes red -> flip the flower config
    (webhooks.signed / webhooks.retries) to enable them for real, and retire the
    tripwire. webhook.update_full_entity must stay not-tested: the reference has
    no post-order adjustment hook (its only update event is order_shipped).

Exit 0 = proven; 1 = failed; 2 = environment skip (vendored server or uv absent).
"""
import copy
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))

SERVER_DIR = ROOT / "conformance" / ".vendor" / "samples" / "rest" / "python" / "server"
DATA_DIR = ROOT / "conformance" / ".vendor" / "samples" / "rest" / "python" / \
    "test_data" / "flower_shop"
REF_PORT = 9414       # agent-private range; 9412/9413 belong to the sig002 gate

DELIVERY_CHECK = "webhook.order_created_full_entity"
# reference gaps pinned as tripwires: these must NOT clean-pass until upstream
# implements webhook signing / UCP-Agent identification / delivery retry
TRIPWIRE_CHECKS = ("webhook.ucp_agent_header", "webhook.signed_rfc9421_verifies",
                   "webhook.signed_components", "webhook.query_component_signed",
                   "webhook.retry_failed_delivery")
UPDATE_CHECK = "webhook.update_full_entity"   # must stay not-tested (no adjust hook)


def _listener_pids(port):
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout
        return [int(p) for p in out.split()]
    except Exception:
        return []


def _boot_reference(db_dir):
    """Seed a fresh DB and boot the vendored reference; return the wrapper Popen.

    ``uv run`` forks, so terminating the wrapper alone can leave the listener
    bound (the serve_golden.sh lesson) — teardown must kill _listener_pids too.
    """
    if _listener_pids(REF_PORT):
        print(f"webhook gate: port {REF_PORT} already in use — refusing to boot")
        return None
    seed = subprocess.run(
        ["uv", "run", "import_csv.py", f"--data_dir={DATA_DIR}",
         f"--products_db_path={db_dir}/products.db",
         f"--transactions_db_path={db_dir}/transactions.db"],
        cwd=SERVER_DIR, capture_output=True, timeout=300)
    if seed.returncode != 0:
        print("webhook gate: seeding failed —", seed.stderr.decode()[-300:])
        return None
    proc = subprocess.Popen(
        ["uv", "run", "server.py",
         f"--products_db_path={db_dir}/products.db",
         f"--transactions_db_path={db_dir}/transactions.db",
         f"--port={REF_PORT}", "--simulation_secret=webhook-gate-secret",
         "--allow_insecure_profile_urls"],
        cwd=SERVER_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{REF_PORT}/.well-known/ucp", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.5)
    _teardown(proc)
    return None


def _teardown(proc):
    pids = _listener_pids(REF_PORT)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    for pid in pids:
        subprocess.run(["kill", str(pid)], capture_output=True)
    for _ in range(20):
        if not _listener_pids(REF_PORT):
            return
        time.sleep(0.25)


def _grade(check_ids, extra_cfg=None):
    """Grade the named WEBHOOK/EVENTS checks against the booted reference with the
    flower config + webhooks enablement; {check_id: detail}."""
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    from merchant_checks_04_08_events import CHECKS_04_08_EVENTS
    from validate_merchant_checks import REF_CONFIG
    cfg = copy.deepcopy(REF_CONFIG)
    cfg.setdefault("webhooks", {}).update({"simulate": True, "wait_seconds": 8.0})
    for k, v in (extra_cfg or {}).items():
        node = cfg
        parts = k.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    base = f"http://localhost:{REF_PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, cfg)
    picked = [c for c in CHECKS_04_08_EVENTS if c.id in check_ids]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d for chk, d in detail}


def main():
    if not SERVER_DIR.is_dir() or not (DATA_DIR / "products.csv").is_file():
        print("webhook gate: vendored reference not present — run fetch_sources.sh (skip)")
        return 2
    if shutil.which("uv") is None or shutil.which("lsof") is None:
        print("webhook gate: uv/lsof not available (skip)")
        return 2

    failures = []
    with tempfile.TemporaryDirectory() as db:
        proc = _boot_reference(db)
        if proc is None:
            print("webhook gate: reference did not come up — skip")
            return 2
        try:
            # LIVE: the samples#140 behavior — delivery carries the full order
            # entity — must clean-pass and be kill_safe on the reference.
            d = _grade([DELIVERY_CHECK, UPDATE_CHECK])
            live = d.get(DELIVERY_CHECK, {})
            st, ks = live.get("status"), live.get("kill_safe")
            print(f"  live:     {DELIVERY_CHECK} -> {st} "
                  f"(kills={live.get('kills')}, kill_safe={ks})")
            if st != "clean-pass":
                failures.append(("live", DELIVERY_CHECK, st,
                                 live.get("observed")))
            if not ks:
                failures.append(("live-killsafe", DELIVERY_CHECK,
                                 live.get("survivors")))
            upd = d.get(UPDATE_CHECK, {})
            print(f"  update:   {UPDATE_CHECK} -> {upd.get('status')} "
                  f"(want not-tested: reference has no post-order adjust hook)")
            if not str(upd.get("status", "")).startswith("not-tested"):
                failures.append(("update-hook", UPDATE_CHECK, upd.get("status")))

            # MUTANT: a merchant that never delivers (profile advertises no
            # webhook_url) must be caught by the same check.
            import webhook_harness
            orig = webhook_harness.platform_profile_0408

            def _no_webhook_profile(url, version="2026-04-08"):
                prof = orig(url, version)
                prof["capabilities"]["dev.ucp.shopping.order"][0].pop("config", None)
                return prof

            webhook_harness.platform_profile_0408 = _no_webhook_profile
            try:
                st = _grade([DELIVERY_CHECK]).get(DELIVERY_CHECK, {}).get("status")
            finally:
                webhook_harness.platform_profile_0408 = orig
            print(f"  mutant:   {DELIVERY_CHECK} -> {st} (no webhook_url; want deviation)")
            if st != "deviation":
                failures.append(("mutant-no-delivery", DELIVERY_CHECK, st))

            # TRIPWIRES: reference gaps (no webhook signing / UCP-Agent / retry).
            # Assert signing+retry config so the checks RUN, and pin non-clean.
            d = _grade(TRIPWIRE_CHECKS,
                       extra_cfg={"webhooks.signed": True, "webhooks.retries": True})
            for cid in TRIPWIRE_CHECKS:
                st = d.get(cid, {}).get("status")
                print(f"  tripwire: {cid} -> {st} (reference gap; want deviation)")
                if st == "clean-pass":
                    failures.append(("tripwire-now-passes", cid, st))
        finally:
            _teardown(proc)

    if failures:
        print("webhook gate: FAIL —")
        for f in failures:
            print("   ", f)
        return 1
    print("webhook gate: PASS — order-event webhook delivery on the reference carries "
          "the full order entity (samples#140 watched: clean-pass + kill_safe, and a "
          "non-delivering merchant DEVIATES); the reference's missing webhook "
          "signing/UCP-Agent/retry stay pinned as tripwires")
    return 0


if __name__ == "__main__":
    sys.exit(main())

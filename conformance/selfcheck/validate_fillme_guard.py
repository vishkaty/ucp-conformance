#!/usr/bin/env python3
"""
validate_fillme_guard.py — the scaffold-placeholder guard, HERMETIC (no server,
no network; pure config-plumbing proof).

THE DEFECT THIS PINS (found 2026-07-09 by running the documented pip recipe
verbatim): `--init` writes a starter config full of `FILL_ME: …` placeholders the
user must edit. `_cfg_has` treated any truthy value as "config supplied", so an
UNEDITED scaffold ran data-dependent checks with literal FILL_ME strings and
reported 7 false MUST deviations (discount.* + validation.payment_failure) against
a conformant server. False reds break credibility exactly like false greens: an
unresolved placeholder MUST mean not-tested (needs config), never a verdict.

Proves BOTH directions:
  * GUARD  every scaffold_config() value still containing FILL_ME (at any nesting
           depth) is treated as ABSENT by _cfg_has — so cfg_needs checks land
           not-tested, exactly as if the key were never written.
  * KILL   the guard must not over-block: every cfg_needs key that
           CONTROLLED_CONFIG genuinely supplies still registers as present, and a
           scaffold value that is real (the detected product_id) stays present.

Exit 0 = proven; 1 = the guard fails in either direction.
"""
import pathlib, sys, types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))

import merchant                                           # scaffold_config
from merchant_checks import _cfg_has, all_checks
from validate_merchant_checks import CONTROLLED_CONFIG


def _contains_fillme(v):
    if isinstance(v, str):
        return "FILL_ME" in v
    if isinstance(v, dict):
        return any(_contains_fillme(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_contains_fillme(x) for x in v)
    return False


def main():
    fails = []

    # a ctx declaring every capability the scaffold branches on, with a real
    # detected product — the richest scaffold (most placeholder keys emitted)
    ctx = types.SimpleNamespace(
        capabilities={"dev.ucp.shopping.checkout", "dev.ucp.shopping.order",
                      "dev.ucp.shopping.discount", "dev.ucp.shopping.fulfillment",
                      "dev.ucp.shopping.catalog", "dev.ucp.shopping.cart"},
        product_id="teapot_ceramic_v1")
    cfg = merchant.scaffold_config(ctx)

    # GUARD: every FILL_ME-bearing scaffold key must read as absent
    placeholder_keys = [k for k, v in cfg.items() if _contains_fillme(v)]
    if not placeholder_keys:
        fails.append("scaffold emitted no FILL_ME placeholders — guard untestable "
                     "(scaffold_config changed shape?)")
    for k in placeholder_keys:
        if _cfg_has(cfg, k):
            fails.append(f"GUARD: _cfg_has says {k!r} is supplied, but its scaffold "
                         f"value still contains FILL_ME — an unedited --init config "
                         f"would run this check with placeholder data")

    # GUARD at depth: dotted sub-keys of a placeholder-bearing branch too
    if "discount" in cfg:
        for sub in ("discount.valid_code", "discount.second_valid_code"):
            if _cfg_has(cfg, sub) and _contains_fillme(cfg["discount"]):
                fails.append(f"GUARD: dotted key {sub!r} reads as supplied from an "
                             f"unedited scaffold")

    # KILL: real values must stay present — no over-blocking
    if not _cfg_has(cfg, "product_id"):
        fails.append("KILL: the scaffold's REAL detected product_id was blocked — "
                     "guard over-matches")
    supplied = sorted({k for chk in all_checks() for k in chk.cfg_needs
                       if _cfg_has(CONTROLLED_CONFIG, k)})
    if not supplied:
        fails.append("KILL: no cfg_needs key from CONTROLLED_CONFIG registers as "
                     "supplied — guard blocks everything")
    for k in supplied:                                    # stability of real config
        cur = CONTROLLED_CONFIG
        for part in k.split("."):
            cur = cur[part]
        if _contains_fillme(cur):
            fails.append(f"CONTROLLED_CONFIG itself carries a FILL_ME at {k!r}")

    for f in fails:
        print(f"  x {f}")
    n = len(placeholder_keys)
    print(f"fillme-guard: {'PASS' if not fails else 'FAIL'} — {n} scaffold "
          f"placeholder key(s) guarded, {len(supplied)} real config key(s) still live")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
shared_api_guard.py — pin the API contract BOTH conformance lanes depend on.

The merchant and agent lanes share `engine.py` primitives (fetch/mutate/mcp_call/... and
the CLEAN/DEVIATION sentinels). This guard freezes their required-argument surface so a
refactor on EITHER side that removes, renames, or makes-required a shared function is
caught before it can silently break the other lane. Additive changes (new OPTIONAL
params, new functions) are allowed — that's the additive-only rule, enforced.

Together with `merchant_stability.py` (which pins the merchant-fixture-sandbox contract
the agent lane shops against), this makes the two-lane isolation SYMMETRIC: neither side
can break the other's contract undetected.

  shared_api_guard.py            # gate: exit 1 if the shared API drifted
"""
import importlib, inspect, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))

# The frozen shared surface: name -> ordered list of REQUIRED positional params (no default).
# Growing this list (a new required param) or dropping a name breaks callers -> fail.
# Adding an OPTIONAL param leaves required[] unchanged -> allowed.
PINNED = {
    "Resp":         ["status", "headers", "body"],
    "fetch":        ["base", "path"],
    "mutate":       ["resp", "mut"],
    "mcp_call":     ["endpoint", "name", "arguments"],
    "mcp_call_raw": ["endpoint", "name", "arguments"],
    "a2a_call":     ["endpoint", "message"],
}
PINNED_VALUES = {"CLEAN": "clean-pass", "DEVIATION": "deviation"}


def _required(obj):
    sig = inspect.signature(obj.__init__ if isinstance(obj, type) else obj)
    return [p.name for p in sig.parameters.values()
            if p.default is p.empty and p.name != "self"
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]


def run():
    eng = importlib.import_module("engine")
    fails = []
    for name, expected in PINNED.items():
        obj = getattr(eng, name, None)
        if obj is None:
            fails.append(f"engine.{name} REMOVED — shared API broken (both lanes depend on it)")
            continue
        got = _required(obj)
        if got != expected:
            fails.append(f"engine.{name} required-args changed: {expected} -> {got} "
                         f"(additive-only: only OPTIONAL params may be added)")
    for name, val in PINNED_VALUES.items():
        if getattr(eng, name, None) != val:
            fails.append(f"engine.{name} value changed from {val!r} — checks compare against it")
    return fails


def main():
    fails = run()
    if fails:
        print("shared-api guard: FAIL — the contract both lanes share drifted:")
        for f in fails:
            print(f"  x {f}")
        print("  If a change is intentional and additive, keep the required-arg list stable; "
              "if it's a real break, fix the callers on BOTH lanes and update PINNED here.")
        return 1
    print(f"shared-api guard: PASS — the {len(PINNED)} shared engine primitives + "
          f"{len(PINNED_VALUES)} sentinels both lanes depend on are stable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
validate_mutation_paths.py — the mutation engine must be able to reach the field it aims at.

A kill-test only proves something if the mutant actually changes what the predicate reads.
A mutation that lands somewhere the predicate never looks is worse than no mutation: the
check reports kill_safe, the gate goes green, and the check can still false-PASS a real
merchant. Silence reads identically to correctness.

This is not theoretical. UCP discovery profiles legitimately come in two shapes — some
implementations nest everything under a top-level `ucp` member, others serve it flat —
and predicates handle both with `profile.get("ucp", profile)`. Mutation strings cannot,
because they are fixed paths. On 2026-07-31 `discovery.endpoints_https` (DISC-005) carried
four mutants aimed at `services`; against the wrapped 2026-04-08 reference they planted a
decoy at the document root while the predicate kept reading the real, valid endpoints
under `ucp`. All four survived. Aiming them at `ucp.services` fixed the reference and
broke the flat fixture in exactly the same way, which is the tell that a fixed path is the
wrong tool.

Hence optional path segments: `ucp?.services` descends into `ucp` when it is present and
stays at the root when it is not, so one mutation string reaches the same logical field in
both shapes.

    --selftest   deterministic, no network, no server. Exit 0 pass, 1 fail.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
from engine import Resp, mutate                            # noqa: E402


def _resp(doc):
    body = json.dumps(doc).encode()
    return Resp(200, {"Content-Type": "application/json"}, body)


WRAPPED = {"ucp": {"version": "2026-04-08",
                   "services": {"dev.ucp.shopping": [{"transport": "rest",
                                                      "endpoint": "https://ok.example/api"}]}}}
FLAT = {"version": "2026-01-11",
        "services": {"dev.ucp.shopping": [{"transport": "rest",
                                           "endpoint": "https://ok.example/api"}]}}

BAD = '{"dev.ucp.shopping":[{"transport":"rest","endpoint":"ftp://evil.example/api"}]}'


def _services(doc):
    """What a predicate reads: the ucp member when present, else the root."""
    inner = doc.get("ucp", doc) if isinstance(doc, dict) else {}
    return inner.get("services")


def _selftest():
    fails = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append(f"{name}: {detail}")

    # --- optional segment reaches the field in BOTH shapes -------------------------
    for label, doc in (("wrapped", WRAPPED), ("flat", FLAT)):
        r = mutate(_resp(doc), "set:ucp?.services=" + BAD)
        got = _services(r.json)
        check(f"optional-{label}",
              got == json.loads(BAD),
              f"mutant did not reach the field the predicate reads; got {got}")

    # --- the mutation must be visible in the SERIALIZED body too --------------------
    # Predicates read r.json, but anything re-parsing the wire bytes must see it as well;
    # a mutation that only edits the parsed dict would diverge from what a server sent.
    r = mutate(_resp(WRAPPED), "set:ucp?.services=" + BAD)
    check("reserialized", b"ftp://evil.example/api" in (r.body or b""),
          "mutated body was not re-serialized")

    # --- a fixed path still behaves exactly as before (no silent semantic change) ---
    r = mutate(_resp(FLAT), "set:services=" + BAD)
    check("plain-path-flat", _services(r.json) == json.loads(BAD),
          "plain paths must keep working on a flat document")

    # --- REGRESSION GUARD: the exact defect that hid for months ---------------------
    # A fixed `services` path against a WRAPPED doc must be recognisable as not having
    # touched what the predicate reads. If this ever starts "passing", the optional
    # segment has stopped being necessary and this file's premise needs rechecking.
    r = mutate(_resp(WRAPPED), "set:services=" + BAD)
    check("fixed-path-misses-wrapped", _services(r.json) != json.loads(BAD),
          "a root-level mutant appeared to reach a wrapped field — premise broken")

    # --- absent optional segment does not invent one --------------------------------
    r = mutate(_resp(FLAT), "set:ucp?.services=" + BAD)
    check("no-phantom-wrapper", "ucp" not in r.json,
          "optional segment must not create the container it was told to skip")

    # --- drop honours optional segments as well -------------------------------------
    for label, doc in (("wrapped", WRAPPED), ("flat", FLAT)):
        r = mutate(_resp(doc), "drop:ucp?.services")
        check(f"drop-{label}", _services(r.json) is None,
              f"drop did not remove the field in the {label} shape")

    # --- the PROXY must agree with the engine ---------------------------------------
    # Two harnesses apply mutations: engine.mutate (in-process) and mutation_proxy
    # (over the wire, driving the kill-rate gate). They read the same mutation strings,
    # so if their path handling diverges the same token means different things depending
    # on who applies it — and a kill-test can be blind in one while looking fine in the
    # other. The proxy now shares the engine's walker; this pins that they stay in step.
    import mutation_proxy                                       # noqa: E402
    for label, doc in (("wrapped", WRAPPED), ("flat", FLAT)):
        _, _, mutated = mutation_proxy._apply(
            "set-field:ucp?.services=" + BAD, 200, [], json.dumps(doc).encode())
        check(f"proxy-set-{label}", _services(json.loads(mutated)) == json.loads(BAD),
              "proxy set-field did not reach the field the predicate reads")
        _, _, dropped = mutation_proxy._apply(
            "drop:ucp?.services", 200, [], json.dumps(doc).encode())
        check(f"proxy-drop-{label}", _services(json.loads(dropped)) is None,
              "proxy drop did not remove the field")

    if fails:
        print("mutation-paths: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("mutation-paths: PASS — optional path segments reach the same logical field in "
          "wrapped and flat profiles, drop included, with no phantom containers.")
    return 0


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

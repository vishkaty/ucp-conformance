#!/usr/bin/env python3
"""
validate_probe_hygiene.py — no probe may send the golden a request it cannot parse.

The reference gate (validate_merchant_checks.py) asks "does this check clean-pass a
known-good server". That catches a broken check but does not explain WHY it broke, and
one why matters more than the rest: a 5xx.

A 5xx from a conformant golden is never a normal conformance outcome. It means either

  * OUR probe sent a request the server could not handle — the check is measuring our
    own malformed input rather than the requirement it claims to verify, or
  * the reference genuinely crashed — a real upstream defect that deserves a report,
    not a line item in a merchant's deviation list.

Both need a human decision, and neither should reach a real merchant as "you deviate".

This is not hypothetical. 2026-07-31: `checkout.idempotency_conflict` (CHK-048) posted a
create body with no `currency` — a field the 04-08 checkout schema requires. The server
crashed with `AttributeError: 'UnifiedCheckoutCreateRequest' object has no attribute
'currency'`, our probe recorded "not 409", and the check was graded a DEVIATION. It looked
exactly like a server that fails to honor idempotency conflicts. It was not: given a valid
body the reference returns a correct 409 with an IDEMPOTENCY_CONFLICT envelope. We were
one report away from telling a conformant implementer they had a bug they did not have.

The distinction this gate draws — "the server is wrong" versus "our question was
malformed" — is the difference between a credible report and a retraction.

Run (golden must be live):
    python3 conformance/selfcheck/validate_probe_hygiene.py [--server http://localhost:8182]
Exit 0 = every runnable probe was well-formed enough to get a real answer; 1 = at least
one probe drew a 5xx and must be investigated before its verdict can be trusted.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))
import merchant_checks                                    # noqa: E402
from merchant import MerchantCtx, discover                # noqa: E402
from validate_merchant_checks import GOLDENS              # noqa: E402

KNOWN = HERE.parents[0] / "ci" / "known_reference_defects.json"


def load_known(path=KNOWN):
    try:
        return {d["check_id"]: d for d in json.loads(path.read_text())["defects"]}
    except FileNotFoundError:
        return {}


def classify(offender_ids, known):
    """Split observed 5xx offenders into (new, acknowledged, stale).

    `stale` is the one that keeps this register honest. An acknowledgement is a claim
    that a specific defect still exists upstream; when the defect stops reproducing the
    claim is false, and a false claim left in place is how a suppression list grows
    silently. Reporting it as a failure forces the entry to be deleted rather than
    outliving the bug it described.
    """
    offenders = set(offender_ids)
    new = sorted(offenders - set(known))
    acknowledged = sorted(offenders & set(known))
    stale = sorted(set(known) - offenders)
    return new, acknowledged, stale


def _selftest():
    """Deterministic cover for the classifier, including the stale-entry rule."""
    fails = []
    known = {"a": {"upstream": "u", "filed": "d", "observed": "o"}}

    new, ack, stale = classify(["a"], known)
    if not (new == [] and ack == ["a"] and stale == []):
        fails.append(f"reproducing known defect must be acknowledged, got {new,ack,stale}")

    new, ack, stale = classify(["b"], known)
    if not (new == ["b"] and ack == [] and stale == ["a"]):
        fails.append(f"unknown offender is new AND absent known is stale, got {new,ack,stale}")

    new, ack, stale = classify([], known)
    if stale != ["a"]:
        fails.append("a known defect that stopped reproducing must be reported stale")

    new, ack, stale = classify([], {})
    if (new, ack, stale) != ([], [], []):
        fails.append("clean run must report nothing")

    if fails:
        print("probe-hygiene selftest: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("probe-hygiene selftest: PASS — new, acknowledged and stale entries are "
          "distinguished, so an acknowledgement cannot outlive its defect.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Probe-hygiene gate: no 5xx from the golden.")
    ap.add_argument("--server", default="http://localhost:8182")
    ap.add_argument("--golden", choices=sorted(GOLDENS), default="flower")
    ap.add_argument("--selftest", action="store_true",
                    help="run the deterministic classifier tests (no server)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    profile, _ = discover(args.server)
    ctx = MerchantCtx(args.server, profile, GOLDENS[args.golden])
    _, detail = merchant_checks.run_merchant_checks(ctx)

    offenders, ran = [], 0
    for chk, d in detail:
        obs = d.get("observed")
        if not obs or obs.get("status") is None:
            continue                      # skipped/not-applicable: no request was made
        ran += 1
        status = obs["status"]
        if isinstance(status, int) and 500 <= status < 600:
            offenders.append((chk.id, status, obs.get("body")))

    known = load_known()
    seen = {cid: (status, body) for cid, status, body in offenders}
    new, acknowledged, stale = classify(seen, known)

    print(f"Probe-hygiene gate — {ran} runnable probe(s) vs {args.server}\n")
    for cid in acknowledged:
        d = known[cid]
        print(f"  ! {cid:34} known reference defect, reported upstream")
        print(f"      {d['upstream']}  (filed {d['filed']})")
        print(f"      {d['observed'][:120]}")
    for cid in new:
        status, body = seen[cid]
        print(f"  ✗ {cid:34} drew HTTP {status} from the golden")
        print(f"      body: {str(body)[:160]}")
    for cid in stale:
        print(f"  ✗ {cid:34} listed as a known reference defect but no longer reproduces")
        print(f"      remove it from known_reference_defects.json — {known[cid]['upstream']}")

    if new:
        print(f"\n  {len(new)} probe(s) drew an UNEXPLAINED 5xx. A conformant golden does not")
        print("  5xx on a well-formed request, so before trusting any verdict from these")
        print("  checks, settle which it is: our request was invalid (fix the probe), or the")
        print("  reference crashed (report it upstream). Grading a merchant on either is wrong.")
        print("  GATE FAILED")
        return 1
    if stale:
        print("\n  A known-defect entry no longer reproduces. Delete it: an acknowledgement")
        print("  that outlives its defect is a suppression rule nobody chose to write.")
        print("  GATE FAILED")
        return 1
    if acknowledged:
        print(f"\n  GATE PASSED — {len(acknowledged)} known reference defect(s) above, each "
              f"already reported upstream; no unexplained 5xx.")
        return 0
    print("  GATE PASSED — no probe drew a 5xx; every verdict rests on an answer the "
          "server could actually give.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

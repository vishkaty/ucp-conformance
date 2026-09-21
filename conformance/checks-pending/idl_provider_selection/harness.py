#!/usr/bin/env python3
"""
harness.py: boots the three actors, seeds the platform with exactly one recognized IdP
(the honest one), runs one scenario in one mode and returns the run record the checks
grade: outcome, the platform session log, and the three actor logs (the hostile log is
the oracle). Everything on loopback, ephemeral ports, torn down after each run.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mocks import HonestIdP, HostileAS, Business, scenario_providers   # noqa: E402
from reference_platform import ReferencePlatform, CONFORMANT           # noqa: E402

CLIENT_ID, CLIENT_SECRET, USER = "platform-client-id", "platform-client-secret", "user-42"


def run_scenario(scenario, mode=CONFORMANT):
    idp, hostile, biz = HonestIdP().start(), HostileAS().start(), Business().start()
    try:
        idp.register(CLIENT_ID, CLIENT_SECRET)
        subject_token = idp.mint(USER)
        biz.providers = scenario_providers(scenario, idp.issuer, hostile.issuer)
        platform = ReferencePlatform(
            {idp.issuer: {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                          "subject_token": subject_token}}, mode=mode)
        outcome = platform.link(biz.issuer)
        return {"scenario": scenario, "mode": mode, "outcome": outcome,
                "session": list(platform.session),
                "honest_issuer": idp.issuer, "hostile_issuer": hostile.issuer,
                "business_issuer": biz.issuer, "subject_token": subject_token,
                "oracle": {"hostile": list(hostile.recorder.requests),
                           "idp": list(idp.recorder.requests),
                           "business": list(biz.recorder.requests)}}
    finally:
        for a in (idp, hostile, biz):
            a.stop()


if __name__ == "__main__":
    import json
    sc = sys.argv[1] if len(sys.argv) > 1 else "hostile_first"
    md = sys.argv[2] if len(sys.argv) > 2 else CONFORMANT
    print(json.dumps(run_scenario(sc, md), indent=1))

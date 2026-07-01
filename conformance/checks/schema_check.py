#!/usr/bin/env python3
"""
schema_check.py — Phase 2 helper: fixture-based checks validated by the schema oracle.

2026-04-08 has no live reference server, so its checks run against synthetic
fixtures: a check loads a hand-built VALID response fixture and validates it through
the official `ucp-schema` oracle (schema_oracle.py). The engine's mutation kill-rate
then corrupts that fixture (drop a required field, etc.) and re-validates — each
mutant MUST become schema-invalid, proving the check catches the specific defect.

A fixture is a normal JSON response carrying `ucp.capabilities` with the capability's
`schema` URL (so the oracle resolves it against the pinned 04-08 schemas), plus the
response body. Fixtures live in conformance/selfcheck/fixtures/<version>/.
"""
import sys, os, json, tempfile, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))
from engine import Resp                      # noqa: E402
import schema_oracle                         # noqa: E402
from verdict_gate import CLEAN, DEVIATION, INCONCLUSIVE  # noqa: E402

FIX = HERE.parents[0] / "selfcheck" / "fixtures"

def fixture_resp(version, name):
    """Load a fixture file into an engine Resp (so mutations apply uniformly)."""
    return Resp(200, {"Content-Type": "application/json"},
                (FIX / version / name).read_bytes())

def schema_predicate(op, direction, version="2026-04-08"):
    """Return predicate(resp) -> CLEAN | DEVIATION | INCONCLUSIVE that schema-validates
    the response body via the official ucp-schema oracle. A corrupt/empty body (json
    is None) is DEVIATION (a well-formed response is a precondition of validity)."""
    def pred(resp):
        if resp.json is None:
            return DEVIATION
        tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        try:
            json.dump(resp.json, tf); tf.close()
            ok, _ = schema_oracle.validate(tf.name, op,
                                           request=(direction == "request"),
                                           response=(direction == "response"),
                                           version=version)
        except schema_oracle.OracleUnavailable:
            return INCONCLUSIVE
        finally:
            os.unlink(tf.name)
        return CLEAN if ok else DEVIATION
    return pred

def fixture_check(cid, req_ids, keyword, version, fixture, op, direction, mutations):
    """Convenience factory for a fixture-based schema check."""
    from engine import Check
    return Check(cid, req_ids, keyword,
                 lambda base, f=fixture: fixture_resp(version, f),
                 schema_predicate(op, direction, version), mutations)

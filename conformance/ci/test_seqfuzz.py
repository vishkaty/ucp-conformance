#!/usr/bin/env python3
"""D4-07 (E3) — stateful sequence fuzz gate: conformance/ci/seq_invariants.py (I1..I9 + I10
`replay_reserialized`, THE single implementation of REPLAY-002 that D1-16b's Row and D3-23's
mutant import), conformance/ci/replay_mode.json (decision 1: `enforce` in golden gates, `report`
in user-facing runs until F3-f is answered or `report_until` passes), seqfuzz_gate.py (--selftest with
planted stubs; --boot disposable golden on a free port + mktemp DB_DIR; --races N).

Failing-first: neither module exists (ModuleNotFoundError) and the gate script is absent.

Run:  python3 -m pytest conformance/ci/test_seqfuzz.py -q
"""
import datetime
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GATE = HERE / "seqfuzz_gate.py"


def test_invariants_module_has_i1_to_i10_with_anchors():
    import seq_invariants as si
    assert [f"I{i}" for i in range(1, 11)] == list(si.INVARIANTS)
    for iid, (fn, anchor) in si.INVARIANTS.items():
        assert callable(fn) and anchor, iid
    assert si.INVARIANTS["I10"][0] is si.replay_reserialized     # the single REPLAY-002 implementation


def test_replay_mode_resolves_per_context_and_self_expires():
    import seq_invariants as si
    mode = si.load_replay_mode()
    assert mode["golden_gates"] == "enforce" and mode["user_facing"] == "report"
    # Boundary-relative, never literal dates: the literal that used to stand here
    # encoded the old report_until and inverted when decision 1c moved the window.
    until = datetime.date.fromisoformat(mode["report_until"])
    day = datetime.timedelta(days=1)
    assert si.resolve_mode("golden_gates", today=until + day) == "enforce"   # never self-expires
    assert si.resolve_mode("user_facing", today=until - day) == "report"
    assert si.resolve_mode("user_facing", today=until) == "report"           # inclusive
    assert si.resolve_mode("user_facing", today=until + day) == "enforce"    # self-expires
    # The VALUE axis too: synthetic documents whose values DIFFER from the committed file.
    # Deriving every expectation from load_replay_mode() only confirms the values the file
    # happens to hold, and a resolve_mode ignoring the document would still pass. The flip
    # to enforce once F3-f is answered IS an edit to this document, so it is under test.
    syn = {"user_facing": "enforce", "golden_gates": "report", "report_until": "2026-12-31",
           "golden_pending": {"task": "D3-23", "until": "2026-12-31"}}
    probe = datetime.date(2026, 12, 1)
    assert si.resolve_mode("user_facing", today=probe, mode=syn) == "enforce"
    assert si.resolve_mode("golden_gates", today=probe, mode=syn) == "report"
    # report_until governs user_facing; golden_pending.until is a separate clock and must
    # not be substituted for it (the two are equal in the committed file by decision 1b/1c).
    split = {"user_facing": "report", "golden_gates": "enforce", "report_until": "2027-06-30",
             "golden_pending": {"task": "D3-23", "until": "2026-01-01"}}
    assert si.resolve_mode("user_facing", today=probe, mode=split) == "report"


def test_pending_task_lapses_after_golden_pending_until():
    """Decision 1c moved `golden_pending.until` and nothing probed it, so the PENDING
    D3-23 exemption could not be shown to lapse. An exemption that cannot expire is the
    silently vacuous kind this suite forbids."""
    import seq_invariants as si
    import seqfuzz_gate as gate
    rm = si.load_replay_mode()
    gp = rm["golden_pending"]
    until = datetime.date.fromisoformat(gp["until"])
    day = datetime.timedelta(days=1)
    assert gate.pending_task(rm, today=until) == gp["task"]        # inclusive, still pending
    assert gate.pending_task(rm, today=until + day) is None        # lapses the day after
    assert gate.pending_task({}, today=until) is None              # no golden_pending at all


def test_selftest_catches_the_four_planted_violations_and_control_is_quiet():
    r = subprocess.run([sys.executable, str(GATE), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4/4 planted violations caught" in r.stdout and "control quiet" in r.stdout, r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_i9_triad_contract_for_the_envelope_mchecks():
    """W1 integration (D1-16a <-> D4-07): merchant_checks_08_25_envelope.py (CHK-048/CHK-078)
    imports the I9 idempotency triad from THIS module by name — the three function
    names/signatures are the cross-lane contract; D4's module is the one implementation.
    Each returns (ok: bool, detail: str) over plain status ints + decoded JSON bodies."""
    import seq_invariants as si
    first = {"id": "chk_1", "status": "completed", "order": {"id": "ord_1"}}
    assert si.replay_cached(200, first, 200, dict(first)) == (True, "cached result replayed")
    ok, detail = si.replay_cached(200, first, 200, {**first, "order": {"id": "ord_2"}})
    assert not ok and "ord_2" in detail and "second order" in detail
    assert not si.replay_cached(200, first, 201, dict(first))[0]           # status must match the cache
    env = {"ucp": {"version": "2026-08-25", "status": "error"},
           "messages": [{"type": "error", "code": "idempotency_conflict", "content": "x", "severity": "unrecoverable"}]}
    assert si.mismatch_409(409, env)[0] and not si.mismatch_409(200, env)[0] and not si.mismatch_409(409, {})[0]
    assert si.terminal_rejected(409, env)[0] and not si.terminal_rejected(200, first)[0] and not si.terminal_rejected(404, {})[0]
    for name in ("replay_cached", "mismatch_409", "terminal_rejected"):
        assert name in si.I9_ANCHORS and si.I9_ANCHORS[name], name

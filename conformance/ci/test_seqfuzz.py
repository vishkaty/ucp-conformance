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


def test_loopback_is_literal_and_port_is_outside_the_ephemeral_range():
    """A self-connect produced every I8 transport drop we have ever recorded.

    `free_port()` bound to port 0, so the disposable golden landed INSIDE the ephemeral
    range; the base URL said `localhost`, which resolves to ::1 first on this machine; and
    uvicorn binds 0.0.0.0, so nothing listens on [::1]:P. The kernel was therefore free to
    pick P as the SOURCE port for that v6 attempt, and a connect from ::1:P to ::1:P
    completes a simultaneous open with itself. The client then reads back its own request
    line, which is exactly the recorded BadStatusLine text. Rate 1/16384 against a measured
    1 in 14k to 22k. Both halves are pinned here: the literal address removes the v6
    attempt, and the port range removes the collision even if someone restores `localhost`."""
    import seqfuzz_gate as gate
    port = gate.SEQFUZZ_PORT
    assert gate.base_url(port) == f"http://127.0.0.1:{port}", gate.base_url(port)
    assert "localhost" not in gate.base_url(port)
    # Outside every common ephemeral range is the protection, not randomness: a port the
    # kernel cannot choose as a SOURCE port cannot be self-connected to.
    assert not (49152 <= port <= 65535), f"{port} is inside the macOS ephemeral range"
    assert not (32768 <= port <= 60999), f"{port} is inside the Linux ephemeral range"
    assert gate.free_port() == port


def test_a_transport_drop_counts_once_not_twice():
    """One BadStatusLine drop used to raise TWO violations: I8 for the transport failure and
    I9 because the same-bytes replay 'answered 0 but the original was 200'. Measured on the
    2026-10-18 re-record: same request, same checkout id, same -> 0. A response that never
    arrived cannot be graded for replay semantics, so a transport failure is I8 only."""
    import seq_invariants as si
    step = {"op": "replay_same", "status": 0, "transport_error": "BadStatusLine",
            "prev_status": 200, "same_bytes": True, "method": "PUT",
            "path": "/checkout-sessions/x"}
    ids = [iid for iid, _ in si.check(step)]
    assert ids == ["I8"], ids

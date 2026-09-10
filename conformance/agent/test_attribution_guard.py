#!/usr/bin/env python3
"""
test_attribution_guard.py — agent-axis attribution requires RUN EVIDENCE (D5-04 / A12,
decision 10). An ACheck counts as CHECK at spec version V only when
conformance/agent/agent_run_evidence.json records a green run of that check against a
sandbox serving V, no older than 30 days, at V's current spec pin. Without evidence the
id is a GAP — the 51 req_ids that 40 checks carried to 2026-08-25 by extending `versions`
(no 08-25 sandbox exists) are the concrete case: they read CHECK 51 / 25% on the public
agent-coverage panel with zero runs behind them.

Run: cd conformance/agent && python3 test_attribution_guard.py   (unittest; hermetic)
"""
import datetime, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_matrix        # noqa: E402
import agent_checks        # noqa: E402

TODAY = datetime.date(2026, 9, 10)
PINS = {"2026-04-08": "a2d8bf0b8f5a6fc790f677899c2c7da0684fe33d",
        "2026-08-25": "cd78fb38e819de77d9b527d110476eccb876f1bd"}
PKCE = next(c for c in agent_checks.CHECKS if c.id == "agent.uses_pkce")   # IDL-011 at 04-08 + 08-25


def ev(check_id, ver, days_old=0, pin=None):
    return {check_id: {ver: {"date": (TODAY - datetime.timedelta(days=days_old)).isoformat(),
                             "spec_pin": pin or PINS[ver]}}}


class AttributionGuard(unittest.TestCase):
    def test_0825_check_without_run_evidence_is_gap(self):
        """No evidence file/entry at 2026-08-25 → NOTHING is a CHECK there (default suppress)."""
        ids = agent_matrix.agent_check_ids("2026-08-25", evidence={}, today=TODAY, pins=PINS)
        self.assertEqual(ids, set(), f"08-25 ids counted as CHECK with no run evidence: {sorted(ids)[:6]}…")

    def test_stale_evidence_is_gap(self):
        """A 31-day-old entry is outside the freshness window → GAP."""
        ids = agent_matrix.agent_check_ids("2026-04-08", evidence=ev(PKCE.id, "2026-04-08", days_old=31),
                                           today=TODAY, pins=PINS)
        self.assertNotIn("IDL-011", ids)

    def test_evidence_at_other_pin_is_gap(self):
        """Evidence recorded at a different spec pin does not carry to the current pin."""
        ids = agent_matrix.agent_check_ids("2026-04-08", evidence=ev(PKCE.id, "2026-04-08", pin="deadbeef"),
                                           today=TODAY, pins=PINS)
        self.assertNotIn("IDL-011", ids)

    def test_fresh_evidence_at_pin_counts(self):
        """Positive control: a fresh entry at the current pin counts, at THAT version only."""
        e = ev(PKCE.id, "2026-04-08", days_old=29)
        self.assertIn("IDL-011", agent_matrix.agent_check_ids("2026-04-08", evidence=e, today=TODAY, pins=PINS))
        self.assertNotIn("IDL-011", agent_matrix.agent_check_ids("2026-08-25", evidence=e, today=TODAY, pins=PINS))


class GovernanceEvidenceCheck(unittest.TestCase):
    """agent_governance's EVIDENCE check is the ONLY governance check for this (D3-11
    writes evidence, adds none): every (check, version) attribution must be backed by
    fresh evidence at the current pin, or the version must be a DECLARED unrun version."""
    def test_planted_evidence_less_attribution_is_red_naming_the_id(self):
        import agent_governance
        fails = agent_governance.evidence_failures([PKCE], evidence={}, today=TODAY, pins=PINS, unrun={})
        self.assertTrue(any(PKCE.id in f and "2026-04-08" in f for f in fails), fails)

    def test_declared_unrun_version_is_not_red(self):
        import agent_governance
        unrun = {"2026-08-25": {"reason": "no 08-25 sandbox until D3-11", "review_by": "2026-12-09"}}
        fails = agent_governance.evidence_failures([PKCE], evidence=ev(PKCE.id, "2026-04-08"),
                                                   today=TODAY, pins=PINS, unrun=unrun)
        self.assertEqual(fails, [])

    def test_stale_window_evidence_is_red(self):
        import agent_governance
        fails = agent_governance.evidence_failures([PKCE], evidence=ev(PKCE.id, "2026-04-08", days_old=31),
                                                   today=TODAY, pins=PINS,
                                                   unrun={"2026-08-25": {"reason": "x", "review_by": "2026-12-09"}})
        self.assertTrue(any(PKCE.id in f and ("stale" in f or "31" in f) for f in fails), fails)


if __name__ == "__main__":
    unittest.main(verbosity=1)

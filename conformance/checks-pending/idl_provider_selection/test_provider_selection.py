#!/usr/bin/env python3
"""
test_provider_selection.py: the reference gate for the pending provider selection checks.
For every check: CLEAN on the conformant reference platform (select_by_issuer) and
DEVIATION on the reference platform with the check's kill_mutation switched on, with the
evidence naming the leak or the wrong selection. Run:
  python3 -m unittest conformance/checks-pending/idl_provider_selection/test_provider_selection.py -v
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checks import CHECKS, CLEAN, DEVIATION          # noqa: E402
from harness import run_scenario                     # noqa: E402
from reference_platform import CONFORMANT, MODES     # noqa: E402

BY_ID = {c.id: c for c in CHECKS}


def _run(cid, mode):
    return run_scenario(BY_ID[cid].scenario, mode)


class ProviderSelectionReferenceGate(unittest.TestCase):
    # ---- check 1: never presents subject_token to an unmatched endpoint -------------
    def test_never_presents_clean_on_conformant(self):
        c = BY_ID["pending.idl.never_presents_subject_token_to_unmatched_endpoint"]
        v, ev = c.predicate(_run(c.id, CONFORMANT))
        self.assertEqual((v, ev), (CLEAN, ""))

    def test_never_presents_red_on_select_by_key(self):
        c = BY_ID["pending.idl.never_presents_subject_token_to_unmatched_endpoint"]
        run = _run(c.id, c.kill_mutation)
        v, ev = c.predicate(run)
        self.assertEqual(v, DEVIATION, ev)
        # the right reason: the hostile token endpoint holds the subject token and the credential
        posts = [r for r in run["oracle"]["hostile"] if r["method"] == "POST"]
        self.assertTrue(posts and posts[0]["form"]["subject_token"] == run["subject_token"], run["oracle"]["hostile"])
        self.assertTrue(posts[0]["authorization"].startswith("Basic "), posts[0])

    # ---- check 2: selects only the recognized issuer -----------------------------------
    def test_selects_only_recognized_clean_on_conformant(self):
        c = BY_ID["pending.idl.selects_only_recognized_issuer"]
        run = _run(c.id, CONFORMANT)
        self.assertEqual(c.predicate(run), (CLEAN, ""))
        self.assertEqual(run["outcome"], "chained")

    def test_selects_only_recognized_red_on_select_by_key(self):
        c = BY_ID["pending.idl.selects_only_recognized_issuer"]
        run = _run(c.id, c.kill_mutation)
        v, ev = c.predicate(run)
        self.assertEqual(v, DEVIATION, ev)
        self.assertIn("not the recognized issuer", ev)

    # ---- check 3: exact match, a trailing slash lookalike is not selected --------------
    def test_exact_match_clean_on_conformant(self):
        c = BY_ID["pending.idl.exact_match_no_normalization"]
        run = _run(c.id, CONFORMANT)
        self.assertEqual(c.predicate(run), (CLEAN, ""))
        self.assertEqual(run["outcome"], "fallback_direct_oauth")

    def test_exact_match_red_on_normalize_issuer(self):
        c = BY_ID["pending.idl.exact_match_no_normalization"]
        run = _run(c.id, c.kill_mutation)
        v, ev = c.predicate(run)
        self.assertEqual(v, DEVIATION, ev)
        # A normalizing platform selects the lookalike and exchanges its subject_token at the
        # IdP; the business then rejects the grant because iss (no slash) is not byte identical
        # to the listed auth_url (with slash), the IDL-071 mirror of the rule. Either way the
        # entry was selected and an exchange happened, which is the deviation.
        self.assertIn(run["outcome"], ("chained", "chain_rejected"), run["outcome"])
        self.assertTrue([e for e in run["session"] if e["op"] == "token_exchange"], "expected an exchange")

    # ---- check 4: falls back when nothing matches --------------------------------------
    def test_fallback_clean_on_conformant(self):
        c = BY_ID["pending.idl.falls_back_when_nothing_matches"]
        self.assertEqual(c.predicate(_run(c.id, CONFORMANT)), (CLEAN, ""))

    def test_fallback_red_on_select_by_profile(self):
        c = BY_ID["pending.idl.falls_back_when_nothing_matches"]
        run = _run(c.id, c.kill_mutation)
        v, ev = c.predicate(run)
        self.assertEqual(v, DEVIATION, ev)
        self.assertTrue(run["oracle"]["hostile"], "the profile trusting platform must have reached the hostile AS")

    # ---- every defect mode is caught by at least one check (the kill matrix) ----------
    def test_every_defect_mode_is_caught(self):
        for mode in MODES:
            if mode == CONFORMANT:
                continue
            caught = [c.id for c in CHECKS if c.predicate(run_scenario(c.scenario, mode))[0] == DEVIATION]
            self.assertTrue(caught, "defect mode %s escaped every check" % mode)


if __name__ == "__main__":
    unittest.main(verbosity=2)

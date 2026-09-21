# Checks pending an upstream spec change

Reference test systems for normative text that is proposed upstream but not yet
merged into any UCP spec version. This directory is deliberately a sibling of
`checks/` and `agent/`: the merchant coverage map globs `checks/*.py` only and the
agent lane imports `agent/agent_checks.py` only, so nothing here feeds the build,
the coverage page, the ratchet, the locks, or any published number. Rows here are
preparation, not certification claims, exactly as `requirements-drafts/` is for
registers.

## Why this exists

When we propose a normative rule upstream, the PR body must point at evidence that
the rule is testable and that a violating implementation is caught. That evidence
has to exist before the text merges, and it cannot be graded against a pinned
version because the clause is not in one yet. So it lives here, keyed to the
upstream branch commit that carries the text, with the same kill discipline as the
governed lanes: every check passes on a conformant reference and fails on a
reference with the defect switched on, and every check is proven non vacuous by
excising its assertions and watching the kill leg go red.

## Layout

`checks-pending/<topic>/` holds one self contained test system: mock actors, a
reference implementation with a defect switch, the checks, a unittest module and a
kill test runner. Its register rows live in
`requirements-drafts/next/<area>-<topic>.json` with `_status: "proposed"` and
`_spec_commit` set to the upstream branch commit the quotes were taken from.

## Promotion protocol (when the upstream text merges and a version tags)

1. Re verify every quote verbatim at the tagged SHA and move the rows into
   `requirements/<version>/` per the `requirements-drafts/` protocol.
2. Port the checks into the governed lane (`agent/agent_checks.py` for platform
   bound rows), wire the sandbox scenarios and defects, and take them through the
   reference gate, review sign off, coverage lock and ratchet like any other check.
3. Delete the topic directory here. This directory should be empty between
   upstream proposals.

## Topics

- `idl_provider_selection/`: issuer anchored provider selection for the
  Accelerated IdP Flow (ucp issue #667 Gap 2). Run:
  `python3 -m unittest conformance/checks-pending/idl_provider_selection/test_provider_selection.py`
  and `python3 conformance/checks-pending/idl_provider_selection/killtest.py`.

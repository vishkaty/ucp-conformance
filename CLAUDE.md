# ucp-conformance

A conformance checker for the Universal Commerce Protocol (UCP), validated by kill
rate: every check has a negative fixture that proves it can fail. This file is the
project guide for anyone working in the tree; a private companion,
`CLAUDE.local.md`, holds operator detail and is not committed.

## Layout

- `conformance/` the suite: `checks/`, `requirements/`, `coverage/`, `selfcheck/`,
  `testbed/`, and `ci/` (the gates). `conformance/.vendor/` holds pinned upstream
  clones the suite runs against; it is ignored by git and rebuilt by the tooling.
  The pins are deliberate and changed only together with a revalidation.
- `packaging/` build and release tooling. `public/` the published site and coverage
  data. `docs/` and `api/` documentation and the API surface. `tests/` includes the
  browser smoke tests for the public pages.
- `ops/` is a separate, private repository and is ignored here.

## How to run things

- `bash packaging/preflight.sh [vX.Y.Z]` gates a change or a release end to end:
  selftest, coverage freshness with a no-regression ratchet, copy freshness,
  coverage lock, responsive web. Run this rather than its pieces.
- `bash conformance/ci/serve_golden.sh` boots the reference server the suite needs.
  Do not boot it by hand; a hand-started server lacks the simulation secret and
  fails for the wrong reason.
- `conformance/ci/run_suite.py` runs the gate table described in
  `conformance/ci/README.md`.
- `conformance/ci/differential.py` is the fixture-circularity gate. It is not a
  spec-diff tool; do not repurpose it.

## Conventions

- Test-driven: write the failing test, watch it fail for the right reason, make it
  pass, then remove the fix and confirm the test goes red. A check that has never
  killed a mutant proves nothing.
- Only test behaviour the reference server actually implements; probe it first.
  Excluded cases are documented with the reason. All-skip tests are not allowed.
- Verify against the schema oracle (`ucp-schema`), not against assumption. Read
  annotations at the version the reference serves, not at spec main.
- Pre-push gate is `bash packaging/preflight.sh`; it runs every selftest gate and the
  clean-tree check. Upstream PRs prepared from here use the target repo pinned
  `pre-commit run --all-files`, where formatting is a separate hook from linting.
- Commits are authored by a named person with a real email address.
- Reports name ecosystem pitfalls, never people. State the observation, the evidence,
  and the expected behaviour, with a link.

# Changelog — spck-conformance (add-only)

Every release, pre-releases included, gets an entry BEFORE its tag: `packaging/release_guards.sh`
refuses a version without one, and a FINAL tag is refused until its rc entry carries a recorded
green acceptance line (`acceptance: green <date> <sha> (…)`) — decision 17, rc first
(PLAN-v3 §2.18). `packaging/release_rc.sh` reads the release TARGET from the first non-rc
heading below. Entries are never rewritten; corrections are new lines.

## 0.4.0 — 2026-09-10

Behaviour changes since 0.3.1 (the three the Wave 0 plan names):
- The 2026-08-25 requirement register ships in the wheel (four spec versions:
  2026-01-11, 2026-01-23, 2026-04-08, 2026-08-25); the wheel guard derives the set from
  `conformance/common/spec_versions.VERSIONS`.
- C1 CLI summary: no applicable-MUST fraction is reported for a served version the runner
  does not support; the JSON gains `checks_summary` and `support` (D1-03).
- Converting-honest coverage: publication state follows rule R-a (`live` only with zero
  testable-tier gap; `converting` otherwise) — 2026-08-25, 2026-01-11 and 2026-01-23 read
  `converting`; the site lists every version with a registered check set as supported (all four), independent of the live/converting state (D2-06/D5-03, B3).
Also: the GitHub Action installs from its own checkout (`source: checkout`, default), so
`uses: vishkaty/ucp-conformance@v0.4.0` runs exactly this engine; README examples pinned.
acceptance: 0.4.0rc1 accepted green 2026-09-10 c6cb0a2 (see the 0.4.0rc1 entry below); released as 0.4.0 from the same tree

## 0.4.0rc1 — 2026-09-10 (published; PNR-3 first step; pre-release — invisible to plain `pip install`)

- Identical content to 0.4.0 above, published as a PEP 440 pre-release for the clean-venv
  acceptance: `python3 -m venv /tmp/v && /tmp/v/bin/pip install spck-conformance==0.4.0rc1 &&
  /tmp/v/bin/spck-conformance --server http://localhost:8197 --json` → `verdict.deviations == 0`
  against golden-0825 (`boot_golden_0825`, port 8197).
acceptance: green 2026-09-10 c6cb0a2 (clean venv, PyPI 0.4.0rc1, golden-0825 :8197 with the reference config: 29 clean-pass / 0 deviations / rc 0; 4 spec versions bundled)

## 0.3.1 — 2026-08 (published)

- Last release before the 2026-08-25 register; three spec versions bundled. Superseded by 0.4.0.

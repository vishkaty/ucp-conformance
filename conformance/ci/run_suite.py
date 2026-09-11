#!/usr/bin/env python3
"""
run_suite.py — the TDD / CI entrypoint: "the test suite for the test suite".

Runs every self-validation gate we have, in one shot, and returns a single red/green
verdict. This is what you run before every change (and what CI runs on every push):
if a change to a check, the register, or the engine breaks soundness, this goes red.

Gates (each anchored to something we did NOT write, to avoid circularity):
  register    verify_register.py     — every register row quotes the pinned spec verbatim
  verdict     verdict_gate.py        — the no-false-green gate's own unit tests
  schema      schema_oracle.py       — our schema checks match the official ucp-schema validator
  dual-oracle validate_dual_oracle.py — every schema check runs BOTH the Rust oracle and an
                                        independent Python referee; verdict divergence alarms
  merchant    validate_merchant_checks.py — every merchant check is clean-pass + kill_safe on a golden
  suite-01-23 run_01_23.py           — the 2026-01-23 suite vs a live golden (no false green)
  suite-04-08 run_04_08.py           — the 2026-04-08 fixture checks (schema-oracle backed)
  proxy-demo  mutation_proxy_demo.py — the mutation PROXY demo: injected wire defects are
                                        caught (2 real checks + the noop canary); the
                                        per-check kill-rate proof is the merchant gates

Server-dependent gates are skipped (not failed) when no golden is reachable, unless
--require-server. The schema gate skips if the ucp-schema binary isn't built (exit 2).

R11 golden-0825 mutant battery (D1-09): a COUNTED gate, `battery-freshness`. In CI
run with --battery so the battery runs inside this invocation (its report is the
in-run source the gate prefers); locally without --battery the gate reads the tracked
conformance/testbed/golden-0825/battery/LAST_RUN.json under a 14-day rule (the owner
commits it on the release path — decision 24: artifacts, no bot commits).

Usage:
    python3 conformance/ci/run_suite.py [--server http://localhost:8182]
                                        [--require-server] [--skip schema,proxy-demo]
                                        [--only NAME[,NAME...]] [--battery]
Exit 0 = all run gates passed; 1 = a gate failed (or a required server was missing);
2 = --only named a gate that is not in the table (never a silent full run or no-op).

--only runs exactly the named gates, in table order, booting ONLY the fixtures those
gates need (so every acceptance command written as `run_suite.py --only <gate>` is
runnable as written, cheaply, and proves the gate it names — pinned by
conformance/ci/validate_run_suite_only.py, gate `run-suite-only`).
"""
import sys, subprocess, argparse, pathlib, urllib.request, time, os, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SELF = ROOT / "conformance" / "selfcheck"
CHK = ROOT / "conformance" / "checks"
SPECLINT = ROOT / "conformance" / "speclint"
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant"
CONTROLLED_PORT = 8184
CONTROLLED = f"http://localhost:{CONTROLLED_PORT}"
CONTROLLED_0123_PORT = 8185
CONTROLLED_0123 = f"http://localhost:{CONTROLLED_0123_PORT}"
CONTROLLED_0111_PORT = 8193          # 8186-8188 sig gate, 8189 static web, 8190/8191 webhook harness
CONTROLLED_0111 = f"http://localhost:{CONTROLLED_0111_PORT}"
PROXY_PORT = 8183
PROXY = f"http://localhost:{PROXY_PORT}"
GOLDEN_0825_PORT = 8197                 # conformance/ci/ports.json (D5-19): the gate golden
GOLDEN_0825 = f"http://localhost:{GOLDEN_0825_PORT}"
GOLDEN_0825_DIR = ROOT / "conformance" / "testbed" / "golden-0825"

def _py(path, *args):
    return [sys.executable, str(path), *args]

# D1-07: run-scoped directory for the merchant gates' run records (validate_merchant_checks
# --record); validate_dormancy.py unions them AFTER the four merchant gates. Override with
# RUN_SUITE_RECORD_DIR to keep the records (the dormancy kill-proof replays them).
RECORD_DIR = pathlib.Path(os.environ.get("RUN_SUITE_RECORD_DIR")
                          or tempfile.mkdtemp(prefix="run_suite_records_"))

def gates(server, require_server=False):
    # (name, argv, needs: None|"golden"|"controlled", skip_exit_codes)
    # require_server also turns a missing TOOLCHAIN into a failure for the gates
    # that name it (golden-0825-unit: `uv`), not just a missing server.
    rec = lambda name: ("--record", str(RECORD_DIR / f"{name}.json"))   # noqa: E731
    return [
        ("register",    _py(SELF / "verify_register.py"),                       None, ()),
        # D2-02: hermetic kill-tests behind the `register` gate's duplicate-pair and
        # manual-but-CHECK checks (synthetic rows), and the merchant check set's
        # unique-id invariant (reach_report / probe-hygiene key by check id).
        ("register-selftest", _py(SELF / "verify_register.py", "--selftest"),   None, ()),
        ("merchant-checks-selftest", _py(SELF / "validate_merchant_checks.py", "--selftest"), None, ()),
        # D2-08: the role seed/heuristic (requirements/tools/assign_roles.py) reproduces its
        # 7-row fixture — lock/NAB/client-bound/subject/direction/queue — hermetically; the
        # roles themselves are gated by `register` (field, enum, lock consistency both
        # ways, empty review queue, agent-side one-lane rule, area map as register data).
        ("roles-selftest", _py(ROOT / "conformance" / "requirements" / "tools" / "assign_roles.py", "--selftest"), None, ()),
        ("register-complete", _py(SELF / "verify_register_completeness.py"),     None, ()),
        # D2-09 (A15): every 2026-04-08 row is accounted exactly once in 2026-08-25
        # (carried/renamed/reworded/dead/merged/downgraded); carried quotes byte-fresh or
        # drift-noted; dead rows dead-proven by search term; merged targets exist; the
        # D2-15 backport (2026-08-25 -> 2026-04-08) checked in reverse. Hermetic.
        ("carry-forward", _py(SELF / "verify_carry_forward.py"),                 None, ()),
        ("carry-forward-selftest", _py(SELF / "verify_carry_forward.py", "--selftest"), None, ()),
        # D2-10 (decision 8): SHOULD-class census, REPORT-ONLY — always rc 0; its totals
        # reach coverage.json surface.should (byte-compared by `coverage`, pinned by
        # `evidence-class`). The selftest proves the scan on a fixture.
        ("should-census", _py(SELF / "verify_should_census.py"),                 None, ()),
        ("should-census-selftest", _py(SELF / "verify_should_census.py", "--selftest"), None, ()),
        # D2-16: the DONE-2 status report (coverage/done2_status.py) is a program-review
        # artefact, never a gate — but its evaluator must provably go red: scratch inputs
        # flip items 2 (merchant-lane GAP) and 8 (expired clock). Hermetic.
        ("done2-selftest", _py(ROOT / "conformance" / "coverage" / "done2_status.py", "--selftest"), None, ()),
        ("citations",   _py(SELF / "verify_citations.py"),                      None, ()),
        # R13: the completeness matcher's coverage decision (register-complete above)
        # must not silently regress to the pre-fix per-physical-line algorithm, which
        # missed a covered line whenever a row's "..." elision landed mid-physical-line
        # (proven on IDL-012/030/050). Hermetic; carries a frozen copy of the old
        # algorithm as its own kill-test mutant, plus a class negative against
        # over-matching.
        ("completeness-matcher", _py(SELF / "validate_completeness_matcher.py"),  None, ()),
        # G0-c (PLAN-0825 A.3): the schema-constraint denominator proof — every
        # schema/service file in the pinned corpus is either referenced by a
        # register row or carried in a recorded, hash-pinned ruling, plus the
        # one-time fenced-hit audit. REPORT-ONLY by default (real findings never
        # fail this gate — 153 files are honestly unreferenced today, expected
        # until L2 lands schema_enforced rows; a gate flip to --enforce is a later,
        # deliberate step per PLAN-0825 G0-c/§G). Still fails on malformed ruling
        # data (our own data-hygiene bugs), which is real regardless of mode.
        # P3 conversion kickoff (2026-08-31, lane/p3-convert): NEW 2026-08-25 register
        # rows checkable with neither a live golden nor the ucp-schema CLI oracle
        # (blocked pre-#66 — G6). Self-referenced evidence class: the judge is our
        # own implementation of an algorithm the released prose/schema specifies
        # exactly, kill-tested per-check (valid must accept, negatives must reject —
        # schema_check_04_08.py idiom). Deliberately unattributed: not wired into
        # checkset_manifest/matrix/coverage — the coverage/site flip is a separate,
        # owner-visible step (PLAN-0825 §G conversion-phase discipline).
        ("struct-check-08-25", _py(CHK / "struct_check_08_25.py"),                None, ()),
        # P3 wave 2 (2026-08-31, lane/p3-wave2): the golden-reading counterpart to
        # struct-check-08-25 above — rows checkable ONLY against a live golden-0825
        # server, unblocked by the R11 defect-injection battery landing. HERMETIC
        # (boots its own golden-0825 instance, own port 8198 — never 8182, never
        # the R11 battery's 8199) so it needs no --server and no already-running
        # fixture, matching the struct-check precedent; skip_exit_code 2 mirrors
        # every other oracle-backed gate (schema/fixture/wrapper/schema-01-23/
        # schema-04-08) when the ucp-schema binary/vendor tree isn't built. Every
        # row's kill-proof is a NAMED mutant from server/defects_config.json
        # (either the shared oracle-graded "mutants" array or the new
        # "self_referenced_mutants" array for rows the released schema cannot
        # itself enforce — see that file's own $comment and the module docstring
        # here). Deliberately unattributed, same discipline as struct-check-08-25.
        ("golden-check-08-25", _py(CHK / "golden_check_08_25.py"),                None, (2,)),
        # D3-29: golden-0825's OWN unit + smoke tests (server/*_test.py, smoke/) are
        # executed here, not merely present -- every D3 task's failing-first test is a
        # server or smoke test, so an unexecuted suite would let a red one sit in the
        # tree (RV2 #10). Runs `uv run --group dev pytest -q ../smoke .` in the server
        # dir (the smoke fixture boots on 8194, golden-0825's own registered port);
        # rc 2 = `uv` absent -> honest SKIP, FAIL under --require-server. The
        # selftest row plants a failing test in a scratch copy (must be red) and
        # hides `uv` (must be rc 2), so the gate itself stays kill-proven.
        ("golden-0825-unit", _py(SELF / "validate_golden_unit_gate.py"),          None,
         () if require_server else (2,)),
        ("golden-0825-unit-selftest", _py(SELF / "validate_golden_unit_gate.py", "--selftest"), None, ()),
        ("schema-census", _py(SELF / "verify_schema_census.py"),                  None, ()),
        # hermetic kill-tests for the census above: proves unreferenced-file
        # detection, stale-ruling (hash-diff self-expiry) detection, and a class
        # negative against over-firing, all independent of today's real findings.
        ("schema-census-killtest", _py(SELF / "validate_schema_census.py"),       None, ()),
        ("coverage-lock", _py(ROOT / "conformance" / "coverage" / "verify_coverage_lock.py"), None, ()),
        ("review-signoff", _py(ROOT / "conformance" / "coverage" / "verify_review_signoffs.py"), None, ()),
        # P-2 expiry clocks (D2-04/D2-19): every register entry carries review_by +
        # spec_pin; expired / pin-drifted / unclocked entries red the build. Hermetic.
        ("expiry-clocks", _py(SELF / "validate_expiry_clocks.py"),                None, ()),
        ("expiry-clocks-selftest", _py(SELF / "validate_expiry_clocks.py", "--selftest"), None, ()),
        ("coverage",    _py(ROOT / "conformance" / "coverage" / "coverage_gate.py"), None, ()),
        ("verdict",     _py(SELF / "verdict_gate.py"),                          None, ()),
        ("schema",      _py(SELF / "schema_oracle.py"),                         None, (2,)),
        ("fixture",     _py(FIXTURE / "selfcheck.py"),                          None, (2,)),
        ("wrapper",     _py(SELF / "test_wrapper.py"),                          None, (2,)),
        ("schema-01-23", _py(CHK / "schema_check_01_23.py"),                    None, (2,)),
        ("schema-04-08", _py(CHK / "run_schema_04_08.py"),                      None, (2,)),
        # every schema check cross-validated against an INDEPENDENT Python jsonschema
        # referee (full $id registry over all 78 schemas): the Rust oracle we otherwise
        # trust blindly can silently pass a malformed payload (ucp-schema#43 false-accepts
        # payment instruments), so both engines run and any VERDICT divergence alarms.
        # The #43 divergence reproduces on the pinned buggy oracle and is acknowledged +
        # self-expiring (known_oracle_divergences.json). --server: opportunistic live probe.
        ("dual-oracle", _py(SELF / "validate_dual_oracle.py", "--server", server), None, (2,)),
        # the divergence detector must provably catch a PLANTED divergence and a STALE
        # acknowledgement (a detector that never caught one proves nothing) + the referee's
        # lifecycle filter must match the official resolver. Hermetic kill-tests.
        ("dual-oracle-killtest", _py(SELF / "validate_dual_oracle.py", "--selftest"), None, (2,)),
        # D4-01 (B5a): the same gate at 2026-08-25 — 116-schema referee base, corpus captured
        # in-process from the pinned golden-0825 (selfcheck/fixtures/2026-08-25), the #43
        # boundary rebuilt on it, plus the `--def` self-root path where the pinned oracle
        # ABORTS (third verdict state "crash", acknowledged by ucp-schema-45-selfroot-def-crash);
        # every acknowledgement now also expires on `schema_validator_pin_not`. Hermetic.
        ("dual-oracle-0825", _py(SELF / "validate_dual_oracle.py", "--version", "2026-08-25"), None, (2,)),
        ("dual-oracle-0825-killtest", _py(SELF / "validate_dual_oracle.py", "--selftest", "--version", "2026-08-25"),
         None, (2,)),
        ("suite-04-08", _py(CHK / "run_04_08.py"),                              None, (2,)),
        ("merchant",    _py(SELF / "validate_merchant_checks.py", "--server", server, *rec("flower")),
         "golden", ()),
        # A 5xx from a conformant golden means our probe was malformed or the reference
        # crashed. Either way the verdict is not about the requirement the check names,
        # so it must not reach a merchant as a deviation.
        ("probe-hygiene", _py(SELF / "validate_probe_hygiene.py", "--server", server),
         "golden", ()),
        ("merchant-catalog", _py(SELF / "validate_merchant_checks.py",
                                 "--server", CONTROLLED, "--golden", "controlled",
                                 *rec("controlled-04-08")), "controlled", ()),
        ("merchant-ctrl-01-23", _py(SELF / "validate_merchant_checks.py",
                                    "--server", CONTROLLED_0123, "--golden", "controlled",
                                    *rec("controlled-01-23")),
         "controlled-01-23", ()),
        ("merchant-ctrl-01-11", _py(SELF / "validate_merchant_checks.py",
                                    "--server", CONTROLLED_0111, "--golden", "controlled",
                                    *rec("controlled-01-11")),
         "controlled-01-11", ()),
        # D1-07: every merchant check runs on SOME golden or is named in
        # dormancy_exemptions.json (floor 13). Unions the four records above; a missing
        # record is a partial union (red under --require-server, never a smaller set);
        # without --require-server an empty record set skips honestly (rc 2).
        ("dormancy",    _py(SELF / "validate_dormancy.py", "--records", str(RECORD_DIR),
                            *(["--require-server"] if require_server else [])), None, (2,)),
        # D1-09: the R11 battery must have run, recently, and passed — counted. --battery
        # runs it in THIS invocation (report copied to RECORD_DIR, preferred); otherwise
        # the tracked LAST_RUN.json under the 14-day rule.
        # D1-04: the merchant CLI vs golden-0825 (booted here on :8197 by boot_golden_0825)
        # in BOTH probe shapes — default (typed destinations) and --omit-destination-type —
        # must show 0 deviations and >= 29 checks run. Kill-proof for D1-01's 08-25 delta
        # (revert it -> red) and, once D3-04 lands, for the C3b default (arm
        # destination_type_required_on_request -> omit mode red).
        ("probe-shape-0825", _py(SELF / "validate_probe_shape_0825.py", "--server", GOLDEN_0825),
         "golden-0825", (2,)),
        ("battery-freshness", _py(SELF / "validate_battery_freshness.py",
                                  "--in-run", str(RECORD_DIR / "battery_LAST_RUN.json")), None, ()),
        ("schema-01-11-01-23", _py(CHK / "schema_check_01_11_01_23.py"),        None, (2,)),
        # the CLOSED testable tier can never silently reopen (wave-2 milestone)
        ("require-testable-04-08",
         _py(ROOT / "conformance" / "coverage" / "matrix.py",
             "--require", "testable", "--version", "2026-04-08"),               None, ()),
        # PLAN-0825 §E publication-state kill-tests: _version_state's `unregistered`
        # branch has no real trigger today (every pinned version already has a
        # register), so this proves the code path works rather than leaving it
        # aspirational. Hermetic (no I/O beyond the real matrix export sanity check).
        ("matrix-state-selftest",
         _py(ROOT / "conformance" / "coverage" / "matrix.py", "--selftest"),      None, ()),
        # PLAN-0825 G0-b kill-test: the version-map consolidation (conformance/common/
        # spec_versions.py) is genuinely the single source matrix/agent_matrix/
        # verify_register*/speclint all read — not five copies that happen to agree
        # today. Proves identity (no private copy crept back in), that a version
        # appended to the one source appears everywhere, that an unknown version
        # fails loud, that the concrete agent_matrix/speclint 2026-08-25 regression
        # stays fixed, and the adjacent stale-waiver fail-noisy fix (PLAN-0825 A.2).
        # Hermetic (reads the vendored trees already required by register-complete).
        ("spec-versions-selftest",
         _py(SELF / "validate_spec_versions.py"),                                None, ()),
        ("tls-check",   _py(SELF / "validate_tls_check.py"),                 "controlled", (2,)),
        ("sig-check",   _py(SELF / "validate_sig_check.py"),                    None, (2,)),
        ("oauth-check", _py(SELF / "validate_oauth_checks.py"),                  None, (2,)),
        ("order-auth-check", _py(SELF / "validate_order_auth_check.py"),         None, (2,)),
        # samples#122 regression watch: SIG-002 graded against the VENDORED reference
        # booted enforcing (clean-pass required) and permissive (deviation required),
        # so an upstream verification regression turns this red instead of invisible.
        ("sig002-reference", _py(SELF / "validate_sig002_reference.py"),         None, (2,)),
        # samples#140/#146 regression watch: order-webhook delivery graded against the
        # VENDORED reference (full order entity as body, clean-pass + kill_safe; a
        # non-delivering merchant deviates). The signing/retry tripwires this gate
        # once pinned FIRED at the 2026-08-13 re-pin (samples#169 implemented both)
        # and were retired: webhooks.signed/.retries are now ON in REF_CONFIG and
        # the flower differential config, graded live by validate_merchant_checks.
        ("webhook-reference", _py(SELF / "validate_webhook_reference.py"),       None, (2,)),
        ("checkout-scope-check", _py(SELF / "validate_checkout_scope_check.py"), None, (2,)),
        ("disc014-check", _py(SELF / "validate_disc014_check.py"),               None, (2,)),
        ("fillme-guard", _py(SELF / "validate_fillme_guard.py"),                 None, (2,)),
        # EVIDENCE-CLASS layer (P1-8): the published coverage split by evidence class
        # (live-wire / fixture-schema / fixture-crypto / self-referenced) must be
        # mechanically derived, kill-tested (a mislabeling classifier reds), must
        # PARTITION the CHECK bucket without moving any total, and the split in
        # site_claims.json must match a fresh export. Hermetic (reach report is
        # committed data).
        ("evidence-class", _py(SELF / "validate_evidence_class.py"),              None, ()),
        # D4-02 (B3): the CI reach-report drift step (gen_reach_report.py --check, in the
        # workflow while :8182/:3000 are up) must provably catch a moved graded status:
        # hermetic planted flip -> 1 drift, unchanged rerun -> 0, reason text is not
        # evidence, write/read round-trip stable. Labels themselves land only under
        # decision 6 (owner commit), never here.
        ("reach-selftest", _py(ROOT / "conformance" / "coverage" / "gen_reach_report.py", "--selftest"),
         None, ()),
        ("speclint",    _py(SPECLINT / "validate_speclint.py"),                   None, ()),
        ("ap2-crypto",  _py(SELF / "validate_ap2_crypto.py"),                     None, ()),
        ("jws-interop", _py(SELF / "validate_jws_interop.py"),                    None, (2,)),
        ("sdjwt-vs-reference", _py(SELF / "validate_sdjwt_vs_reference.py"),       None, ()),
        ("ap2-e2e",     _py(SELF / "validate_ap2_e2e.py"),                          None, ()),
        # the known-AP2-reference-defect register (our filed #329/#330 encoded as
        # correct-behavior cases, acknowledged while the pinned reference is buggy,
        # auto-flipping to enforcing on a fixed re-pin) self-expires: covered
        # hermetically so the classifier + register hygiene hold even where the
        # reference SDK is not installed and the semantic tier itself skips.
        ("ap2-defect-register", _py(SELF / "validate_ap2_e2e.py", "--selftest"),    None, ()),
        ("ap2-enforce", _py(SELF / "validate_ap2_enforce.py"),                      None, (2,)),
        ("site-checkdocs", _py(ROOT / "conformance" / "ci" / "site_gates.py", "checkdocs"), None, ()),
        ("web-unit",    _py(ROOT / "conformance" / "ci" / "web_gates.py", "unit"),    None, (2,)),
        ("web-browser", _py(ROOT / "conformance" / "ci" / "web_gates.py", "browser"), "controlled", (2,)),
        # --- site-governance lane: the website held to the same red/green bar as the
        #     suite (TDD traceability, claims register, voice law, security, redirects,
        #     shared-design-system consistency, product-freshness). Runs on every
        #     public/** change; blocks a red push.
        ("site-tdd",       _py(ROOT / "conformance" / "ci" / "site_gates.py", "tdd"),       None, ()),
        ("site-claims",    _py(ROOT / "conformance" / "ci" / "site_gates.py", "claims"),    None, ()),
        ("site-voice",     _py(ROOT / "conformance" / "ci" / "site_gates.py", "voice"),     None, ()),
        ("site-security",  _py(ROOT / "conformance" / "ci" / "site_gates.py", "security"),  None, ()),
        ("site-redirects", _py(ROOT / "conformance" / "ci" / "site_gates.py", "redirects"), None, ()),
        ("site-consistency", _py(ROOT / "conformance" / "ci" / "site_gates.py", "consistency"), None, ()),
        ("site-freshness", _py(ROOT / "conformance" / "ci" / "site_gates.py", "freshness"), None, ()),
        # non-page copy (README, ci/README, packaging README, docs, functions/**/*.js) held to
        # the page bar: counts equal the product, registered doc claims hold, ci/README's
        # gate rows exist, Action snippets pinned (D5-01/D5-20/D5-21).
        ("site-docclaims", _py(ROOT / "conformance" / "ci" / "site_gates.py", "docclaims"), None, ()),
        # the register's generated blocks (manifest, evidence.per_version) equal the engine
        # byte-for-byte (D5-05) and no REG claim is an orphan (text gone from its page).
        ("site-claims-sync", _py(ROOT / "conformance" / "web" / "sync_site_claims.py", "--check"), None, ()),
        ("site-claims-orphans", _py(ROOT / "conformance" / "ci" / "site_gates.py", "claims", "--orphans"), None, ()),
        # PLAN-0825 §E state-consistency kill-tests: a `state` field that disagrees
        # with its own CHECK/EXEMPT counts must redden freshness(); hermetic
        # (SPCK_PUBLIC scratch copy, repo untouched) — proves the validator can fail.
        ("site-state-selftest", _py(ROOT / "conformance" / "ci" / "site_gates.py", "--selftest"), None, ()),
        # the site lane's own kill-tests (D5-10/D5-01/D5-05): scratch copies of public/
        # with one planted defect each must redden the audits — recursive scope, stale
        # doc counts, registry drift, review-field rewrites. Hermetic (repo untouched).
        ("site-gates-selftest", _py(ROOT / "conformance" / "ci" / "validate_site_gates.py"), None, ()),
        ("suite-01-23", _py(CHK / "run_01_23.py", server),                      "golden",  ()),
        ("differential", _py(ROOT / "conformance" / "ci" / "differential.py", "--server", server,
                             "--target-name", "flower-shop-official-sample",
                             "--config", str(ROOT / "conformance" / "ci" / "differential_flower.config.json")),
         "golden", (2,)),
        # the differential ALLOWLIST is self-expiring like the defect registers: an entry
        # whose silenced (target, check) deviation stops reproducing on a PROBED target is
        # STALE and reds the gate (so a documented silence can't outlive its bug and mask a
        # future regression of the same pair). Hermetic kill-test asserts the classifier
        # catches a probed-but-vanished deviation, keeps a reproducing one, and never
        # falsely expires an entry for an un-probed target. No network/golden.
        ("differential-selftest", _py(ROOT / "conformance" / "ci" / "differential.py", "--selftest"),
         None, ()),
        # D1-08: the old name overstated this gate. It is the mutation PROXY demo (2 real
        # checks + the noop canary over :8183); the per-check kill-rate proof every
        # public claim rests on is the merchant* gates above.
        ("proxy-demo",  _py(SELF / "mutation_proxy_demo.py"),                   "proxy",   (2,)),
        # SCHEMA-GUIDED FUZZ LANE: enumerate the boundary/constraint points of the pinned
        # 04-08 request schemas and fire one payload per point at the golden, classifying
        # each response (crash vs conformant-4xx vs spec-contradicting-accept). The #156
        # currency-omit 500 was found by luck; this finds the whole #156 CLASS
        # systematically. Fails on any NEW 5xx/reset/hang not in the self-expiring
        # known_fuzz_defects.json (a registered defect that stops reproducing also fails).
        # Expected-validity for every payload comes from the INDEPENDENT referee, so a
        # spec-contradicting ACCEPT is detectable; conformant 4xx envelopes are not
        # findings. Skips (rc 2) if the referee lib or the golden is unavailable.
        ("fuzz",        _py(ROOT / "conformance" / "ci" / "fuzz_gate.py", "--server", server),
         "golden", (2,)),
        # the fuzzer must provably catch a crash (a fuzzer that never caught one proves
        # nothing): hermetic kill-test stands up a stub that 500s on a PLANTED boundary
        # input and asserts the gate reddens on it AND on a stale register entry, plus
        # corpus determinism + register hygiene. No network/golden.
        ("fuzz-selftest", _py(ROOT / "conformance" / "ci" / "fuzz_gate.py", "--selftest"),
         None, (2,)),
        # --- isolation safety net + agent-conformance lane (separate tree; can't move
        #     merchant numbers). merchant-stability fails if agent work drifts merchant output.
        ("merchant-stability", _py(ROOT / "conformance" / "ci" / "merchant_stability.py",
                                   "--server", CONTROLLED),                     "controlled", (2,)),
        ("shared-api",  _py(ROOT / "conformance" / "ci" / "shared_api_guard.py"), None, ()),
        # drift tripwire: the pure staleness logic behind the preflight sources-age
        # warning (SOURCES.lock.json pins that silently age past upstream HEAD). Network
        # lives in the preflight --check step; this gate covers the logic deterministically.
        ("sources-age", _py(ROOT / "conformance" / "ci" / "sources_age.py", "--selftest"), None, ()),
        # provenance of every behavioral verdict: the golden must be the pinned server,
        # running the pinned SDK, and must not be a stale process that merely answers.
        # Hermetic (stub uv, synthetic root); each case carries a mutant so the guards
        # cannot pass by being unable to fail.
        ("golden-guards", _py(ROOT / "conformance" / "ci" / "golden_boot_guards.py", "--selftest"), None, ()),
        # every literal port the harness binds is registered in conformance/ci/ports.json
        # (single source: selftest.sh's sweep derives from it) and no two names claim one
        # port. Hermetic; the checker runs its own kill-tests first (plants an unregistered
        # literal and a collision) so the gate cannot pass by being unable to fail.
        ("ports-registry", _py(ROOT / "conformance" / "ci" / "validate_ports_registry.py"), None, ()),
        # the deploy path itself is guarded (D5-08): on a synthetic repo with stub gh/wrangler,
        # deploy.sh must refuse a dirty tree / HEAD≠origin/main / a failed selftest check-run /
        # a stale export / a red gate, and must deploy preview-<sha7> BEFORE main. Hermetic.
        ("deploy-guards", ["bash", str(ROOT / "packaging" / "deploy.sh"), "--selftest"], None, ()),
        # the single KNOWN ISSUES file (PLAN-v3 §2.13): no refuted/stale/unevidenced row can
        # publish; ledger cross-ref needs ops/ mounted (rc 2 = honest SKIP in CI). Hermetic
        # kill-tests (--selftest) run first inside the same invocation.
        ("known-issues", _py(ROOT / "conformance" / "ci" / "validate_known_issues.py"), None, (2,)),
        # suite-01-23 (run_01_23.py) IS a gate — it must be ABLE to go red. Before P0-2 it
        # printed "aggregate: FAIL … UNSAFE" and unconditionally exited 0, so every one of
        # its engine checks was enforcement-free. This pins run_01_23.verdict_exit: red on any
        # unsound check / MUST deviation / rogue (non-allowlisted) version-skip / vacuous run
        # (nothing executed), honest-skip only when the target is unreachable, green only when
        # every executed check is a kill-safe clean-pass. Hermetic; carries the return-0 mutant.
        ("suite-01-23-exit", _py(SELF / "validate_run_01_23_exit.py", "--selftest"), None, ()),
        # a broken checks/area_*.py module must not silently vanish from a suite run while
        # the gate stays green and the matrix still claims its ids (P0-3). Pins the strict
        # manifest loader (run_01_23/run_04_08 red on a missing/unimportable/count-drifted
        # module) AND matrix.py (a checks/ import failure is a gate FAILURE, not a text-scan
        # shrug). Hermetic; each case carries the mutant a weaker guard would miss.
        ("area-loading", _py(SELF / "validate_area_module_loading.py", "--selftest"), None, ()),
        # P0-4: the AREA lock (above) did not cover the CORE checkset count, so an emptied
        # v2026_01_23.CHECKS (12) / v2026_04_08.CHECKS (1) left the areas running and every
        # gate green while the core kill-tests vanished. Pins checkset_manifest's core_checks/
        # expected_total lock (run_01_23/run_04_08 red on a drifted core) AND matrix.py (a
        # core module that imports fine but exports the wrong CHECKS count is a gate FAILURE,
        # not a text-scan shrug). Hermetic; each case carries the mutant a weaker guard misses.
        ("core-checkset", _py(SELF / "validate_core_checkset_count.py", "--selftest"), None, ()),
        # python-sdk#57/#59 regression watch: the pinned PyPI release must enforce the
        # contains/uniqueItems validators, and the probe must go red on 0.4.3 (the real
        # predecessor release without them) so it cannot pass vacuously.
        ("sdk-constraints", _py(SELF / "validate_sdk_constraints.py"),           None, (2,)),
        # a mutation that cannot reach the field its predicate reads is a kill-test that
        # certifies nothing while reporting kill_safe — green by being unable to fail.
        ("mutation-paths", _py(SELF / "validate_mutation_paths.py", "--selftest"), None, ()),
        # D1-01: checks/wire_shapes.py is the one place that knows the per-version
        # request delta (08-25 destinations[].type, CHK-025 async branch, keys[] vs
        # signing_keys, Purpose objects); fail-closed on an unreviewed version; the three
        # older versions' bodies are frozen byte-for-byte so the delta cannot leak back.
        ("wire-shapes",  _py(SELF / "validate_wire_shapes.py", "--selftest"),  None, ()),
        # D1-03: the CLI denominator is capability- AND transport-aware from
        # requirements/<v>/_area_capabilities.json (fail-closed on an unmapped area);
        # unreviewed version / no REST -> coverage null + banner, never 0.0; checks_summary
        # counts checks, the headline never mixes MUST ids with checks. Loopback stubs.
        ("cli-summary",  _py(SELF / "validate_cli_summary.py", "--selftest"),  None, ()),
        # D1-06: every kill set (MCheck/engine mutations, schema-tier + struct negatives,
        # golden-row mutants, agent kill_mutation) is hashed in selfcheck/killset_lock.json;
        # a silent shrink or drift reds here, named. Regenerate DELIBERATELY with
        # gen_killset_lock.py in the same commit as a check change (a shrink needs a note).
        ("killset-lock", _py(SELF / "validate_killset_lock.py"),                None, ()),
        ("killset-lock-selftest", _py(SELF / "validate_killset_lock.py", "--selftest"), None, ()),
        # the golden speaks ONE spec version (2026-04-08 since the 2026-08-03 re-pin);
        # engine checks whose citations are 01-era-scoped are version-skipped by the
        # served-version gate instead of deviating/reported-UNSAFE on a known-good
        # server. Hermetic; the gate-excised mutant proves the gate is load-bearing
        # AND that each scoped check still bites; envelope-tolerant predicates are
        # proven clean-pass + kill in BOTH profile shapes (wrapped 04-08 / flat 01-era).
        ("version-scope", _py(SELF / "validate_version_scope.py", "--selftest"), None, ()),
        # the known-reference-defect register self-expires: covered hermetically so the
        # rule holds even when no golden is reachable and probe-hygiene itself skips.
        ("defect-register", _py(SELF / "validate_probe_hygiene.py", "--selftest"), None, ()),
        ("crypto-interop", _py(ROOT / "conformance" / "ci" / "crypto_interop.py"), None, ()),
        ("agent-lane",  _py(ROOT / "conformance" / "agent" / "run_agent.py",
                            "--evidence-out", str(RECORD_DIR / "agent_run_evidence.json")), None, ()),
        # governance runs AFTER the lane (D5-04 / decision 24 in-run freshness): the lane
        # hands THIS run's attribution evidence to RECORD_DIR (never the tracked, bundled
        # agent_run_evidence.json — CI-1: its dates rolled 09-10 -> 09-11 inside CI and the
        # bundle diff went red), and governance's EVIDENCE check reads it with --in-run —
        # fresh, at the current pin. The tracked file is refreshed only by the owner's
        # `run_agent.py --record` on the release path.
        # Hermetic kill-tests for the guard itself (evidence-less / stale / other-pin → GAP;
        # governance names the id) run first.
        ("agent-attribution-guard", _py(ROOT / "conformance" / "agent" / "test_attribution_guard.py"), None, ()),
        ("agent-governance", _py(ROOT / "conformance" / "agent" / "agent_governance.py",
                                 "--in-run", str(RECORD_DIR / "agent_run_evidence.json")), None, ()),
        # CI-1 (decision 24): the lane hands THIS run's evidence to RECORD_DIR and governance
        # reads it with --in-run; the tracked agent_run_evidence.json (bundled) is rewritten
        # only by an explicit `run_agent.py --record` at release time. Kill-proof: make the
        # lane record into the tracked file again -> this test reds (mtime/bytes) and the
        # bundle diff reds on the next UTC-date rollover (CI run 34547382551).
        ("agent-evidence-handoff", _py(ROOT / "conformance" / "agent" / "test_evidence_handoff.py"), None, ()),
        # R8/R14/S8a kill-proof (agent phase B, 08-25 kickoff): proves
        # reference_agent.extract_signing_keys reads the 08-25 top-level keys[] location
        # against a REAL frozen golden-0825 capture (not just our own sandbox), and that
        # the pre-fix nested-only reader finds nothing on that same capture. Hermetic —
        # frozen fixture, no live server needed.
        ("agent-r8-keys", _py(ROOT / "conformance" / "agent" / "test_r8_keys_location.py"),
         None, ()),
        # the public interop demo (public/agent-demo.json) must stay real + in sync with the
        # harness: every case's catching check still kills its defect, no drift.
        ("agent-demo",  _py(ROOT / "conformance" / "agent" / "build_demo_data.py", "--check"), None, ()),
        # the pip package is two-sided: the bundled `--agent` lane must run + pass from the
        # bundle (proves sync_bundle shipped a working agent lane, deps + path-resolution intact).
        ("package-agent", _py(ROOT / "packaging" / "spck_conformance" / "cli.py", "--agent"), None, ()),
        # ATTRIBUTION (decision 16, PNR-0, 2026-09-10; D4-16): no AI/bot author, co-author
        # or generated-with line on any commit, forward-only. The gate checks (1) every
        # commit committed on/after 2026-09-10 on HEAD is clean and (2) this clone's active
        # commit-msg hook IS the tracked ops/tools/hooks/commit-msg (rc 2 = ops/ not
        # mounted, e.g. CI, after the history half passed). The selftest plants a trailer,
        # a bot author and a missing/stale hook so the gate provably can go red.
        ("attribution-hook", _py(ROOT / "conformance" / "ci" / "attribution_hook_gate.py"), None, (2,)),
        ("attribution-selftest", _py(ROOT / "conformance" / "ci" / "attribution_hook_gate.py", "--selftest"),
         None, ()),
        # the branch-level attribution net (D4-15): ops/tools/filing_lint.py greps the unpushed
        # range of every local branch of every repo in ops/tools/repos.json (own repos since
        # 2026-09-10, upstream-bound forks at any date) and lints ops/filings/. Hermetic
        # selftest (scratch repos with planted trailers); rc 2 = ops/ not mounted (CI).
        ("filing-lint", _py(ROOT / "conformance" / "ci" / "ops_tool_gate.py", "tools/filing_lint.py", "--selftest"),
         None, (2,)),
        # D1-05: the bundle must be COMPLETE, not just current — every first-party module
        # merchant.py transitively imports (ast, from SOURCE) and every data file it reads
        # must be in the bundle, and it must import + run in an isolated interpreter.
        ("package-bundle", _py(ROOT / "packaging" / "validate_bundle.py"), None, ()),
        # D1-22: `--only <gate>` runs exactly the named gates, boots only what they need,
        # and refuses an unknown name (rc 2) — so every acceptance command written as
        # `run_suite.py --only X` proves X. In-process against the real table; the
        # :8198 case observes golden-check-08-25's own boot + teardown via lsof.
        ("run-suite-only", _py(ROOT / "conformance" / "ci" / "validate_run_suite_only.py", "--selftest"), None, ()),
        # …and the bundled MERCHANT runner must grade the controlled fixture clean from the
        # bundle (D5-06): the Action installs this package from its own checkout, so the
        # wheel's engine/register must work outside the repo tree, not just in it.
        ("package-merchant", _py(ROOT / "packaging" / "spck_conformance" / "cli.py",
                                 "--server", CONTROLLED), "controlled", ()),
    ]

class UnknownGate(ValueError):
    pass


def select_gates(table, only):
    """The subset of `table` named by `only` (a comma list), in TABLE order. Raises
    UnknownGate naming the first unknown name — a typo must never become a silent
    no-op (rc 0 with nothing run) or a silent full run."""
    wanted = [n.strip() for n in only.split(",") if n.strip()]
    known = {g[0] for g in table}
    for n in wanted:
        if n not in known:
            raise UnknownGate(f"unknown gate: {n}")
    return [g for g in table if g[0] in set(wanted)]


def server_up(server, timeout=3):
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/.well-known/ucp", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

def _boot(argv, health_url, tries=40):
    """Spawn a background server and wait for it to answer; return the Popen or None."""
    if server_up(health_url):
        return None                                   # already up (external); leave it
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(tries):
        if server_up(health_url):
            return p
        time.sleep(0.25)
    return p            # return anyway; the gate will report it DOWN

def boot_controlled():
    """Start the dependency-free controlled merchant fixture (default 2026-04-08)."""
    return _boot([sys.executable, str(FIXTURE / "server.py"), "--port", str(CONTROLLED_PORT)],
                 CONTROLLED)

def boot_controlled_0123():
    """Start a second controlled fixture serving spec 2026-01-23 (the version-switched
    golden for pre-04-08 checks the Flower Shop can't exercise)."""
    return _boot([sys.executable, str(FIXTURE / "server.py"), "--port", str(CONTROLLED_0123_PORT),
                  "--spec-version", "2026-01-23"], CONTROLLED_0123)

def boot_controlled_0111():
    """Start a third controlled fixture serving spec 2026-01-11 (wave-2: the oldest
    envelope generation — array capabilities, discovery_profile def)."""
    return _boot([sys.executable, str(FIXTURE / "server.py"), "--port", str(CONTROLLED_0111_PORT),
                  "--spec-version", "2026-01-11"], CONTROLLED_0111)

def boot_tls_proxy():
    """Start the CHK-051 TLS harness (1.3-only golden :8443 + 1.2-accepting negative
    :8444) in front of the controlled fixture. No HTTP health URL (TLS listeners);
    the gate itself reports the harness down as a skip."""
    return subprocess.Popen([sys.executable, str(FIXTURE / "tls_proxy.py")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

class _Golden0825:
    """Handle for a golden-0825 booted by boot_golden_0825: stop() runs the stop script
    with the same run-scoped DB_DIR (kills wrapper + listener, waits for the port)."""
    def __init__(self, port, db_dir):
        self.port, self.db_dir = port, db_dir

    def stop(self):
        env = dict(os.environ, PORT=str(self.port), DB_DIR=str(self.db_dir))
        subprocess.run([str(GOLDEN_0825_DIR / "stop_golden_0825.sh")], env=env,
                       capture_output=True, text=True, timeout=60)


def boot_golden_0825(port=GOLDEN_0825_PORT, env=None):
    """Boot our own 2026-08-25 golden via its serve script (uv sync + seed + health wait)
    with a run-scoped DB_DIR; shared by probe-shape-0825, merchant-0825 (D1-10) and the
    battery. Returns a handle with .stop(), or None if something already answers on the
    port (left alone, like _boot) or the boot failed (the gate reports it DOWN)."""
    if server_up(f"http://localhost:{port}"):
        return None
    db_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"golden_0825_{port}_"))
    e = dict(os.environ, PORT=str(port), DB_DIR=str(db_dir), SIM_SECRET="run-suite-secret")
    e.pop("DEFECTS_CONFIG", None); e.pop("DEFECTS_STATE_FILE", None)
    e.update(env or {})
    r = subprocess.run([str(GOLDEN_0825_DIR / "serve_golden_0825.sh")], env=e,
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(f"golden-0825 boot failed on :{port}: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
        return None
    return _Golden0825(port, db_dir)


def boot_proxy(golden):
    """Start the mutation proxy (wraps the golden) that the kill-rate gate drives."""
    return _boot([sys.executable, str(SELF / "mutation_proxy.py"),
                  "--upstream", golden, "--port", str(PROXY_PORT)], PROXY)

def run_gate(name, argv, timeout=180):
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": 124, "dt": timeout, "tail": "TIMEOUT"}
    tail = (p.stdout + p.stderr).strip().splitlines()
    return {"rc": p.returncode, "dt": time.monotonic() - t0,
            "tail": tail[-1] if tail else "", "out": p.stdout + p.stderr}

BATTERY = SELF / "validate_golden_0825_battery.py"
BATTERY_REPORT = ROOT / "conformance" / "testbed" / "golden-0825" / "battery" / "LAST_RUN.json"


def run_battery():
    """--battery: run the R11 golden-0825 mutant battery inside this invocation and copy
    its report into RECORD_DIR as the in-run source for battery-freshness. Its own port
    (GOLDEN_0825_BATTERY_PORT, default 8199) and oracle-skip semantics are the battery's."""
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, str(BATTERY)], cwd=str(ROOT), capture_output=True, text=True)
    tail = (p.stdout + p.stderr).strip().splitlines()
    print(f"battery (--battery): rc={p.returncode} [{time.monotonic() - t0:.1f}s] "
          f"{tail[-1] if tail else ''}")
    if p.returncode == 0 and BATTERY_REPORT.exists():
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        (RECORD_DIR / "battery_LAST_RUN.json").write_bytes(BATTERY_REPORT.read_bytes())

R11_BATTERY_REPORT = BATTERY_REPORT     # D3-01's report line (behavior N/N) reads the same tracked file
R11_BATTERY_STALE_DAYS = 14


def r11_battery_report_line():
    """PLAN-0825 SS C.4 (R11): the golden-0825 mutant battery is a REPORT-ONLY
    line here, not a gate in gates() above -- it boots a server twice and
    takes ~15s, too heavy for the default per-change run_suite invocation
    (same call the schema-census gate makes: report-only by default, a flip
    to a hard gate is a later, deliberate step). Run it directly:
        python3 conformance/selfcheck/validate_golden_0825_battery.py
    This function only reads the JSON report that script writes on its own
    last run and never re-executes it -- so this line is O(1) and never boots
    anything itself.

    Self-expiring (P-2): a report older than R11_BATTERY_STALE_DAYS is flagged
    STALE rather than quietly trusted forever, same doctrine as every other
    intermediate/report-mode state in this suite."""
    import json as _json
    if not R11_BATTERY_REPORT.exists():
        return "R11 battery      · not yet run — see conformance/selfcheck/validate_golden_0825_battery.py"
    try:
        report = _json.loads(R11_BATTERY_REPORT.read_text())
    except (OSError, ValueError) as e:
        return f"R11 battery      ✗ LAST_RUN.json unreadable ({e})"
    age_days = (time.time() - report.get("ran_at", 0)) / 86400
    stale = " [STALE — re-run]" if age_days > R11_BATTERY_STALE_DAYS else ""
    mark = "✓" if report.get("ok") else "✗"
    acked = report.get("acknowledged_open", 0)
    # D3-01: behavior rows (decision 19) are reported next to the patch total.
    b_total = report.get("behavior_total")
    behavior = f" · behavior {report.get('behavior_killed', 0)}/{b_total}" if b_total is not None else ""
    return (f"R11 battery      {mark} {report.get('killed')}/{report.get('total')} killed"
            + (f" ({acked} acknowledged-open)" if acked else "")
            + behavior
            + f", {age_days:.1f}d ago{stale}")


def main():
    ap = argparse.ArgumentParser(description="TDD/CI gate runner for the UCP conformance suite.")
    ap.add_argument("--server", default="http://localhost:8182",
                    help="golden UCP server for behavioral gates")
    ap.add_argument("--require-server", action="store_true",
                    help="fail (not skip) server-dependent gates if the golden is down")
    ap.add_argument("--skip", default="", help="comma-separated gate names to skip")
    ap.add_argument("--only", default="",
                    help="comma-separated gate names to run (exactly those, in table order; "
                         "boots only the fixtures they need; rc 2 on an unknown name)")
    ap.add_argument("--battery", action="store_true",
                    help="run the R11 golden-0825 battery in this invocation (CI); "
                         "battery-freshness then reads that run instead of the tracked file")
    ap.add_argument("-v", "--verbose", action="store_true", help="print full gate output on failure")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if args.battery:
        run_battery()

    table = gates(args.server, args.require_server)
    if args.only:
        try:
            table = select_gates(table, args.only)
        except UnknownGate as e:
            known = ", ".join(g[0] for g in gates(args.server))
            print(f"run_suite: {e}\n  known gates: {known}", file=sys.stderr)
            return 2
    # which fixtures to boot: everything (the historical default) unless --only
    # narrows the table, in which case only what the selected gates declare.
    needed = {g[2] for g in table if g[2]} if args.only else \
        {"golden", "controlled", "controlled-01-23", "controlled-01-11", "proxy", "golden-0825"}

    up = server_up(args.server) if needed & {"golden", "proxy"} else False
    ctrl_proc = boot_controlled() if "controlled" in needed else None
    ctrl_up = server_up(CONTROLLED) if "controlled" in needed else False
    ctrl0123_proc = boot_controlled_0123() if "controlled-01-23" in needed else None
    ctrl0111_proc = boot_controlled_0111() if "controlled-01-11" in needed else None
    ctrl0123_up = server_up(CONTROLLED_0123) if "controlled-01-23" in needed else False
    ctrl0111_up = server_up(CONTROLLED_0111) if "controlled-01-11" in needed else False
    tls_proc = boot_tls_proxy() if ctrl_up else None
    if tls_proc: time.sleep(1.0)            # cert mint + listener bind
    proxy_proc = boot_proxy(args.server) if (up and "proxy" in needed) else None   # kill-rate gate drives the proxy
    proxy_up = server_up(PROXY) if "proxy" in needed else False
    g0825 = boot_golden_0825() if "golden-0825" in needed else None
    g0825_up = server_up(GOLDEN_0825) if "golden-0825" in needed else False
    print(f"golden server {args.server}: {'UP' if up else 'DOWN'}")
    print(f"controlled fixture {CONTROLLED}: {'UP' if ctrl_up else 'DOWN'}")
    print(f"controlled fixture (01-23) {CONTROLLED_0123}: {'UP' if ctrl0123_up else 'DOWN'}")
    print(f"controlled fixture (01-11) {CONTROLLED_0111}: {'UP' if ctrl0111_up else 'DOWN'}")
    print(f"mutation proxy {PROXY}: {'UP' if proxy_up else 'DOWN'}")
    print(f"golden-0825 {GOLDEN_0825}: {'UP' if g0825_up else 'DOWN'}")
    print(f"run records: {RECORD_DIR}\n")
    avail = {"golden": up, "controlled": ctrl_up, "controlled-01-23": ctrl0123_up,
             "controlled-01-11": ctrl0111_up,
             "proxy": proxy_up and up, "golden-0825": g0825_up}

    results = []
    try:
      for name, argv, needs, skip_codes in table:
        if name in skip:
            results.append((name, "SKIP", "explicitly skipped")); continue
        if needs and not avail.get(needs):
            if args.require_server:
                results.append((name, "FAIL", f"{needs} server required but DOWN"))
            else:
                results.append((name, "SKIP", f"no {needs} server"))
            continue
        r = run_gate(name, argv)
        if r["rc"] == 0:
            status = "PASS"
        elif r["rc"] in skip_codes:
            status = "SKIP"
        else:
            status = "FAIL"
        results.append((name, status, f"{r['tail']}  [{r['dt']:.1f}s, rc={r['rc']}]"))
        if status == "FAIL" and args.verbose:
            print(f"----- {name} output -----\n{r.get('out','')}\n-------------------------")
    finally:
        for proc in (ctrl_proc, ctrl0123_proc, ctrl0111_proc, tls_proc, proxy_proc):
            if proc is not None:
                proc.terminate()
        if g0825 is not None:
            g0825.stop()

    print(f"{'gate':14} {'status':6} detail")
    print("-" * 72)
    for name, status, detail in results:
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}[status]
        print(f"{name:14} {mark} {status:4} {detail}")

    failed = [n for n, s, _ in results if s == "FAIL"]
    passed = [n for n, s, _ in results if s == "PASS"]
    skipped = [n for n, s, _ in results if s == "SKIP"]
    print("-" * 72)
    print(f"{len(passed)} passed · {len(failed)} failed · {len(skipped)} skipped")
    print(r11_battery_report_line() + "  (report-only, not counted above; the counted gate is battery-freshness)")
    if args.only:
        # the acceptance-line form: one `✓ PASS <gate> <detail>` per selected gate
        for name, status, detail in results:
            mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}[status]
            print(f"{mark} {status} {name} {detail}")
        n = len(results)
        print(f"{n} gate{'s' if n != 1 else ''} run (--only {args.only})")
    if failed:
        print(f"\nRED — gates failed: {', '.join(failed)}")
        return 1
    print(f"\nGREEN — every run gate passed"
          + (f" ({len(skipped)} skipped: {', '.join(skipped)})" if skipped else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())

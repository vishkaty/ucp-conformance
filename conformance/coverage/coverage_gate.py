#!/usr/bin/env python3
"""
coverage_gate.py — the accuracy/anti-regression gate for coverage accounting.

Three invariants, enforced on every CI run (via run_suite gate `coverage`):

1. NO STALE VISIBILITY — public/coverage.json (the data behind spck.dev/coverage)
   must byte-match a fresh matrix export. If a check/register/exemption changed and
   the export wasn't regenerated, this fails with the exact command to run. The
   public coverage page therefore can never silently lie.

2. RATCHET (never regress) — per version, accounted MUSTs (CHECK+EXEMPT) must be
   >= the floor in coverage/ratchet.json. Deleting/renaming a check, breaking the
   id-attribution regex, or losing a register row can only lower the number — and
   trips this gate. Raising the floor is a deliberate, reviewed act.

3. HONEST EXEMPTIONS — every entry in coverage/exemptions.json must (a) reference a
   real MUST row id in at least one register, (b) NOT be covered by a check anywhere
   it exempts (no double-booking), and (c) carry a non-empty written `reason` plus a
   `class` from the irreducibly-manual taxonomy. An exemption is a documented claim,
   never a shrug.
   Entries may carry an optional `"versions": ["2026-01-11", ...]` scope (the 04-08
   registers renumbered ids, so one id can be exempt-worthy at one version and
   covered/testable at another). A scoped entry must list only real spec versions,
   the id must be a MUST/MUST NOT row in EVERY listed version (over-scoping to a
   version where the requirement under that id doesn't exist or isn't normative =
   failure), and it must not be covered by a check in ANY listed version (scoping
   into a covered version = failure). Unscoped entries keep the original semantics
   (apply wherever the id is a MUST) and are held to the same no-double-booking bar
   across all versions where the id is a MUST row.
   An id's value may also be a LIST of scoped entries — one id can be irreducibly
   manual of a DIFFERENT class at different versions (the 04-08 renumbering means
   e.g. DISC-006 is spec-authoring @01-era but client-bound @04-08). Every entry in
   a list must be version-scoped and the scopes must be DISJOINT (a version can
   carry exactly one class); each entry is otherwise held to the same bar above.

Run with --selftest to execute the injection kill-tests for invariant 3 (each
synthetic defect must be CAUGHT — proves the validator can actually fail).

4. SITE COPY TELLS THE TRUTH — every "N kill-rate-validated checks" claim in
   public/*.html must equal the ACTUAL merchant-check count (MCheck registrations).
   This number went stale twice already; now it can't.

Exit 0 = all invariants hold; 1 = violation (with actionable detail).
"""
import json, os, re, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import matrix  # noqa: E402

PUBLIC_JSON = os.path.join(ROOT, "public", "coverage.json")
RATCHET = os.path.join(HERE, "ratchet.json")
EXEMPT_CLASSES = {"real-world-act", "human-perception", "subjective-judgment",
                  "out-of-band-legal",
                  # client-bound: the MUST binds the platform/agent (request author or
                  # renderer), not the business under test — no message the suite can
                  # elicit from the party under test can prove or refute it, and
                  # platforms are autonomous agent surfaces the suite cannot drive.
                  # (Precedent: CAT-008/CAT-034 register reclassifications.)
                  "client-bound",
                  # spec-authoring: the MUST binds authors of ecosystem specification
                  # documents (capability transport definitions, extension schemas,
                  # payment-handler specs) — a party distinct from any implementation
                  # under test; conformance is established by document review.
                  "spec-authoring"}


def _validate_entry(rid, meta, must_in, covered, require_scope):
    """Validate ONE exemption entry (a {class,reason,versions?} dict). `must_in` is
    the list of versions where `rid` is a MUST/MUST NOT row. `require_scope` is True
    for entries that live inside a multi-entry list (there, an unscoped entry would be
    ambiguous — which class wins where?). Returns the list of failure strings."""
    failures = []
    if not isinstance(meta, dict):
        failures.append(f"exemption {rid}: entry must be an object, got {type(meta).__name__}")
        return failures
    vscope = meta.get("versions")
    if vscope is None and require_scope:
        failures.append(f"exemption {rid}: every entry in a multi-class list MUST carry a "
                        f"`versions` scope (an unscoped entry alongside others is ambiguous)")
    if vscope is not None:
        # the version scope itself must be well-formed and real
        if not isinstance(vscope, list) or not vscope:
            failures.append(f"exemption {rid}: `versions` must be a non-empty list of spec versions")
            return failures
        bogus = [v for v in vscope if v not in matrix.VERSIONS]
        if bogus:
            failures.append(f"exemption {rid}: unknown version(s) {bogus} — must be from {matrix.VERSIONS}")
            return failures
        if len(set(vscope)) != len(vscope):
            failures.append(f"exemption {rid}: duplicate versions in scope {vscope}")
        # over-scoping: at every scoped version the id must be a normative MUST row
        not_must = [v for v in vscope if v not in must_in]
        if not_must:
            failures.append(f"exemption {rid}: scoped to {not_must} where the id is not a "
                            f"MUST/MUST NOT row (the 04-08 renumbering means that version's "
                            f"row is a different/absent requirement — narrow the scope)")
        scope = [v for v in vscope if v in must_in]
    else:
        scope = must_in
    # no double-booking anywhere the entry actually exempts
    double = [v for v in scope if rid in covered[v]]
    if double:
        failures.append(f"exemption {rid}: ALSO covered by a check in {double} "
                        f"(double-booked — remove the exemption or narrow its `versions`)")
    if not str(meta.get("reason", "")).strip():
        failures.append(f"exemption {rid}: missing a written `reason`")
    if meta.get("class") not in EXEMPT_CLASSES:
        failures.append(f"exemption {rid}: `class` must be one of {sorted(EXEMPT_CLASSES)}")
    return failures


def validate_exemptions(exempt, must_ids, covered):
    """Invariant-3 validator (pure — also driven by --selftest injections).

    exempt:   {id: entry}  where entry is EITHER a {class, reason, versions?} dict
              OR a LIST of scoped entries (one id can be irreducibly-manual of a
              DIFFERENT class at different versions — e.g. DISC-006 is spec-authoring
              @01-era but client-bound @04-08 — which a single class field can't
              express). List entries must each be version-scoped, and their scopes
              must be DISJOINT (a version can carry exactly one class).
    must_ids: {version: set(ids that are MUST/MUST NOT rows)}
    covered:  {version: set(ids covered by shipped checks)}
    Returns the list of failure strings (empty = honest)."""
    failures = []
    for rid, meta in sorted(exempt.items()):
        must_in = [v for v in matrix.VERSIONS if rid in must_ids[v]]
        if not must_in:
            failures.append(f"exemption {rid}: not a MUST/MUST NOT register row in ANY version")
            continue
        if isinstance(meta, list):
            if not meta:
                failures.append(f"exemption {rid}: multi-class list must be non-empty")
                continue
            # scopes across list entries must be disjoint — one version, one class
            seen = {}
            for e in meta:
                for v in (e.get("versions") or []) if isinstance(e, dict) else []:
                    if v in seen:
                        failures.append(f"exemption {rid}: version {v} is claimed by more than "
                                        f"one list entry (a version can carry only one class)")
                    seen[v] = True
            for e in meta:
                failures += _validate_entry(rid, e, must_in, covered, require_scope=True)
        else:
            failures += _validate_entry(rid, meta, must_in, covered, require_scope=False)
    return failures


def selftest():
    """Injection kill-tests: every synthetic defect below MUST be caught by
    validate_exemptions, and every honest entry must pass. This is the proof the
    exemptions gate can actually fail (a validator that can't fail validates nothing)."""
    V1, V2, V3 = matrix.VERSIONS  # 2026-01-11, 2026-01-23, 2026-04-08
    must_ids = {V1: {"AAA-001", "AAA-002", "AAA-003"},
                V2: {"AAA-001", "AAA-002", "AAA-003"},
                V3: {"AAA-002", "AAA-003", "AAA-004"}}   # AAA-001 not a MUST at 04-08
    covered = {V1: set(), V2: set(), V3: {"AAA-002"}}     # AAA-002 covered at 04-08 only
    ok = lambda e: validate_exemptions(e, must_ids, covered)
    cases = [  # (name, entry-dict, must_fail)
        ("ghost id", {"ZZZ-999": {"class": "client-bound", "reason": "x"}}, True),
        ("empty reason", {"AAA-001": {"class": "client-bound", "reason": "  "}}, True),
        ("bogus class", {"AAA-001": {"class": "too-hard", "reason": "x"}}, True),
        ("unscoped double-book (id covered at a MUST version)",
         {"AAA-002": {"class": "client-bound", "reason": "x"}}, True),
        ("scoped INTO the covered version",
         {"AAA-002": {"class": "client-bound", "reason": "x", "versions": [V3]}}, True),
        ("scoped into covered version among others",
         {"AAA-002": {"class": "client-bound", "reason": "x", "versions": [V1, V3]}}, True),
        ("over-scoped to a version where the id is not a MUST",
         {"AAA-001": {"class": "client-bound", "reason": "x", "versions": [V1, V3]}}, True),
        ("versions not a list", {"AAA-001": {"class": "client-bound", "reason": "x", "versions": V1}}, True),
        ("versions empty list", {"AAA-001": {"class": "client-bound", "reason": "x", "versions": []}}, True),
        ("unknown version string",
         {"AAA-001": {"class": "client-bound", "reason": "x", "versions": ["2026-13-99"]}}, True),
        ("duplicate versions in scope",
         {"AAA-001": {"class": "client-bound", "reason": "x", "versions": [V1, V1]}}, True),
        ("scoped entry missing reason",
         {"AAA-003": {"class": "client-bound", "versions": [V1]}}, True),
        # --- multi-class LIST schema (one id, different class per version) ---
        ("list: empty list", {"AAA-003": []}, True),
        ("list: entry missing a versions scope (ambiguous)",
         {"AAA-003": [{"class": "client-bound", "reason": "x"}]}, True),
        ("list: entry with a versions scope and a bare entry mixed",
         {"AAA-003": [{"class": "client-bound", "reason": "x", "versions": [V1]},
                      {"class": "spec-authoring", "reason": "y"}]}, True),
        ("list: overlapping scopes (a version claimed by two classes)",
         {"AAA-003": [{"class": "client-bound", "reason": "x", "versions": [V1]},
                      {"class": "spec-authoring", "reason": "y", "versions": [V1, V2]}]}, True),
        ("list: an entry over-scoped to a non-MUST version",
         {"AAA-001": [{"class": "client-bound", "reason": "x", "versions": [V1]},
                      {"class": "spec-authoring", "reason": "y", "versions": [V3]}]}, True),
        ("list: an entry scoped into the covered version",
         {"AAA-002": [{"class": "client-bound", "reason": "x", "versions": [V1]},
                      {"class": "spec-authoring", "reason": "y", "versions": [V3]}]}, True),
        ("list: an entry with a bogus class",
         {"AAA-003": [{"class": "client-bound", "reason": "x", "versions": [V1]},
                      {"class": "too-hard", "reason": "y", "versions": [V2]}]}, True),
        ("list: an entry with an empty reason",
         {"AAA-003": [{"class": "client-bound", "reason": "  ", "versions": [V1]},
                      {"class": "spec-authoring", "reason": "y", "versions": [V2]}]}, True),
        # honest entries must PASS
        ("honest unscoped entry", {"AAA-001": {"class": "client-bound", "reason": "x"}}, False),
        ("honest scope avoiding the covered version",
         {"AAA-002": {"class": "client-bound", "reason": "x", "versions": [V1, V2]}}, False),
        ("honest full scope on an uncovered id",
         {"AAA-003": {"class": "spec-authoring", "reason": "x", "versions": [V1, V2, V3]}}, False),
        ("honest two-class list with disjoint scopes",
         {"AAA-003": [{"class": "client-bound", "reason": "x", "versions": [V1, V2]},
                      {"class": "spec-authoring", "reason": "y", "versions": [V3]}]}, False),
    ]
    bad = 0
    for name, entry, must_fail in cases:
        fails = ok(entry)
        caught = bool(fails)
        good = caught == must_fail
        print(f"  {'✓' if good else '✗'} {name}: {'CAUGHT' if caught else 'clean'}"
              + ("" if good else f"  <-- EXPECTED {'a failure' if must_fail else 'clean'} {fails}"))
        bad += 0 if good else 1
    # matrix-side scope semantics: a scoped entry buckets only in its versions
    ex = {"AAA-003": {"class": "client-bound", "reason": "x", "versions": [V2]}}
    sem = (not matrix.exempt_at(ex, "AAA-003", V1)
           and matrix.exempt_at(ex, "AAA-003", V2)
           and not matrix.exempt_at(ex, "AAA-003", V3)
           and matrix.exempt_at({"AAA-003": {"class": "c", "reason": "x"}}, "AAA-003", V3)
           and not matrix.exempt_at(ex, "AAA-004", V2))
    print(f"  {'✓' if sem else '✗'} matrix.exempt_at scope semantics")
    bad += 0 if sem else 1
    # matrix-side LIST semantics: each entry buckets only in its own scope
    lst = {"AAA-003": [{"class": "client-bound", "reason": "x", "versions": [V1]},
                       {"class": "spec-authoring", "reason": "y", "versions": [V2]}]}
    sem_list = (matrix.exempt_at(lst, "AAA-003", V1)
                and matrix.exempt_at(lst, "AAA-003", V2)
                and not matrix.exempt_at(lst, "AAA-003", V3)
                and matrix.exempt_reason_at(lst, "AAA-003", V1) == "x"
                and matrix.exempt_reason_at(lst, "AAA-003", V2) == "y")
    print(f"  {'✓' if sem_list else '✗'} matrix.exempt_at/exempt_reason_at list semantics")
    bad += 0 if sem_list else 1
    print(f"\ncoverage gate selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


def main():
    failures = []
    fresh = matrix.export_json()

    # 1. drift: the published JSON must match a fresh export exactly
    if not os.path.exists(PUBLIC_JSON):
        failures.append(f"public/coverage.json missing — run:\n"
                        f"    python3 conformance/coverage/matrix.py "
                        f"--json public/coverage.json --md docs/spec-coverage-matrix.md")
    else:
        published = json.load(open(PUBLIC_JSON))
        if published != fresh:
            failures.append("public/coverage.json is STALE (does not match the current "
                            "registers/checks/exemptions) — regenerate:\n"
                            "    python3 conformance/coverage/matrix.py "
                            "--json public/coverage.json --md docs/spec-coverage-matrix.md")

    # 2. ratchet: accounted coverage must never drop
    floors = {k: v for k, v in json.load(open(RATCHET)).items() if not k.startswith("_")}
    for ver, floor in sorted(floors.items()):
        got = fresh["versions"][ver]["check"] + fresh["versions"][ver]["exempt"]
        mark = "✓" if got >= floor else "✗"
        print(f"  {mark} ratchet {ver}: accounted {got} (floor {floor})")
        if got < floor:
            failures.append(f"coverage REGRESSED for {ver}: accounted {got} < floor {floor} "
                            f"(a check/register row was lost, or attribution broke)")
        elif got > floor:
            print(f"      note: floor can be raised to {got} in coverage/ratchet.json")

    # 3. exemptions are honest (incl. optional per-entry `versions` scope)
    exempt = matrix.load_exemptions()
    must_ids = {v: {r.get("id") for r in matrix.load_rows(v)
                    if r.get("keyword") in ("MUST", "MUST NOT")}
                for v in matrix.VERSIONS}
    covered = matrix.covered_ids_by_version()
    failures += validate_exemptions(exempt, must_ids, covered)

    # 4. copy freshness: any advertised check count — on the public site OR in the
    #    tracked docs (README, ROADMAP, packaging README) — must equal the real
    #    MCheck count. This is what stops a doc drifting to a stale "38 checks" claim.
    actual = 0
    for f in glob.glob(os.path.join(ROOT, "conformance", "checks", "merchant_checks*.py")):
        actual += len(re.findall(r"^    MCheck\(", open(f).read(), re.M))
    claim_res = [re.compile(r"(\d+)\+? kill-rate-validated checks?"),
                 re.compile(r"(\d+)\+? checks across"),
                 # the landing-page hero stat: <div class="stat-num">47</div>...Kill-rate-validated
                 re.compile(r'stat-num">(\d+)\+?</div><div class="stat-label">Kill-rate-validated')]
    copy_files = glob.glob(os.path.join(ROOT, "public", "*.html")) + [
        os.path.join(ROOT, "README.md"),
        os.path.join(ROOT, "docs", "ROADMAP.md"),
        os.path.join(ROOT, "packaging", "README.md")]
    for page in copy_files:
        if not os.path.exists(page):
            continue
        txt = open(page).read()
        for cre in claim_res:
            for m in cre.finditer(txt):
                if int(m.group(1)) != actual:
                    failures.append(f"{os.path.relpath(page, ROOT)} claims '{m.group(0)}' but the "
                                    f"suite has {actual} — update the copy")
    print(f"  copy freshness: advertised check count vs actual ({actual}) "
          f"{'✓' if not any('claims' in f for f in failures) else '✗'}")

    if failures:
        print("\ncoverage gate: FAIL")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("\ncoverage gate: PASS — published matrix fresh, ratchet holds, exemptions honest")
    return 0

if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())

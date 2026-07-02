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
   (no double-booking), and (c) carry a non-empty written `reason` plus a `class`
   from the irreducibly-manual taxonomy. An exemption is a documented claim, never
   a shrug.

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
                  "out-of-band-legal"}

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

    # 3. exemptions are honest
    exempt = matrix.load_exemptions()
    all_ids = {v: {r.get("id") for r in matrix.load_rows(v)} for v in matrix.VERSIONS}
    covered = matrix.covered_ids_by_version()
    for rid, meta in sorted(exempt.items()):
        in_any = [v for v in matrix.VERSIONS if rid in all_ids[v]]
        if not in_any:
            failures.append(f"exemption {rid}: not a register row in ANY version")
            continue
        double = [v for v in in_any if rid in covered[v]]
        if double:
            failures.append(f"exemption {rid}: ALSO covered by a check in {double} "
                            f"(double-booked — remove the exemption)")
        if not (isinstance(meta, dict) and str(meta.get("reason", "")).strip()):
            failures.append(f"exemption {rid}: missing a written `reason`")
        if isinstance(meta, dict) and meta.get("class") not in EXEMPT_CLASSES:
            failures.append(f"exemption {rid}: `class` must be one of {sorted(EXEMPT_CLASSES)}")

    # 4. site copy: the advertised check count must equal the real MCheck count
    actual = 0
    for f in glob.glob(os.path.join(ROOT, "conformance", "checks", "merchant_checks*.py")):
        actual += len(re.findall(r"^    MCheck\(", open(f).read(), re.M))
    claim_res = [re.compile(r"(\d+)\+? kill-rate-validated checks?"),
                 # the landing-page hero stat: <div class="stat-num">47</div>...Kill-rate-validated
                 re.compile(r'stat-num">(\d+)\+?</div><div class="stat-label">Kill-rate-validated')]
    for page in glob.glob(os.path.join(ROOT, "public", "*.html")):
        txt = open(page).read()
        for cre in claim_res:
            for m in cre.finditer(txt):
                if int(m.group(1)) != actual:
                    failures.append(f"{os.path.basename(page)} claims '{m.group(0)}' but the "
                                    f"suite has {actual} — update the page copy")
    print(f"  site copy: advertised check count vs actual ({actual}) "
          f"{'✓' if not any('claims' in f for f in failures) else '✗'}")

    if failures:
        print("\ncoverage gate: FAIL")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("\ncoverage gate: PASS — published matrix fresh, ratchet holds, exemptions honest")
    return 0

if __name__ == "__main__":
    sys.exit(main())

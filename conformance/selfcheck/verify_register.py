#!/usr/bin/env python3
"""
verify_register.py — anti-hallucination gate for the Requirements Register.

For every row in conformance/requirements/<version>/*.json, confirm that the row's
`quote` actually appears in the vendored source file at (or very near) the cited
`source` location. A row whose quote cannot be found in its source file FAILS — it
is almost certainly hallucinated or miscited and must not enter the register.

Source format:  "<repo>:<path>#L<n>"  |  "#L<n>-L<m>"  |  "#L<n>,L<m>"
Repos map to conformance/.vendor/<repo>. Quotes may concatenate snippets with "...";
each fragment is checked independently. Matching is whitespace/emphasis-insensitive.

Exit non-zero if any row fails. Usage: verify_register.py [version ...]
"""
import json, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]          # repo root
REQ_DIR = ROOT / "conformance" / "requirements"
VENDOR = ROOT / "conformance" / ".vendor"
sys.path.insert(0, str(ROOT / "conformance"))
# VERSION_TREE used to be a private copy here — one of five independent lists across
# the suite (PLAN-0825 G0-b / A.4, the version-map whack-a-mole seam) — now the single
# shared source every consumer imports; see conformance/common/spec_versions.py.
from common.spec_versions import VERSION_TREE  # noqa: E402

def norm(s: str) -> str:
    s = s.replace("**", "").replace("`", "").replace("_", "")
    s = s.replace("|", " ").replace("…", "...")
    return re.sub(r"\s+", " ", s).strip().lower()

def parse_source(src: str):
    # "<repo>:<path>#<anchor>"  -> (repo, path, [line ints])
    repo_path, _, anchor = src.partition("#")
    repo, _, path = repo_path.partition(":")
    lines = [int(n) for n in re.findall(r"L(\d+)", anchor)]
    return repo, path, lines

def load_file(repo: str, path: str, ucp_dir: str = "ucp"):
    root = ucp_dir if repo == "ucp" else repo
    f = VENDOR / root / path
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8", errors="replace").splitlines()

def check_row(row, ucp_dir="ucp"):
    src = row.get("source", "")
    quote = row.get("quote", "")
    repo, path, lines = parse_source(src)
    flines = load_file(repo, path, ucp_dir)
    if flines is None:
        return ("FILE_MISSING", f"{repo}:{path}")
    nfile = norm("\n".join(flines))
    # quote may be several snippets joined by "..."
    fragments = [f for f in re.split(r"\.\.\.|…", quote) if norm(f)]
    missing = [f.strip()[:60] for f in fragments if norm(f) not in nfile]
    if missing:
        return ("QUOTE_NOT_FOUND", "; ".join(missing))
    # locality: warn if the first fragment isn't within +/-6 of any cited line
    if lines:
        first = norm(fragments[0])
        hit_line = next((i + 1 for i in range(len(flines))
                         if first in norm(flines[max(0, i-1)] + " " + flines[i] +
                                          " " + (flines[i+1] if i+1 < len(flines) else ""))), None)
        if hit_line and all(abs(hit_line - L) > 6 for L in lines):
            return ("LINE_OFF", f"quote near L{hit_line}, cited {lines}")
    return ("OK", "")

def load_version_rows(ver):
    """Every row of every register file under conformance/requirements/<ver>/."""
    rows = []
    vdir = REQ_DIR / ver
    if not vdir.is_dir():
        return rows
    for af in sorted(vdir.glob("*.json")):
        rows += json.loads(af.read_text()).get("rows", [])
    return rows


def find_dupes(rows):
    """Duplicate register rows (D2-02 / A2): sorted (id, id) pairs of rows that quote
    the SAME normalized text from the SAME `source` under the SAME `keyword`.

    The register owns each keyword sentence exactly once; two rows meeting all three
    criteria are the same obligation registered twice (CHK-056/SAE-018, DSC-006/
    DSC-029 at 2026-08-25 before A2). The criteria are deliberately ALL three:
      - same normalized quote alone is NOT a dupe (a shared sentence quoted from
        different files — the four MCP `meta` rows, the TLS rows per transport doc —
        is one obligation per document);
      - same quote + source but a different keyword is NOT a dupe (a SHOULD clause
        carved from a MUST sentence, CHK-004/CHK-005);
      - a schema `required[]` array quoted once per member (ORD-003/ORD-004) is
        excluded by requiring the quote to be prose: sources under `source/schemas`
        never pair here (one array, one row per required member, by design)."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        src = r.get("source", "")
        if ":source/schemas/" in src or ":source/services/" in src:
            continue
        key = (norm(r.get("quote", "")), src, r.get("keyword"))
        if key[0]:
            groups[key].append(r.get("id"))
    pairs = []
    for ids in groups.values():
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.append((ids[i], ids[j]))
    return sorted(pairs)


def manual_mislabels(rows, check_ids):
    """Ids of rows labelled `testability: manual` that a shipped CHECK grades at this
    version. "manual" asserts no check can observe the obligation, so a CHECK on it
    is a contradiction: the row is mislabelled (decision 32; the 31 agent-CHECK rows
    at 2026-08-25 relabelled `testable` by D2-02)."""
    return sorted(r["id"] for r in rows
                  if r.get("testability") == "manual" and r.get("id") in check_ids)


# Versions where a manual-but-CHECK row FAILS the `register` gate. Decision 32
# (2026-09-10) adjudicated the 31 mislabelled rows at 2026-08-25 (relabelled
# `testable` by D2-02). The 2026-04-08 register carries 33 rows in the same
# contradiction (32 of them EXEMPT on the merchant axis as irreducibly-manual, plus
# IDL-062 GAP) — their relabel changes 04-08's published gap_by_testability and
# collides with the "never EXEMPT a testable row" rule, so it belongs to the role /
# testability pass (D2-08, W1) and the manual-row EXEMPT pass (D2-13, W2), not to a
# W0 data edit. Until then those versions REPORT (WARN) rather than fail — fail-noisy,
# never silent. Extend this tuple as each version's pass lands.
RELABEL_GATED_VERSIONS = ("2026-08-25",)


def _shipped_check_ids(ver):
    """Ids graded by a shipped CHECK at `ver` on the AGENT axis (agent_checks.CHECKS,
    per-check `versions` scope) — the axis whose rows were mislabelled. The merchant
    axis is matrix.py's covmap; it is not imported here (the `register` gate stays
    light), and coverage_gate separately forbids a merchant CHECK over a `manual`
    row via its own export-freshness path. Subprocess-free import, same pattern as
    agent_matrix.agent_check_ids()."""
    sys.path.insert(0, str(ROOT / "conformance" / "agent"))
    import importlib
    try:
        am = importlib.import_module("agent_matrix")
        return set(am.agent_check_ids(ver))
    except Exception as e:                       # noqa: BLE001 — surfaced, never silent
        print(f"  WARN  agent axis unavailable for the manual-relabel check: {e!r}")
        return set()


def main(argv):
    if argv and argv[0] == "--dupes":
        versions = argv[1:] or [p.name for p in sorted(REQ_DIR.iterdir()) if p.is_dir()]
        n = 0
        for ver in versions:
            pairs = find_dupes(load_version_rows(ver))
            for a, b in pairs:
                print(f"  DUPE  {ver}: {a} == {b} (same quote, source, keyword)")
            n += len(pairs)
        print(f"\nregister dupes: {n} duplicate pairs")
        return 1 if n else 0

    versions = argv or [p.name for p in sorted(REQ_DIR.iterdir()) if p.is_dir()]
    total = ok = warn = fail = 0
    mislabels = 0
    for ver in versions:
        vdir = REQ_DIR / ver
        if not vdir.is_dir():
            continue
        ucp_dir = VERSION_TREE.get(ver, "ucp")
        vrows = []
        for af in sorted(vdir.glob("*.json")):
            data = json.loads(af.read_text())
            for row in data.get("rows", []):
                vrows.append(row)
                total += 1
                status, detail = check_row(row, ucp_dir)
                if status == "OK":
                    ok += 1
                elif status == "LINE_OFF":
                    warn += 1
                    print(f"  WARN  {row['id']:10} {status}: {detail}")
                else:
                    fail += 1
                    print(f"  FAIL  {row['id']:10} {status}: {detail}  [{row.get('source')}]")
        # D2-02: a `manual` row graded by a shipped agent CHECK is mislabelled. GATED
        # at the versions decision 32 adjudicated (RELABEL_GATED_VERSIONS); reported
        # (WARN, rc unchanged) elsewhere — see the constant's comment.
        gated = ver in RELABEL_GATED_VERSIONS
        for rid in manual_mislabels(vrows, _shipped_check_ids(ver)):
            mislabels += 1 if gated else 0
            print(f"  {'FAIL' if gated else 'WARN'}  {rid:10} MANUAL_BUT_CHECK: labelled "
                  f"testability=manual yet graded by agent_checks at {ver} — "
                  f"{'relabel `testable` (decision 32)' if gated else 'report-only at this version (D2-08/D2-13 pass)'}")
    print(f"\nregister quote-check: {ok}/{total} verified, {warn} line-warnings, {fail} FAILED, "
          f"{mislabels} manual-but-CHECK mislabels (gated at {', '.join(RELABEL_GATED_VERSIONS)})")
    return 1 if (fail or mislabels) else 0

def selftest():
    """D2-02 kill-tests, hermetic (synthetic rows, no vendored I/O).

    dupes fixture: two mandatory rows quoting the SAME normalized text from the
    SAME source under the SAME keyword are one duplicate pair — the register must
    own each MUST sentence once (A2). A third row with the same quote but a
    different keyword (a SHOULD clause carved from the same sentence) is NOT a
    duplicate.

    relabel fixture: a row labelled `testability: manual` whose id is graded by
    a shipped CHECK at that version is mislabelled — "manual" means no check can
    observe it, so a CHECK on it is a contradiction the `register` gate must red
    on (decision 32; the 31 agent-CHECK rows at 2026-08-25)."""
    bad = 0
    a = {"id": "ZZZ-001", "keyword": "MUST", "testability": "testable",
         "source": "ucp:docs/specification/x.md#L10-L11",
         "quote": "The platform **MUST** send\nthe entire resource."}
    b = {**a, "id": "ZZZ-002",
         "quote": "The platform MUST send the entire resource."}      # same after norm()
    c = {**a, "id": "ZZZ-003", "keyword": "SHOULD"}                   # same quote, SHOULD clause
    pairs = find_dupes([a, b, c])
    ok = pairs == [("ZZZ-001", "ZZZ-002")]
    print(f"  {'✓' if ok else '✗'} dupes fixture: {len(pairs)} duplicate pair(s) {pairs}"
          + ("" if ok else "  <-- expected exactly [('ZZZ-001', 'ZZZ-002')]"))
    bad += 0 if ok else 1

    rows = [{"id": "ZZZ-010", "keyword": "MUST", "testability": "manual"},
            {"id": "ZZZ-011", "keyword": "MUST", "testability": "manual"},
            {"id": "ZZZ-012", "keyword": "MUST", "testability": "testable"}]
    mis = manual_mislabels(rows, check_ids={"ZZZ-011", "ZZZ-012"})
    ok = mis == ["ZZZ-011"]
    print(f"  {'✓' if ok else '✗'} relabel fixture: manual-but-CHECK rows = {mis}"
          + ("" if ok else "  <-- expected ['ZZZ-011']"))
    bad += 0 if ok else 1

    print(f"\nverify_register selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

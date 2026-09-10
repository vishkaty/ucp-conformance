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

def main(argv):
    versions = argv or [p.name for p in sorted(REQ_DIR.iterdir()) if p.is_dir()]
    total = ok = warn = fail = 0
    for ver in versions:
        vdir = REQ_DIR / ver
        if not vdir.is_dir():
            continue
        ucp_dir = VERSION_TREE.get(ver, "ucp")
        for af in sorted(vdir.glob("*.json")):
            data = json.loads(af.read_text())
            for row in data.get("rows", []):
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
    print(f"\nregister quote-check: {ok}/{total} verified, {warn} line-warnings, {fail} FAILED")
    return 1 if fail else 0

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

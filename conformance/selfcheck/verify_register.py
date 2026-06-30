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

def load_file(repo: str, path: str):
    f = VENDOR / repo / path
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8", errors="replace").splitlines()

def check_row(row):
    src = row.get("source", "")
    quote = row.get("quote", "")
    repo, path, lines = parse_source(src)
    flines = load_file(repo, path)
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
        for af in sorted(vdir.glob("*.json")):
            data = json.loads(af.read_text())
            for row in data.get("rows", []):
                total += 1
                status, detail = check_row(row)
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

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
verify_register_completeness.py — the DENOMINATOR gate.

verify_register.py proves every register ROW has a real quote. This proves the
reverse: every mandatory (RFC-2119) keyword in the pinned prose spec BECAME a row
— i.e. nothing normative was missed during extraction. Without this, a coverage
percentage is a fraction of an unverified denominator.

For each pinned version it scans every prose spec file (docs/specification/**/*.md)
for mandatory keyword occurrences (MUST, MUST NOT, SHALL, SHALL NOT, REQUIRED),
outside code fences and excluding the RFC-2119 boilerplate. Each occurrence must be
ACCOUNTED, one of two ways:

  1. Covered by a register row for that version that cites the same file, matched
     either by the row's quote sitting on that line or by a cited line within a
     small window.
  2. Explicitly WAIVED in register_completeness_waivers.json with a class + reason
     (a duplicate restatement, a non-normative example/definition, or a prose MUST
     that a schema row enforces structurally).

Any keyword occurrence that is neither covered nor waived FAILS the build — it is a
normative clause with no test and no acknowledgement, exactly the silent gap this
gate exists to make impossible.

Usage:
  verify_register_completeness.py            # gate: exit 1 on any unaccounted
  verify_register_completeness.py --report   # print every unaccounted occurrence
  verify_register_completeness.py --json      # machine-readable summary
"""
import json, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQ_DIR = ROOT / "conformance" / "requirements"
VENDOR = ROOT / "conformance" / ".vendor"
WAIVERS = ROOT / "conformance" / "coverage" / "register_completeness_waivers.json"

VERSION_TREE = {
    "2026-04-08": "ucp",
    "2026-01-23": "ucp-2026-01-23",
    "2026-01-11": "ucp-2026-01-11",
}
VALID_WAIVER_CLASSES = {"duplicate", "non-normative", "schema-enforced"}
# scope exclusions are file-level and carry an extra reason class: a whole spec file
# whose obligations are structurally outside what a server-endpoint checker can observe
# (e.g. browser-embedded MessagePort UI) or are non-normative (narrative/examples/guides).
VALID_SCOPE_CLASSES = {"out-of-scope", "non-normative-doc"}

# longest-first so "MUST NOT" wins over "MUST"; all-caps only (normative form)
KW_RE = re.compile(r"\b(MUST NOT|MUST|SHALL NOT|SHALL|REQUIRED)\b")


def norm(s: str) -> str:
    s = s.replace("**", "").replace("`", "").replace("_", "")
    s = s.replace("|", " ").replace("…", "...")
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_source(src: str):
    repo_path, _, anchor = src.partition("#")
    repo, _, path = repo_path.partition(":")
    lines = [int(n) for n in re.findall(r"L(\d+)", anchor)]
    return repo, path, lines


def spec_files(ucp_dir: str):
    base = VENDOR / ucp_dir / "docs" / "specification"
    if not base.is_dir():
        return []
    return sorted(base.rglob("*.md"))


def scan_keywords(path: pathlib.Path):
    """Yield (lineno, keyword, raw_line) for each mandatory keyword outside code
    fences, skipping the RFC-2119 boilerplate definition."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_fence = False
    out = []
    for i, raw in enumerate(lines, start=1):
        stripped = raw.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # the RFC-2119 boilerplate defines the keywords; not itself a requirement
        low = raw.lower()
        if "interpreted as described in" in low or "the key words" in low:
            continue
        # strip emphasis so "MUST **NOT**" still reads as MUST NOT
        probe = raw.replace("**", "").replace("`", "")
        for m in KW_RE.finditer(probe):
            out.append((i, m.group(1), raw))
    return out, lines


def covered_lines_for(rows, flines):
    """The set of line numbers in this file covered by these register rows.

    Coverage is QUOTE-CONTENT based, not proximity based: a keyword line is covered
    only if some row's actual quote text sits on it. This is deliberately tight — a
    loose +/-N window would let a row be deleted (shrinking the denominator to inflate
    the percentage) while an adjacent row's window still 'covered' the orphaned line.
    So we mark a line covered when: the row's normalized quote fragment appears on that
    line, OR that line's own text is contained in the fragment (which marks every line
    a multi-line quote spans). The row's exact cited line is added only as a EXACT
    anchor (no window), to tolerate a quote that is paraphrased around a precisely
    cited line."""
    covered = set()
    nfile_lines = [norm(x) for x in flines]
    for row in rows:
        _, _, cited = parse_source(row.get("source", ""))
        for L in cited:
            covered.add(L)                       # exact cited anchor, no window
        for frag in re.split(r"\.\.\.|…", row.get("quote", "")):
            nf = norm(frag)
            if len(nf) < 8:
                continue
            for idx, nl in enumerate(nfile_lines, start=1):
                # fragment sits on this line, OR this line is part of a multi-line quote
                if nf in nl or (len(nl) >= 12 and nl in nf):
                    covered.add(idx)
    return covered


def load_waivers():
    if not WAIVERS.exists():
        return {}, [], {}, []
    data = json.loads(WAIVERS.read_text())
    idx = {}
    for w in data.get("waivers", []):
        key = (w["version"], w["file"], int(w["line"]))
        idx[key] = w
    # scope exclusions: (version, file) -> exclusion record; "versions": "*" means all
    scope_idx = {}
    for sx in data.get("scope_exclusions", []):
        vers = sx.get("versions")
        vlist = list(VERSION_TREE) if vers in ("*", None) else vers
        for v in vlist:
            scope_idx[(v, sx["file"])] = sx
    return idx, data.get("waivers", []), scope_idx, data.get("scope_exclusions", [])


def validate_waiver(w):
    errs = []
    if w.get("class") not in VALID_WAIVER_CLASSES:
        errs.append(f"bad class {w.get('class')!r} (valid: {sorted(VALID_WAIVER_CLASSES)})")
    reason = (w.get("reason") or "").strip()
    if len(reason) < 30:
        errs.append("reason too thin (<30 chars) — say WHY it is not a missed MUST")
    if w.get("class") == "duplicate" and not w.get("duplicate_of"):
        errs.append("class 'duplicate' requires 'duplicate_of' (the row id it restates)")
    if w.get("class") == "schema-enforced" and not w.get("row_id"):
        errs.append("class 'schema-enforced' requires 'row_id' (the schema row that enforces it)")
    return errs


def validate_scope(sx):
    errs = []
    if sx.get("class") not in VALID_SCOPE_CLASSES:
        errs.append(f"bad scope class {sx.get('class')!r} (valid: {sorted(VALID_SCOPE_CLASSES)})")
    reason = (sx.get("reason") or "").strip()
    if len(reason) < 60:
        errs.append("scope reason too thin (<60 chars) — justify WHY the whole file is not "
                    "server-observable normative surface")
    if not sx.get("file"):
        errs.append("scope exclusion needs 'file'")
    return errs


def rows_by_version_file():
    out = {}
    for vdir in sorted(REQ_DIR.iterdir()):
        if not vdir.is_dir():
            continue
        ver = vdir.name
        for af in sorted(vdir.glob("*.json")):
            for row in json.loads(af.read_text()).get("rows", []):
                if ver not in (row.get("versions") or [ver]):
                    continue
                _, path, _ = parse_source(row.get("source", ""))
                out.setdefault((ver, path), []).append(row)
    return out


def main(argv):
    report = "--report" in argv
    as_json = "--json" in argv
    rvf = rows_by_version_file()
    waiver_idx, waiver_list, scope_idx, scope_list = load_waivers()

    # validate every waiver / scope exclusion up front — a bogus one is itself a failure
    waiver_errs = []
    for w in waiver_list:
        for e in validate_waiver(w):
            waiver_errs.append((w.get("version"), w.get("file"), w.get("line"), e))
    for sx in scope_list:
        for e in validate_scope(sx):
            waiver_errs.append(("scope", sx.get("file"), "-", e))

    used_waivers = set()
    used_scopes = set()
    per_version = {}
    unaccounted = []

    for ver, ucp_dir in VERSION_TREE.items():
        total = covered = waived = scoped = missed = 0
        for path in spec_files(ucp_dir):
            rel = str(path.relative_to(VENDOR / ucp_dir))
            rows = rvf.get((ver, rel), [])
            occ, flines = scan_keywords(path)
            excluded = (ver, rel) in scope_idx
            cov = covered_lines_for(rows, flines) if (occ and not excluded) else set()
            for (lineno, kw, raw) in occ:
                total += 1
                if excluded:
                    scoped += 1
                    used_scopes.add((ver, rel))
                    continue
                if lineno in cov:
                    covered += 1
                    continue
                key = (ver, rel, lineno)
                if key in waiver_idx:
                    waived += 1
                    used_waivers.add(key)
                    continue
                missed += 1
                unaccounted.append((ver, rel, lineno, kw, raw.strip()[:90]))
        per_version[ver] = dict(total=total, covered=covered, waived=waived,
                                scoped=scoped, missed=missed)

    stale_waivers = [k for k in waiver_idx if k not in used_waivers]
    stale_scopes = [k for k in scope_idx if k not in used_scopes]

    if as_json:
        print(json.dumps(dict(per_version=per_version, unaccounted=len(unaccounted),
                              waiver_errors=len(waiver_errs), stale_waivers=len(stale_waivers)),
                         indent=2))
        return 1 if (unaccounted or waiver_errs) else 0

    print("register-completeness — every mandatory keyword in prose must be a row, a "
          "waiver, or an in-scope-excluded file\n")
    for ver, s in per_version.items():
        flag = "" if s["missed"] == 0 else f"  <-- {s['missed']} UNACCOUNTED"
        print(f"  {ver}:  {s['total']:4} kw   {s['covered']:4} covered   "
              f"{s['scoped']:4} scope-excl   {s['waived']:3} waived   {s['missed']:3} missed{flag}")

    if waiver_errs:
        print(f"\n  {len(waiver_errs)} INVALID waiver/scope record(s):")
        for ver, f, l, e in waiver_errs[:40]:
            print(f"    FAIL  {ver} {f}:{l}  {e}")
    if stale_waivers:
        print(f"\n  {len(stale_waivers)} STALE waiver(s) (no longer match a keyword — remove them):")
        for (ver, f, l) in stale_waivers[:40]:
            print(f"    STALE {ver} {f}:{l}")
    if stale_scopes:
        print(f"\n  {len(stale_scopes)} STALE scope exclusion(s) (file has no keywords / renamed):")
        for (ver, f) in stale_scopes[:40]:
            print(f"    STALE {ver} {f}")

    if report or unaccounted:
        shown = unaccounted if report else unaccounted[:60]
        print(f"\n  {len(unaccounted)} unaccounted keyword occurrence(s)"
              + (f" (showing {len(shown)})" if not report and len(unaccounted) > len(shown) else "") + ":")
        for (ver, f, l, kw, txt) in shown:
            print(f"    {ver}  {f}:{l}  [{kw}]  {txt}")

    ok = not unaccounted and not waiver_errs
    print(f"\nregister-completeness: {'PASS' if ok else 'FAIL'}"
          + ("" if ok else "  — extract the missed clause as a row, or waive it with a reason"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

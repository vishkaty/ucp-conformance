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
     (a duplicate restatement whose `duplicate_of` RESOLVES to a register row at that
     version, a non-normative example/definition, a prose MUST that a schema row
     enforces structurally, an obligation binding an out-of-scope transport, or one
     binding the AUTHORS of an ecosystem document — `spec-authoring`, which must name
     the `authored_doc` class it binds).

Any keyword occurrence that is neither covered nor waived FAILS the build — it is a
normative clause with no test and no acknowledgement, exactly the silent gap this
gate exists to make impossible.

Usage:
  verify_register_completeness.py            # gate: exit 1 on any unaccounted
  verify_register_completeness.py --report   # print every unaccounted occurrence
  verify_register_completeness.py --json      # machine-readable summary
"""
import json, re, sys, pathlib
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQ_DIR = ROOT / "conformance" / "requirements"
VENDOR = ROOT / "conformance" / ".vendor"
WAIVERS = ROOT / "conformance" / "coverage" / "register_completeness_waivers.json"
sys.path.insert(0, str(ROOT / "conformance"))
# VERSION_TREE and REPORT_MODE_UNTIL used to be private copies here — two of five
# independent lists across the suite (PLAN-0825 G0-b / A.4, the version-map
# whack-a-mole seam) — now the single shared source every consumer imports; see
# conformance/common/spec_versions.py for the full doctrine on both (report-mode's
# fail-noisy self-expiry included).
from common.spec_versions import VERSION_TREE, REPORT_MODE_UNTIL  # noqa: E402
from common.keywords import KW_RE  # noqa: E402 — D2-01: the shared longest-first MANDATORY regex

# `out-of-scope-transport` (D2-05 / A13, interim until decision 2's extraction in
# W2): a mandatory-keyword hit whose obligation binds a transport the suite cannot
# drive today (the cart/embedded.md MessagePort host<->iframe duties). It REQUIRES a
# `transport` so the surface export can say which transport the waived hits belong
# to — never a bare "not our problem" class.
# `spec-authoring` (F5 G1): a mandatory-keyword hit whose obligation binds the AUTHORS of
# an ecosystem specification document — a capability transport definition, an extension
# schema, a payment-handler specification — a party distinct from any implementation under
# test, so conformance to it is established by document review and no runtime message can
# prove or refute it. The class is NOT new to the project: conformance/coverage/
# exemptions.json has adjudicated 12 register rows under it (DISC-006, DISC-009, DISC-010,
# FUL-029, PAY-001/004/005/006/007/025/028/029) and coverage_gate.py's EXEMPT_CLASSES
# carries it with that definition. Its absence here was the gap: an author-bound prose MUST
# in an in-scope file had no honest label, so it was recorded as `non-normative` ("this
# keyword is not a requirement at all"), a materially different and weaker claim that
# carries no evidence requirement whatever.
# It REQUIRES an `authored_doc` from VALID_AUTHORED_DOCS — the same evidentiary discipline
# its siblings carry (`duplicate` names the row it restates, `schema-enforced` names the
# enforcing schema row, `out-of-scope-transport` names the transport) — so the surface
# export can say WHICH authoring party the waived hits belong to, never a bare
# "binds someone else, not our problem".
VALID_WAIVER_CLASSES = {"duplicate", "non-normative", "schema-enforced",
                        "out-of-scope-transport", "spec-authoring"}
VALID_TRANSPORTS = {"rest", "mcp", "a2a", "embedded"}
# The closed vocabulary of ecosystem document classes a `spec-authoring` waiver may name,
# derived from the obligations the 12 committed exemptions.json spec-authoring entries
# actually bind — not invented here:
#   transport-definition — a capability's OpenAPI/OpenRPC transport-definition document
#                          published by the namespace authority (DISC-006, DISC-009)
#   extension-schema     — an extension / composed JSON Schema document published by a
#                          capability authority (DISC-010, FUL-029)
#   handler-spec         — a payment-handler specification document, including the
#                          credential schemas it defines (PAY-001/004/005/006/007/025/
#                          028/029)
VALID_AUTHORED_DOCS = {"transport-definition", "extension-schema", "handler-spec"}
# scope exclusions are file-level and carry an extra reason class: a whole spec file
# whose obligations are structurally outside what a server-endpoint checker can observe
# (e.g. browser-embedded MessagePort UI) or are non-normative (narrative/examples/guides).
VALID_SCOPE_CLASSES = {"out-of-scope", "non-normative-doc"}

# KW_RE (imported above) is the shared MANDATORY-class regex — longest-first so
# "MUST NOT" wins over "MUST"; all-caps only (normative form). It is derived from the
# same tuple matrix/coverage_gate/agent_matrix filter register rows by, so the census
# and the accounting denominator can never disagree about what "mandatory" means.


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


def scan_keywords(path: pathlib.Path, kw_re=KW_RE):
    """Yield (lineno, keyword, raw_line) for each mandatory keyword outside code
    fences, skipping the RFC-2119 boilerplate definition. `kw_re` defaults to the
    MANDATORY class; the SHOULD census (verify_should_census.py, D2-10) passes
    SHOULD_RE so both censuses share one fence/boilerplate scan."""
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
        for m in kw_re.finditer(probe):
            out.append((i, m.group(1), raw))
    return out, lines


def flatten_lines(flines):
    """Build (flat_text, line_spans) for line-boundary-agnostic quote matching (R13).

    flat_text is every line's normalized text (norm() already collapses whitespace
    including newlines) concatenated with a single separating space; line_spans maps
    each line's [start, end) character range in flat_text back to its 1-based line
    number. Blank normalized lines carry no span — they can never hold a keyword or
    quoted content, and including them would just add ambiguous zero-width spans.
    """
    parts = []
    spans = []
    pos = 0
    for idx, raw in enumerate(flines, start=1):
        nl = norm(raw)
        if not nl:
            continue
        parts.append(nl)
        spans.append((pos, pos + len(nl), idx))
        pos += len(nl) + 1
        parts.append(" ")
    return "".join(parts), spans


def covered_lines_for(rows, flines):
    """The set of line numbers in this file covered by these register rows.

    Coverage is QUOTE-CONTENT based, not proximity based: a keyword line is covered
    only if some row's actual quote text sits on it. This is deliberately tight — a
    loose +/-N window would let a row be deleted (shrinking the denominator to inflate
    the percentage) while an adjacent row's window still 'covered' the orphaned line.

    R13 fix: matching happens against the WHOLE FILE'S flattened text (line breaks
    normalized away, same as a fragment's own newlines already were via norm()), not
    per physical line. The original per-line matcher required either the fragment to
    sit wholly on one line or a line's FULL text to be wholly contained in the
    fragment — so a row whose "..." elision lands mid-physical-line (the elided text
    starts or ends partway through what the vendored file renders as one line, e.g. a
    prose sentence wrapped across lines where the kept prefix ends mid-line and the
    kept suffix resumes mid-line) left that physical line's real coverage invisible:
    the fragment neither fully contains nor is fully contained by the line, even
    though the row's quote is verbatim over the span it does cover (proven on
    IDL-012/030/050, whose elisions land inside physical lines 173 and 175 of
    identity-linking/index.md — see validate_completeness_matcher.py). Matching in
    flattened-file space instead marks a line covered whenever the matched span
    OVERLAPS that line's range at all, which is exactly "this line carries some
    verbatim-quoted content" — no proximity window, still quote-content anchored, and
    the anti-gaming property (a deleted row can't be covered by a neighbor's fuzzy
    window) is unaffected because the match is still exact substring content, just
    unbound from physical line boundaries. The row's exact cited line is added
    separately as an EXACT anchor (no window), to tolerate a quote that is
    paraphrased around a precisely cited line.
    """
    covered = set()
    flat_text, spans = flatten_lines(flines)
    for row in rows:
        _, _, cited = parse_source(row.get("source", ""))
        for L in cited:
            covered.add(L)                       # exact cited anchor, no window
        for frag in re.split(r"\.\.\.|…", row.get("quote", "")):
            nf = norm(frag)
            if len(nf) < 8:
                continue
            start = 0
            while True:
                idx = flat_text.find(nf, start)
                if idx == -1:
                    break
                mstart, mend = idx, idx + len(nf)
                for (lstart, lend, lineno) in spans:
                    if lstart < mend and lend > mstart:
                        covered.add(lineno)
                start = idx + 1
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


ROW_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def register_row_ids():
    """{version: {row ids present at that version}} — the resolution target for
    `duplicate_of` (F5 G2). Built from the same per-version register walk the coverage
    accounting uses, honouring each row's `versions` scope, so an id that exists only at
    another pin does not resolve here."""
    out = {}
    if not REQ_DIR.is_dir():
        return out
    for vdir in sorted(REQ_DIR.iterdir()):
        if not vdir.is_dir() or vdir.name[:2] != "20":
            continue
        ver = vdir.name
        ids = out.setdefault(ver, set())
        for af in sorted(vdir.glob("*.json")):
            for row in json.loads(af.read_text()).get("rows", []):
                if ver in (row.get("versions") or [ver]):
                    ids.add(row.get("id"))
    return out


_ROW_IDS_CACHE = None


def validate_waiver(w, row_ids=None):
    """Validate one waiver record. `row_ids` is {version: {row ids}} for resolving
    `duplicate_of`; None means "load the committed register" (cached). Pass an explicit
    dict to prove the rule hermetically."""
    global _ROW_IDS_CACHE
    if row_ids is None:
        if _ROW_IDS_CACHE is None:
            _ROW_IDS_CACHE = register_row_ids()
        row_ids = _ROW_IDS_CACHE
    errs = []
    if w.get("class") not in VALID_WAIVER_CLASSES:
        errs.append(f"bad class {w.get('class')!r} (valid: {sorted(VALID_WAIVER_CLASSES)})")
    reason = (w.get("reason") or "").strip()
    if len(reason) < 30:
        errs.append("reason too thin (<30 chars) — say WHY it is not a missed MUST")
    if w.get("class") == "duplicate":
        # F5 G2: HAVING a duplicate_of was never enough — nothing resolved the id it
        # named, so a ghost pointer ('NEW:overview.md#L83', 'new_row@ucp:...#L110', a bare
        # line cite) passed as a reconciliation while reconciling the hit against nothing.
        # Resolve it against the register FOR THIS WAIVER'S OWN VERSION: the 2026-04-08
        # renumbering means the same id can be a different requirement, or absent, at
        # another pin, so a cross-version hit is not a hit. The failure names the id.
        dof = w.get("duplicate_of")
        if not dof:
            errs.append("class 'duplicate' requires 'duplicate_of' (the row id it restates)")
        else:
            ver = w.get("version")
            known = row_ids.get(ver) or set()
            named = [tok for tok in re.split(r"[\s,;/]+", str(dof)) if ROW_ID_RE.match(tok)]
            if not named:
                errs.append(f"class 'duplicate' duplicate_of {dof!r} names no register row id "
                            f"(expected e.g. 'OVR-003'; a line pointer, a 'NEW:' / 'new_row@' "
                            f"placeholder or prose is not a resolvable pointer)")
            else:
                missing = [tok for tok in named if tok not in known]
                if missing:
                    errs.append(f"class 'duplicate' duplicate_of names {missing} which is not a "
                                f"register row at {ver} (duplicate_of={dof!r}) — a duplicate of "
                                f"a row that does not exist reconciles nothing; point it at the "
                                f"real row id for this version")
    if w.get("class") == "schema-enforced" and not w.get("row_id"):
        errs.append("class 'schema-enforced' requires 'row_id' (the schema row that enforces it)")
    if w.get("class") == "out-of-scope-transport" and w.get("transport") not in VALID_TRANSPORTS:
        errs.append(f"class 'out-of-scope-transport' requires 'transport' in "
                    f"{sorted(VALID_TRANSPORTS)} (got {w.get('transport')!r})")
    if w.get("class") == "spec-authoring" and w.get("authored_doc") not in VALID_AUTHORED_DOCS:
        errs.append(f"class 'spec-authoring' requires 'authored_doc' in "
                    f"{sorted(VALID_AUTHORED_DOCS)} — name WHICH ecosystem document class "
                    f"the obligation binds (got {w.get('authored_doc')!r})")
    return errs


def stale_file_errors(waiver_idx, scope_idx, vendor=None, version_tree=None):
    """(version, file, line, message) for every waiver/scope-exclusion entry whose
    target file does not exist AT ALL in the vendored spec tree for the version it
    claims to cover (PLAN-0825 A.2 / P-2 fail-noisy self-expiry): "every existing
    scope-exclusion and waiver is keyed by file path. At 08-25 those paths moved.
    ... the gate must FAIL on any waiver/scope-exclusion entry, scoped to a version,
    whose file does not exist in that version's tree — a stale waiver is a stale
    claim, and today it would go silently inert."

    Deliberately narrower than the pre-existing `stale_waivers`/`stale_scopes` report
    below (unused entries — a file that exists but coincidentally matched zero
    occurrences during the scan, e.g. a row was deleted or the file lost its last
    keyword): that case is not necessarily a FALSE claim, so it stays a printed
    warning. THIS check is a factual claim ("this path exists at this version") that
    is simply wrong, exactly the #723-reorg hazard — always a hard gate failure, never
    merely a warning, kill-tested by validate_spec_versions.py planting a waiver at a
    ghost path."""
    vendor = VENDOR if vendor is None else vendor
    version_tree = VERSION_TREE if version_tree is None else version_tree
    errs = []
    for (ver, rel, line), _w in sorted(waiver_idx.items()):
        ucp_dir = version_tree.get(ver)
        if ucp_dir and not (vendor / ucp_dir / rel).exists():
            errs.append((ver, rel, line,
                         f"waiver targets {rel!r}, which does not exist under "
                         f"{ucp_dir}/ — the path moved or was deleted; fix the path or "
                         f"drop the waiver"))
    for (ver, rel), _sx in sorted(scope_idx.items()):
        ucp_dir = version_tree.get(ver)
        if ucp_dir and not (vendor / ucp_dir / rel).exists():
            errs.append((ver, rel, "-",
                         f"scope exclusion targets {rel!r}, which does not exist "
                         f"under {ucp_dir}/ — the path moved or was deleted; fix the "
                         f"path, scope the exclusion away from this version, or drop "
                         f"it"))
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


def report_mode_status(ver, today=None):
    """PURE — kill-testable via the `today` override (no real-clock dependency, same
    pattern as conformance/ci/sources_age.py's evaluate()). Returns one of:
      "gate"     — ver is not a report-mode version at all (misses always gate)
      "active"   — ver is report-mode and within its window (misses print, don't gate)
      "expired"  — ver is report-mode but PAST its flip-by date (misses now gate, loud)
    `today` is an ISO "YYYY-MM-DD" string; plain string comparison is correct for
    ISO-formatted dates."""
    deadline = REPORT_MODE_UNTIL.get(ver)
    if deadline is None:
        return "gate"
    today = today if today is not None else date.today().isoformat()
    return "active" if today <= deadline else "expired"


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


def main(argv, today=None):
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
    # P-2 fail-noisy self-expiry (PLAN-0825 A.2): a waiver/scope-exclusion whose file
    # does not exist AT ALL in the version's vendor tree is a stale, false claim — gate
    # it the same way a bogus class/reason already gates (waiver_errs), not just warn.
    waiver_errs += stale_file_errors(waiver_idx, scope_idx)

    used_waivers = set()
    used_scopes = set()
    per_version = {}
    unaccounted = []          # EVERY missed occurrence, report-mode or not (visibility)
    gating_unaccounted = []   # the subset that actually fails the build
    report_status = {ver: report_mode_status(ver, today) for ver in VERSION_TREE}

    for ver, ucp_dir in VERSION_TREE.items():
        total = covered = waived = scoped = missed = 0
        waived_by_class = {}      # hits per waiver class (a waived line can carry 2 hits)
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
                    wc = waiver_idx[key].get("class", "?")
                    waived_by_class[wc] = waived_by_class.get(wc, 0) + 1
                    used_waivers.add(key)
                    continue
                missed += 1
                row = (ver, rel, lineno, kw, raw.strip()[:90])
                unaccounted.append(row)
                # report-mode ("active"): counted and printed, but does not gate.
                # "gate" (not a report-mode version) or "expired" (past flip-by): gates.
                if report_status[ver] != "active":
                    gating_unaccounted.append(row)
        per_version[ver] = dict(total=total, covered=covered, waived=waived,
                                waived_by_class=dict(sorted(waived_by_class.items())),
                                scoped=scoped, missed=missed,
                                report_mode=report_status[ver])

    stale_waivers = [k for k in waiver_idx if k not in used_waivers]
    stale_scopes = [k for k in scope_idx if k not in used_scopes]

    if as_json:
        print(json.dumps(dict(per_version=per_version,
                              unaccounted=len(unaccounted),
                              gating_unaccounted=len(gating_unaccounted),
                              waiver_errors=len(waiver_errs), stale_waivers=len(stale_waivers),
                              # the flip-by date per report-mode version, exposed so a
                              # consumer (matrix.py's `building`-state census — PLAN-0825
                              # §E) never needs its own copy of REPORT_MODE_UNTIL: this
                              # is the single source of truth for that date.
                              report_mode_until=REPORT_MODE_UNTIL),
                         indent=2))
        return 1 if (gating_unaccounted or waiver_errs) else 0

    print("register-completeness — every mandatory keyword in prose must be a row, a "
          "waiver, or an in-scope-excluded file\n")
    for ver, s in per_version.items():
        rm = s["report_mode"]
        if rm == "active":
            flag = (f"  ({s['missed']} unaccounted — REPORT MODE until "
                    f"{REPORT_MODE_UNTIL[ver]}, not gated)" if s["missed"] else "  (report mode)")
        elif rm == "expired":
            flag = (f"  <-- {s['missed']} UNACCOUNTED — report mode EXPIRED "
                    f"{REPORT_MODE_UNTIL[ver]}, now GATING" if s["missed"]
                    else f"  (report mode expired {REPORT_MODE_UNTIL[ver]})")
        else:
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
        gating_set = set(gating_unaccounted)
        shown = unaccounted if report else unaccounted[:60]
        print(f"\n  {len(unaccounted)} unaccounted keyword occurrence(s) total "
              f"({len(gating_unaccounted)} gating, "
              f"{len(unaccounted) - len(gating_unaccounted)} report-mode)"
              + (f" (showing {len(shown)})" if not report and len(unaccounted) > len(shown) else "") + ":")
        for row in shown:
            ver, f, l, kw, txt = row
            tag = "" if row in gating_set else "  [report-mode]"
            print(f"    {ver}  {f}:{l}  [{kw}]  {txt}{tag}")

    ok = not gating_unaccounted and not waiver_errs
    print(f"\nregister-completeness: {'PASS' if ok else 'FAIL'}"
          + ("" if ok else "  — extract the missed clause as a row, waive it with a reason, "
                            "or (report-mode versions past their flip-by date) close the census"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

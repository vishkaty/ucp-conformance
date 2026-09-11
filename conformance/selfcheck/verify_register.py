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
from common.spec_versions import VERSION_TREE, VERSIONS  # noqa: E402
from common.keywords import MANDATORY  # noqa: E402
from common.roles import ROLES, AGENT_LANE, valid_provenance  # noqa: E402
AGENT_LOCK = ROOT / "conformance" / "agent" / "agent_denominator_lock.json"

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


# ---------------------------------------------------------------- D2-11a: normative_basis (A6)
BASES = ("sentence", "table", "bullet", "pseudocode", "schema", "definition", "inferred")
REVIEW_BASES = ("definition", "inferred")
KW_IN_QUOTE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in sorted(MANDATORY, key=len, reverse=True)) + r")\b")


def basis_errors(rows, ver, queued_ids, signed_ids):
    """Every mandatory row at `ver` has `normative_basis` in BASES, or sits in the
    version's normative_basis_review_queue (D2-11b's set); `sentence` requires a
    MANDATORY keyword in the quote (emphasis stripped); `definition` / `inferred` need a
    review_signoffs batch naming the (version, id) (decision 29: an inferred MUST is
    kept only with a written, signed-off justification)."""
    errs = []
    for r in _mandatory_at(rows, ver):
        rid, basis = r.get("id"), r.get("normative_basis")
        if basis is None:
            if rid not in queued_ids:
                errs.append(f"{rid}: mandatory row without `normative_basis` and not in normative_basis_review_queue "
                            f"(run requirements/tools/assign_normative_basis.py --apply)")
            continue
        if basis not in BASES:
            errs.append(f"{rid}: normative_basis {basis!r} not in {list(BASES)}")
            continue
        if basis == "sentence" and not KW_IN_QUOTE.search((r.get("quote") or "").replace("**", "").replace("`", "")):
            errs.append(f"{rid}: normative_basis `sentence` but no mandatory keyword in the quote (table/bullet/inferred?)")
        if basis in REVIEW_BASES and rid not in signed_ids:
            errs.append(f"{rid}: normative_basis `{basis}` without a review_signoffs batch naming it at {ver}")
    return errs


def _signed_basis_ids(ver):
    """Ids named by any review_signoffs batch's `ids[ver]` (a batch with reviewer + date)."""
    f = ROOT / "conformance" / "coverage" / "review_signoffs.json"
    out = set()
    if not f.exists():
        return out
    for s in json.loads(f.read_text()).get("signoffs", []):
        if s.get("reviewer") and s.get("date"):
            out.update((s.get("ids") or {}).get(ver, []))
    return out


# ---------------------------------------------------------------- D2-08: roles (A4)
def _mandatory_at(rows, ver):
    return [r for r in rows if r.get("keyword") in MANDATORY
            and ver in (r.get("versions") or [ver])]


def role_errors(rows, ver, lock_ids):
    """Every mandatory row at `ver` carries `role` (ROLES) and `role_provenance` (an
    enum value or review:<batch>); the agent-denominator lock and the roles agree BOTH
    ways — a locked id must be an agent-lane role (platform|both|host), and an agent-lane
    row must be locked (else regenerate the lock deliberately: agent_matrix.py
    --snapshot-lock). Returns a list of messages (empty = clean)."""
    errs = []
    mand = _mandatory_at(rows, ver)
    ids = {r.get("id") for r in mand}
    for r in mand:
        rid, role, prov = r.get("id"), r.get("role"), r.get("role_provenance")
        if role is None:
            errs.append(f"{rid}: mandatory row without `role` (run requirements/tools/assign_roles.py --apply, then review the queue)")
            continue
        if role not in ROLES:
            errs.append(f"{rid}: role {role!r} not in {list(ROLES)}")
            continue
        if not valid_provenance(prov):
            errs.append(f"{rid}: role_provenance {prov!r} is neither an enum value nor review:<batch>")
        if rid in lock_ids and role not in AGENT_LANE:
            errs.append(f"{rid}: role {role} but the id is in the agent denominator lock at {ver} — "
                        f"re-adjudicate or regenerate the lock (agent_matrix.py --snapshot-lock)")
        if rid not in lock_ids and role in AGENT_LANE:
            errs.append(f"{rid}: role {role} (agent lane) but the id is NOT in the agent denominator lock at {ver} — "
                        f"regenerate the lock deliberately (agent_matrix.py --snapshot-lock)")
    for rid in sorted(lock_ids - ids):
        pass                                # a locked id with no mandatory row: agent_governance's business
    return errs


def queue_errors(queue, ver):
    """The review queue (requirements/<ver>/role_review_queue.json) must be empty."""
    if not queue:
        return []
    ids = [q.get("id") for q in queue]
    return [f"{ver}: role_review_queue has {len(ids)} unreviewed row(s): {ids[:12]}{' …' if len(ids) > 12 else ''} — "
            f"set role + role_provenance review:<batch> on each row and delete its queue entry"]


def lane_errors(rows, ver, agent_check_ids):
    """One-lane rule (agent side): a shipped AGENT check may grade only an agent-lane row
    (platform|both|host). An agent CHECK on a business-only / handler / spec-author row
    is a contradiction — either the row's role is wrong (it binds the platform too ->
    `both`) or the check cites the wrong id. The merchant side of the same rule lives in
    coverage_gate (merchant CHECK only on a merchant-lane row)."""
    errs = []
    for r in _mandatory_at(rows, ver):
        if r.get("id") in agent_check_ids and r.get("role") not in AGENT_LANE:
            errs.append(f"{r.get('id')}: role {r.get('role')} (not agent-lane) yet graded by an agent check at {ver} "
                        f"— re-adjudicate the role (both?) or fix the check's citation")
    return errs


def area_map_errors(area_map, register_areas, spec_capabilities):
    """requirements/<ver>/_area_capabilities.json as REGISTER DATA (RV1 #15, D2-08):
    every area present in the register is listed (null = core), and every capability
    name it maps to exists in the vendored spec at that version's pin (a typo'd name
    would silently drop a whole area from the CLI denominator — merchant.py
    applicable_areas fails closed on an unlisted area; this gate makes both failures
    visible at commit time, not at grading time)."""
    errs = []
    for area in sorted(register_areas):
        if area not in area_map:
            errs.append(f"register area {area!r} is not listed in _area_capabilities.json")
    for area, cap in sorted(area_map.items()):
        if cap is not None and cap not in spec_capabilities:
            errs.append(f"_area_capabilities.json {area!r} -> {cap!r}: capability name not found in the vendored spec")
    return errs


_CAP_RE = re.compile(r"dev\.ucp\.[a-z_]+(?:\.[a-z_]+)*")


def spec_capability_names(ucp_dir):
    """Every `dev.ucp.*` identifier mentioned in the vendored spec tree (docs/ + source/)."""
    names = set()
    for sub in ("docs", "source"):
        base = VENDOR / ucp_dir / sub
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix in (".md", ".json", ".yaml", ".yml") and f.is_file():
                names.update(_CAP_RE.findall(f.read_text(encoding="utf-8", errors="replace")))
    return names


def _area_map_and_areas(ver):
    vdir = REQ_DIR / ver
    amap = None
    f = vdir / "_area_capabilities.json"
    if f.exists():
        amap = json.loads(f.read_text()).get("areas", {})
    areas = set()
    for af in sorted(vdir.glob("*.json")):
        if af.name.startswith("_"):
            continue
        d = json.loads(af.read_text())
        if isinstance(d, dict) and "rows" in d:
            areas.add(d.get("_area") or af.stem)
    return amap, areas


def _lock_ids(ver):
    if not AGENT_LOCK.exists():
        return set()
    return set(json.loads(AGENT_LOCK.read_text()).get("versions", {}).get(ver, []))


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
        versions = argv[1:] or [p.name for p in sorted(REQ_DIR.iterdir()) if p.is_dir() and p.name[:2] == "20"]
        n = 0
        for ver in versions:
            pairs = find_dupes(load_version_rows(ver))
            for a, b in pairs:
                print(f"  DUPE  {ver}: {a} == {b} (same quote, source, keyword)")
            n += len(pairs)
        print(f"\nregister dupes: {n} duplicate pairs")
        return 1 if n else 0

    versions = argv or [p.name for p in sorted(REQ_DIR.iterdir())
                        if p.is_dir() and p.name[:2] == "20"]      # version dirs only (not tools/)
    total = ok = warn = fail = 0
    mislabels = 0
    role_fails = 0
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
        agent_checks = _shipped_check_ids(ver)
        for rid in manual_mislabels(vrows, agent_checks):
            mislabels += 1 if gated else 0
            print(f"  {'FAIL' if gated else 'WARN'}  {rid:10} MANUAL_BUT_CHECK: labelled "
                  f"testability=manual yet graded by agent_checks at {ver} — "
                  f"{'relabel `testable` (decision 32)' if gated else 'report-only at this version (D2-08/D2-13 pass)'}")
        # D2-08 (A4): roles — field + enum + provenance, lock consistency both ways,
        # empty review queue, agent-side one-lane rule, area map as register data.
        rerrs = role_errors(vrows, ver, _lock_ids(ver))
        qf = vdir / "role_review_queue.json"
        queue = json.loads(qf.read_text()).get("queue", []) if qf.exists() else []
        rerrs += queue_errors(queue, ver)
        rerrs += lane_errors(vrows, ver, agent_checks)
        amap, areas = _area_map_and_areas(ver)
        if amap is None:
            rerrs.append(f"{ver}: requirements/{ver}/_area_capabilities.json missing (D1-03 seeds it; D2-08 validates it)")
        else:
            rerrs += area_map_errors(amap, areas, spec_capability_names(ucp_dir))
        for e in rerrs:
            print(f"  FAIL  ROLE/AREA {ver}: {e}")
        role_fails += len(rerrs)
        # D2-11a (A6): normative_basis — enum, sentence⇒keyword, definition|inferred⇒signed,
        # null only for queued rows.
        bqf = vdir / "normative_basis_review_queue.json"
        bqueue = {q.get("id") for q in json.loads(bqf.read_text()).get("queue", [])} if bqf.exists() else set()
        berrs = basis_errors(vrows, ver, bqueue, _signed_basis_ids(ver))
        for e in berrs:
            print(f"  FAIL  BASIS {ver}: {e}")
        role_fails += len(berrs)
    print(f"\nregister quote-check: {ok}/{total} verified, {warn} line-warnings, {fail} FAILED, "
          f"{mislabels} manual-but-CHECK mislabels (gated at {', '.join(RELABEL_GATED_VERSIONS)}), "
          f"{role_fails} role/area/basis failures")
    return 1 if (fail or mislabels or role_fails) else 0

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

    bad += _selftest_roles()
    bad += _selftest_normative_basis()

    print(f"\nverify_register selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


def _selftest_normative_basis():
    """D2-11a (PLAN-v3 §2.4 / A6, decision 29) kill-tests, hermetic. Every mandatory row
    carries `normative_basis` in {sentence, table, bullet, pseudocode, schema, definition,
    inferred} OR sits in the version's normative_basis_review_queue (the nokw review set,
    D2-11b); `sentence` requires a MANDATORY keyword in the quote; `definition|inferred`
    require a review_signoffs batch naming the (version, id); the mechanical fill
    (requirements/tools/assign_normative_basis.py) classifies schema / sentence /
    pseudocode and queues the rest."""
    import importlib.util
    bad = 0
    V = "2026-08-25"
    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} basis: {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1
    rows = [{"id": "ZZZ-101", "keyword": "MUST", "versions": [V], "quote": "The server MUST reply."},       # no basis, not queued
            {"id": "ZZZ-102", "keyword": "MUST", "versions": [V], "quote": "Return 200.",
             "normative_basis": "inferred"},                                                             # inferred, unsigned
            {"id": "ZZZ-103", "keyword": "MUST", "versions": [V], "quote": "| Create | POST /x |",
             "normative_basis": "sentence"},                                                             # sentence w/o keyword
            {"id": "ZZZ-104", "keyword": "MUST", "versions": [V], "quote": "The server MUST reply.",
             "normative_basis": "sentence"},                                                             # fine
            {"id": "ZZZ-105", "keyword": "MUST", "versions": [V], "quote": "Return 200."},                # no basis, queued -> ok
            {"id": "ZZZ-106", "keyword": "MUST", "versions": [V], "quote": "x", "normative_basis": "vibes"},  # bad enum
            {"id": "ZZZ-107", "keyword": "MUST", "versions": [V], "quote": "Return 200.",
             "normative_basis": "definition"},                                                           # definition, signed -> ok
            {"id": "ZZZ-108", "keyword": "SHOULD", "versions": [V], "quote": "x"}]                        # SHOULD: optional
    try:
        errs = basis_errors(rows, V, queued_ids={"ZZZ-105"}, signed_ids={"ZZZ-107"})
    except NameError as e:
        errs = f"NameError: {e}"
    ok = isinstance(errs, list) and all(any(i in e for e in errs) for i in ("ZZZ-101", "ZZZ-102", "ZZZ-103", "ZZZ-106")) \
        and not any(i in e for e in errs for i in ("ZZZ-104", "ZZZ-105", "ZZZ-107", "ZZZ-108"))
    bad += case("missing+unqueued / inferred-unsigned / sentence-without-keyword / bad enum FAIL; "
                "sentence-with-keyword, queued, signed definition, SHOULD pass", ok, repr(errs)[:220])
    tool = ROOT / "conformance" / "requirements" / "tools" / "assign_normative_basis.py"
    if not tool.exists():
        bad += case("assign_normative_basis fill fixture", False, f"{tool.relative_to(ROOT)} absent")
    else:
        spec = importlib.util.spec_from_file_location("assign_normative_basis", tool)
        nb = importlib.util.module_from_spec(spec); spec.loader.exec_module(nb)
        fx = [({"id": "F-1", "keyword": "MUST", "source": "ucp:source/schemas/x.json#L3", "quote": "\"required\": [\"id\"]"}, "schema"),
              ({"id": "F-2", "keyword": "MUST", "source": "ucp:docs/specification/x.md#L3", "quote": "The server **MUST** reply."}, "sentence"),
              ({"id": "F-3", "keyword": "MUST", "source": "ucp:docs/specification/x.md#L9", "quote": "if not key: return error()"}, "pseudocode"),
              ({"id": "F-4", "keyword": "MUST", "source": "ucp:docs/specification/x.md#L12", "quote": "| Create | POST /x |"}, None)]
        got = {r["id"]: nb.classify(r, fenced_lines={"docs/specification/x.md": {8, 9, 10}}) for r, _ in fx}
        ok = all(got[r["id"]] == want for r, want in fx)
        bad += case("fill fixture: schema / sentence / pseudocode (fenced line) / queue(None)", ok, repr(got)[:200])
    # B6 (W1 review V4): the queue is the ONLY thing making a null normative_basis acceptable,
    # so it is an exemption register and must carry the standard clock (decision 23 / D2-19:
    # its truth depends only on the spec pin -> the 90-day `pin-only` tier). An expired,
    # pin-drifted or unclocked queue confers nothing.
    import datetime as _dt
    today = _dt.date(2026, 9, 11)
    PIN = "cd78fb38"
    live = {"version": V, "review_by": "2026-12-09", "spec_pin": PIN, "clock_tier": "pin-only",
            "converts_when": "D2-11b", "queue": [{"id": "ZZZ-105"}]}
    try:
        clean = basis_queue_clock_errors(live, V, PIN, today, "normative_basis_review_queue.json")
        expired = basis_queue_clock_errors({**live, "review_by": "2026-09-10"}, V, PIN, today,
                                           "normative_basis_review_queue.json")
        drift = basis_queue_clock_errors({**live, "spec_pin": "deadbeef"}, V, PIN, today,
                                         "normative_basis_review_queue.json")
        unclocked = basis_queue_clock_errors({"version": V, "queue": [{"id": "ZZZ-105"}]}, V, PIN, today,
                                             "normative_basis_review_queue.json")
        badtier = basis_queue_clock_errors({**live, "clock_tier": "sideways"}, V, PIN, today,
                                           "normative_basis_review_queue.json")
    except NameError as e:
        clean = expired = drift = unclocked = badtier = f"NameError: {e}"
    ok = (clean == [] and
          isinstance(expired, list) and len(expired) == 1 and "2026-09-10" in expired[0]
          and "normative_basis_review_queue.json" in expired[0] and
          isinstance(drift, list) and any("deadbeef" in e for e in drift) and
          isinstance(unclocked, list) and len(unclocked) >= 1 and
          isinstance(badtier, list) and any("sideways" in e for e in badtier))
    bad += case("queue clock: live queue clean; expired review_by / spec_pin drift / no clock "
                "hands / bad clock_tier each FAIL naming the file",
                ok, repr((clean, expired, drift, unclocked, badtier))[:400])
    # and the real committed queues must carry a live clock (the gate's own data)
    try:
        real = []
        for _v in VERSIONS:
            _f = ROOT / "conformance" / "requirements" / _v / "normative_basis_review_queue.json"
            if _f.exists():
                _d = json.loads(_f.read_text())
                real += basis_queue_clock_errors(_d, _v, _pin_for(_v), _dt.date.today(),
                                                 f"requirements/{_v}/normative_basis_review_queue.json")
    except NameError as e:
        real = [f"NameError: {e}"]
    bad += case("the four committed normative_basis_review_queue.json files carry a live "
                "pin-only clock", real == [], repr(real)[:400])
    return bad


def _selftest_roles():
    """D2-08 (PLAN-v3 §2.5 / A4) kill-tests, hermetic (synthetic rows, no vendored I/O).

    role fixture: every mandatory row carries `role` (enum) + `role_provenance`
    (enum or review:<batch>); the agent-denominator lock and the roles agree BOTH
    ways (a locked id is platform|both|host; a platform|both|host row is locked);
    the review queue is empty; an agent CHECK grades only an agent-lane row (one-lane
    rule); every register area is listed in _area_capabilities.json and every
    capability it names exists in the vendored spec (RV1 #15); assign_roles' 7-row
    fixture reproduces the expected roles and queues the 7th."""
    import importlib.util
    bad = 0
    V = "2026-08-25"
    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} roles: {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1
    rows = [{"id": "ZZZ-001", "keyword": "MUST", "versions": [V]},                       # no role
            {"id": "ZZZ-002", "keyword": "MUST", "versions": [V], "role": "business",
             "role_provenance": "subject"},                                             # business but locked
            {"id": "ZZZ-003", "keyword": "MUST", "versions": [V], "role": "platform",
             "role_provenance": "agent-lock"},                                          # platform, unlocked
            {"id": "ZZZ-004", "keyword": "MUST", "versions": [V], "role": "wizard",
             "role_provenance": "subject"},                                             # bad enum
            {"id": "ZZZ-005", "keyword": "MUST", "versions": [V], "role": "both",
             "role_provenance": "review:roles-2026-09"},                                # fine
            {"id": "ZZZ-006", "keyword": "SHOULD", "versions": [V]}]                    # SHOULD: role optional
    try:
        errs = role_errors(rows, V, lock_ids={"ZZZ-002", "ZZZ-005"})
    except NameError as e:
        errs = f"NameError: {e}"
    ok = isinstance(errs, list) and \
        any("ZZZ-001" in e for e in errs) and any("ZZZ-002" in e for e in errs) and \
        any("ZZZ-003" in e for e in errs) and any("ZZZ-004" in e for e in errs) and \
        not any("ZZZ-005" in e or "ZZZ-006" in e for e in errs)
    bad += case("row without role / business-but-locked / platform-but-unlocked / bad enum "
                "each FAIL; a review:* both row and a SHOULD row pass", ok, repr(errs)[:200])
    try:
        q = queue_errors([{"id": "ZZZ-009", "heuristic": None}], V)
        q0 = queue_errors([], V)
    except NameError as e:
        q, q0 = f"NameError: {e}", None
    bad += case("non-empty role_review_queue -> FAIL; empty -> ok",
                isinstance(q, list) and len(q) == 1 and q0 == [], repr(q)[:200])
    try:
        le = lane_errors([{"id": "ZZZ-010", "keyword": "MUST", "versions": [V], "role": "business"},
                          {"id": "ZZZ-011", "keyword": "MUST", "versions": [V], "role": "platform"},
                          {"id": "ZZZ-012", "keyword": "MUST", "versions": [V], "role": "both"}],
                         V, agent_check_ids={"ZZZ-010", "ZZZ-011", "ZZZ-012"})
    except NameError as e:
        le = f"NameError: {e}"
    bad += case("agent CHECK on a business-only row -> FAIL (one-lane rule); platform/both ok",
                isinstance(le, list) and [e for e in le if "ZZZ-010" in e] and
                not [e for e in le if "ZZZ-011" in e or "ZZZ-012" in e], repr(le)[:200])
    try:
        ae = area_map_errors({"cart": "dev.ucp.shopping.cart", "overview": None,
                              "orphan": "dev.ucp.shopping.nope"},
                             register_areas={"cart", "overview", "loyalty"},
                             spec_capabilities={"dev.ucp.shopping.cart"})
    except NameError as e:
        ae = f"NameError: {e}"
    bad += case("area map: unlisted register area FAILS; capability absent from the vendored "
                "spec FAILS; null (core) and a known capability pass",
                isinstance(ae, list) and any("loyalty" in e for e in ae) and
                any("dev.ucp.shopping.nope" in e for e in ae) and len(ae) == 2, repr(ae)[:200])
    # assign_roles.py 7-row fixture (heuristic §A4: lock / NAB / client-bound / subject /
    # direction / conflict-queue), imported from requirements/tools.
    tool = ROOT / "conformance" / "requirements" / "tools" / "assign_roles.py"
    if not tool.exists():
        bad += case("assign_roles.py 7-row fixture", False, f"{tool.relative_to(ROOT)} absent")
    else:
        spec = importlib.util.spec_from_file_location("assign_roles", tool)
        ar = importlib.util.module_from_spec(spec); spec.loader.exec_module(ar)
        fx = [
            {"id": "F-001", "keyword": "MUST", "quote": "Businesses MUST return the full resource."},
            {"id": "F-002", "keyword": "MUST", "quote": "The Platform MUST send the entire object."},
            {"id": "F-003", "keyword": "MUST", "quote": "Both the Platform and the Business MUST use HTTPS."},
            {"id": "F-004", "keyword": "MUST", "quote": "Requests MUST carry an Idempotency-Key header."},
            {"id": "F-005", "keyword": "MUST", "quote": "The response MUST include a `status` field."},
            {"id": "F-006", "keyword": "MUST", "quote": "The payment handler MUST tokenize the credential."},
            {"id": "F-007", "keyword": "MUST", "quote": "Platforms MUST cache the profile."},   # conflict: subject platform, not locked
        ]
        got = {r["id"]: ar.assign(r, lock_ids={"F-002"}, agent_extra=set(), nab=set(), cb=set())
               for r in fx}
        want = {"F-001": ("business", "subject", None), "F-002": ("platform", "agent-lock", None),
                "F-003": ("both", "subject", None), "F-004": ("platform", "direction", None),
                "F-005": ("business", "direction", None)}
        ok = all(got[k][:2] == want[k][:2] and got[k][2] is None for k in want) \
            and got["F-006"][0] == "handler" and got["F-006"][2] is not None \
            and got["F-007"][2] is not None
        bad += case("assign_roles 7-row fixture: 5 resolved as expected, handler + platform-"
                    "unlocked conflict queued", ok, repr(got)[:300])
    return bad


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

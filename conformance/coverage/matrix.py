#!/usr/bin/env python3
"""
matrix.py — the coverage accounting engine (Phase-0 backbone of the 100% goal).

For every UCP version, it buckets EVERY normative MUST/MUST NOT into exactly one of:
  - CHECK     : referenced by a shipped conformance check (its id appears in a check's req-id list)
  - EXEMPT    : listed in coverage/exemptions.json with a written justification
  - GAP       : neither — unaccounted (this is what we drive to zero)

GAP is sub-classified by the register's `testability` so we see what's actionable now
(testable) vs what needs a harness (needs-receiver / needs-oauth) vs what must become a
documented exemption (manual / untestable).

Usage:
  python3 conformance/coverage/matrix.py                 # print the accounting for all versions
  python3 conformance/coverage/matrix.py --md FILE       # also write a markdown matrix
  python3 conformance/coverage/matrix.py --require testable            # exit 1 if any TESTABLE gap remains
  python3 conformance/coverage/matrix.py --require all --version 2026-01-23   # exit 1 if ANY gap remains (version "closed" gate)

Coverage attribution by version (a check counts for a version when its id is a MUST there AND):
  - file name contains 04_08/04-08  -> attributes to 2026-04-08 only
  - file name contains 01_23        -> 2026-01-23
  - file name contains 01_11        -> 2026-01-11
  - otherwise (merchant_checks.py, engine, area_*, selfcheck) -> version-adaptive: all versions where the id is a MUST
This is intentionally conservative; per-version applicability is tightened in each version's
Stage-C reconciliation.
"""
import json, os, re, glob, sys, argparse, subprocess
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONF = os.path.join(ROOT, "conformance")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CONF)
import evidence  # noqa: E402 — the evidence-class layer (sibling module)
# VERSIONS / CURRENT_SITE_VERSION / REGISTER_ONLY_VERSIONS used to be hand-maintained
# HERE, one of five independent copies of the same data across the suite (PLAN-0825
# G0-b / A.4 — the version-map whack-a-mole seam). All three now live in
# conformance/common/spec_versions.py, the single source every consumer imports;
# see that module's docstring for what each field means and why it isn't a formula.
from common.spec_versions import (  # noqa: E402
    VERSIONS, CURRENT_SITE_VERSION, REGISTER_ONLY_VERSIONS)
# The accounting denominator is the MANDATORY keyword class (MUST, MUST NOT, SHALL,
# SHALL NOT, REQUIRED) — one tuple shared with coverage_gate / agent_matrix / the two
# census scripts (D2-01), never a local pair that can drift from the census regex.
from common.keywords import MANDATORY  # noqa: E402
# D2-08: the role vocabulary and the lane each role enters — one module shared with
# agent_matrix.agent_rows() and verify_register, so the two lanes partition the MUSTs.
from common.roles import ROLES, MERCHANT_LANE, AGENT_LANE, OTHER_LANE  # noqa: E402
REQ = os.path.join(CONF, "requirements")
EXEMPT_FILE = os.path.join(CONF, "coverage", "exemptions.json")
ID_RE = re.compile(r'\b([A-Z]{2,6}-\d{2,3})\b')
# capture the req-id list: Check("name", [ ... ]) / MCheck("name", [ ... ]) /
# fixture_check("name", [ ... ]) — schema_check.py's factory builds an engine.Check
# at runtime, so its citations are as real as literal constructors; its checks are
# kill-gated by the suite-04-08 gate (run_04_08.py exits red on any unsound check).
REQIDS_RE = re.compile(r'(?:M?Check|fixture_check)\(\s*"[^"]*"\s*,\s*\[([^\]]*)\]', re.S)


def load_rows(ver):
    rows = []
    for f in glob.glob(os.path.join(REQ, ver, "*.json")):
        d = json.load(open(f))
        rows += d if isinstance(d, list) else d.get("requirements", d.get("rows", []))
    return rows


def load_rows_with_area(ver):
    """Like load_rows but each row carries its register file's `_area`."""
    rows = []
    for f in sorted(glob.glob(os.path.join(REQ, ver, "*.json"))):
        d = json.load(open(f))
        area = (d.get("_area") if isinstance(d, dict) else None) \
            or os.path.basename(f).replace(".json", "")
        for r in (d if isinstance(d, list) else d.get("requirements", d.get("rows", []))):
            rows.append({**r, "_area": area})
    return rows


def check_files():
    return glob.glob(os.path.join(CONF, "checks", "*.py")) + \
           glob.glob(os.path.join(CONF, "selfcheck", "*.py"))


_VERSION_TOKENS = (("04_08", "2026-04-08"), ("04-08", "2026-04-08"),
                   ("01_23", "2026-01-23"), ("01_11", "2026-01-11"))


def _file_targets(path):
    """Version scope from the FILE name: any embedded version tokens, else all."""
    name = os.path.basename(path).lower()
    targets = [v for tok, v in _VERSION_TOKENS if tok in name]
    return sorted(set(targets)) or list(VERSIONS)


# checks/ modules that FAILED to import during the current scan. A checks/ module that
# won't import silently drops its live kill-tests from the run, yet _module_checks used
# to fall back to a TEXT-SCAN of its citations so the ids still bucketed as CHECK — the
# matrix advertising coverage that no longer runs (P0-3). We still text-scan (so the
# report renders), but we RECORD the failure and treat it as a gate FAILURE: matrix main
# reds, and has_import_failures() lets any consumer refuse a coverage number built over a
# module whose checks vanished. selfcheck/ files are not check-list modules, so — as
# before — they are never import-attempted here.
_IMPORT_FAILURES = []

# A manifest-declared CORE checkset module (v2026_01_23 / v2026_04_08) that imports
# FINE but exports fewer CHECKS than its committed count — in particular ZERO, an
# emptied core module whose Check(...) citations are still textually present — used to
# slip past matrix entirely: _module_checks returned (None, mod) for the empty CHECKS,
# coverage_map fell back to the TEXT SCAN, and every id still bucketed as CHECK while the
# 12 (01-23) / 1 (04-08) live kill-tests no longer ran (P0-4, the core-module twin of the
# P0-3 area/import hole). We still text-scan (so the report renders), but RECORD the drift
# and treat it as a gate FAILURE. The expected counts come from the committed checkset
# manifests (checks/area_manifest_*.json) — never hardcoded — so adding a real core check
# is a one-line manifest bump, not a silent matrix red.
_CORE_CHECKSET_FAILURES = []
_CORE_EXPECTED = {}          # {core_module_stem: expected len(CHECKS)}, refreshed per scan


def _reset_import_failures():
    _IMPORT_FAILURES.clear()
    _CORE_CHECKSET_FAILURES.clear()
    _CORE_EXPECTED.clear()
    _CORE_EXPECTED.update(_core_expected())


def _core_expected():
    """{core_module_stem: core_checks} declared by the committed checkset manifests
    (checks/area_manifest_*.json). The single source of the expected core counts, so
    matrix never hardcodes them — a deliberate manifest bump is the only way to move
    the number. Tolerant of a manifest without the field (skipped)."""
    out = {}
    for mf in sorted(glob.glob(os.path.join(CONF, "checks", "area_manifest_*.json"))):
        try:
            d = json.load(open(mf))
        except Exception:                           # noqa: BLE001 — a broken manifest is caught by its own gate
            continue
        stem, cnt = d.get("core_module"), d.get("core_checks")
        if stem is not None and cnt is not None:
            out[stem] = cnt
    return out


def import_failures():
    """[(stem, repr(exc)), ...] recorded in the most recent coverage_map() scan."""
    return list(_IMPORT_FAILURES)


def has_import_failures():
    return bool(_IMPORT_FAILURES)


def core_checkset_failures():
    """[(stem, expected, got), ...]: manifest-declared core modules that imported but
    drifted from their committed CHECKS count in the most recent coverage_map() scan."""
    return list(_CORE_CHECKSET_FAILURES)


def has_core_checkset_failures():
    return bool(_CORE_CHECKSET_FAILURES)


def _module_checks(path):
    """Import a conformance/checks module and return its CHECKS list, or None if the
    module has none / cannot be imported (caller falls back to the text scan). An import
    FAILURE of a checks/ module is recorded in _IMPORT_FAILURES (a gate failure), as
    distinct from a module that simply exports no CHECKS."""
    if os.path.basename(os.path.dirname(path)) != "checks":
        return None, None
    import importlib
    for d in (os.path.join(CONF, "checks"), os.path.join(CONF, "selfcheck")):
        if d not in sys.path:
            sys.path.insert(0, d)
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        mod = importlib.import_module(stem)
        checks = []
        # every CHECKS* list (CHECKS, CHECKS_01_23, CHECKS_04_08, per-area exports)
        # plus RESOLVE_CHECKS* (resolver-level checks) — additive by convention so
        # parallel area modules attribute without central wiring
        for attr in sorted(dir(mod)):
            if attr.startswith("CHECKS") or attr.startswith("RESOLVE_CHECKS"):
                checks += list(getattr(mod, attr) or [])
        # CORE checkset integrity (P0-4): a manifest-declared core module that imported
        # fine but whose primary CHECKS list drifted from its committed count — most
        # dangerously EMPTIED (its ids still text-scan as CHECK) — is a gate FAILURE, the
        # core-module twin of the recorded import failure above. Counts the same `CHECKS`
        # attribute the runners load (v2026_xx.CHECKS) against the committed manifest.
        exp = _CORE_EXPECTED.get(stem)
        if exp is not None:
            n_core = len(list(getattr(mod, "CHECKS", []) or []))
            if n_core != exp:
                _CORE_CHECKSET_FAILURES.append((stem, exp, n_core))
                print(f"(matrix: core checkset {stem} exports {n_core} CHECKS, manifest "
                      f"expects {exp} — GATE FAILURE, not a text-scan shrug)", file=sys.stderr)
        return (checks or None), mod
    except Exception as e:
        _IMPORT_FAILURES.append((stem, repr(e)))
        print(f"(matrix: {stem} not importable — text-scan fallback + GATE FAILURE: {e})",
              file=sys.stderr)
        return None, None


def attribution():
    """The single coverage-attribution walk: yields one (version, req_id, basename,
    check_obj_or_None) row per attributed citation. `check_obj` is the live check
    object when the module was importable and introspected (the PRIMARY source);
    None for the conservative text-scan fallback. coverage_map() AND the
    evidence-class layer (evidence.py) both consume THIS walk, so the two can never
    disagree about what covers what.

    PRIMARY source: runtime INTROSPECTION of each conformance/checks module's CHECKS
    list. Per check object, the citation scope is:
      chk.versions  (explicit per-check scope)          — else —
      module VERSIONS marker (whole file is version-scoped) — else —
      file-name version tokens (schema_check_04_08.py etc.) — else all versions;
    and the ids AT a version are chk.req_ids_map[version] when present (the 2026-04-08
    registers renumbered many CHK/DSC/ORD ids onto DIFFERENT requirements), else
    chk.req_ids. FALLBACK (module not importable / no CHECKS / selfcheck files): the
    conservative text scan of Check(/MCheck(/fixture_check( citations + file tokens.
    Either way an id only attributes where it is a real register row."""
    rows = []
    _reset_import_failures()          # fresh per scan; enforced by main()/has_import_failures()
    # gather row ids per version so we only attribute real rows
    all_ids = {v: {r.get("id") for r in load_rows(v)} for v in VERSIONS}
    for path in check_files():
        base = os.path.basename(path)
        file_targets = _file_targets(path)
        checks, mod = _module_checks(path)
        if checks:
            mod_versions = getattr(mod, "VERSIONS", None)
            # attribution bound: the file-name version tokens, WIDENED by an explicit
            # module-level VERSIONS marker (a reviewed declaration that the file's
            # citations were verified at those versions too — e.g.
            # merchant_checks_01_23.py carries VERSIONS=(01-11, 01-23) after its
            # DSC/PAY rows were verified textually identical at 2026-01-11)
            bound = set(file_targets) | set(mod_versions or ())
            for chk in checks:
                scope = getattr(chk, "versions", None) or mod_versions or file_targets
                vmap = getattr(chk, "req_ids_map", None) or {}
                for v in scope:
                    if v not in bound:
                        continue          # outside every declared scope
                    for i in vmap.get(v, list(getattr(chk, "req_ids", []) or [])):
                        if i in all_ids[v]:
                            rows.append((v, i, base, chk))
            continue
        txt = open(path).read()
        ids = set()
        for grp in REQIDS_RE.findall(txt):
            ids |= set(ID_RE.findall(grp))
        for v in file_targets:
            for i in ids:
                if i in all_ids[v]:
                    rows.append((v, i, base, None))
    # REGISTER_ONLY_VERSIONS: drop every attribution at these versions here, at the
    # single walk every consumer (coverage_map/covered_ids_by_version/evidence.py's
    # evidence_by_id/export_json) shares — so none of them can independently
    # disagree by forgetting to re-check the wall.
    return [r for r in rows if r[0] not in REGISTER_ONLY_VERSIONS]


def _covmap_from(rows):
    cov = {v: defaultdict(set) for v in VERSIONS}
    for v, i, base, _chk in rows:
        cov[v][i].add(base)
    return {v: {i: sorted(fs) for i, fs in m.items()} for v, m in cov.items()}


def coverage_map():
    """Return {version: {req_id: sorted[check file basenames]}} referenced by shipped
    checks — the traceability layer of the matrix. Derived from attribution()."""
    return _covmap_from(attribution())


def covered_ids_by_version():
    """Return {version: set(ids)} referenced by shipped checks."""
    return {v: set(m.keys()) for v, m in coverage_map().items()}


def _spec_pins():
    """{version: commit_sha} from SOURCES.lock.json (for pinned-spec deep links)."""
    lock = os.path.join(CONF, "SOURCES.lock.json")
    try:
        d = json.load(open(lock))
        return {v: info.get("commit", "") for v, info in
                d.get("spec", {}).get("versions", {}).items()}
    except Exception:
        return {}


def _pinned_spec_versions():
    """Set of spec versions pinned in SOURCES.lock.json — may be WIDER than VERSIONS
    the moment a new spec is pinned before its register lands (VERSIONS is a
    deliberately hand-maintained list, see the comment at its definition; a version
    joins it only via a reasoned commit). Used to compute the `unregistered`
    publication state (PLAN-0825 §E) for exactly that gap, data-driven off the same
    lock file `_spec_pins()` already reads."""
    return set(_spec_pins().keys())


# GAP tiers that keep a version out of `live` (rule R-a, PLAN-v3 §2.18 / decision 25):
# a MUST the suite COULD grade — directly, or with the webhook receiver / OAuth harness
# the program is building — but has not yet. `manual` / `untestable` GAPs are the
# exemption program's business (A10) and never block `live`.
CONVERTING_TIERS = ("testable", "needs-receiver", "needs-oauth")


def _version_state(n_check, n_exempt, has_register, testable_gap):
    """The per-version publication `state` (PLAN-0825 §E's no-overclaim state
    machine, refined to rule R-a by decision 25), computed ENTIRELY from data —
    never hand-set. `testable_gap` is the version's gap_by_testability dict.

      unregistered — no register tree exists for this version at all (pinned in
                     SOURCES.lock but nothing under conformance/requirements/<ver>/
                     yet). Renderable only as a roadmap line.
      building     — register rows exist but zero CHECK and zero EXEMPT. This is
                     exactly the filter site_gates.py's _real_manifest() already uses
                     to decide what "backs site copy" (check or exempt > 0).
      converting   — CHECK+EXEMPT > 0 AND at least one GAP in a CONVERTING_TIERS
                     tier: checks are still landing; the version must not render as
                     supported (D5-03 mirrors this rule from the export's own fields —
                     check, exempt, gap_by_testability — never from this module).
      live         — CHECK+EXEMPT > 0 and no GAP in any CONVERTING_TIERS tier.

    Consequence at today's data (decision 25, accepted by the owner): 2026-04-08 is
    live; 2026-08-25 and BOTH 01-era versions (17 / 18 needs-receiver GAPs) read
    converting. Pure and independently testable (no I/O)."""
    if not has_register:
        return "unregistered"
    if not (n_check or n_exempt):
        return "building"
    blocking = sum((testable_gap or {}).get(t, 0) for t in CONVERTING_TIERS)
    return "converting" if blocking else "live"


def _completeness_report():
    """Runs verify_register_completeness.py --json ONCE and returns its parsed
    output, or None if it can't be reached. Subprocess, not import: matrix.py
    deliberately never imports conformance/selfcheck/*.py modules directly (see
    _module_checks's own comment on that boundary — selfcheck files are standalone
    scripts, not introspectable library modules), the same discipline
    site_gates.py's _real_manifest() already uses (subprocess out to agent_checks/
    reference_agent rather than importing them into its own process). This is also
    the SINGLE place matrix.py learns the census flip-by date — it is never
    duplicated here, only read out of the completeness module's own JSON."""
    script = os.path.join(CONF, "selfcheck", "verify_register_completeness.py")
    try:
        r = subprocess.run([sys.executable, script, "--json"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)
    except Exception:
        return None


def _schema_census_report():
    """Runs verify_schema_census.py --json ONCE (report-only mode) for the file-level
    schema surface; None if unreachable. Same subprocess discipline as
    _completeness_report()."""
    script = os.path.join(CONF, "selfcheck", "verify_schema_census.py")
    try:
        r = subprocess.run([sys.executable, script, "--json"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout)
    except Exception:
        return None


TRANSPORTS = ("rest", "mcp", "a2a", "embedded", "any")
LANES = ("merchant", "agent", "other")


def _row_role(r):
    """A row's `role` (D2-08); a row without one reads `unassigned` — never silently
    bucketed into a lane (the `register` gate reds on any mandatory row without a role)."""
    role = r.get("role")
    return role if role in ROLES else "unassigned"


def _agent_axis_report():
    """Runs agent_matrix.py --json ONCE (subprocess — the agent lane lives in its own
    tree and is never imported by the merchant matrix) and returns
    {version: {agent_musts, check, exempt, gap}} or None if unreachable."""
    script = os.path.join(CONF, "agent", "agent_matrix.py")
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp = tf.name
        r = subprocess.run([sys.executable, script, "--json", tmp], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        out = json.load(open(tmp)) if r.returncode == 0 and os.path.exists(tmp) else None
        try:
            os.remove(tmp)
        except OSError:
            pass
        return out
    except Exception:
        return None


def roles_block(rows, status_by_id, agent_axis=None):
    """The per-role denominators (export v2 `roles`, PLAN-v3 §2.4 / D2-08). `rows` are
    the mandatory rows of one version, `status_by_id` their MERCHANT-axis bucket
    (check|exempt|gap), `agent_axis` the agent lane's own {agent_musts, check, exempt,
    gap} for the version (None -> nulls; the agent axis is a separate program).

      summary   {merchant, agent, both, other} — lane sizes; `both`-role rows sit in
                both lanes, so merchant + agent − both + other == musts (W1-4; asserted
                by matrix --selftest and validate_evidence_class).
      merchant  merchant-lane rows (business | both | handler — decision 27) with their
                merchant-axis buckets and gap_by_testability.
      agent     agent-lane rows (platform | both | host — decision 27): musts from the
                role field (== agent_matrix.agent_rows by construction), check/exempt/
                gap from the agent axis when available.
      other     spec-author rows (speclint register-selfcheck, D2-18), by_role.
    """
    summary = {"merchant": 0, "agent": 0, "both": 0, "other": 0}
    merchant = {"musts": 0, "check": 0, "exempt": 0, "gap": 0}
    mgap = Counter()
    other_by_role = Counter()
    n_agent = 0
    for r in rows:
        role = _row_role(r)
        st = status_by_id.get(r.get("id"), "gap")
        if role in MERCHANT_LANE:
            summary["merchant"] += 1
            merchant["musts"] += 1
            merchant[st] += 1
            if st == "gap":
                mgap[r.get("testability", "?")] += 1
        if role in AGENT_LANE:
            summary["agent"] += 1
            n_agent += 1
        if role == "both":
            summary["both"] += 1
        if role in OTHER_LANE or role == "unassigned":
            summary["other"] += 1
            other_by_role[role] += 1
    merchant["gap_by_testability"] = dict(sorted(mgap.items()))
    agent = {"musts": n_agent, "check": None, "exempt": None, "gap": None}
    if agent_axis:
        agent.update({"check": agent_axis.get("check"), "exempt": agent_axis.get("exempt"),
                      "gap": agent_axis.get("gap")})
        if agent_axis.get("agent_musts") != n_agent:
            agent["axis_musts_mismatch"] = agent_axis.get("agent_musts")
    return {"summary": summary, "merchant": merchant, "agent": agent,
            "other": {"musts": summary["other"], "by_role": dict(sorted(other_by_role.items()))}}


def _row_transport(r):
    """A row's transport bucket: `transport` is exactly one of rest|mcp|a2a|embedded
    or ["any"] (row v2); a row without the field counts as `any` so the transport
    sum always partitions the MUSTs."""
    t = r.get("transport")
    if isinstance(t, list):
        t = t[0] if t else None
    return t if t in TRANSPORTS else "any"


def _should_census_report():
    """Runs verify_should_census.py --json ONCE (report-only, D2-10) for `surface.should`;
    None if unreachable. Same subprocess discipline as _completeness_report()."""
    script = os.path.join(CONF, "selfcheck", "verify_should_census.py")
    try:
        r = subprocess.run([sys.executable, script, "--json"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout)
    except Exception:
        return None


def _surface_for(ver, completeness, schema_census, pins, should_census=None):
    """The published SURFACE (export v2, PLAN-v3 §2.4 / D2-07): what the accounting
    denominator is measured against, by layer.
      prose  — the completeness census: every mandatory-keyword hit in the pinned
               spec prose is covered by a register row, scope-excluded, waived (by
               class, in HITS) or missed; `census_mode` gate|report.
      schema — file-level counts from the schema census; atom-level fields are null
               until the atom census (D2-14) lands.
      should — null until the SHOULD census (D2-10)."""
    prose = None
    pv = ((completeness or {}).get("per_version") or {}).get(ver)
    if pv is not None:
        prose = {"pin": (pins.get(ver) or "")[:8],
                 "mandatory_hits": pv.get("total", 0),
                 "covered_by_rows": pv.get("covered", 0),
                 "scope_excluded": pv.get("scoped", 0),
                 "waived": pv.get("waived_by_class", {}),
                 "missed": pv.get("missed", 0),
                 "census_mode": "report" if pv.get("report_mode") == "active" else "gate"}
    schema = None
    sv = ((schema_census or {}).get("per_version") or {}).get(ver)
    if sv is not None:
        schema = {"files": sv.get("file_count"),
                  "atoms": None, "atoms_referenced": None, "atoms_ruled": None,
                  "atoms_unaccounted": None,
                  "files_unreferenced": len(sv.get("unreferenced") or []),
                  "enforce": bool((schema_census or {}).get("enforce"))}
    should = None
    sh = ((should_census or {}).get("per_version") or {}).get(ver)
    if sh is not None:
        should = {"hits": sh.get("hits"), "should": sh.get("should"), "should_not": sh.get("should_not"),
                  "recommended": sh.get("recommended"), "not_recommended": sh.get("not_recommended"),
                  "rows": sh.get("rows"), "hit_lines": sh.get("hit_lines"),
                  "hit_lines_under_a_row_quote": sh.get("hit_lines_under_a_row_quote"),
                  "uncovered": sh.get("uncovered"), "scope": "report-only (decision 8)"}
    return {"prose": prose, "schema": schema, "should": should}


def _census_for(ver, completeness):
    """{mode, unaccounted, gate_from} for a `building`-state version (PLAN-0825 §E),
    or None if the completeness report has no data for it (never invents a number)."""
    if not completeness:
        return None
    pv = (completeness.get("per_version") or {}).get(ver)
    if pv is None:
        return None
    mode = "report" if pv.get("report_mode") == "active" else "gate"
    gate_from = (completeness.get("report_mode_until") or {}).get(ver, "")
    return {"mode": mode, "unaccounted": pv.get("missed", 0), "gate_from": gate_from}


def export_json():
    """The full requirements-traceability export: per version, every MUST row with its
    bucket (check/exempt/gap), testability, verbatim requirement, pinned-spec source,
    and the check files that cover it. Deterministic ordering (stable for drift-diff).
    This is the single data source for the public coverage page AND the coverage gate."""
    attr = attribution()
    covmap = _covmap_from(attr)
    evmap = evidence.evidence_by_id(attr)
    exempt = load_exemptions()
    pins = _spec_pins()
    pinned_versions = _pinned_spec_versions()
    unregistered_versions = sorted(pinned_versions - set(VERSIONS))
    # census (§E): the completeness report is fetched once (subprocess) and its
    # per-version mode/unaccounted count is emitted for every state (D2-06).
    completeness = None
    schema_census = None
    should_census = None
    agent_axis = _agent_axis_report()        # D2-08: roles.agent check/exempt/gap (subprocess)
    out = {"_about": "spck.dev UCP conformance coverage — every normative MUST accounted "
                     "as CHECK (kill-rate-validated), EXEMPT (documented), or GAP. "
                     "Generated by conformance/coverage/matrix.py --json; the `coverage` "
                     "CI gate fails if this file is stale or coverage regresses. "
                     "Each CHECK row also carries its EVIDENCE CLASS (see "
                     "`evidence_classes`) and `reach` (the independent targets it "
                     "actually graded on, per coverage/reach_report.json).",
           "evidence_classes": {
               "live-wire": "kill-tested on the wire against at least one "
                            "independently-authored server (flower golden / node "
                            "reference — see `reach` per row)",
               "fixture-schema": "our fixture validated through the OFFICIAL "
                                 "ucp-schema oracle (spec-anchored, not circular)",
               "fixture-crypto": "self-signed crypto primitive self-test (e.g. the "
                                 "AP2 fixture-key checks) — spec-shaped, not "
                                 "independently corroborated",
               "self-referenced": "graded only against our own fixture, with no "
                                  "independent oracle or target — the "
                                  "fixture-circularity class we flag upstream "
                                  "(conformance#79), named here in our own suite"},
           "spec_repo": "Universal-Commerce-Protocol/ucp",
           "spec_pins": {v: pins.get(v, "") for v in sorted(set(VERSIONS) | pinned_versions)},
           "versions": {}}
    for ver in VERSIONS:
        rows = [r for r in load_rows_with_area(ver)
                if r.get("keyword") in MANDATORY]
        areas = {}
        jrows = []
        n_check = n_exempt = 0
        gap_by_test = Counter()
        for r in sorted(rows, key=lambda x: x.get("id", "")):
            rid = r.get("id")
            if rid in covmap[ver]:
                status = "check"; n_check += 1
            elif exempt_at(exempt, rid, ver):
                status = "exempt"; n_exempt += 1
            else:
                status = "gap"
                gap_by_test[r.get("testability", "?")] += 1
            a = areas.setdefault(r["_area"], Counter())
            a["musts"] += 1
            a[status] += 1
            if status == "gap":
                a["gap_" + r.get("testability", "?")] += 1
            ev = evmap.get(ver, {}).get(rid) if status == "check" else None
            jrows.append({"id": rid, "area": r["_area"], "keyword": r.get("keyword"),
                          "testability": r.get("testability", "?"), "status": status,
                          "role": _row_role(r),
                          "requirement": r.get("requirement", ""),
                          "source": r.get("source", ""),
                          "covered_by": covmap[ver].get(rid, []),
                          **({"evidence": ev["evidence"], "reach": ev["reach"]}
                             if ev is not None else {}),
                          **({"exempt_reason": exempt_reason_at(exempt, rid, ver)}
                             if status == "exempt" else {})})
        n = len(rows)
        check_ids = [r.get("id") for r in rows
                     if r.get("id") in covmap[ver]]
        by_transport = {t: {"musts": 0, "check": 0, "exempt": 0, "gap": 0} for t in TRANSPORTS}
        for r, jr in zip(sorted(rows, key=lambda x: x.get("id", "")), jrows):
            bt = by_transport[_row_transport(r)]
            bt["musts"] += 1
            bt[jr["status"]] += 1
        # publication state (PLAN-0825 §E) — data-driven off the counts just computed,
        # never hand-set. has_register is the register TREE existing at all (a version
        # can only be VERSIONS-listed once its register lands, so this is True for
        # every iteration here today; the check still runs for real rather than being
        # assumed, so a future refactor of VERSIONS's own sourcing can't silently
        # break it).
        has_register = os.path.isdir(os.path.join(REQ, ver))
        state = _version_state(n_check, n_exempt, has_register, dict(gap_by_test))
        entry = {
            "state": state,
            # D5-03 contract (one landing with D2-06): the legacy "no further work
            # planned; N webhook-receiver rows ungraded" line is rendered ONLY for a
            # converting version older than the current site version — emitted
            # explicitly so the page never guesses it from the GAP mix.
            "no_further_work": state == "converting" and ver < CURRENT_SITE_VERSION,
            "musts": n, "check": n_check, "exempt": n_exempt,
            "gap": n - n_check - n_exempt,
            "accounted_pct": round(100 * (n_check + n_exempt) / n) if n else 0,
            # the honest split of the CHECK bucket by evidence class — the CHECK
            # count itself is unchanged; this names what kind of evidence backs it
            # export v2 (D2-07): the surface the denominator is measured against, the
            # transport split, and the scaffolds later tasks fill (null until then).
            # D5 renders exactly these names (RV1 C2).
            "roles": roles_block(rows, {jr["id"]: jr["status"] for jr in jrows},
                                 (agent_axis or {}).get(ver)),          # D2-08
            "by_transport": by_transport,
            "surface": None,                        # filled below (subprocess census)
            "discovery_live": None,                 # D4-08 writes {stores, as_of}
            "evidence_classes": list(evidence.CLASSES),
            "evidence_breakdown": evidence.breakdown(evmap, ver, check_ids),
            "gap_by_testability": dict(sorted(gap_by_test.items())),
            "areas": [{"area": k, **dict(sorted(v.items()))}
                      for k, v in sorted(areas.items())],
            "rows": jrows,
        }
        # census (§E) for EVERY state (D2-06): the completeness mode/unaccounted count
        # is a fact about the register whatever the publication state — it used to be
        # emitted only for `building`, which hid the census the moment a version had
        # one CHECK row.
        if completeness is None:
            completeness = _completeness_report() or {}
        if schema_census is None:
            schema_census = _schema_census_report() or {}
        if should_census is None:
            should_census = _should_census_report() or {}      # D2-10: surface.should
        census = _census_for(ver, completeness)
        if census is not None:
            entry["census"] = census
        entry["surface"] = _surface_for(ver, completeness, schema_census, pins, should_census)
        out["versions"][ver] = entry

    # `unregistered` (§E): pinned in SOURCES.lock but no register tree at all yet.
    # Today this set is empty (every pinned spec version already has a register —
    # see REGISTER_ONLY_VERSIONS for the newest one), but the branch is real
    # production code, not aspirational: the day a version is pinned ahead of its
    # register work, it appears here automatically with zero code change, instead
    # of being silently hidden until "it looks good" (the exact overclaim-by-
    # omission §E calls out).
    for ver in unregistered_versions:
        out["versions"][ver] = {
            "state": "unregistered",
            "musts": 0, "check": 0, "exempt": 0, "gap": 0, "accounted_pct": 0,
            "roles": roles_block([], {}, None),
            "by_transport": {t: {"musts": 0, "check": 0, "exempt": 0, "gap": 0} for t in TRANSPORTS},
            "surface": {"prose": None, "schema": None, "should": None},
            "discovery_live": None,
            "evidence_classes": list(evidence.CLASSES),
            "evidence_breakdown": {c: 0 for c in evidence.CLASSES},
            "gap_by_testability": {},
            "areas": [],
            "rows": [],
        }
    return out


def load_exemptions():
    if not os.path.exists(EXEMPT_FILE):
        return {}
    return json.load(open(EXEMPT_FILE))


def _entry_exempts_at(entry, ver):
    """True when a single exemption entry (a {class,reason,versions?} dict) applies
    at `ver`. A scoped entry (with `versions`) applies only in its listed versions;
    an unscoped entry applies at every version where the id is a MUST row."""
    if isinstance(entry, dict) and entry.get("versions") is not None:
        return ver in entry["versions"]
    return True


def exempt_at(exempt, rid, ver):
    """True when `rid` is exempt AT `ver`.

    An id's value is EITHER a single entry dict OR a LIST of scoped entries. The
    list form is needed because the 2026-04-08 registers RENUMBERED ids, so one id
    can name an irreducibly-manual MUST of one CLASS at one version and an
    irreducibly-manual MUST of a DIFFERENT class at another (e.g. DISC-006 is
    spec-authoring @01-era but client-bound @04-08). Each list entry carries its own
    class/reason/versions and buckets EXEMPT only in its listed versions.

    Entries may carry an optional `"versions": ["2026-01-11", ...]` list — needed
    because the same id can name an irreducibly-manual MUST at one version and a
    covered/testable requirement at another. A scoped entry buckets EXEMPT only in
    its listed versions. A single entry WITHOUT the field keeps the original
    semantics: it applies at every version where the id is a MUST row (matrix only
    ever buckets MUST/MUST NOT rows, and coverage_gate.py separately forbids
    exempting a covered id).

    REGISTER_ONLY_VERSIONS short-circuit: an unscoped exemptions.json entry applies
    "wherever the id is a MUST", which would otherwise auto-grant EXEMPT at a
    register-only version with exactly the same zero-review hazard attribution()
    guards against for CHECK. False here forces those ids to GAP instead."""
    if ver in REGISTER_ONLY_VERSIONS:
        return False
    meta = exempt.get(rid)
    if meta is None:
        return False
    if isinstance(meta, list):
        return any(_entry_exempts_at(e, ver) for e in meta)
    return _entry_exempts_at(meta, ver)


def exempt_reason_at(exempt, rid, ver):
    """The written `reason` of the entry that exempts `rid` at `ver` (for the export)."""
    meta = exempt.get(rid)
    for e in (meta if isinstance(meta, list) else [meta]):
        if isinstance(e, dict) and _entry_exempts_at(e, ver):
            return e.get("reason", "")
    return ""


def _in_lane(r, lane):
    role = _row_role(r)
    return {"merchant": role in MERCHANT_LANE, "agent": role in AGENT_LANE,
            "other": role in OTHER_LANE or role == "unassigned"}[lane]


def account(ver, cov, exempt, role=None, transport=None):
    """Bucket the mandatory rows at `ver`; `role` (a LANES name) and `transport`
    (a TRANSPORTS name) narrow the rows — D2-08 per-lane / per-transport views."""
    rows = load_rows(ver)
    musts = [r for r in rows if r.get("keyword") in MANDATORY]
    if role:
        musts = [r for r in musts if _in_lane(r, role)]
    if transport:
        musts = [r for r in musts if _row_transport(r) == transport]
    buckets = {"CHECK": [], "EXEMPT": [], "GAP": []}
    gap_by_test = defaultdict(list)
    cov_ver = cov.get(ver, set())
    for r in musts:
        rid = r.get("id")
        if rid in cov_ver:
            buckets["CHECK"].append(rid)
        elif exempt_at(exempt, rid, ver):
            buckets["EXEMPT"].append(rid)
        else:
            buckets["GAP"].append(rid)
            gap_by_test[r.get("testability", "?")].append(rid)
    return musts, buckets, gap_by_test


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    ap.add_argument("--json", help="write the full traceability export (per-row status, "
                                   "covered_by, pinned-spec sources) to FILE")
    ap.add_argument("--require", choices=["testable", "all"], help="hard-fail on remaining gaps of this class")
    ap.add_argument("--version", help="restrict --require to one version")
    ap.add_argument("--role", choices=LANES,
                    help="D2-08: restrict --require and the report to one lane's rows — "
                         "merchant (business|both|handler), agent (platform|both|host; the "
                         "agent axis grades these, so --require reads agent_matrix), other "
                         "(spec-author)")
    ap.add_argument("--transport", choices=TRANSPORTS,
                    help="D2-08: restrict --require and the report to rows bound to one "
                         "transport (`any` = rows not bound to a transport)")
    a = ap.parse_args()
    agent_axis = _agent_axis_report() if a.role == "agent" else None

    attr = attribution()                # populates _IMPORT_FAILURES
    cov = {v: set(m.keys()) for v, m in _covmap_from(attr).items()}
    evmap = evidence.evidence_by_id(attr)
    exempt = load_exemptions()
    md = ["# UCP Conformance Coverage Matrix\n",
          "_Every MUST is CHECK (has a kill-rate check), EXEMPT (documented), or GAP (unaccounted)._\n",
          "_CHECK is split by EVIDENCE CLASS — live-wire (kill-tested against an "
          "independently-authored server), fixture-schema (our fixture through the "
          "official ucp-schema oracle), fixture-crypto (self-signed primitive "
          "self-test), self-referenced (only our own fixture; no independent oracle "
          "or target — the fixture-circularity class, conformance#79)._\n"]
    failed = False

    for ver in VERSIONS:
        musts, b, gap_by_test = account(ver, cov, exempt, role=a.role, transport=a.transport)
        n = len(musts)
        pct = 100 * (len(b["CHECK"]) + len(b["EXEMPT"])) / n if n else 0
        ebd = evidence.breakdown(evmap, ver, b["CHECK"])
        ebd_line = " · ".join(f"{k} {ebd[k]}" for k in evidence.CLASSES)
        scope = "".join(f" [{k}={v}]" for k, v in (("role", a.role), ("transport", a.transport)) if v)
        print(f"\n===== {ver}{scope} =====")
        print(f"  MUSTs: {n} | CHECK: {len(b['CHECK'])} | EXEMPT: {len(b['EXEMPT'])} | GAP: {len(b['GAP'])}  -> accounted {pct:.0f}%")
        if a.role == "agent":
            ax = (agent_axis or {}).get(ver)
            if ax:
                print(f"  agent axis (agent_matrix): musts {ax.get('agent_musts')} | CHECK {ax.get('check')} "
                      f"| EXEMPT {ax.get('exempt')} | GAP {ax.get('gap')}")
            else:
                print("  agent axis (agent_matrix): unavailable")
        print(f"  CHECK by evidence: {ebd_line}")
        if gap_by_test:
            print("  GAP by testability:", {k: len(v) for k, v in sorted(gap_by_test.items())})
        md.append(f"\n## {ver} — {pct:.0f}% accounted ({len(b['CHECK'])} check · {len(b['EXEMPT'])} exempt · {len(b['GAP'])} gap of {n} MUSTs)\n")
        md.append(f"- CHECK by evidence: {ebd_line}")
        for k in sorted(gap_by_test):
            md.append(f"- GAP/{k}: {', '.join(sorted(gap_by_test[k]))}")

        if a.require and (not a.version or a.version == ver):
            if a.role == "agent":
                # the agent lane's own gate over the same role field (DONE-2 item 2)
                ax = (agent_axis or {}).get(ver)
                agap = ax.get("gap") if ax else None
                if agap is None:
                    print(f"  ✗ {ver} [role=agent]: agent axis unavailable — cannot certify"); failed = True
                elif a.require == "all" and agap:
                    print(f"  ✗ {ver} [role=agent]: {agap} MUST(s) unaccounted (agent_matrix)"); failed = True
            elif a.require == "all" and b["GAP"]:
                print(f"  ✗ {ver}{scope}: {len(b['GAP'])} MUST(s) unaccounted (require=all)"); failed = True
            elif a.require == "testable":
                tg = gap_by_test.get("testable", [])
                if tg:
                    print(f"  ✗ {ver}: {len(tg)} TESTABLE gap(s) remain: {sorted(tg)}"); failed = True

    if a.md:
        open(a.md, "w").write("\n".join(md) + "\n")
        print(f"\nmatrix written -> {a.md}")

    if a.json:
        open(a.json, "w").write(json.dumps(export_json(), indent=1, sort_keys=False) + "\n")
        print(f"traceability export written -> {a.json}")

    # A checks/ module that would not import is an integrity failure independent of gap
    # accounting: its live kill-tests silently stopped running while its ids still
    # bucket as CHECK here (via the text-scan fallback). Fail LOUD — a matrix that can't
    # import a check module must not certify coverage over it (P0-3).
    if has_import_failures():
        print("\n✗ checks/ modules failed to import (their kill-tests are NOT running, "
              "yet their ids still bucket as CHECK):")
        for stem, err in import_failures():
            print(f"    {stem}: {err}")
        failed = True

    # A core checkset module that imported but exports the wrong CHECKS count (emptied /
    # shrunk / grown) is the same integrity failure as an import failure: its live
    # kill-tests stopped running while its ids still bucket as CHECK via the text scan.
    if has_core_checkset_failures():
        print("\n✗ core checkset module(s) drifted from the committed manifest count "
              "(their kill-tests are NOT running, yet their ids still bucket as CHECK):")
        for stem, exp, got in core_checkset_failures():
            print(f"    {stem}: exports {got} CHECKS, manifest expects {exp}")
        failed = True

    if failed:
        print("\nMATRIX GATE: FAIL"); sys.exit(1)
    print("\nMATRIX GATE: OK" if a.require else "\n(report only; pass --require to enforce)")


def selftest():
    """Kill-tests for `_version_state` (PLAN-0825 §E's publication state machine).

    The `unregistered` branch has no real production trigger TODAY — every spec
    version pinned in SOURCES.lock already has a register tree (see
    _pinned_spec_versions() vs VERSIONS) — so without this, "the code path must
    exist and be tested" would be aspirational, not proven. Pure/hermetic: no I/O
    beyond the one real `export_json()` sanity call at the end, no repo mutation."""
    # Rule R-a (PLAN-v3 §2.18, decision 25; D2-06). `testable_gap` is the version's
    # gap_by_testability dict; a GAP in the testable / needs-receiver / needs-oauth
    # tiers means checks are still landing -> `converting`, never `live`.
    cases = [
        ((0, 0, False, {}), "unregistered"),
        ((3, 0, False, {}), "unregistered"),   # register-tree absence wins regardless of counts
        ((0, 5, False, {"testable": 1}), "unregistered"),
        ((0, 0, True, {}), "building"),
        ((0, 0, True, {"manual": 4}), "building"),
        ((1, 0, True, {}), "live"),
        ((0, 1, True, {}), "live"),
        ((4, 2, True, {"manual": 7}), "live"),          # manual/untestable GAP never blocks live
        ((1, 0, True, {"testable": 3}), "converting"),
        ((4, 2, True, {"needs-receiver": 17}), "converting"),
        ((4, 2, True, {"needs-oauth": 1, "manual": 9}), "converting"),
        ((1, 0, True, {"testable": 0}), "live"),        # a zero-count tier is no GAP
    ]
    bad = 0
    for (n_check, n_exempt, has_register, testable_gap), want in cases:
        try:
            got = _version_state(n_check, n_exempt, has_register, testable_gap)
        except TypeError as e:
            got = f"TypeError: {e}"
        ok = got == want
        print(f"  {'✓' if ok else '✗'} state(check={n_check}, exempt={n_exempt}, "
              f"has_register={has_register}, gap={testable_gap}) = {got!r}"
              + ("" if ok else f"  <-- expected {want!r}"))
        bad += 0 if ok else 1

    bad += test_mandatory_filter()
    bad += test_roles_partition()

    # production sanity: today every pinned+registered version must classify
    # building/live (never unregistered) — proves the real export wires the same
    # function this selftest exercises, not a parallel copy that could drift.
    fresh = export_json()
    prod_states = {v: e["state"] for v, e in fresh["versions"].items()}
    wired_ok = bool(prod_states) and all(s in ("building", "live", "converting")
                                         for s in prod_states.values())
    # R-a on the real export: a version is `live` iff it has no GAP in the
    # testable / needs-receiver / needs-oauth tiers, and `converting` iff it has one.
    for v, e in fresh["versions"].items():
        blocking = sum(e.get("gap_by_testability", {}).get(t, 0) for t in CONVERTING_TIERS)
        if e["state"] == "live" and blocking:
            wired_ok = False
            print(f"  ✗ {v}: state=live with {blocking} blocking GAP(s) {e['gap_by_testability']}")
        if e["state"] == "converting" and not blocking:
            wired_ok = False
            print(f"  ✗ {v}: state=converting with no blocking GAP")
    print(f"  {'✓' if wired_ok else '✗'} production export states: {prod_states}"
          + ("" if wired_ok else "  <-- R-a violated (see above)"))
    bad += 0 if wired_ok else 1

    # D2-06 × D5-03 one-landing contract: `no_further_work` is EMITTED per version —
    # true iff the version is `converting` AND older than CURRENT_SITE_VERSION (the
    # legacy "no further work planned; N webhook-receiver rows ungraded" line), false
    # otherwise (the current site version and the latest register never carry it, so
    # the page cannot fall back to a heuristic that misfires on 2026-04-08).
    nfw_ok = True
    for v, e in fresh["versions"].items():
        want = e["state"] == "converting" and v < CURRENT_SITE_VERSION
        got = e.get("no_further_work")
        if got is not want:
            nfw_ok = False
            print(f"  ✗ {v}: no_further_work={got!r} (state {e['state']}, current site {CURRENT_SITE_VERSION}) <-- expected {want!r}")
    print(f"  {'✓' if nfw_ok else '✗'} no_further_work emitted per version (converting AND older than {CURRENT_SITE_VERSION})")
    bad += 0 if nfw_ok else 1

    print(f"\nmatrix selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


def test_mandatory_filter():
    """D2-01 (PLAN-v3 §2.4 / A1): the accounting denominator is the MANDATORY
    keyword class — MUST, MUST NOT, SHALL, SHALL NOT, REQUIRED (RFC 2119 §1-§3) —
    not just MUST/MUST NOT. A synthetic register with one row per keyword plus a
    SHOULD row must count exactly 4 mandatory rows through the SAME `account()`
    the report path uses. Hermetic: `load_rows` is swapped for the duration."""
    global load_rows
    synthetic = [
        {"id": "ZZZ-001", "keyword": "MUST", "testability": "testable"},
        {"id": "ZZZ-002", "keyword": "MUST NOT", "testability": "testable"},
        {"id": "ZZZ-003", "keyword": "REQUIRED", "testability": "testable"},
        {"id": "ZZZ-004", "keyword": "SHALL", "testability": "testable"},
        {"id": "ZZZ-005", "keyword": "SHOULD", "testability": "testable"},
    ]
    real = load_rows
    load_rows = lambda ver: list(synthetic)      # noqa: E731 — scoped swap
    try:
        musts, buckets, _gap = account("2026-08-25", {"2026-08-25": set()}, {})
    finally:
        load_rows = real
    got = sorted(r["id"] for r in musts)
    want = ["ZZZ-001", "ZZZ-002", "ZZZ-003", "ZZZ-004"]
    ok = got == want and len(buckets["GAP"]) == 4
    print(f"  {'✓' if ok else '✗'} test_mandatory_filter: account() counts {len(musts)} "
          f"mandatory of 5 synthetic rows (MUST/MUST NOT/REQUIRED/SHALL/SHOULD)"
          + ("" if ok else f"  <-- expected 4 {want}, got {got}"))
    return 0 if ok else 1


def test_roles_partition():
    """D2-08 (PLAN-v3 §2.4/§2.5 / A4): the per-role denominators PARTITION the
    MUSTs — `merchant + agent − both + other == musts` (both-role rows sit in both
    lanes; handler rows are merchant-lane and host rows agent-lane per decision 27;
    spec-author rows are `other`) — on a synthetic register through the SAME
    `roles_block()` the export uses, and on the real export."""
    synthetic = [
        {"id": "ZZZ-001", "keyword": "MUST", "role": "business"},
        {"id": "ZZZ-002", "keyword": "MUST", "role": "platform"},
        {"id": "ZZZ-003", "keyword": "MUST", "role": "both"},
        {"id": "ZZZ-004", "keyword": "MUST", "role": "handler"},
        {"id": "ZZZ-005", "keyword": "MUST", "role": "host"},
        {"id": "ZZZ-006", "keyword": "MUST", "role": "spec-author"},
    ]
    status = {r["id"]: "gap" for r in synthetic}
    try:
        rb = roles_block(synthetic, status, agent_axis=None)
        s = rb["summary"]
        ok = (s["merchant"], s["agent"], s["both"], s["other"]) == (3, 3, 1, 1) and \
            s["merchant"] + s["agent"] - s["both"] + s["other"] == 6 and \
            rb["merchant"]["musts"] == 3 and rb["agent"]["musts"] == 3 and \
            rb["other"]["by_role"] == {"spec-author": 1}
        detail = repr(rb)[:200]
    except NameError as e:
        ok, detail = False, f"NameError: {e}"
    print(f"  {'✓' if ok else '✗'} test_roles_partition: synthetic merchant 3 + agent 3 − both 1 "
          f"+ other 1 == 6 MUSTs" + ("" if ok else f"  <-- {detail}"))
    bad = 0 if ok else 1
    fresh = export_json()
    for v, e in fresh["versions"].items():
        rb = e.get("roles")
        if not rb:
            print(f"  ✗ test_roles_partition: {v} export has no roles block")
            bad += 1
            continue
        s = rb["summary"]
        tot = s["merchant"] + s["agent"] - s["both"] + s["other"]
        if tot != e["musts"]:
            print(f"  ✗ test_roles_partition: {v} merchant {s['merchant']} + agent {s['agent']} − "
                  f"both {s['both']} + other {s['other']} = {tot} != musts {e['musts']}")
            bad += 1
    if not bad:
        print("  ✓ test_roles_partition: real export roles partition the MUSTs at every version")
    return bad


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    main()

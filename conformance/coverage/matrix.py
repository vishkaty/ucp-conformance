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
import json, os, re, glob, sys, argparse
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence  # noqa: E402 — the evidence-class layer (sibling module)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONF = os.path.join(ROOT, "conformance")
REQ = os.path.join(CONF, "requirements")
EXEMPT_FILE = os.path.join(CONF, "coverage", "exemptions.json")
# 2026-08-25 added (register-only lane, see conformance/requirements/2026-08-25/):
# the reorganized-docs carry-forward register exists and is citation-checked, but is
# deliberately NOT locked into coverage_lock.json / review_signoffs.json yet (no
# independent-review pass has been done on the checks that now auto-extend their
# coverage claim to it) and does not back any site copy. See CURRENT_SITE_VERSION.
VERSIONS = ["2026-01-11", "2026-01-23", "2026-04-08", "2026-08-25"]
# The version whose accounted-coverage figures the public site currently advertises
# (evidence-class copy freshness in coverage_gate.py). Deliberately a NAMED constant,
# not VERSIONS[-1]: a newly added spec version starts register-only (no site copy, no
# ratchet floor, no coverage-lock/review-signoff entries) and must not silently become
# "current" for site claims just by being appended to VERSIONS. Bump this only when the
# site is deliberately migrated to a new version's published figures.
CURRENT_SITE_VERSION = "2026-04-08"
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
    return rows


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
           "spec_pins": {v: pins.get(v, "") for v in VERSIONS},
           "versions": {}}
    for ver in VERSIONS:
        rows = [r for r in load_rows_with_area(ver)
                if r.get("keyword") in ("MUST", "MUST NOT")]
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
        out["versions"][ver] = {
            "musts": n, "check": n_check, "exempt": n_exempt,
            "gap": n - n_check - n_exempt,
            "accounted_pct": round(100 * (n_check + n_exempt) / n) if n else 0,
            # the honest split of the CHECK bucket by evidence class — the CHECK
            # count itself is unchanged; this names what kind of evidence backs it
            "evidence_breakdown": evidence.breakdown(evmap, ver, check_ids),
            "gap_by_testability": dict(sorted(gap_by_test.items())),
            "areas": [{"area": k, **dict(sorted(v.items()))}
                      for k, v in sorted(areas.items())],
            "rows": jrows,
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
    exempting a covered id)."""
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


def account(ver, cov, exempt):
    rows = load_rows(ver)
    musts = [r for r in rows if r.get("keyword") in ("MUST", "MUST NOT")]
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
    a = ap.parse_args()

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
        musts, b, gap_by_test = account(ver, cov, exempt)
        n = len(musts)
        pct = 100 * (len(b["CHECK"]) + len(b["EXEMPT"])) / n if n else 0
        ebd = evidence.breakdown(evmap, ver, b["CHECK"])
        ebd_line = " · ".join(f"{k} {ebd[k]}" for k in evidence.CLASSES)
        print(f"\n===== {ver} =====")
        print(f"  MUSTs: {n} | CHECK: {len(b['CHECK'])} | EXEMPT: {len(b['EXEMPT'])} | GAP: {len(b['GAP'])}  -> accounted {pct:.0f}%")
        print(f"  CHECK by evidence: {ebd_line}")
        if gap_by_test:
            print("  GAP by testability:", {k: len(v) for k, v in sorted(gap_by_test.items())})
        md.append(f"\n## {ver} — {pct:.0f}% accounted ({len(b['CHECK'])} check · {len(b['EXEMPT'])} exempt · {len(b['GAP'])} gap of {n} MUSTs)\n")
        md.append(f"- CHECK by evidence: {ebd_line}")
        for k in sorted(gap_by_test):
            md.append(f"- GAP/{k}: {', '.join(sorted(gap_by_test[k]))}")

        if a.require and (not a.version or a.version == ver):
            if a.require == "all" and b["GAP"]:
                print(f"  ✗ {ver}: {len(b['GAP'])} MUST(s) unaccounted (require=all)"); failed = True
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


if __name__ == "__main__":
    main()

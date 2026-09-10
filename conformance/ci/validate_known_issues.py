#!/usr/bin/env python3
"""
validate_known_issues.py — the single KNOWN ISSUES file (conformance/ci/known_issues.json,
schema conformance/ci/known_issues.schema.json — D4's field set, PLAN-v3 §2.13, D5-07)
can never publish a refuted, stale, unevidenced or unanchored row:

  · `refuted` is a const false — a refuted finding (S8d) never renders anywhere
  · `re_verified` ≤ 30 days old, `review_by` ≤ 90 days ahead and unexpired
  · every `status_by_cut` entry with status `fixed` names its evidence (SHA/PR)
  · `repro.{command,expected,observed}` non-empty; symptom ≤ 300 chars; ids KI-###, unique
  · `ledger_row` exists in ops/GAP-LEDGER-0825.md when ops/ is mounted — else SKIP rc 2
    (never silently green)

  validate_known_issues.py            # real file → `known-issues: N rows · 0 stale · 0 refuted · ledger cross-ref OK`
  validate_known_issues.py --selftest # hermetic kill-tests (each planted defect must red)
Exit 0 pass · 1 fail · 2 honest skip (ops/ not mounted). Stdlib only; jsonschema used when importable.
"""
import copy, datetime, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
KI = ROOT / "conformance" / "ci" / "known_issues.json"
SCHEMA = ROOT / "conformance" / "ci" / "known_issues.schema.json"
LEDGER = ROOT / "ops" / "GAP-LEDGER-0825.md"
TODAY = datetime.date.today()

ARTIFACTS = ("ucp-spec", "ucp-schema", "python-sdk", "js-sdk", "samples-python", "samples-node",
             "official-conformance", "golden-0825", "spck-conformance")
STATUSES = ("open", "fixed", "not-applicable")
SPEC_PIN = "cd78fb38e819de77d9b527d110476eccb876f1bd"
RE_VERIFIED_MAX_DAYS = 30
REVIEW_BY_MAX_DAYS = 90


def _date(s):
    try:
        return datetime.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def ledger_row_ids(path=None):
    """Row ids of ops/GAP-LEDGER-0825.md (`| G1 |`, `| R20 |`, `| S8d |`), or None if ops/
    is not mounted (SPCK_LEDGER overrides the path for tests)."""
    import os
    path = pathlib.Path(path or os.environ.get("SPCK_LEDGER") or LEDGER)
    if not path.exists():
        return None
    return set(re.findall(r"^\| ([GRS]\d+[a-e]?) \|", path.read_text(encoding="utf-8"), re.M))


def validate_doc(doc, today, ledger_ids):
    """Failure strings for the known-issues document (empty = green). `ledger_ids=None`
    = ops/ not mounted (cross-ref skipped; the caller reports SKIP)."""
    fails = []
    if doc.get("spec_pin") != SPEC_PIN:
        fails.append(f"spec_pin must be the pinned 2026-08-25 commit {SPEC_PIN[:8]} (got {str(doc.get('spec_pin'))[:8]})")
    if not _date(doc.get("generated")):
        fails.append("generated: not a date")
    seen = set()
    for i, r in enumerate(doc.get("issues") or []):
        rid = r.get("id", f"#{i}")
        if not re.fullmatch(r"KI-\d{3}", str(rid)):
            fails.append(f"{rid}: id must match KI-###")
        if rid in seen:
            fails.append(f"{rid}: duplicate id")
        seen.add(rid)
        for k in ("id", "artifact", "component", "symptom", "repro", "status_by_cut", "origin",
                  "re_verified", "confidence", "ledger_row", "review_by", "refuted"):
            if k not in r:
                fails.append(f"{rid}: missing field {k!r}")
        if r.get("refuted") is not False:
            fails.append(f"{rid}: refuted must be the const false — a refuted finding never renders")
        if r.get("artifact") not in ARTIFACTS:
            fails.append(f"{rid}: artifact {r.get('artifact')!r} not in the enum")
        if r.get("origin") not in ("ours", "theirs", "shared"):
            fails.append(f"{rid}: origin {r.get('origin')!r} not in ours|theirs|shared")
        if r.get("confidence") not in ("high", "medium", "low"):
            fails.append(f"{rid}: confidence {r.get('confidence')!r} not in high|medium|low")
        sym = r.get("symptom") or ""
        if not sym or len(sym) > 300:
            fails.append(f"{rid}: symptom must be 1..300 chars (got {len(sym)})")
        if not (r.get("component") or "").strip():
            fails.append(f"{rid}: component empty")
        rp = r.get("repro") or {}
        for k in ("command", "expected", "observed"):
            if not str(rp.get(k, "")).strip():
                fails.append(f"{rid}: repro.{k} empty — a known issue without a reproduction is an opinion")
        cuts = r.get("status_by_cut") or []
        if not cuts:
            fails.append(f"{rid}: status_by_cut empty")
        for c in cuts:
            if not str(c.get("cut", "")).strip():
                fails.append(f"{rid}: status_by_cut entry without a cut")
            if c.get("status") not in STATUSES:
                fails.append(f"{rid}: cut {c.get('cut')!r} status {c.get('status')!r} not in {'|'.join(STATUSES)}")
            if c.get("status") == "fixed" and not str(c.get("evidence", "")).strip():
                fails.append(f"{rid}: cut {c.get('cut')!r} is `fixed` without evidence (SHA/PR required)")
        for u in r.get("filed") or []:
            if not str(u).startswith("https://github.com/"):
                fails.append(f"{rid}: filed entry {u!r} is not a github.com URL")
        rv = _date(r.get("re_verified"))
        if rv is None:
            fails.append(f"{rid}: re_verified not a date")
        elif (today - rv).days > RE_VERIFIED_MAX_DAYS:
            fails.append(f"{rid}: stale — re_verified {r.get('re_verified')} is {(today - rv).days} days old (max {RE_VERIFIED_MAX_DAYS})")
        elif (today - rv).days < 0:
            fails.append(f"{rid}: re_verified {r.get('re_verified')} is in the future")
        rb = _date(r.get("review_by"))
        if rb is None:
            fails.append(f"{rid}: review_by not a date")
        elif rb < today:
            fails.append(f"{rid}: review_by {r.get('review_by')} expired")
        elif (rb - today).days > REVIEW_BY_MAX_DAYS:
            fails.append(f"{rid}: review_by {r.get('review_by')} is more than {REVIEW_BY_MAX_DAYS} days out")
        lr = r.get("ledger_row")
        if not re.fullmatch(r"[GRS]\d+[a-e]?", str(lr or "")):
            fails.append(f"{rid}: ledger_row {lr!r} is not a ledger row id")
        elif ledger_ids is not None and lr not in ledger_ids:
            fails.append(f"{rid}: ledger_row {lr} not found in ops/GAP-LEDGER-0825.md")
    # the JSON-schema referee, when the independent engine is installed
    try:
        import jsonschema  # noqa: F401
        from jsonschema import Draft202012Validator
        if SCHEMA.exists():
            sch = json.load(open(SCHEMA))
            for e in Draft202012Validator(sch).iter_errors(doc):
                fails.append(f"schema: {'/'.join(str(x) for x in e.absolute_path)}: {e.message[:120]}")
    except ImportError:
        pass
    return fails


def main():
    if selftest() != 0:                    # a validator that cannot fail validates nothing
        return 1
    print()
    if not KI.exists():
        print("known-issues: FAIL — conformance/ci/known_issues.json missing"); return 1
    doc = json.load(open(KI))
    ledger = ledger_row_ids()
    fails = validate_doc(doc, TODAY, ledger)
    for f in fails:
        print(f"  x {f}")
    rows = doc.get("issues") or []
    stale = sum(1 for f in fails if ": stale" in f)
    refuted = sum(1 for r in rows if r.get("refuted") is not False)
    xref = "ledger cross-ref OK" if ledger is not None else "ledger cross-ref SKIP (ops/ not mounted)"
    print(f"known-issues: {len(rows)} rows · {stale} stale · {refuted} refuted · {xref}"
          + ("" if not fails else f" · FAIL ({len(fails)} finding(s))"))
    if fails:
        return 1
    return 2 if ledger is None else 0


def selftest():
    bad = 0
    today = datetime.date(2026, 9, 10)
    seed = {"generated": "2026-09-10", "spec_pin": "cd78fb38e819de77d9b527d110476eccb876f1bd",
            "issues": [{
                "id": "KI-001", "artifact": "python-sdk", "component": "generated models",
                "symptom": "constraint families dropped", "repro": {"command": "python3 x.py", "expected": "enforced", "observed": "not enforced"},
                "status_by_cut": [{"cut": "PyPI 0.5.0", "status": "open"},
                                  {"cut": "main 836b0228", "status": "fixed", "evidence": "python-sdk#89 merged 836b0228"}],
                "origin": "theirs", "filed": ["https://github.com/Universal-Commerce-Protocol/python-sdk/pull/89"],
                "re_verified": "2026-09-09", "confidence": "high", "ledger_row": "G1",
                "review_by": "2026-12-08", "refuted": False}]}
    ledger_ids = {"G1", "R20"}

    def case(name, mutate, want_red, ledger=ledger_ids, must_name=None):
        nonlocal bad
        doc = copy.deepcopy(seed)
        mutate(doc)
        fails = validate_doc(doc, today, ledger)
        got_red = bool(fails)
        ok = got_red == want_red and (not must_name or any(must_name in f for f in fails))
        print(f"  {'✓' if ok else '✗'} {name}: {'RED' if got_red else 'GREEN'}"
              + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}"
                               + (f" naming {must_name!r}" if must_name else ""))
              + (("\n      " + "\n      ".join(fails)) if fails and not ok else ""))
        bad += 0 if ok else 1

    case("honest seed row", lambda d: None, want_red=False)
    case("re_verified 40 days old (stale)", lambda d: d["issues"][0].__setitem__("re_verified", "2026-08-01"), True, must_name="stale")
    case("refuted: true (S8d must never render)", lambda d: d["issues"][0].__setitem__("refuted", True), True, must_name="refuted")
    case("fixed status without evidence", lambda d: d["issues"][0]["status_by_cut"][1].pop("evidence"), True, must_name="evidence")
    case("unknown ledger_row (ops mounted)", lambda d: d["issues"][0].__setitem__("ledger_row", "G99"), True, must_name="G99")
    case("empty repro command", lambda d: d["issues"][0]["repro"].__setitem__("command", ""), True, must_name="repro")
    case("review_by expired", lambda d: d["issues"][0].__setitem__("review_by", "2026-09-01"), True, must_name="review_by")
    case("review_by beyond 90 days", lambda d: d["issues"][0].__setitem__("review_by", "2027-06-01"), True, must_name="review_by")
    case("symptom over 300 chars", lambda d: d["issues"][0].__setitem__("symptom", "x" * 301), True, must_name="symptom")
    case("duplicate id", lambda d: d["issues"].append(copy.deepcopy(d["issues"][0])), True, must_name="duplicate")
    case("wrong spec_pin const", lambda d: d.__setitem__("spec_pin", "deadbeef"), True, must_name="spec_pin")
    case("ledger not mounted → cross-ref skipped, otherwise green", lambda d: None, want_red=False, ledger=None)

    print(f"\nknown-issues selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())

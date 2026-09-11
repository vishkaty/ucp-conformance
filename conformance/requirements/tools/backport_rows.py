#!/usr/bin/env python3
"""
backport_rows.py — copy register rows from one spec version to another, id-preserving
(D2-15, decision 3: the 19 mandatory + 1 MAY loyalty rows that ucp#813 backported into
the v2026-04-08 tag at a25a4a24). Each copied row keeps its id, keyword, requirement,
quote, transport, testability flags, role and normative_basis; its `source` path is
rewritten per the path map and its line anchor RE-LOCATED by searching the quote's first
fragment in the target file (never copied blindly — the backported doc has different
line numbers); `versions` becomes [target]; `lineage` becomes
{from: <source version>, disposition: backported}; `role_provenance` becomes
`backported`. verify_register then proves every copied quote verbatim at the target pin,
and verify_carry_forward checks the reverse direction (each backported row has its source
counterpart with a normalized-identical quote).

  python3 conformance/requirements/tools/backport_rows.py --from 2026-08-25 --to 2026-04-08 \\
      --area loyalty --map docs/specification/common/extensions/loyalty.md=docs/specification/loyalty.md \\
      [--dry-run]
  python3 conformance/requirements/tools/backport_rows.py --selftest
"""
import argparse, json, os, pathlib, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.dirname(HERE)
CONF = os.path.dirname(REQ)
sys.path.insert(0, CONF)
from common.spec_versions import VERSION_TREE  # noqa: E402

VENDOR = pathlib.Path(CONF) / ".vendor"


def selftest():
    """2-row fixture: rows quoting lines 3 and 7 of a source doc are relocated onto a
    target doc where the same sentences sit at lines 5 and 9 (path rewritten via the
    map, line found by quote, lineage/versions/provenance set, id preserved); a quote
    absent from the target is reported, never guessed."""
    import tempfile
    bad = 0

    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1

    target_text = "\n".join(["# Loyalty", "", "intro", "", "Programs MUST be modeled as siblings.",
                             "", "more", "", "The business MUST return `provisional: true`.", ""]) + "\n"
    with tempfile.TemporaryDirectory() as td:
        tdir = pathlib.Path(td)
        (tdir / "docs" / "specification").mkdir(parents=True)
        (tdir / "docs" / "specification" / "loyalty.md").write_text(target_text)
        rows = [
            {"id": "LOY-002", "keyword": "MUST", "requirement": "modeled as siblings",
             "source": "ucp:docs/specification/common/extensions/loyalty.md#L3",
             "quote": "Programs MUST be modeled as siblings.", "versions": ["2026-08-25"],
             "transport": ["any"], "testability": "testable", "role": "business",
             "role_provenance": "subject", "normative_basis": "sentence", "notes": "n"},
            {"id": "LOY-009", "keyword": "MUST", "requirement": "provisional true",
             "source": "ucp:docs/specification/common/extensions/loyalty.md#L7",
             "quote": "the business MUST return **`provisional: true`**.", "versions": ["2026-08-25"],
             "transport": ["any"], "testability": "testable", "role": "business",
             "role_provenance": "review:x", "normative_basis": "sentence", "notes": "n"},
            {"id": "LOY-099", "keyword": "MUST", "requirement": "absent",
             "source": "ucp:docs/specification/common/extensions/loyalty.md#L20",
             "quote": "This sentence is not in the target.", "versions": ["2026-08-25"],
             "transport": ["any"], "testability": "testable", "role": "business",
             "role_provenance": "subject", "normative_basis": "sentence", "notes": "n"},
        ]
        pmap = {"docs/specification/common/extensions/loyalty.md": "docs/specification/loyalty.md"}
        try:
            out, problems = backport(rows, "2026-08-25", "2026-04-08", pmap, tdir)
        except NameError as e:
            out, problems = f"NameError: {e}", None
        ok = isinstance(out, list) and len(out) == 2 and \
            out[0]["source"] == "ucp:docs/specification/loyalty.md#L5" and \
            out[1]["source"] == "ucp:docs/specification/loyalty.md#L9" and \
            all(r["versions"] == ["2026-04-08"] and r["lineage"] == {"from": "2026-08-25", "disposition": "backported"}
                and r["role_provenance"] == "backported" and r["role"] == "business" and r["id"].startswith("LOY-")
                for r in out)
        bad += case("2 rows relocated onto the target doc (path rewritten, line found by quote, lineage/versions/"
                    "provenance set, id + role preserved)", ok, repr(out)[:300])
        bad += case("a quote absent from the target is reported, not guessed",
                    isinstance(problems, list) and any("LOY-099" in p for p in problems), repr(problems)[:200])
    print(f"\nbackport_rows selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

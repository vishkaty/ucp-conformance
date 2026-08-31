#!/usr/bin/env python3
"""
validate_schema_census.py — kill-tests for verify_schema_census.py (G0-c).

Hermetic (synthetic in-memory fixtures, no vendor-tree or network dependency).
Proves the two detection paths the G0-c brief names explicitly:

  1. An unreferenced schema file (no register row cites it, no ruling covers it) is
     DETECTED by classify_files().
  2. A mutated vendored hash (a ruling's recorded hash no longer matches the live
     file — the re-pin case) is DETECTED as a stale ruling.

Plus:
  3. A referenced file (a row's source cites it) and a validly-ruled file are BOTH
     correctly left out of both the unreferenced and stale lists (class negative —
     the detector does not over-fire).
  4. validate_ruling()/validate_fenced_ruling() reject malformed entries (bad class,
     thin reason, missing hash) — the same data-hygiene discipline
     verify_register_completeness.py's validate_waiver() already carries.
  5. constraint_counts() correctly counts JSON-Schema constraint keywords as object
     KEYS (not as incidental substrings inside string values — a description field
     that happens to say "the minimum order total" must not count as a `minimum`
     keyword hit).
  6. A SANITY CHECK against the real vendored tree: the tool runs end-to-end without
     crashing and produces a per-version report with a non-negative file_count for
     every pinned version (catches a wiring break — e.g. a version whose vendor dir
     silently vanished — without asserting today's exact unreferenced count, which is
     expected to change as L2 lands schema_enforced rows).

Run:  python3 conformance/selfcheck/validate_schema_census.py
Exit 0 = every proof holds; 1 = a detector is broken.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from verify_schema_census import (   # noqa: E402
    classify_files, constraint_counts, run_census, validate_ruling,
    validate_fenced_ruling,
)


def check_unreferenced_detection():
    """Kill-test 1: a file with no referencing row and no ruling is unreferenced."""
    failures = []
    entries = [("source/schemas/a.json", "hash-a"),
               ("source/schemas/b.json", "hash-b")]
    referenced = {"source/schemas/a.json"}   # b is NOT referenced
    ruling_idx = {}                          # and NOT ruled
    unreferenced, stale = classify_files(entries, referenced, ruling_idx, "vX")
    if unreferenced != ["source/schemas/b.json"]:
        failures.append(f"expected exactly b.json unreferenced, got {unreferenced}")
    if stale:
        failures.append(f"expected no stale rulings, got {stale}")
    return failures


def check_stale_hash_detection():
    """Kill-test 2: a ruling recorded at an OLD hash must flag as stale when the
    live file's hash (the re-pin case, simulated) has moved on."""
    failures = []
    entries = [("source/schemas/c.json", "hash-NEW")]   # live hash changed
    referenced = set()
    ruling_idx = {
        ("vX", "source/schemas/c.json"):
            {"version": "vX", "file": "source/schemas/c.json",
             "class": "out-of-scope", "hash": "hash-OLD",
             "reason": "was out of scope at the old content — needs re-review"},
    }
    unreferenced, stale = classify_files(entries, referenced, ruling_idx, "vX")
    if unreferenced:
        failures.append(f"a ruled-but-stale file must not ALSO show as bare "
                        f"unreferenced, got {unreferenced}")
    if stale != [("source/schemas/c.json", "hash-OLD", "hash-NEW")]:
        failures.append(f"expected c.json flagged stale (OLD->NEW), got {stale}")
    return failures


def check_class_negative():
    """Kill-test 3: a referenced file and a validly-ruled (hash-matching) file must
    NOT appear in either finding list — the detector does not over-fire."""
    failures = []
    entries = [("source/schemas/referenced.json", "h1"),
               ("source/schemas/ruled.json", "h2")]
    referenced = {"source/schemas/referenced.json"}
    ruling_idx = {
        ("vX", "source/schemas/ruled.json"):
            {"version": "vX", "file": "source/schemas/ruled.json",
             "class": "non-normative", "hash": "h2",
             "reason": "a $defs-only helper fragment covered by its parent's row"},
    }
    unreferenced, stale = classify_files(entries, referenced, ruling_idx, "vX")
    if unreferenced or stale:
        failures.append(f"class negative fired: unreferenced={unreferenced} "
                        f"stale={stale}")
    return failures


def check_ruling_validation():
    """Kill-test 4: malformed rulings are rejected."""
    failures = []
    bad_class = validate_ruling({"class": "not-a-real-class",
                                 "reason": "x" * 40, "hash": "abc"})
    if not bad_class:
        failures.append("validate_ruling did not catch a bogus class")
    thin_reason = validate_ruling({"class": "out-of-scope", "reason": "too short",
                                   "hash": "abc"})
    if not thin_reason:
        failures.append("validate_ruling did not catch a too-thin reason")
    missing_hash = validate_ruling({"class": "out-of-scope", "reason": "x" * 40})
    if not missing_hash:
        failures.append("validate_ruling did not catch a missing hash")
    good = validate_ruling({"class": "out-of-scope", "reason": "x" * 40, "hash": "abc"})
    if good:
        failures.append(f"validate_ruling rejected a well-formed ruling: {good}")

    bad_fenced = validate_fenced_ruling({"class": "nonsense", "reason": "x" * 25})
    if not bad_fenced:
        failures.append("validate_fenced_ruling did not catch a bogus class")
    good_fenced = validate_fenced_ruling({"class": "duplicate", "reason": "x" * 25})
    if good_fenced:
        failures.append(f"validate_fenced_ruling rejected a well-formed entry: "
                        f"{good_fenced}")
    return failures


def check_constraint_counts():
    """Kill-test 5: constraint keywords are counted as object KEYS, not as
    substrings inside unrelated string values (the false-positive class a naive
    text-regex approach would fall into)."""
    failures = []
    doc = {
        "type": "object",
        "description": "Must be at least the minimum order total.",  # NOT a hit —
        # "minimum" appears only inside a string value here, not as a key.
        "properties": {
            "amount": {"type": "integer", "minimum": 0, "maximum": 999},
            "currency": {"type": "string", "enum": ["USD", "EUR"]},
            "nested": {"if": {}, "then": {}, "else": {}},
        },
        "additionalProperties": False,
    }
    counts = constraint_counts(doc)
    want = {"minimum": 1, "maximum": 1, "enum": 1, "if": 1, "then": 1, "else": 1,
            "additionalProperties": 1}
    if counts != want:
        failures.append(f"constraint_counts mismatch: got {counts}, want {want}")
    return failures


def check_live_sanity():
    """Sanity check 6: the real tool runs end-to-end against the vendored tree
    without crashing, and every pinned version reports a positive file_count (a
    version whose vendor dir silently vanished would report 0 and go unnoticed
    otherwise)."""
    failures = []
    per_version, ruling_errs, fenced, fenced_errs = run_census()
    if ruling_errs:
        failures.append(f"real schema_census_rulings.json has invalid entries: "
                        f"{ruling_errs}")
    if fenced_errs:
        failures.append(f"real fenced_hit_rulings.json has invalid entries: "
                        f"{fenced_errs}")
    for version, d in per_version.items():
        if d["file_count"] <= 0:
            failures.append(f"{version}: file_count is {d['file_count']} — vendor "
                            f"dir missing or glob broken")
    return failures


def main():
    failures = []
    failures += check_unreferenced_detection()
    failures += check_stale_hash_detection()
    failures += check_class_negative()
    failures += check_ruling_validation()
    failures += check_constraint_counts()
    failures += check_live_sanity()
    if failures:
        print("SCHEMA-CENSUS GATE: FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("SCHEMA-CENSUS GATE: PASS")
    print("  unreferenced-file detection: OK")
    print("  stale-ruling (hash-diff self-expiry) detection: OK")
    print("  class negative (referenced + validly-ruled): silent")
    print("  ruling/fenced-ruling validation: OK")
    print("  constraint_counts: key-based, no string-substring false positives")
    print("  live sanity: real census runs clean across all pinned versions")
    return 0


if __name__ == "__main__":
    sys.exit(main())

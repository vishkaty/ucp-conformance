#!/usr/bin/env python3
"""
verify_schema_census.py — G0-c: the schema-constraint denominator proof.

PLAN-0825 A.3: a `pattern` on `version`, a `maxProperties: 1` on `location_serves`,
the five JWK `if/then` rules — all normative, none carrying an RFC-2119 keyword, all
invisible to the prose keyword census (verify_register_completeness.py). This is not
hypothetical: these exact constraints are the ones ucp-sdk 0.5.0 silently dropped
(our filed #88-#93). A conformance suite that can't see them repeats the SDK's
failure at the register level.

Design (P-7 acknowledged: the ONE new moving part this plan adds, rather than folding
schema-constraint accounting into the prose census, which structurally cannot see it):
per-schema-file rows, proven complete by a mechanical inventory diff. This tool:

  1. Walks the pinned vendored tree's schema corpus (`source/schemas/**/*.json`) and
     service/handler contracts (`source/services/**/*.openapi.json` +
     `*.openrpc.json` — OpenAPI/OpenRPC service definitions encode required
     parameters/headers/status codes, the same class of schema-adjacent normative
     surface, per A.3 point 3) for every pinned spec version.
  2. Inventories each file: a constraint-keyword count (the same class the 08-27
     schema-embedded-constraint census counted: additionalProperties, minimum, const,
     minItems, format, enum, minLength, pattern, if/then/else, propertyNames,
     uniqueItems, maximum, minProperties, maxLength, maxProperties) and a SHA-256
     content hash.
  3. Requires every file to be either (a) REFERENCED — a register row's `source`
     already points into `source/schemas/` or `source/services/` for that file (some
     do today: existing rows that cite a schema/handler contract directly land 45/81
     of 04-08's files, 56/123 of 08-25's — real, if incomplete, coverage; the
     systematic ONE-ROW-PER-FILE census A.3 point 1 describes is future L2 work, not
     built yet, so the remainder is an honest, expected gap rather than a bug), or
     (b) RULED — a recorded, spec-grounded, hash-pinned entry in
     `conformance/coverage/schema_census_rulings.json` (e.g. a deliberately
     out-of-scope vertical, or a non-normative helper `$defs`-only fragment already
     covered by its parent's row).
  4. Self-expiring hash-diff acknowledgement (the repo's `known_*` idiom — see
     `conformance/ci/known_oracle_divergences.json`'s doctrine, applied here to
     schema files instead of oracle-verdict divergences): a ruling records the file's
     hash AT RULING TIME. If the live vendored hash no longer matches (a re-pin
     changed the file's content), the ruling is STALE — the file reverts to
     effectively un-ruled until a human re-reviews it and updates the hash. A stale
     ruling can never quietly keep covering changed content.
  5. A one-time FENCED-HIT AUDIT (A.2's last bullet): the prose keyword census
     deliberately skips fenced code blocks (examples), but a normative sentence
     could in principle hide inside one. This re-runs the same MUST/MUST
     NOT/SHALL/SHALL NOT/REQUIRED scan *inside* fences, in report mode only —
     NEVER a standing gate (fences are examples by convention; gating on them would
     flood false positives, violating speclint's zero-false-positive doctrine) — and
     cross-checks every hit against `conformance/coverage/fenced_hit_rulings.json`.
     Deliberately NOT folded into `register_completeness_waivers.json`: a fenced hit
     is invisible to `scan_keywords()` by construction (in_fence lines are skipped
     entirely), so a waiver entry keyed to one would never be "used" by that gate's
     own bookkeeping and would misreport as permanently STALE there — a mechanical
     anti-pattern for a genuinely different concern. This tool owns its own small,
     dedicated rulings file instead.

Wiring (PLAN-0825 A.1's own lesson re-applied): landing this in GATE mode on day one
would either block forever (every schema file starts unreferenced — no
`schema_enforced` rows exist yet) or invite padding the ruling file to force green.
So this tool is REPORT-ONLY by default — it always prints the full findings and exits
0 — with an explicit `--enforce` flag reserved for the later, deliberate gate flip
(PLAN-0825 G0-c / L2 landing). `run_suite.py` wires the report-only invocation so the
findings are visible on every run without blocking anything yet.

Run:
  python3 conformance/selfcheck/verify_schema_census.py            # report, exit 0
  python3 conformance/selfcheck/verify_schema_census.py --enforce  # gate (future flip)
  python3 conformance/selfcheck/verify_schema_census.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VENDOR = ROOT / "conformance" / ".vendor"
REQ_DIR = ROOT / "conformance" / "requirements"
RULINGS_FILE = ROOT / "conformance" / "coverage" / "schema_census_rulings.json"
FENCED_RULINGS_FILE = ROOT / "conformance" / "coverage" / "fenced_hit_rulings.json"
sys.path.insert(0, str(ROOT / "conformance"))
sys.path.insert(0, str(HERE))
from common.spec_versions import VERSION_TREE          # noqa: E402
from verify_register_completeness import parse_source   # noqa: E402

# The 08-27 schema-embedded-constraint census (PLAN-0825 section 2 item 6) counted
# exactly this keyword set over common/** + capability.json (65 files); reused here
# as the per-file inventory's constraint vocabulary so a file's count is comparable
# to that census's totals.
CONSTRAINT_KEYWORDS = (
    "additionalProperties", "minimum", "const", "minItems", "format", "enum",
    "minLength", "pattern", "if", "then", "else", "propertyNames", "uniqueItems",
    "maximum", "minProperties", "maxLength", "maxProperties",
)

# Schema files proper, plus the OpenAPI/OpenRPC service/handler contracts that
# encode required parameters/headers/status codes (A.3 point 3) — same walk.
SCHEMA_GLOBS = ("source/schemas/**/*.json",)
SERVICE_GLOBS = ("source/services/**/*.openapi.json", "source/services/**/*.openrpc.json")

VALID_RULING_CLASSES = {"out-of-scope", "non-normative"}
VALID_FENCED_CLASSES = {"non-normative", "duplicate", "promote"}

KW_RE = re.compile(r"\b(MUST NOT|MUST|SHALL NOT|SHALL|REQUIRED)\b")


# --- inventory (I/O) --------------------------------------------------------------

def inventory_files(vendor_dir: pathlib.Path):
    """Sorted list of (relative-posix-path, absolute Path) for every schema/service
    file under `vendor_dir`, deduped across overlapping glob patterns."""
    if not vendor_dir.is_dir():
        return []
    seen = {}
    for pattern in SCHEMA_GLOBS + SERVICE_GLOBS:
        for path in vendor_dir.glob(pattern):
            rel = str(path.relative_to(vendor_dir))
            seen[rel] = path
    return sorted(seen.items())


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def constraint_counts(doc) -> dict:
    """PURE: count CONSTRAINT_KEYWORDS as object keys anywhere in a parsed JSON
    document (dict/list walk, not text regex — avoids false hits inside string
    values like descriptions that happen to mention 'minimum')."""
    counts: dict = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in CONSTRAINT_KEYWORDS:
                    counts[k] = counts.get(k, 0) + 1
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return counts


def referenced_files(req_dir: pathlib.Path, version: str) -> set:
    """Relative paths (under the version's vendor dir) that a register row already
    cites via `source` pointing into `source/schemas/` or `source/services/` — the
    future per-schema-file `schema_enforced: true` row shape (A.3 point 1). None
    exist at any pinned version yet; this returns the empty set honestly until L2
    lands them, at which point a row's own `source` field wires it up for free."""
    referenced = set()
    vdir = req_dir / version
    if not vdir.is_dir():
        return referenced
    for af in sorted(vdir.glob("*.json")):
        for row in json.loads(af.read_text()).get("rows", []):
            path = parse_source(row.get("source", ""))[1]
            if path.startswith("source/schemas/") or path.startswith("source/services/"):
                referenced.add(path)
    return referenced


def load_rulings(path: pathlib.Path = RULINGS_FILE):
    if not path.exists():
        return {}, []
    data = json.loads(path.read_text())
    idx = {(r["version"], r["file"]): r for r in data.get("rulings", [])}
    return idx, data.get("rulings", [])


def validate_ruling(r: dict) -> list:
    errs = []
    if r.get("class") not in VALID_RULING_CLASSES:
        errs.append(f"bad class {r.get('class')!r} (valid: {sorted(VALID_RULING_CLASSES)})")
    reason = (r.get("reason") or "").strip()
    if len(reason) < 30:
        errs.append("reason too thin (<30 chars) — say WHY this file needs no row")
    if not r.get("hash"):
        errs.append("ruling needs 'hash' (the file's content hash at ruling time, "
                    "for hash-diff self-expiry)")
    return errs


# --- classification (PURE — unit/kill-testable without any I/O) -------------------

def classify_files(entries, referenced: set, ruling_idx: dict, version: str):
    """entries: iterable of (rel_path, hash). Returns (unreferenced[], stale[])
    where unreferenced = files with neither a referencing row nor ANY ruling, and
    stale = (rel_path, ruling_hash, live_hash) for files whose ruling's recorded
    hash no longer matches — a re-pin changed content a ruling was written against
    (P-2 fail-noisy self-expiry: a ruling that goes stale must be visible, not
    silently keep covering changed content)."""
    unreferenced = []
    stale = []
    for rel, h in entries:
        if rel in referenced:
            continue
        ruling = ruling_idx.get((version, rel))
        if ruling is None:
            unreferenced.append(rel)
        elif ruling.get("hash") != h:
            stale.append((rel, ruling.get("hash"), h))
    return unreferenced, stale


# --- fenced-hit audit (I/O + pure scan) --------------------------------------------

def fenced_keyword_hits(prose_dir: pathlib.Path):
    """(rel_file, lineno, keyword, text) for every mandatory keyword found INSIDE a
    fenced code block — the prose census's blind spot by design (fences are
    examples). Mirrors verify_register_completeness.scan_keywords's boilerplate
    exclusion, inverted to require in_fence."""
    if not prose_dir.is_dir():
        return []
    hits = []
    for path in sorted(prose_dir.rglob("*.md")):
        in_fence = False
        for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace")
                                 .splitlines(), start=1):
            stripped = raw.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
            low = raw.lower()
            if "interpreted as described in" in low or "the key words" in low:
                continue
            probe = raw.replace("**", "").replace("`", "")
            for m in KW_RE.finditer(probe):
                hits.append((str(path.relative_to(prose_dir)), i, m.group(1),
                            raw.strip()[:100]))
    return hits


def load_fenced_rulings(path: pathlib.Path = FENCED_RULINGS_FILE):
    if not path.exists():
        return {}, []
    data = json.loads(path.read_text())
    idx = {(r["version"], r["file"], int(r["line"])): r for r in data.get("rulings", [])}
    return idx, data.get("rulings", [])


def validate_fenced_ruling(r: dict) -> list:
    errs = []
    if r.get("class") not in VALID_FENCED_CLASSES:
        errs.append(f"bad class {r.get('class')!r} (valid: {sorted(VALID_FENCED_CLASSES)})")
    reason = (r.get("reason") or "").strip()
    if len(reason) < 20:
        errs.append("reason too thin (<20 chars)")
    return errs


# --- driver -------------------------------------------------------------------

def run_census():
    """Returns (per_version dict, ruling_errs, fenced report dict)."""
    ruling_idx, ruling_list = load_rulings()
    ruling_errs = []
    for r in ruling_list:
        for e in validate_ruling(r):
            ruling_errs.append((r.get("version"), r.get("file"), e))

    per_version = {}
    for version, vdir_name in VERSION_TREE.items():
        vendor_dir = VENDOR / vdir_name
        files = inventory_files(vendor_dir)
        entries = []
        for rel, path in files:
            try:
                doc = json.loads(path.read_text())
                counts = constraint_counts(doc)
            except (OSError, json.JSONDecodeError):
                counts = {}
            entries.append((rel, sha256_of(path), counts))
        referenced = referenced_files(REQ_DIR, version)
        unreferenced, stale = classify_files(
            [(rel, h) for rel, h, _ in entries], referenced, ruling_idx, version)
        per_version[version] = dict(
            file_count=len(entries),
            referenced_count=len(referenced & {rel for rel, _, _ in entries}),
            unreferenced=unreferenced,
            stale_rulings=stale,
            constraint_totals=_sum_counts(c for _, _, c in entries),
        )

    fenced_idx, fenced_list = load_fenced_rulings()
    fenced_errs = []
    for r in fenced_list:
        for e in validate_fenced_ruling(r):
            fenced_errs.append((r.get("version"), r.get("file"), r.get("line"), e))
    fenced = {}
    for version, vdir_name in VERSION_TREE.items():
        prose_dir = VENDOR / vdir_name / "docs" / "specification"
        hits = fenced_keyword_hits(prose_dir)
        unruled = [h for h in hits if (version, h[0], h[1]) not in fenced_idx]
        fenced[version] = dict(total=len(hits), unruled=unruled)

    return per_version, ruling_errs, fenced, fenced_errs


def _sum_counts(counts_iter):
    total = {}
    for c in counts_iter:
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enforce", action="store_true",
                    help="gate on findings (the later, deliberate flip — default is "
                         "report-only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    per_version, ruling_errs, fenced, fenced_errs = run_census()

    total_unreferenced = sum(len(v["unreferenced"]) for v in per_version.values())
    total_stale = sum(len(v["stale_rulings"]) for v in per_version.values())

    if args.json:
        print(json.dumps(dict(
            per_version={v: {k: (val if k != "unreferenced" else val)
                             for k, val in d.items()} for v, d in per_version.items()},
            ruling_errors=len(ruling_errs),
            fenced={v: dict(total=d["total"], unruled=len(d["unruled"]))
                    for v, d in fenced.items()},
            fenced_errors=len(fenced_errs),
            enforce=args.enforce,
        ), indent=2, default=list))
    else:
        print("schema census — every schema/service file must be referenced by a "
              "register row or carried in a recorded ruling\n")
        for version, d in per_version.items():
            print(f"  {version}:  {d['file_count']:4} files   "
                  f"{d['referenced_count']:4} referenced   "
                  f"{len(d['unreferenced']):4} unreferenced   "
                  f"{len(d['stale_rulings']):3} stale-ruling")
        if ruling_errs:
            print(f"\n  {len(ruling_errs)} INVALID ruling(s):")
            for ver, f, e in ruling_errs[:40]:
                print(f"    FAIL  {ver} {f}  {e}")
        for version, d in per_version.items():
            if d["unreferenced"]:
                print(f"\n  {version}: {len(d['unreferenced'])} unreferenced file(s) "
                      f"(showing up to 20):")
                for rel in d["unreferenced"][:20]:
                    print(f"    {rel}")
            if d["stale_rulings"]:
                print(f"\n  {version}: {len(d['stale_rulings'])} STALE ruling(s) "
                      "(vendored hash changed since the ruling):")
                for rel, old, new in d["stale_rulings"]:
                    print(f"    {rel}  ruled-at={old[:12]}  live={new[:12]}")

        fenced_total = sum(d["total"] for d in fenced.values())
        fenced_unruled_total = sum(len(d["unruled"]) for d in fenced.values())
        print(f"\n  fenced-hit audit: {fenced_total} keyword hit(s) inside code "
              f"fences across all pinned versions ({fenced_unruled_total} unruled) "
              "— report-only, never a standing gate")
        for version, d in fenced.items():
            if d["unruled"]:
                print(f"    {version}: {len(d['unruled'])} unruled")
                for (f, line, kw, text) in d["unruled"]:
                    print(f"      {f}:{line}  [{kw}]  {text}")
        if fenced_errs:
            print(f"\n  {len(fenced_errs)} INVALID fenced-hit ruling(s):")
            for ver, f, line, e in fenced_errs[:40]:
                print(f"    FAIL  {ver} {f}:{line}  {e}")

        gating = bool(ruling_errs) or bool(fenced_errs) or (
            args.enforce and (total_unreferenced or total_stale))
        mode = "ENFORCE" if args.enforce else "REPORT-ONLY"
        print(f"\nschema-census [{mode}]: {'FAIL' if gating else 'PASS'}"
              + ("" if not (total_unreferenced or total_stale) else
                 f"  ({total_unreferenced} unreferenced, {total_stale} stale"
                 + ("" if args.enforce else ", not gated — pass --enforce to gate")
                 + ")"))
        return 1 if gating else 0

    gating = bool(ruling_errs) or bool(fenced_errs) or (
        args.enforce and (total_unreferenced or total_stale))
    return 1 if gating else 0


if __name__ == "__main__":
    sys.exit(main())

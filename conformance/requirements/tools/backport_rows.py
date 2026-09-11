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


def norm(s):
    s = (s or "").replace("**", "").replace("`", "").replace("_", "")
    s = s.replace("|", " ").replace("…", "...")
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_source(src):
    repo_path, _, anchor = (src or "").partition("#")
    repo, _, path = repo_path.partition(":")
    return repo, path, [int(n) for n in re.findall(r"L(\d+)", anchor)]


def locate(quote, text):
    """1-based line of the first quote fragment in `text` (line-boundary agnostic:
    the flattened, normalized file is searched and the hit mapped back to its line;
    same discipline as verify_register_completeness.covered_lines_for). None if absent."""
    frags = [f for f in re.split(r"\.\.\.|…", quote or "") if norm(f)]
    if not frags:
        return None
    nf = norm(frags[0])
    lines = text.splitlines()
    parts, spans, pos = [], [], 0
    for idx, raw in enumerate(lines, start=1):
        nl = norm(raw)
        if not nl:
            continue
        parts.append(nl); spans.append((pos, pos + len(nl), idx)); pos += len(nl) + 1; parts.append(" ")
    flat = "".join(parts)
    i = flat.find(nf)
    if i == -1:
        return None
    for (a, b, ln) in spans:
        if a <= i < b:
            return ln
    return None


def backport(rows, src_version, dst_version, path_map, vendor_root, quote_overrides=None):
    """-> (copied_rows, problems). `vendor_root` is the TARGET version's vendored tree
    root (…/.vendor/<dir>). A row whose quote cannot be located in the target file is a
    problem (skipped), never guessed — unless `quote_overrides` ({id: (quote, why)})
    supplies a reviewed replacement quote for the target pin; the copied row then carries
    `lineage.drift_note` (the reverse-direction carry-forward check requires it)."""
    out, problems = [], []
    quote_overrides = quote_overrides or {}
    for r in rows:
        repo, path, _lines = parse_source(r.get("source"))
        tpath = path_map.get(path, path)
        f = pathlib.Path(vendor_root) / tpath
        if not f.exists():
            problems.append(f"{r.get('id')}: target file {tpath} missing under {vendor_root}")
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        quote, why = r.get("quote"), None
        if r.get("id") in quote_overrides:
            quote, why = quote_overrides[r["id"]]
        ln = locate(quote, text)
        if ln is None:
            problems.append(f"{r.get('id')}: quote not found in {tpath} at the target pin")
            continue
        nr = {}
        for k, v in r.items():
            if k in ("lineage", "_completeness_batch", "_relabel_batch"):
                continue
            nr[k] = v
        nr["quote"] = quote
        nr["source"] = f"{repo}:{tpath}#L{ln}"
        nr["versions"] = [dst_version]
        nr["lineage"] = {"from": src_version, "disposition": "backported"}
        if why:
            nr["lineage"]["drift_note"] = f"quote differs from the {src_version} source row at the {dst_version} pin: {why}"
        if r.get("role"):
            nr["role_provenance"] = "backported"
        out.append(nr)
    return out, problems


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--area", required=True, help="register area file stem to copy (e.g. loyalty)")
    ap.add_argument("--map", action="append", default=[], help="src_path=dst_path (repeatable)")
    ap.add_argument("--spec-commit", help="_spec_commit for the new area file (40-hex)")
    ap.add_argument("--quote", action="append", default=[],
                    help="ID=<quote>|<why>: reviewed replacement quote for a row whose source quote is not "
                         "verbatim at the target pin (recorded as lineage.drift_note); repeatable")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    pmap = dict(m.split("=", 1) for m in a.map)
    src_file = pathlib.Path(REQ) / a.src / f"{a.area}.json"
    doc = json.loads(src_file.read_text())
    vendor_root = VENDOR / VERSION_TREE[a.dst]
    qo = {}
    for q in a.quote:
        rid, _, rest = q.partition("=")
        quote, _, why = rest.rpartition("|")
        qo[rid] = (quote, why)
    out, problems = backport(doc.get("rows", []), a.src, a.dst, pmap, vendor_root, qo)
    for pr in problems:
        print(f"  ✗ {pr}")
    print(f"backport {a.area} {a.src} -> {a.dst}: {len(out)} row(s) relocated, {len(problems)} problem(s)")
    if problems:
        return 1
    dst_file = pathlib.Path(REQ) / a.dst / f"{a.area}.json"
    new_doc = {"_area": doc.get("_area", a.area),
               "_spec_commit": a.spec_commit or doc.get("_spec_commit"),
               "rows": out,
               "notes": f"BACKPORTED from {a.src} (D2-15, decision 3): upstream ucp#813 backported the loyalty "
                        f"extension into the v{a.dst} tag; rows copied id-preserving with lineage.backported, "
                        f"sources relocated by quote at the {a.dst} pin. "
                        + (doc.get("notes") or "")}
    if a.dry_run:
        for r in out:
            print(f"   {r['id']:9} {r['source']}")
        return 0
    if dst_file.exists():
        print(f"  ✗ {dst_file} already exists — refusing to overwrite (delete it to regenerate)")
        return 1
    dst_file.write_text(json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {dst_file.relative_to(pathlib.Path(REQ).parent.parent)}")
    return 0


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

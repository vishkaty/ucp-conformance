#!/usr/bin/env python3
"""
assign_normative_basis.py — mechanical fill of the register's `normative_basis` field
(D2-11a, PLAN-v3 §2.4 / D2 design §A6, decision 29). Idempotent: writes the field only
where a mandatory row has none; rows it cannot classify go to
`requirements/<v>/normative_basis_review_queue.json` (the nokw review set — D2-11b
adjudicates it) and the `register` gate accepts a null basis ONLY for a queued row.

Classes (row.normative_basis):
  schema      the quote is a schema constraint (source under `source/`)
  sentence    a MANDATORY keyword (MUST / MUST NOT / SHALL / SHALL NOT / REQUIRED) is in
              the quote itself (emphasis stripped)
  pseudocode  every cited line sits inside a code fence (an algorithm the prose specifies)
  table       a "Yes" / "MUST" cell in a column headed Required / Cond. (review outcome)
  bullet      a list item under a keyword lead-in (review outcome)
  definition  a definitional sentence — downgrade candidate (review outcome; sign-off)
  inferred    a MUST inferred from context — kept only with a written justification
              (review outcome; sign-off — decision 29)
Reviewed outcomes come from requirements/normative_basis_adjudications.json (version-
scoped {version, id, basis, justification}); the tool writes them as
`normative_basis` + `normative_basis_justification` on the row.

  python3 conformance/requirements/tools/assign_normative_basis.py --dry-run [--version V]
  python3 conformance/requirements/tools/assign_normative_basis.py --apply   [--version V]
"""
import argparse, glob, json, os, pathlib, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.dirname(HERE)
CONF = os.path.dirname(REQ)
sys.path.insert(0, CONF)
from common.keywords import MANDATORY, KW_RE          # noqa: E402
from common.spec_versions import VERSION_TREE        # noqa: E402

VENDOR = pathlib.Path(CONF) / ".vendor"
ADJUDICATIONS = os.path.join(REQ, "normative_basis_adjudications.json")
BASES = ("sentence", "table", "bullet", "pseudocode", "schema", "definition", "inferred")
REVIEW_BASES = ("definition", "inferred")            # need a review_signoffs batch naming the row


def parse_source(src):
    repo_path, _, anchor = (src or "").partition("#")
    repo, _, path = repo_path.partition(":")
    return repo, path, [int(n) for n in re.findall(r"L(\d+)", anchor)]


def fenced_lines_of(path):
    """Line numbers inside ``` / ~~~ fences of one vendored file."""
    out, in_fence = set(), False
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        st = raw.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.add(i)
    return out


def classify(row, fenced_lines):
    """Mechanical class or None (queue). `fenced_lines` = {rel_path: {line numbers}}."""
    _, path, lines = parse_source(row.get("source"))
    if path.startswith("source/"):
        return "schema"
    if KW_RE.search((row.get("quote") or "").replace("**", "").replace("`", "")):
        return "sentence"
    fl = fenced_lines.get(path)
    if lines and fl is not None and all(L in fl for L in lines):
        return "pseudocode"
    return None


def load_adjudications(path=ADJUDICATIONS):
    if not os.path.exists(path):
        return {}, None
    d = json.load(open(path))
    return {(e["version"], e["id"]): e for e in d.get("adjudications", [])}, d.get("batch")


def register_files(ver):
    return [f for f in sorted(glob.glob(os.path.join(REQ, ver, "*.json")))
            if not os.path.basename(f).startswith("_")]


def dump_like(path, doc):
    raw = open(path).read()
    orig = json.loads(raw)
    for ind in (1, 2, 4):
        for ea in (True, False):
            if json.dumps(orig, indent=ind, ensure_ascii=ea) + "\n" == raw:
                open(path, "w").write(json.dumps(doc, indent=ind, ensure_ascii=ea) + "\n")
                return
    open(path, "w").write(json.dumps(doc, indent=2) + "\n")


def run_version(ver, apply=False):
    adj, batch = load_adjudications()
    tree = VERSION_TREE.get(ver, "ucp")
    fenced = {}
    stats = {"rows": 0, "already": 0, "filled": 0, "adjudicated": 0, "queued": 0, "by_basis": {}}
    queue = []
    for f in register_files(ver):
        doc = json.load(open(f))
        if "rows" not in doc:
            continue
        changed = False
        for r in doc["rows"]:
            if r.get("keyword") not in MANDATORY or ver not in (r.get("versions") or [ver]):
                continue
            stats["rows"] += 1
            a = adj.get((ver, r["id"]))
            if r.get("normative_basis") and not (a and a["basis"] != r["normative_basis"]):
                stats["already"] += 1
                continue
            if a:
                basis = a["basis"]
                stats["adjudicated"] += 1
                if apply:
                    r["normative_basis"] = basis
                    r["normative_basis_justification"] = f"[{batch}] {a['justification']}"
                    changed = True
            else:
                _, path, _ = parse_source(r.get("source"))
                p = VENDOR / tree / path
                if path not in fenced:
                    fenced[path] = fenced_lines_of(p) if p.exists() else set()
                basis = classify(r, fenced)
                if basis is None:
                    stats["queued"] += 1
                    queue.append({"id": r["id"], "version": ver, "file": os.path.basename(f),
                                  "source": r.get("source"), "requirement": r.get("requirement", "")[:120],
                                  "reason_needed": "no mandatory keyword in the quote, not a schema constraint, "
                                                   "not inside a fence — table / bullet / definition / inferred?"})
                    continue
                stats["filled"] += 1
                if apply:
                    r["normative_basis"] = basis
                    changed = True
            stats["by_basis"][basis] = stats["by_basis"].get(basis, 0) + 1
        if apply and changed:
            dump_like(f, doc)
    if apply:
        qf = os.path.join(REQ, ver, "normative_basis_review_queue.json")
        if queue:
            json.dump(queue_doc(qf, ver, queue), open(qf, "w"), indent=1)
            open(qf, "a").write("\n")
        elif os.path.exists(qf):
            os.remove(qf)
    return stats, queue


DEFAULT_ABOUT = ("normative_basis review queue (D2-11a -> D2-11b): mandatory rows whose quote "
                 "carries no mandatory keyword, is not a schema constraint and is not fenced "
                 "pseudocode. The `register` gate accepts a null normative_basis ONLY for a row "
                 "listed here. To resolve: record the row in "
                 "requirements/normative_basis_adjudications.json (table / bullet / definition / "
                 "inferred with a justification; definition|inferred need a review_signoffs batch) "
                 "and re-run assign_normative_basis.py --apply.")
QUEUE_CLOCK_TIER = "pin-only"          # decision 23 / D2-19: truth depends only on the spec pin
QUEUE_HORIZON_DAYS = 90


def _lock_pin(ver):
    try:
        d = json.load(open(os.path.join(REQ, "..", "SOURCES.lock.json")))
        return (d["spec"]["versions"][ver]["commit"] or "")[:8] or None
    except Exception:                                        # noqa: BLE001
        return None


def queue_doc(path, ver, queue):
    """The queue file's document. B6 (W1 review V4): the queue is an EXEMPTION register, so it
    carries the standard expiry clock; regenerating it must CARRY THE CLOCK FORWARD rather than
    silently de-clock 216 rows. An existing file's `_about` and clock hands are preserved
    verbatim; a brand-new queue is stamped with the version's lock pin and a full pin-only
    horizon so the gate has something live to police (re-review it deliberately, don't let a
    regeneration extend it)."""
    import datetime
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path)) or {}
        except Exception:                                    # noqa: BLE001
            old = {}
    doc = {"_about": old.get("_about") or DEFAULT_ABOUT,
           "version": old.get("version") or ver,
           "spec_pin": old.get("spec_pin") or _lock_pin(ver),
           "review_by": old.get("review_by") or
           (datetime.date.today() + datetime.timedelta(days=QUEUE_HORIZON_DAYS)).isoformat(),
           "clock_tier": old.get("clock_tier") or QUEUE_CLOCK_TIER,
           "converts_when": old.get("converts_when") or "D2-11b",
           "queue": queue}
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--version")
    a = ap.parse_args(argv)
    versions = [a.version] if a.version else sorted(
        d for d in os.listdir(REQ) if os.path.isdir(os.path.join(REQ, d)) and d[:2] == "20")
    for ver in versions:
        st, _ = run_version(ver, apply=a.apply)
        print(f"{ver}: mandatory {st['rows']} · already {st['already']} · filled {st['filled']} · "
              f"adjudicated {st['adjudicated']} · queued {st['queued']} · by_basis {st['by_basis']}")
    if not (a.dry_run or a.apply):
        print("(dry run by default; pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

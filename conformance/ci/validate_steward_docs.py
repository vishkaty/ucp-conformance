#!/usr/bin/env python3
"""
validate_steward_docs.py — the steward docs exist and are current (G2, D5-14; run_suite gate
`docs-steward`):

  · the five docs are present: ARCHITECTURE.md, PRINCIPLES.md, DECISIONS.md, WORKSTREAMS.md,
    docs/archive/ROADMAP.md
  · each carries a `last-reviewed: YYYY-MM-DD` stamp that is ≤ 90 days old and not in the future
  · DECISIONS.md tracks every PLAN-v3 decision 1–34 and 4b: one table row per id, marked closed
    with a date and a who; it also carries the `enforce_admins` on/off commands (decision 12) and
    the attribution rule dated 2026-09-10 (decision 16)

    validate_steward_docs.py            # `steward docs: 5/5 present · 35/35 decisions tracked · reviewed ≤90d · PASS`
    validate_steward_docs.py --selftest # hermetic kill-tests on a scratch copy (each plant must red)
SPCK_STEWARD_ROOT points the validator at a scratch tree. Exit 0 pass · 1 fail. Stdlib only.
"""
import datetime
import os
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ("ARCHITECTURE.md", "PRINCIPLES.md", "DECISIONS.md", "WORKSTREAMS.md", "docs/archive/ROADMAP.md")
DECISION_IDS = [str(i) for i in range(1, 35)] + ["4b"]
MAX_AGE_DAYS = 90
STAMP = re.compile(r"last-reviewed:\s*(\d{4}-\d{2}-\d{2})")


def failures(root, today):
    root = pathlib.Path(root)
    fails, present = [], 0
    for rel in DOCS:
        f = root / rel
        if not f.exists():
            fails.append(f"{rel}: missing"); continue
        present += 1
        txt = f.read_text(encoding="utf-8", errors="replace")
        m = STAMP.search(txt)
        if not m:
            fails.append(f"{rel}: no `last-reviewed: YYYY-MM-DD` stamp"); continue
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            fails.append(f"{rel}: last-reviewed {m.group(1)!r} is not a date"); continue
        age = (today - d).days
        if age > MAX_AGE_DAYS:
            fails.append(f"{rel}: last-reviewed {m.group(1)} is {age} days old (max {MAX_AGE_DAYS}) — re-review it")
        elif age < 0:
            fails.append(f"{rel}: last-reviewed {m.group(1)} is in the future")
    tracked = 0
    dec = root / "DECISIONS.md"
    if dec.exists():
        txt = dec.read_text(encoding="utf-8", errors="replace")
        rows = {}
        for line in txt.splitlines():
            m = re.match(r"^\|\s*(\d+|4b)\s*\|(.*)\|\s*$", line)
            if m:
                rows[m.group(1)] = m.group(2)
        for did in DECISION_IDS:
            row = rows.get(did)
            if row is None:
                fails.append(f"DECISIONS.md: decision {did} has no row"); continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", row)]   # `\|` inside a cell is not a separator
            if len(cells) < 5:
                fails.append(f"DECISIONS.md: decision {did} row has {len(cells) + 1} cells (want topic · answer · date · who · carried by)"); continue
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[2]):
                fails.append(f"DECISIONS.md: decision {did} has no close date (got {cells[2]!r})")
            if not cells[3]:
                fails.append(f"DECISIONS.md: decision {did} has no who")
            if not cells[1]:
                fails.append(f"DECISIONS.md: decision {did} has no closed answer")
            tracked += 1 if len(cells) >= 5 and cells[1] and cells[3] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[2]) else 0
        if "protection/enforce_admins" not in txt or "-X POST" not in txt or "-X DELETE" not in txt:
            fails.append("DECISIONS.md: the enforce_admins on (POST) / off (DELETE) commands are missing (decision 12)")
        if not re.search(r"2026-09-10", txt) or "Co-Authored-By" not in txt:
            fails.append("DECISIONS.md: the attribution rule dated 2026-09-10 (no Co-Authored-By / bot identity) is missing (decision 16)")
    return fails, present, tracked


def main():
    root = pathlib.Path(os.environ.get("SPCK_STEWARD_ROOT", ROOT))
    today = datetime.date.today()
    fails, present, tracked = failures(root, today)
    for f in fails:
        print(f"  x {f}")
    stale = any("days old" in f or "future" in f or "stamp" in f for f in fails)
    print(f"steward docs: {present}/{len(DOCS)} present · {tracked}/{len(DECISION_IDS)} decisions tracked · "
          f"{'reviewed ≤90d' if not stale else 'STALE review stamp'} · {'PASS' if not fails else f'FAIL ({len(fails)} finding(s))'}")
    return 0 if not fails else 1


def selftest():
    today = datetime.date.today()
    bad = 0

    def scratch():
        d = pathlib.Path(tempfile.mkdtemp(prefix="steward_"))
        for rel in DOCS:
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, d / rel)
        return d

    def case(name, mutate, want_red, must_name=None):
        nonlocal bad
        d = scratch()
        try:
            mutate(d)
            fails, _, _ = failures(d, today)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        red = bool(fails)
        ok = red == want_red and (not must_name or any(must_name in f for f in fails))
        print(f"  {'✓' if ok else '✗'} {name}: {'RED' if red else 'GREEN'}"
              + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}" + (f" naming {must_name!r}" if must_name else ""))
              + (("\n      " + "\n      ".join(fails[:4])) if fails and not ok else ""))
        bad += 0 if ok else 1

    def drop_4b(d):
        p = d / "DECISIONS.md"
        p.write_text("\n".join(l for l in p.read_text().splitlines() if not re.match(r"^\|\s*4b\s*\|", l)) + "\n")

    def backdate(d):
        p = d / "PRINCIPLES.md"
        old = (today - datetime.timedelta(days=100)).isoformat()
        p.write_text(STAMP.sub(f"last-reviewed: {old}", p.read_text(), count=1))

    def strip_admins(d):
        p = d / "DECISIONS.md"
        p.write_text(p.read_text().replace("protection/enforce_admins", "protection/other"))

    case("unmodified docs", lambda d: None, want_red=False)
    case("DECISIONS.md missing decision 4b", drop_4b, True, must_name="decision 4b")
    case("ARCHITECTURE.md deleted", lambda d: (d / "ARCHITECTURE.md").unlink(), True, must_name="ARCHITECTURE.md: missing")
    case("last-reviewed backdated 100 days", backdate, True, must_name="100 days old")
    case("enforce_admins commands removed", strip_admins, True, must_name="enforce_admins")
    print(f"\nsteward docs selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else main())

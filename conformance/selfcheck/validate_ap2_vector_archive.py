#!/usr/bin/env python3
"""
validate_ap2_vector_archive.py — the golden-vector ARCHIVE RATCHET gate.

docs/ap2-vectors.md promises: "When the draft or the reference moves, a new
keyed set is added alongside this one — existing sets are never mutated."
This gate makes that promise mechanical instead of aspirational:

  INTEGRITY   every file locked in ARCHIVE.lock.json exists on disk with the
              exact recorded sha256 — a shipped vector that is EDITED or
              DELETED reds the gate. (Editing the lock to match is a visible
              hash change on a shipped set in review.)
  COMPLETE    every vector file under fixtures/ap2/golden/ is locked in some
              set — an addition that skipped the archive record reds the gate.
  PIN-SYNC    the CURRENT testbed pin (provenance.DRAFT + REFERENCE_SHA) has a
              set whose draft+reference_sha match — re-pinning the reference or
              draft WITHOUT minting the new keyed vector set reds the gate:
              that is the ratchet trigger that forces "add a set", never
              "regenerate over the old one".
  REF-SHA     each golden's embedded ref_sha equals its set's reference_sha —
              a vector can't claim one provenance in-band and another in the
              archive.

Hermetic: no network, no reference SDK. --selftest kill-proves every rule on a
temp copy (mutated byte, deleted file, unrecorded addition, drifted pin,
in-band ref_sha mismatch — each must red).

Exit 0 = archive consistent; 1 = a ratchet violation.
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "testbed"))
import provenance  # noqa: E402

GOLD = HERE / "fixtures" / "ap2" / "golden"
# The lock lives BESIDE golden/ (not inside): the sibling gates sweep
# golden/*.json as wire vectors and the lock is a record, not a vector.
LOCK_NAME = "ARCHIVE.lock.json"
LOCK = GOLD.parent / LOCK_NAME


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def archive_problems(gold_dir, lock_path, draft, reference_sha):
    """Pure rule evaluation -> list of problem strings (selftest-able)."""
    problems = []
    try:
        lock = json.loads(lock_path.read_text())
        sets = lock["sets"]
        assert isinstance(sets, dict) and sets
    except Exception as exc:
        return [f"lock unreadable ({exc}) — the archive has no record"]

    locked = {}
    for set_key, s in sets.items():
        files = s.get("files")
        if not isinstance(files, dict) or not files:
            problems.append(f"set {set_key}: empty/missing files map")
            continue
        for rel, digest in files.items():
            locked[rel] = (set_key, digest, s.get("reference_sha"))

    # INTEGRITY + REF-SHA
    for rel, (set_key, digest, set_ref) in sorted(locked.items()):
        p = gold_dir / rel
        if not p.is_file():
            problems.append(f"{set_key}/{rel}: shipped vector DELETED "
                            "(the archive is additions-only)")
            continue
        b = p.read_bytes()
        if hashlib.sha256(b).hexdigest() != digest:
            problems.append(f"{set_key}/{rel}: shipped vector MUTATED "
                            "(bytes differ from the locked sha256 — add a new "
                            "keyed set instead of editing a shipped vector)")
            continue
        try:
            inband = json.loads(b).get("ref_sha")
        except Exception:
            inband = None
        if inband is not None and set_ref is not None and inband != set_ref:
            problems.append(f"{set_key}/{rel}: in-band ref_sha {inband[:10]} != "
                            f"the set's reference_sha {str(set_ref)[:10]}")

    # COMPLETE
    for p in sorted(gold_dir.rglob("*.json")):
        if p.name == LOCK_NAME:
            continue
        rel = str(p.relative_to(gold_dir))
        if rel not in locked:
            problems.append(f"{rel}: vector on disk but locked in NO set "
                            "(record every addition in the archive)")

    # PIN-SYNC
    if not any(s.get("draft") == draft and s.get("reference_sha") == reference_sha
               for s in sets.values()):
        problems.append(
            f"no set is keyed to the CURRENT pin ({draft} @ "
            f"{reference_sha[:10]}) — a re-pin must MINT its new vector set "
            "(gen_goldens.py) and lock it alongside the old ones")
    return problems


def _selftest():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td) / "golden"
        shutil.copytree(GOLD, tmp)
        lock_p = pathlib.Path(td) / LOCK_NAME
        shutil.copy(LOCK, lock_p)
        draft, ref = provenance.DRAFT, provenance.REFERENCE_SHA

        if archive_problems(tmp, lock_p, draft, ref):
            fails.append("a faithful copy of the shipped archive must be clean")

        # mutated shipped vector
        f = tmp / "checkout_chain.json"
        orig = f.read_bytes()
        f.write_bytes(orig.replace(b'"wire"', b'"wIre"', 1))
        if not any("MUTATED" in p
                   for p in archive_problems(tmp, lock_p, draft, ref)):
            fails.append("a mutated shipped vector must red the gate")
        f.write_bytes(orig)

        # deleted shipped vector
        g = tmp / "payment_chain.json"
        keep = g.read_bytes()
        g.unlink()
        if not any("DELETED" in p
                   for p in archive_problems(tmp, lock_p, draft, ref)):
            fails.append("a deleted shipped vector must red the gate")
        g.write_bytes(keep)

        # unrecorded addition
        extra = tmp / "new_chain.json"
        extra.write_text('{"wire": "x~"}')
        if not any("locked in NO set" in p
                   for p in archive_problems(tmp, lock_p, draft, ref)):
            fails.append("an unrecorded vector addition must red the gate")
        extra.unlink()

        # pin drift without a new set
        if not any("re-pin" in p for p in
                   archive_problems(tmp, lock_p, draft, "f" * 40)):
            fails.append("a reference re-pin without a new keyed set must red "
                         "the gate (the ratchet trigger)")

        # in-band provenance mismatch
        lock = json.loads(lock_p.read_text())
        lock["sets"]["2026-07"]["reference_sha"] = "a" * 40
        lock_p.write_text(json.dumps(lock))
        if not any("in-band ref_sha" in p or "re-pin" in p
                   for p in archive_problems(tmp, lock_p, draft, ref)):
            fails.append("an in-band/archive provenance mismatch must red")

    if fails:
        print("ap2-vector-archive selftest: FAIL")
        for f_ in fails:
            print("  ✗ " + f_)
        return 1
    print("ap2-vector-archive selftest: PASS — mutation, deletion, unrecorded "
          "addition, silent re-pin and provenance mismatch each red the gate.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="AP2 golden-vector archive ratchet.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    ok = True
    problems = archive_problems(GOLD, LOCK, provenance.DRAFT,
                                provenance.REFERENCE_SHA)
    for p in problems:
        ok &= check(p, False)
    if not problems:
        lock = json.loads(LOCK.read_text())
        n_sets = len(lock["sets"])
        n_files = sum(len(s["files"]) for s in lock["sets"].values())
        print(f"  ✓ archive consistent: {n_sets} keyed set(s), {n_files} "
              f"shipped vector(s), current pin {provenance.DRAFT} @ "
              f"{provenance.REFERENCE_SHA[:10]} has its set")
    print("\nap2-vector-archive: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

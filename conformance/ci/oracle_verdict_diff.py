#!/usr/bin/env python3
"""
oracle_verdict_diff.py — BOTH ucp-schema builds x BOTH schema layouts (D4-04 / B6, decision 7b).

The per-version oracle split (conformance/ci/oracle_manifest.json: the pinned 9b5c3206 build
for the 2026-04-08 layout, the merged-main b52518f5 build for 2026-08-25) is justified only
while it is MEASURED: this tool runs every build over every corpus and records the verdict
matrix {build} x {layout} x {item} -> valid | invalid | crash | rc2.

  own cells   the build the manifest assigns to a layout must show 0 crash and 0 rc2 on that
              layout's corpus (a crash is not a verdict — ucp-schema#45 class; rc 2 is a
              resolution error — the #71 `--schema-local-base` path-doubling class).
  cross cells printed as the recorded reason for the split (pin-on-0825 crash N,
              main-on-0408 rc2 K).
  STALE split when both builds agree item-for-item on both layouts the split is stale: the
              gate FAILS with "collapse the manifest" (self-expiry — a justified split can
              never quietly outlive its reason).

Corpora: the dual-oracle gate's own (validate_dual_oracle.agreement_corpus + divergence_corpus
at each version: the schema_check_04_08* fixtures at 04-08, the captured golden-0825 responses
+ the #43/#45 boundary at 08-25). No new payloads: the cells describe exactly what the suite
validates.

Modes (decision 24: the tracked file is rewritten only by an explicit --record):
    --record   run live and write conformance/ci/oracle_verdict_diff.json ({ran_at, cells, ...})
    --check    run live, assert the own cells + stale-split, and require the tracked file to be
               <= 14 days old and cell-identical (drift => re-record deliberately)   [gate]
    (default)  run live and print the cells (rc 1 on an own-cell crash/rc2 or a stale split)
Exit: 0 PASS · 1 finding · 2 a build/corpus is unavailable (never a false green).
"""
import argparse, datetime, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
sys.path.insert(0, str(ROOT / "conformance" / "checks"))

TRACKED = HERE / "oracle_verdict_diff.json"
STALE_DAYS = 14


def _today():
    """Today in UTC, because `ran_at` is written in UTC.

    Comparing a UTC timestamp against `datetime.date.today()` (local) makes the age read a
    day short on any machine behind UTC, so this gate could pass locally and fail in CI for
    the identical tree. That happened on 2026-10-16: rc 0 at 21:08 EDT, rc 1 at 01:07 UTC
    with "STALE: 15 days old (> 14)", and the failure was unreproducible on the owner's
    machine. CI runs in UTC, and "run every applicable CI job locally" has to mean the same
    thing in both places. Pinned by --selftest
    cases (a), (a') and (a''), the last of which re-invokes in UTC+14 and UTC-11 so a
    local-date regression dies at any hour, not only while the two dates happen to differ.

    SCOPE, surveyed 2026-10-16 and CORRECTED after an independent review refuted my first
    claim that this was the only instance. conformance/ci/discovery_shapes.py was a second
    one, fixed in the same change: discovery_reach ages `fetched_at`, which discovery_live
    writes in UTC, against check_dir's locally-defaulted today. Other gates compare a local
    date against a hand-written date LITERAL (official_crosscheck or_date,
    seqfuzz_gate golden_pending.until, site_gates, seq_invariants, discovery_shapes,
    preview_parity, and roughly fifteen more by the reviewer's census, so "six" was an
    undercount): not a wrong comparison, but local and CI still disagree about "today"
    between local midnight and UTC midnight, so a window boundary can flip in CI and not
    locally. discovery_live compares local dates against locally-named capture directories
    and is internally consistent. Converting the literal class to UTC is worth doing as one
    deliberate change with its own tests, not as a late addition here.
    """
    return datetime.datetime.now(datetime.timezone.utc).date()


LAYOUTS = ("2026-04-08", "2026-08-25")
KINDS = ("valid", "invalid", "crash", "rc2")


def classify_rc(rc):
    """Rust exit code -> verdict kind: 0 valid, 1 invalid, 2 rc2 (resolution/usage error),
    anything else (134 / -6 abort, ...) crash."""
    if rc == 0:
        return "valid"
    if rc == 1:
        return "invalid"
    if rc == 2:
        return "rc2"
    return "crash"


def summarize(item_verdicts):
    """{kind: count} over {label: kind}."""
    out = {k: 0 for k in KINDS}
    for v in item_verdicts.values():
        out[v] += 1
    return out


def builds(manifest):
    """{build_name: (bin_path, entry)} — one per distinct binary in the manifest; the name is
    `<layout>-bin` of the entry that declares it."""
    import schema_oracle as so
    out = {}
    for v, e in manifest["per_version"].items():
        if "alias_of" in e:
            continue
        name = f"{e['layout']}-bin"
        out[name] = (so.VENDOR / e["bin"], e)
    return out


def corpus(version):
    import validate_dual_oracle as vdo
    vdo.set_version(version)
    try:
        return vdo.agreement_corpus() + vdo.divergence_corpus()
    finally:
        vdo.set_version(vdo.DEFAULT_VERSION)


def verdicts(bin_path, layout, items):
    """{label: kind} for one build over one layout's items — dispatched exactly as the checks
    dispatch (root / --def / nested), with the binary forced to `bin_path`."""
    import schema_oracle as so
    import validate_dual_oracle as vdo
    orig = so.bin_for
    so.bin_for = lambda version=None, manifest=None: bin_path
    vdo.set_version(layout)
    out = {}
    try:
        for it in items:
            try:
                vdo.rust_verdict(it.payload, it.schema_rel, it.def_name, it.op, it.direction)
            except vdo.GateUnavailable as ex:
                raise
            out[it.label] = classify_rc(so.LAST_RC)
    finally:
        so.bin_for = orig
        vdo.set_version(vdo.DEFAULT_VERSION)
    return out


def matrix(manifest):
    """{build: {layout: {"cells": {kind: n}, "items": {label: kind}}}}; raises
    validate_dual_oracle.GateUnavailable when a build or corpus is absent."""
    import validate_dual_oracle as vdo
    bl = builds(manifest)
    for name, (b, _e) in bl.items():
        if not b.exists():
            raise vdo.GateUnavailable(f"{name} not built at {b}")
    corpora = {lay: corpus(lay) for lay in LAYOUTS}
    out = {}
    for name, (b, _e) in bl.items():
        out[name] = {}
        for lay in LAYOUTS:
            iv = verdicts(b, lay, corpora[lay])
            out[name][lay] = {"cells": summarize(iv), "items": iv}
    return out


def assess(manifest, mat):
    """(findings, cross_line): own-cell assertions + stale-split, and the printed cross cells."""
    f = []
    own = {}
    for v, e in manifest["per_version"].items():
        if "alias_of" in e or v not in LAYOUTS:
            continue
        name = f"{e['layout']}-bin"
        cells = mat[name][v]["cells"]
        own[v] = (name, cells)
        if cells["crash"]:
            f.append(f"{name} on {v} corpus: {cells['crash']} crash — the assigned build aborts on its own layout")
        if cells["rc2"]:
            f.append(f"{name} on {v} corpus: {cells['rc2']} rc2 — the assigned build cannot resolve its own layout")
    # cross cells: every build on the layout it is NOT assigned to
    cross = []
    for name in mat:
        for lay in LAYOUTS:
            if own.get(lay, (None,))[0] == name:
                continue
            c = mat[name][lay]["cells"]
            cross.append(f"{name} on {lay}: crash {c['crash']}, rc2 {c['rc2']}")
    # stale split: item-for-item agreement of all builds on all layouts
    names = sorted(mat)
    agree = all(mat[a][lay]["items"] == mat[b][lay]["items"]
                for lay in LAYOUTS for a in names for b in names)
    if agree and len(names) > 1:
        f.append("split STALE: every build agrees item-for-item on every layout — collapse "
                 "oracle_manifest.json to one entry (decision 7b self-expiry)")
    o0825 = own.get("2026-08-25", ("0825-bin", {"crash": "?"}))[1]
    o0408 = own.get("2026-04-08", ("0408-bin", {"rc2": "?"}))[1]
    line = (f"0825-bin on 0825 corpus: {o0825['crash']} crash · 0408-bin on 0408 corpus: "
            f"{o0408['rc2']} rc2 · cross: " + ", ".join(cross)
            + (" (split justified)" if not agree else " (split STALE)"))
    return f, line


def cells_only(mat):
    return {b: {lay: mat[b][lay]["cells"] for lay in mat[b]} for b in mat}


def check_tracked(mat, tracked=TRACKED, today=None):
    f = []
    if not pathlib.Path(tracked).exists():
        return [f"tracked {tracked.name} missing — run oracle_verdict_diff.py --record"]
    d = json.loads(pathlib.Path(tracked).read_text())
    today = today or _today()
    try:
        ran = datetime.date.fromisoformat(d["ran_at"][:10])
    except Exception:  # noqa: BLE001
        return [f"tracked {tracked.name}: ran_at unreadable"]
    age = (today - ran).days
    if age > STALE_DAYS:
        f.append(f"tracked {tracked.name} is STALE: {age} days old (> {STALE_DAYS}) — re-run --record")
    if d.get("cells") != cells_only(mat):
        f.append(f"tracked {tracked.name} cells differ from this run — a build or corpus moved; "
                 f"re-record deliberately (git diff shows the moved cell)")
    return f


def selftest():
    """Hermetic kill-tests for the staleness clock. No oracle build, no network.

    Exists because this gate had no test, which is why a UTC-versus-local defect lived in
    it: `ran_at` is written in UTC and was compared against the LOCAL date, so on a machine
    behind UTC the age read a day short. On 2026-10-16 that made the gate pass locally
    (rc 0, 21:08 EDT) and fail in CI (rc 1, 01:07 UTC, "STALE: 15 days old"), so the defect
    was unreproducible on the owner's machine.
    """
    bad = 0

    def case(label, cond, detail=""):
        nonlocal bad
        print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  <-- {detail}"))
        bad += 0 if cond else 1

    # (a) the default `today` must be the UTC date, not the local one. On any machine
    # behind UTC these differ for part of each day, which is exactly the window that hid
    # the defect.
    want = datetime.datetime.now(datetime.timezone.utc).date()
    got = _today()
    case("(a) the default today is the UTC date", got == want, f"got {got}, UTC is {want}")
    case("(a') and it is NOT silently the local date when they differ",
         not (datetime.date.today() != want and got == datetime.date.today()),
         f"local {datetime.date.today()} leaked in while UTC is {want}")

    # (a'') TZ invariance. Cases (a) and (a') only discriminate while the local and UTC
    # dates differ, which is a few hours a day, so a local-date mutant would survive them
    # for most of the day. Re-invoking in two extreme zones pins it at any hour: UTC+14 and
    # UTC-11 are on different calendar dates almost always, so date.today() differs between
    # them while a UTC-based _today() cannot.
    import os, subprocess
    code = ("import sys; sys.path.insert(0, %r); "
            "import oracle_verdict_diff as m; print(m._today().isoformat())" % str(HERE))
    outs = {}
    for tz in ("Pacific/Kiritimati", "Pacific/Niue"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "TZ": tz})
        outs[tz] = r.stdout.strip()
    case("(a'') _today() is the same date in UTC+14 and UTC-11",
         outs["Pacific/Kiritimati"] == outs["Pacific/Niue"] and outs["Pacific/Niue"] != "",
         outs)

    # (b) the staleness boundary, injected so it does not depend on the clock
    ran = "2026-10-01T00:33:48+00:00"
    doc = {"ran_at": ran, "cells": {}}
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp()) / "oracle_verdict_diff.json"
    tmp.write_text(json.dumps(doc))

    def mk(d):
        return check_tracked({}, tmp, today=d)
    at14 = mk(datetime.date(2026, 10, 15))
    at15 = mk(datetime.date(2026, 10, 16))
    case("(b) 14 days old is not stale", not any("STALE" in x for x in at14), at14)
    case("(b') 15 days old IS stale", any("STALE" in x for x in at15), at15)
    case("(b'') and the finding names the age and the window",
         any("15 days old (> 14)" in x for x in at15), at15)

    # (c) THE WIRING. Cases (a*) test _today() alone and (b*) inject `today`, so reverting
    # the one production line (`today or _today()` back to `today or date.today()`) left
    # this selftest green: the gate could not catch the regression it was written for. An
    # independent review found that as mutant M5. This case omits `today` so the default
    # path runs, in two zones whose local date disagrees with UTC at every hour of the day
    # (Niue trails UTC 00:00-10:59, Kiritimati leads 10:00-23:59), so at least one zone
    # always exposes a local-date default.
    drv = (
        "import sys, json, datetime, pathlib, tempfile; sys.path.insert(0, %r); "
        "import oracle_verdict_diff as m; "
        "u = datetime.datetime.now(datetime.timezone.utc).date(); "
        "out = []; "
        "t = pathlib.Path(tempfile.mkdtemp()); "
        "f15 = t / 'a.json'; f15.write_text(json.dumps({'ran_at': (u - datetime.timedelta(days=15)).isoformat() + 'T00:00:00+00:00', 'cells': {}})); "
        "f14 = t / 'b.json'; f14.write_text(json.dumps({'ran_at': (u - datetime.timedelta(days=14)).isoformat() + 'T00:00:00+00:00', 'cells': {}})); "
        "out.append(any('STALE' in x for x in m.check_tracked({}, f15))); "
        "out.append(any('STALE' in x for x in m.check_tracked({}, f14))); "
        "out.append(datetime.date.today().isoformat()); "
        "print(json.dumps(out))"
    ) % str(HERE)
    wired = {}
    for tz in ("Pacific/Kiritimati", "Pacific/Niue"):
        r = subprocess.run([sys.executable, "-c", drv], capture_output=True, text=True,
                           env={**os.environ, "TZ": tz})
        try:
            wired[tz] = json.loads(r.stdout.strip())
        except Exception:                      # noqa: BLE001 — a crash is a failing case
            wired[tz] = ["crash", r.stderr[-200:], ""]
    case("(c) 15 days old is STALE in both zones with `today` defaulted",
         all(w[0] is True for w in wired.values()), wired)
    case("(c') 14 days old is NOT stale in either zone with `today` defaulted",
         all(w[1] is False for w in wired.values()), wired)
    # positive control: if the two zones report the SAME local date, TZ was not honoured or
    # tzdata is absent, and (a'')/(c) would pass vacuously. Fail loudly instead.
    case("(c'') the two zones really do see different local dates (tzdata honoured)",
         wired["Pacific/Kiritimati"][2] != wired["Pacific/Niue"][2] != "",
         {k: v[2] for k, v in wired.items()})

    print(f"\noracle-verdict-diff selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0



def main():
    ap = argparse.ArgumentParser(description="both oracle builds x both layouts (D4-04)")
    ap.add_argument("--record", action="store_true", help="write the tracked JSON (owner-committed)")
    ap.add_argument("--check", action="store_true", help="gate: live cells + tracked file freshness/identity")
    ap.add_argument("--selftest", action="store_true", help="hermetic kill-tests for the staleness clock")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    import schema_oracle as so
    import validate_dual_oracle as vdo
    manifest = so.load_manifest()
    try:
        mat = matrix(manifest)
    except (vdo.GateUnavailable, so.OracleUnavailable) as ex:
        print(f"oracle-verdict-diff: SKIP — {ex}")
        return 2
    findings, line = assess(manifest, mat)
    for b in sorted(mat):
        for lay in LAYOUTS:
            c = mat[b][lay]["cells"]
            print(f"  {b:9} on {lay}: " + " · ".join(f"{k} {c[k]}" for k in KINDS))
    if a.record:
        doc = {"_about": "verdict matrix of every ucp-schema build in oracle_manifest.json over every "
                         "layout's dual-oracle corpus (oracle_verdict_diff.py --record; decision 24: "
                         "rewritten only by an explicit --record, the owner commits it)",
               "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "builds": {b: {"commit": e["commit"], "bin_version": e["bin_version"]}
                          for b, (_p, e) in builds(manifest).items()},
               "cells": cells_only(mat),
               "items": {b: {lay: mat[b][lay]["items"] for lay in LAYOUTS} for b in mat},
               "summary": line}
        TRACKED.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
        print(f"recorded -> {TRACKED.relative_to(ROOT)}")
    if a.check:
        findings += check_tracked(mat)
    print(line)
    for x in findings:
        print(f"  ✗ {x}")
    print("oracle-verdict-diff: " + ("PASS" if not findings else "FAIL"))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())

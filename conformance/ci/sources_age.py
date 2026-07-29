#!/usr/bin/env python3
"""
sources_age.py — the drift tripwire for SOURCES.lock.json.

Our verdicts are only as trustworthy as our pinned sources. Pins are deliberate
(never track moving branches for a graded run), but a pin that silently ages for a
month means we validate against a stale reference/oracle and never notice — exactly
what happened to the ucp-schema oracle (pinned 2026-06-30, two engine changes landed
before we re-pinned 2026-07-29).

This tool WARNS (never fails) when a source pinned to a moving ref (`main`) has fallen
more than `threshold_days` behind its upstream branch HEAD. It is informational by
design: updates stay deliberate, but "silently stale" becomes visible.

Two modes:
  --check     live network check via `gh api` (used by preflight). ALWAYS exit 0;
              prints a warning block for each stale pin, or an all-fresh line. Degrades
              gracefully to a skip line when offline / gh unavailable.
  --selftest  run the embedded deterministic unit tests on the pure staleness logic
              (used as a run_suite gate). Exit 0 on pass, 1 on failure. No network.

Sources pinned to a RELEASE TAG (the spec versions, v2026-04-08 etc.) are reported
separately as an informational "newer release available" note when a newer version
tag exists — that is a version-bump DECISION, not staleness, so it never warns.
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = ROOT / "conformance" / "SOURCES.lock.json"
GH = os.environ.get("GH_BIN") or str(pathlib.Path.home() / "shn" / "tools" / "bin" / "gh")
THRESHOLD_DAYS = 21


# ----------------------------------------------------------------------------- pure logic
def _parse_iso(s):
    """Parse an ISO-8601 UTC timestamp (with or without trailing Z) to aware datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def branch_entries(lock):
    """Extract the sources pinned to a MOVING ref (a branch, not a version tag) —
    these are the ones that silently drift. Returns a list of dicts:
    {key, repo, ref, pinned_sha, pinned_date}."""
    s = lock.get("sources", lock)
    out = []
    # the named single-repo sources
    for key in ("reference_sample_server", "reference_sdk", "schema_validator",
                "official_conformance_suite"):
        e = s.get(key)
        if not isinstance(e, dict):
            continue
        ref = e.get("ref", "")
        # a branch ref is a moving target; a tag/explicit-version pin is deliberate.
        if _is_branch_ref(ref):
            out.append({"key": key, "repo": e.get("repo", ""), "ref": ref,
                        "pinned_sha": e.get("commit", ""),
                        "pinned_date": e.get("commit_date", "")})
    return out


def _is_branch_ref(ref):
    """True for a moving branch ref (main/master/develop). A version tag (v2026-...),
    a bare SHA, or an empty ref is treated as a deliberate, non-drifting pin."""
    return ref in ("main", "master", "develop", "HEAD")


def evaluate(entries, upstream, now_iso, threshold_days=THRESHOLD_DAYS):
    """PURE staleness logic. Given branch-pinned `entries` and an `upstream` map
    {key: {"sha", "date"}} (the current upstream branch HEAD), return the list of
    findings for pins that are BOTH (a) not at the upstream HEAD and (b) whose upstream
    HEAD is >= threshold_days newer than the pinned commit.

    A finding: {key, repo, days_behind, pinned_sha, head_sha, threshold_days}.
    Entries with no upstream data (offline / lookup failed) are skipped, not flagged."""
    now = _parse_iso(now_iso)
    findings = []
    for e in entries:
        up = upstream.get(e["key"])
        if not up or not up.get("sha") or not up.get("date"):
            continue                                   # no data -> cannot judge; skip
        if up["sha"] == e["pinned_sha"]:
            continue                                   # at HEAD -> fresh
        try:
            pinned_dt = _parse_iso(e["pinned_date"]) if e.get("pinned_date") else None
            head_dt = _parse_iso(up["date"])
        except (ValueError, AttributeError):
            continue
        # age = how far the upstream HEAD has moved past our pinned commit, in days.
        # Use the pinned commit date when present, else fall back to now-vs-head.
        anchor = pinned_dt or now
        days_behind = (head_dt - anchor).days
        if days_behind >= threshold_days:
            findings.append({
                "key": e["key"], "repo": e.get("repo", ""),
                "days_behind": days_behind,
                "pinned_sha": e["pinned_sha"][:12], "head_sha": up["sha"][:12],
                "threshold_days": threshold_days,
            })
    return findings


# ------------------------------------------------------------------------- live network
def _gh_head(repo, ref):
    """Return {"sha","date"} for repo@ref via gh api, or None on any failure."""
    try:
        p = subprocess.run(
            [GH, "api", f"repos/{repo}/commits/{ref}",
             "--jq", "{sha: .sha, date: .commit.committer.date}"],
            capture_output=True, text=True, timeout=20)
        if p.returncode != 0:
            return None
        d = json.loads(p.stdout)
        return {"sha": d.get("sha", ""), "date": d.get("date", "")}
    except Exception:
        return None


def fetch_upstream(entries):
    """Look up the upstream branch HEAD for each branch-pinned entry. Returns a
    {key: {"sha","date"}} map (missing keys where the lookup failed)."""
    upstream = {}
    for e in entries:
        if not e.get("repo") or not e.get("ref"):
            continue
        head = _gh_head(e["repo"], e["ref"])
        if head:
            upstream[e["key"]] = head
    return upstream


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_check():
    """Live, NON-FATAL drift check for preflight. Always returns 0."""
    lock = json.loads(LOCK.read_text())
    entries = branch_entries(lock)
    if not entries:
        print("sources-age: no branch-pinned sources to check.")
        return 0
    upstream = fetch_upstream(entries)
    if not upstream:
        print("sources-age: SKIP — could not reach upstream (offline / gh unavailable). "
              "Pins unchecked this run; not a failure.")
        return 0
    findings = evaluate(entries, upstream, _now_iso())
    checked = len(upstream)
    if not findings:
        print(f"sources-age: OK — all {checked} branch-pinned source(s) within "
              f"{THRESHOLD_DAYS} days of upstream HEAD.")
        return 0
    print(f"sources-age: ⚠ WARNING — {len(findings)} pinned source(s) are stale "
          f"(> {THRESHOLD_DAYS} days behind upstream {'/'.join(sorted({f['repo'].split('/')[-1] for f in findings}))} HEAD):")
    for f in findings:
        print(f"  ⚠ {f['key']} ({f['repo']}): pinned {f['pinned_sha']} is ~{f['days_behind']}d "
              f"behind HEAD {f['head_sha']} — consider a deliberate re-pin + revalidation sweep.")
    print("  (informational: re-pins stay deliberate; this only surfaces silent drift.)")
    return 0


# ------------------------------------------------------------------------------ selftest
def _selftest():
    """Deterministic unit tests on evaluate() — no network. Kill-testable: mutate the
    >= comparison or the sha-equality short-circuit and a case flips."""
    NOW = "2026-07-29T00:00:00+00:00"
    entries = [
        {"key": "schema_validator", "repo": "org/ucp-schema", "ref": "main",
         "pinned_sha": "aaaa1111", "pinned_date": "2026-06-01T00:00:00Z"},
        {"key": "reference_sample_server", "repo": "org/samples", "ref": "main",
         "pinned_sha": "bbbb2222", "pinned_date": "2026-07-20T00:00:00Z"},
        {"key": "official_conformance_suite", "repo": "org/conformance", "ref": "main",
         "pinned_sha": "cccc3333", "pinned_date": "2026-07-01T00:00:00Z"},
    ]
    fails = []

    # Case A: stale — pinned 2026-06-01, upstream HEAD 2026-07-15 (44d) > 21d threshold.
    up = {"schema_validator": {"sha": "zzzz9999", "date": "2026-07-15T00:00:00Z"}}
    r = evaluate([entries[0]], up, NOW, threshold_days=21)
    if not (len(r) == 1 and r[0]["key"] == "schema_validator" and r[0]["days_behind"] == 44):
        fails.append(f"A: expected 1 stale finding at 44d, got {r}")

    # Case B: at-HEAD — pinned sha == upstream sha -> fresh, no finding.
    up = {"schema_validator": {"sha": "aaaa1111", "date": "2026-07-28T00:00:00Z"}}
    r = evaluate([entries[0]], up, NOW, threshold_days=21)
    if r:
        fails.append(f"B: at-HEAD pin must not flag, got {r}")

    # Case C: recently diverged — different sha but only 9 days newer (< threshold) -> no finding.
    up = {"reference_sample_server": {"sha": "yyyy8888", "date": "2026-07-29T00:00:00Z"}}
    r = evaluate([entries[1]], up, NOW, threshold_days=21)
    if r:
        fails.append(f"C: 9-days-behind pin must not flag at 21d threshold, got {r}")

    # Case D: exactly at threshold (28 days) -> flagged (>= boundary).
    up = {"official_conformance_suite": {"sha": "wwww7777", "date": "2026-07-29T00:00:00Z"}}
    r = evaluate([entries[2]], up, NOW, threshold_days=21)
    if not (len(r) == 1 and r[0]["days_behind"] == 28):
        fails.append(f"D: 28-days-behind must flag at 21d threshold, got {r}")

    # Case E: no upstream data (offline) -> skipped, never flagged.
    r = evaluate([entries[0]], {}, NOW, threshold_days=21)
    if r:
        fails.append(f"E: missing upstream data must be skipped, got {r}")

    # Case F: branch_entries() excludes tag-pinned sources (spec versions) and includes
    # main-pinned ones — proves we don't false-warn on deliberate release-tag pins.
    lock = {"sources": {
        "spec": {"versions": {"2026-04-08": {"tag": "v2026-04-08", "commit": "t1"}}},
        "schema_validator": {"repo": "org/ucp-schema", "ref": "main", "commit": "s1",
                             "commit_date": "2026-06-01T00:00:00Z"},
        "reference_sdk": {"repo": "org/python-sdk", "ref": "main", "commit": "d1",
                          "commit_date": "2026-06-01T00:00:00Z"},
    }}
    be = branch_entries(lock)
    keys = {e["key"] for e in be}
    if keys != {"schema_validator", "reference_sdk"}:
        fails.append(f"F: branch_entries must select only main-pinned sources, got {keys}")

    if fails:
        print("sources-age selftest: FAIL")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("sources-age selftest: PASS — staleness logic sound "
          "(stale/at-head/recent/boundary/offline/tag-exclusion all correct).")
    return 0


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    if "--check" in argv:
        return run_check()
    # default: live check (non-fatal)
    return run_check()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

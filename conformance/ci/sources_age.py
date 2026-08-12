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

Two blind spots this tool ALSO closes (P1-12):

  1. The AP2 reference pin. The AP2 mandate testbed pins a reference implementation
     (google-agentic-commerce/AP2) that used to live ONLY as prose in
     testbed/provenance.py, so an AP2 upstream release was watched by nobody. It is
     now a real locked source (`ap2_reference`, ref `main`) and rides the same
     branch-drift path as every other moving pin — which is the concrete re-pin
     trigger the P1-9 self-expiring AP2-defect guard needs to become real.

  2. Release-branch-past-tag drift. A spec version is pinned to a release TAG (the
     normative artifact), but the release BRANCH it was cut from is live and can
     receive post-tag cherry-picks. A tag pin never "ages" against a branch HEAD, so
     that advance was invisible. For any tag pin carrying a `release_branch`, we now
     also compare the pinned tag commit against that branch HEAD and WARN when the
     branch has advanced ("tag is the release artifact; branch advanced N commits —
     review whether normative"). This never forces a re-pin; it only makes the drift
     visible (consistent with the SOURCES.lock spec-version notes).
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
    # the named single-repo sources. ap2_reference is the AP2 interop-oracle pin
    # (testbed/provenance.py REFERENCE_SHA) — pinned to `main`, so it drifts silently
    # unless watched here; watching it is what makes the P1-9 re-pin trigger real.
    for key in ("reference_sample_server", "reference_sdk", "schema_validator",
                "official_conformance_suite", "ap2_reference"):
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


def tag_release_entries(lock):
    """Extract spec version entries that are pinned to a TAG (the normative release
    artifact) but also declare a live `release_branch`. A tag pin never ages against a
    branch HEAD, so a release branch advancing past its tag is invisible to the
    staleness path above. Returns a list of dicts:
    {key, repo, tag, release_branch, pinned_sha}."""
    s = lock.get("sources", lock)
    spec = s.get("spec", {})
    repo = spec.get("repo", "")
    out = []
    for ver, e in spec.get("versions", {}).items():
        if not isinstance(e, dict):
            continue
        rb = e.get("release_branch")
        pinned = e.get("commit", "")
        if rb and pinned:
            out.append({"key": f"spec/{ver}", "repo": repo,
                        "tag": e.get("tag", ""), "release_branch": rb,
                        "pinned_sha": pinned})
    return out


def evaluate_release_drift(entries, release_heads):
    """PURE release-branch-past-tag logic. For each tag-pinned `entry` tracking a live
    release branch, compare the pinned tag commit against the release-branch HEAD
    (`release_heads` = {key: {"sha", "ahead"}}). A finding when the branch HEAD DIFFERS
    from the pinned tag commit — i.e. the branch has advanced past the release artifact.

    This is NOT staleness and NEVER forces a re-pin: the tag stays the pinned artifact.
    It only makes 'branch moved past the tag' visible for a normative review.
    A finding: {key, repo, tag, release_branch, pinned_sha, head_sha, ahead}.
    Entries with no upstream data (offline / lookup failed) are skipped, not flagged."""
    findings = []
    for e in entries:
        rh = release_heads.get(e["key"])
        if not rh or not rh.get("sha"):
            continue                                   # no data -> cannot judge; skip
        if rh["sha"] == e["pinned_sha"]:
            continue                                   # branch still at the tag -> no drift
        findings.append({
            "key": e["key"], "repo": e.get("repo", ""),
            "tag": e.get("tag", ""), "release_branch": e["release_branch"],
            "pinned_sha": e["pinned_sha"][:12], "head_sha": rh["sha"][:12],
            "ahead": rh.get("ahead"),
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


def _gh_ahead(repo, base, head):
    """Commits `head` is ahead of `base` via the compare API, or None on any failure."""
    try:
        p = subprocess.run(
            [GH, "api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".ahead_by"],
            capture_output=True, text=True, timeout=20)
        if p.returncode != 0:
            return None
        return int(p.stdout.strip())
    except Exception:
        return None


def fetch_release_heads(entries):
    """Look up the release-branch HEAD (and commits-ahead-of-tag) for each tag-pinned
    entry that tracks a live release branch. Returns {key: {"sha","ahead"}} (missing
    keys where the lookup failed)."""
    heads = {}
    for e in entries:
        repo = e.get("repo")
        rb = e.get("release_branch")
        if not repo or not rb:
            continue
        head = _gh_head(repo, rb)
        if not head or not head.get("sha"):
            continue
        heads[e["key"]] = {"sha": head["sha"],
                           "ahead": _gh_ahead(repo, e.get("pinned_sha", ""), rb)}
    return heads


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_check():
    """Live, NON-FATAL drift check for preflight. Always returns 0.

    Covers TWO drift classes, both informational (never fatal):
      - branch-pinned sources aging past upstream HEAD (incl. the AP2 reference pin), and
      - release-branch-past-tag advances for tag-pinned spec versions."""
    lock = json.loads(LOCK.read_text())
    entries = branch_entries(lock)
    rel_entries = tag_release_entries(lock)
    if not entries and not rel_entries:
        print("sources-age: no branch-pinned or release-tracked sources to check.")
        return 0

    upstream = fetch_upstream(entries) if entries else {}
    rel_heads = fetch_release_heads(rel_entries) if rel_entries else {}
    if entries and not upstream and rel_entries and not rel_heads:
        print("sources-age: SKIP — could not reach upstream (offline / gh unavailable). "
              "Pins unchecked this run; not a failure.")
        return 0

    findings = evaluate(entries, upstream, _now_iso()) if upstream else []
    rel_findings = evaluate_release_drift(rel_entries, rel_heads) if rel_heads else []

    # --- branch-staleness (main-pinned sources incl. ap2_reference) ---
    if upstream:
        if not findings:
            print(f"sources-age: OK — all {len(upstream)} branch-pinned source(s) within "
                  f"{THRESHOLD_DAYS} days of upstream HEAD.")
        else:
            print(f"sources-age: ⚠ WARNING — {len(findings)} pinned source(s) are stale "
                  f"(> {THRESHOLD_DAYS} days behind upstream {'/'.join(sorted({f['repo'].split('/')[-1] for f in findings}))} HEAD):")
            for f in findings:
                print(f"  ⚠ {f['key']} ({f['repo']}): pinned {f['pinned_sha']} is ~{f['days_behind']}d "
                      f"behind HEAD {f['head_sha']} — consider a deliberate re-pin + revalidation sweep.")
            print("  (informational: re-pins stay deliberate; this only surfaces silent drift.)")
    else:
        print("sources-age: SKIP — branch-pinned upstream unreachable this run (not a failure).")

    # --- release-branch-past-tag drift (tag-pinned spec versions) ---
    if rel_heads:
        if not rel_findings:
            print(f"sources-age: OK — all {len(rel_heads)} tag-pinned release(s) still at "
                  f"their release-branch HEAD (no post-tag advance).")
        else:
            print(f"sources-age: ⚠ NOTE — {len(rel_findings)} release branch(es) have advanced "
                  f"past their pinned tag (the tag is the release artifact; review whether normative):")
            for f in rel_findings:
                ahead = f"{f['ahead']} commit(s)" if f.get("ahead") is not None else "N commits"
                print(f"  ⚠ {f['key']} ({f['repo']}): tag {f['tag']} pinned at {f['pinned_sha']}; "
                      f"branch {f['release_branch']} advanced {ahead} to {f['head_sha']} — "
                      f"review whether the new commits are normative; NOT an auto-re-pin.")
            print("  (informational: the pinned tag remains the artifact; this only surfaces branch drift.)")
    elif rel_entries:
        print("sources-age: SKIP — release-branch HEADs unreachable this run (not a failure).")
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

    # Case G (P1-12 gap 1): the AP2 reference pin is a branch-pinned (ref main) source,
    # so branch_entries MUST select it and evaluate MUST flag an AP2 upstream move. This
    # is the re-pin trigger the P1-9 self-expiring AP2-defect guard keys to. Kill-test:
    # drop "ap2_reference" from the branch_entries key list and this case flips.
    ap2_lock = {"sources": {
        "spec": {"repo": "org/ucp", "versions": {"2026-04-08": {"tag": "v", "commit": "t1"}}},
        "ap2_reference": {"repo": "google-agentic-commerce/AP2", "ref": "main",
                          "commit": "e1ea56db72a6", "commit_date": "2026-04-29T00:00:00Z"},
    }}
    ap2_be = branch_entries(ap2_lock)
    if not any(e["key"] == "ap2_reference" for e in ap2_be):
        fails.append("G: branch_entries must select ap2_reference (AP2 pin left unwatched)")
    else:
        ap2_entry = [e for e in ap2_be if e["key"] == "ap2_reference"]
        # a NEWER AP2 HEAD (different sha, dated well past our April pin) -> flagged.
        up_moved = {"ap2_reference": {"sha": "cafef00d99", "date": "2026-09-01T00:00:00Z"}}
        r = evaluate(ap2_entry, up_moved, NOW, threshold_days=21)
        if not (len(r) == 1 and r[0]["key"] == "ap2_reference"):
            fails.append(f"G: AP2 upstream move must be flagged, got {r}")
        # AP2 still at the pinned HEAD (upstream quiet) -> fresh, no finding.
        up_same = {"ap2_reference": {"sha": "e1ea56db72a6", "date": "2026-04-29T00:00:00Z"}}
        r2 = evaluate(ap2_entry, up_same, NOW, threshold_days=21)
        if r2:
            fails.append(f"G: AP2 at-HEAD (quiet upstream) must NOT flag, got {r2}")

    # Case H (P1-12 gap 2): a release branch that has advanced past its pinned tag must
    # be surfaced, while a branch still AT its tag (healthy) must NOT. Kill-test: mutate
    # the sha-equality short-circuit in evaluate_release_drift and a sub-case flips.
    rel_lock = {"sources": {"spec": {"repo": "org/ucp", "versions": {
        "2026-01-23": {"tag": "v2026-01-23", "commit": "dcf7eac71fc3",
                       "release_branch": "release/2026-01-23"},
        "2026-04-08": {"tag": "v2026-04-08", "commit": "a2d8bf0b8f5a",
                       "release_branch": "release/2026-04-08"},
        "2026-04-99": {"tag": "v-no-branch", "commit": "deadbeef00"},  # no release_branch -> excluded
    }}}}
    tre = tag_release_entries(rel_lock)
    tre_keys = {e["key"] for e in tre}
    if tre_keys != {"spec/2026-01-23", "spec/2026-04-08"}:
        fails.append(f"H: tag_release_entries must select only release_branch-carrying versions, got {tre_keys}")
    heads = {
        "spec/2026-01-23": {"sha": "e783ffa2eaea", "ahead": 10},   # branch advanced 10 past tag
        "spec/2026-04-08": {"sha": "a2d8bf0b8f5a", "ahead": 0},     # branch == tag (healthy)
    }
    rd = evaluate_release_drift(tre, heads)
    rd_keys = {f["key"] for f in rd}
    if "spec/2026-01-23" not in rd_keys:
        fails.append(f"H: release/2026-01-23 advanced 10 past tag must flag, got {rd}")
    if "spec/2026-04-08" in rd_keys:
        fails.append(f"H: release/2026-04-08 (branch==tag, healthy) must NOT flag, got {rd}")
    got_ahead = next((f["ahead"] for f in rd if f["key"] == "spec/2026-01-23"), None)
    if got_ahead != 10:
        fails.append(f"H: 01-23 finding must carry ahead=10, got {got_ahead}")

    # Case I: release-drift with no upstream data (offline) -> skipped, never flagged.
    rd_off = evaluate_release_drift(tre, {})
    if rd_off:
        fails.append(f"I: missing release-branch data must be skipped, got {rd_off}")

    if fails:
        print("sources-age selftest: FAIL")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("sources-age selftest: PASS — staleness logic sound "
          "(stale/at-head/recent/boundary/offline/tag-exclusion correct; "
          "AP2 pin watched + release-branch-past-tag drift surfaced).")
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

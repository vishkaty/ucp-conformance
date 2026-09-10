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
  --check     live network check via `gh api` (used by preflight). Exit codes (D2-03,
              PLAN-v3 §2.8 — FAIL-not-SKIP):
                0  every pinned tag still has its locked identity (or its move is
                   acknowledged, unexpired, in known_tag_moves.json — D2-17);
                1  a spec tag MOVED (dereferenced commit or tag object differs from the
                   lock) and is not acknowledged;
                2  the check could not run: `gh` not found (GH_BIN -> which gh -> known
                   paths) or a tag identity could not be fetched (offline).
              Branch-staleness and release-branch drift stay INFORMATIONAL (printed,
              never change the exit code): re-pins are deliberate.
  --selftest  run the embedded deterministic unit tests on the pure logic (used as a
              run_suite gate). Exit 0 on pass, 1 on failure. Network only for case L's
              own subprocess (which is told gh is absent).

TAG IDENTITY (D2-03): a spec version is pinned to a release tag whose identity is the
pair (tag object sha, dereferenced commit). Upstream can re-point a tag (v2026-04-08
was re-pointed 2026-09-09 to a25a4a24, ucp#813 — the pinned artifact a2d8bf0b is no
longer what the published tag names) or re-tag the same commit. `--check` fetches
both live and compares them with the lock's `commit` / `tag_object_sha` — never
dates, which a re-tag can carry unchanged. Findings print as
  TAG MOVED v2026-04-08: locked a2d8bf0b -> a25a4a24 (tag object ebac9d15, tagger 2026-04-13)

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
KNOWN_TAG_MOVES = ROOT / "conformance" / "ci" / "known_tag_moves.json"
THRESHOLD_DAYS = 21
GH_KNOWN_PATHS = (pathlib.Path.home() / "shn" / "tools" / "bin" / "gh",
                  pathlib.Path("/opt/homebrew/bin/gh"), pathlib.Path("/usr/local/bin/gh"),
                  pathlib.Path("/usr/bin/gh"))


def _resolve_gh():
    """The gh binary: $GH_BIN (must exist) -> `which gh` -> known install paths -> None.
    An explicit GH_BIN that does not exist is NOT silently replaced by another gh —
    the caller asked for that one."""
    import shutil
    env = os.environ.get("GH_BIN")
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    found = shutil.which("gh")
    if found:
        return found
    for p in GH_KNOWN_PATHS:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


GH = _resolve_gh() or ""


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


def tag_entries(lock):
    """Every spec version pinned to a tag: {key, repo, tag, commit, tag_object_sha}
    (the lock's locked identity; `tag_object_sha` absent in a lock that predates
    D2-03 — then only the commit is compared)."""
    s = lock.get("sources", lock)
    spec = s.get("spec", {})
    repo = spec.get("repo", "")
    out = []
    for ver, e in spec.get("versions", {}).items():
        if isinstance(e, dict) and e.get("tag") and e.get("commit"):
            out.append({"key": f"spec/{ver}", "version": ver, "repo": repo, "tag": e["tag"],
                        "commit": e["commit"], "tag_object_sha": e.get("tag_object_sha")})
    return out


def _same_sha(a, b):
    """Prefix-tolerant sha equality (locks may carry 8-hex, the API 40-hex)."""
    a, b = (a or "").lower(), (b or "").lower()
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def evaluate_tag_identity(entries, identities):
    """PURE tag-identity logic. `identities` = {key: {tag_object_sha, tag_object_type,
    commit, tagger_date}} as fetched live. A finding when the dereferenced COMMIT
    differs from the lock's `commit`, OR the TAG OBJECT sha differs from the lock's
    `tag_object_sha` (when the lock carries one) — a re-tag of the same commit is an
    identity change too. Dates are never compared (a re-tag can copy the date).
    Entries with no live identity are skipped (the caller decides whether that is
    fatal). A finding: {key, version, tag, from, to, tag_object, tag_object_from,
    tagger_date}."""
    findings = []
    for e in entries:
        live = identities.get(e["key"])
        if not live or not live.get("commit"):
            continue
        commit_moved = not _same_sha(live["commit"], e["commit"])
        object_moved = bool(e.get("tag_object_sha")) and \
            not _same_sha(live.get("tag_object_sha"), e["tag_object_sha"])
        if commit_moved or object_moved:
            findings.append({
                "key": e["key"], "version": e.get("version"), "tag": e["tag"],
                "from": e["commit"][:8], "to": live["commit"][:8],
                "tag_object": (live.get("tag_object_sha") or "")[:8],
                "tag_object_from": (e.get("tag_object_sha") or "")[:8] or None,
                "tagger_date": live.get("tagger_date"),
            })
    return findings


# ------------------------------------------------------------------------- live network
def fetch_tag_identity(repo, tag):
    """{tag_object_sha, tag_object_type, commit, tagger_date} for repo's `tag` via
    gh api (refs/tags -> object; an annotated tag is dereferenced through
    git/tags/<sha>; a lightweight tag IS its commit). None on any failure."""
    try:
        p = subprocess.run([GH, "api", f"repos/{repo}/git/ref/tags/{tag}",
                            "--jq", "{sha: .object.sha, type: .object.type}"],
                           capture_output=True, text=True, timeout=20)
        if p.returncode != 0:
            return None
        ref = json.loads(p.stdout)
        obj_sha, obj_type = ref.get("sha", ""), ref.get("type", "")
        if obj_type == "commit":
            return {"tag_object_sha": obj_sha, "tag_object_type": "commit",
                    "commit": obj_sha, "tagger_date": None}
        q = subprocess.run([GH, "api", f"repos/{repo}/git/tags/{obj_sha}",
                            "--jq", "{commit: .object.sha, date: .tagger.date}"],
                           capture_output=True, text=True, timeout=20)
        if q.returncode != 0:
            return None
        t = json.loads(q.stdout)
        return {"tag_object_sha": obj_sha, "tag_object_type": "tag",
                "commit": t.get("commit", ""), "tagger_date": t.get("date")}
    except Exception:
        return None


def fetch_tag_identities(entries):
    """{key: identity} for every tag entry whose identity could be fetched."""
    out = {}
    for e in entries:
        ident = fetch_tag_identity(e["repo"], e["tag"]) if e.get("repo") else None
        if ident:
            out[e["key"]] = ident
    return out


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


def load_known_tag_moves(path=KNOWN_TAG_MOVES):
    """Acknowledged tag moves (D2-17): [{tag, repo, from, to, tag_object, decision,
    reason, review_by, spec_pin, ...}]. Missing file -> []."""
    try:
        return json.load(open(path)).get("moves", [])
    except Exception:
        return []


def acknowledged_move(finding, moves, today):
    """The unexpired acknowledgement entry matching this TAG MOVED finding on
    (tag, from, to, tag_object) — or None. An entry whose review_by is past is as
    good as absent (fail-noisy self-expiry); a different `to` is a NEW move."""
    for m in moves:
        if m.get("tag") != finding["tag"]:
            continue
        if not (_same_sha(m.get("from"), finding["from"]) and _same_sha(m.get("to"), finding["to"])
                and _same_sha(m.get("tag_object"), finding["tag_object"])):
            continue
        try:
            expired = datetime.fromisoformat(m.get("review_by", "")).date() < today
        except Exception:
            expired = True
        if expired:
            continue
        return m
    return None


def run_check(today=None):
    """Live drift check for preflight. Exit 0 / 1 / 2 (see the module docstring).

      FATAL   (rc 1): a pinned spec TAG MOVED — dereferenced commit or tag object differs
              from the lock — and no unexpired acknowledgement in known_tag_moves.json.
      FATAL   (rc 2): gh not found, or a tag identity could not be fetched (offline).
      INFORMATIONAL: branch-pinned sources aging past upstream HEAD (incl. the AP2
              reference pin) and release-branch-past-tag advances — printed, rc unchanged."""
    from datetime import date as _date
    today = today or _date.today()
    if not GH:
        print("sources-age: FAIL — gh not found (set GH_BIN, or install gh on PATH / a known "
              "path); tag identities cannot be verified.")
        return 2
    lock = json.loads(LOCK.read_text())
    rc = 0

    # --- TAG IDENTITY (fatal class) ---
    tags = tag_entries(lock)
    idents = fetch_tag_identities(tags) if tags else {}
    missing = [e["key"] for e in tags if e["key"] not in idents]
    if missing:
        print(f"sources-age: FAIL — could not fetch the tag identity of {', '.join(missing)} "
              f"(offline / API error); the pins are unverified this run.")
        return 2
    moves = load_known_tag_moves()
    for f in evaluate_tag_identity(tags, idents):
        line = (f"TAG MOVED {f['tag']}: locked {f['from']} → {f['to']} "
                f"(tag object {f['tag_object']}, tagger {(f.get('tagger_date') or 'n/a')[:10]})")
        ack = acknowledged_move(f, moves, today)
        if ack:
            print(f"sources-age: {line} — acknowledged (decision {ack.get('decision')}, "
                  f"review_by {ack.get('review_by')})")
        else:
            print(f"sources-age: FAIL — {line} — the pinned artifact is no longer what the "
                  f"published tag names; acknowledge it in known_tag_moves.json (decision 3) "
                  f"or re-pin deliberately.")
            rc = 1
    if tags and not any(evaluate_tag_identity(tags, idents)):
        print(f"sources-age: OK — all {len(tags)} pinned spec tag(s) still carry their locked "
              f"identity (tag object + dereferenced commit).")

    entries = branch_entries(lock)
    rel_entries = tag_release_entries(lock)
    if not entries and not rel_entries:
        print("sources-age: no branch-pinned or release-tracked sources to check.")
        return rc

    upstream = fetch_upstream(entries) if entries else {}
    rel_heads = fetch_release_heads(rel_entries) if rel_entries else {}
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
        print("sources-age: SKIP — release-branch HEADs unreachable this run (informational class).")
    return rc


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

    # ---- D2-03 (PLAN-v3 §2.8): TAG IDENTITY. A spec version is pinned to a release
    # tag; the tag can be re-pointed upstream (v2026-04-08 was, 2026-09-09, ucp#813)
    # or re-tagged onto the same commit. evaluate_tag_identity compares the live
    # dereferenced COMMIT and the TAG OBJECT sha against the lock — never dates.
    tag_lock = {"spec": {"repo": "org/ucp", "versions": {
        "2026-04-08": {"tag": "v2026-04-08", "commit": "a2d8bf0b8f5a6fc790f677899c2c7da0684fe33d",
                       "tag_object_sha": "0ld0bj3c70000000000000000000000000000000"},
        "2026-08-25": {"tag": "v2026-08-25", "commit": "cd78fb38e819de77d9b527d110476eccb876f1bd",
                       "tag_object_sha": "cd78fb38e819de77d9b527d110476eccb876f1bd"},
    }}}
    try:
        te = tag_entries(tag_lock)
        # Case J: moved tag — the live tag object dereferences to a DIFFERENT commit.
        ident = {"spec/2026-04-08": {"tag_object_sha": "ebac9d155805aabd1bab37e78cb893c5a2be8a78",
                                     "tag_object_type": "tag",
                                     "commit": "a25a4a24e738b74c8fa83254448d0666e478f595",
                                     "tagger_date": "2026-04-13T14:51:29Z"}}
        tj = evaluate_tag_identity(te, ident)
        if not (len(tj) == 1 and tj[0]["key"] == "spec/2026-04-08" and tj[0]["from"] == "a2d8bf0b"
                and tj[0]["to"] == "a25a4a24" and tj[0]["tag_object"] == "ebac9d15"):
            fails.append(f"J: moved tag must yield one finding with from/to/tag_object, got {tj}")
        # Case K: unchanged — live object and commit equal the lock -> no finding.
        ident_same = {"spec/2026-08-25": {"tag_object_sha": "cd78fb38e819de77d9b527d110476eccb876f1bd",
                                          "tag_object_type": "commit",
                                          "commit": "cd78fb38e819de77d9b527d110476eccb876f1bd",
                                          "tagger_date": None}}
        tk = evaluate_tag_identity(te, ident_same)
        if tk:
            fails.append(f"K: unchanged tag must not flag, got {tk}")
        # Case K2: SAME commit but a re-tagged (new) tag object -> finding (identity changed).
        ident_retag = {"spec/2026-08-25": {"tag_object_sha": "n3w0bj3c70000000000000000000000000000000",
                                           "tag_object_type": "tag",
                                           "commit": "cd78fb38e819de77d9b527d110476eccb876f1bd",
                                           "tagger_date": "2027-01-01T00:00:00Z"}}
        tk2 = evaluate_tag_identity(te, ident_retag)
        if not (len(tk2) == 1 and tk2[0]["key"] == "spec/2026-08-25" and tk2[0]["to"] == "cd78fb38"
                and tk2[0]["tag_object"] == "n3w0bj3c"):
            fails.append(f"K2: re-tagged object on the same commit must flag, got {tk2}")
        # Case J' (date-blindness): identical shas with a different tagger date -> NO finding.
        ident_date = {"spec/2026-08-25": {**ident_same["spec/2026-08-25"], "tagger_date": "2099-01-01T00:00:00Z"}}
        if evaluate_tag_identity(te, ident_date):
            fails.append("J': a tagger-date change alone must never flag (identity is shas, not dates)")
    except NameError as e:
        fails.append(f"J/K/K2: tag identity functions absent: {e}")
    # Case L: `--check` with no usable gh binary must FAIL rc 2 ("gh not found"), never SKIP rc 0.
    env = dict(os.environ, GH_BIN="/nonexistent/gh")
    pl = subprocess.run([sys.executable, __file__, "--check"], capture_output=True, text=True,
                        env=env, timeout=60)
    if not (pl.returncode == 2 and "FAIL" in pl.stdout and "gh not found" in pl.stdout):
        fails.append(f"L: GH_BIN=/nonexistent --check must exit 2 with 'FAIL — gh not found', got rc "
                     f"{pl.returncode}: {pl.stdout.strip()[:120]!r}")

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

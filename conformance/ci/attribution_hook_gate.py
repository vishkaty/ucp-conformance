#!/usr/bin/env python3
"""
attribution_hook_gate.py — the `attribution-hook` gate (decision 16, PNR-0; D4-16).

Standing rule dated 2026-09-10: NO AI or bot author, co-author or "generated with" line on
any commit in any repo, forward-only (history before the date stays; no rewrite). Two
halves, both mechanical:

  1. HISTORY — every commit on HEAD with a committer date on or after RULE_SINCE must carry
     no AI/bot attribution in its message, author or committer. Runs everywhere (CI too,
     where checkout depth may be 1 — then it covers exactly the pushed HEAD).
  2. HOOK — this clone's active commit-msg hook (core.hooksPath if set, else the common
     .git/hooks; worktrees share it) must BE the tracked hook ops/tools/hooks/commit-msg
     (symlink or byte-identical). Requires ops/ mounted; a clone without ops/ (CI) SKIPs
     this half with rc 2 after the history half passed.

Exit 0 = both halves green; 1 = an attribution hit since RULE_SINCE, or (ops mounted) the
hook is missing/stale; 2 = history clean but ops/ not mounted (run_suite SKIP).
--selftest: hermetic kill-tests in a scratch repo (planted trailer, planted bot author,
missing hook, installed hook) so the gate provably can go red.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
OPS_HOOK = ROOT / "ops" / "tools" / "hooks" / "commit-msg"
RULE_SINCE = "2026-09-10"
GATE = "attribution-hook"

MESSAGE_PATTERNS = (
    re.compile(r"co-authored-by:.*(claude|anthropic|\[bot\]|[^a-z]bot[^a-z])", re.I),
    re.compile(r"noreply@anthropic", re.I),
    re.compile(r"(^|[^a-z])generated[ -]with([^a-z]|$)", re.I),
)
BOT_IDENT = re.compile(r"claude|anthropic|noreply@anthropic|\[bot\]|github-actions|copilot", re.I)


def _git(repo, *args, env=None):
    e = dict(os.environ)
    e.pop("GIT_DIR", None)
    if env:
        e.update(env)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=e)


def _commits(repo):
    """Every commit reachable from HEAD as (sha, committer_date_iso, an, ae, cn, ce, body).
    A full walk filtered in Python — NOT `git log --since`, which stops descending at the
    first commit older than the date and so hides newer commits behind a backdated one."""
    r = _git(repo, "log", "--format=%H%x1f%cI%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e")
    if r.returncode != 0:
        raise RuntimeError(f"git log failed: {r.stderr.strip()[:120]}")
    out = []
    for rec in r.stdout.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = (rec.split("\x1f", 6) + [""] * 7)[:7]
        out.append(parts)
    return out


def _since(repo, since):
    try:
        return [c for c in _commits(repo) if c[1][:10] >= since]
    except RuntimeError as e:
        return [("?", "", "", "", "", "", str(e))]


def history_hits(repo, since=RULE_SINCE):
    """[(sha, reason)] for commits with a committer date on/after `since` that carry an
    AI/bot attribution in the message, the author or the committer."""
    hits = []
    for sha, _date, an, ae, cn, ce, body in _since(repo, since):
        if sha == "?":
            hits.append(("?", body)); continue
        for ident, role in ((f"{an} <{ae}>", "author"), (f"{cn} <{ce}>", "committer")):
            if BOT_IDENT.search(ident):
                hits.append((sha[:10], f"{role} is a bot identity: {ident}"))
        for line in body.splitlines():
            if any(p.search(line) for p in MESSAGE_PATTERNS):
                hits.append((sha[:10], f"message line: {line.strip()}"))
    return hits


def history_count(repo, since=RULE_SINCE):
    return len([c for c in _since(repo, since) if c[0] != "?"])


def active_hook_path(repo):
    hp = _git(repo, "config", "--get", "core.hooksPath").stdout.strip()
    if hp:
        p = pathlib.Path(os.path.expanduser(hp))
        return (p if p.is_absolute() else pathlib.Path(repo) / p) / "commit-msg"
    d = _git(repo, "rev-parse", "--git-path", "hooks").stdout.strip()
    p = pathlib.Path(d)
    return (p if p.is_absolute() else pathlib.Path(repo) / p) / "commit-msg"


def hook_status(repo, tracked=OPS_HOOK):
    """ok | missing | stale | no-ops (the tracked hook itself is not on this machine)."""
    if not pathlib.Path(tracked).exists():
        return "no-ops"
    active = active_hook_path(repo)
    if not active.exists():
        return "missing"
    try:
        if active.resolve() == pathlib.Path(tracked).resolve():
            return "ok"
        if active.read_bytes() == pathlib.Path(tracked).read_bytes():
            return "ok"
    except OSError:
        pass
    return "stale"


def run(repo=ROOT, tracked=OPS_HOOK):
    hits = history_hits(repo)
    n = history_count(repo)
    if hits:
        for sha, why in hits:
            print(f"  ✗ {sha}: {why}")
        print(f"✗ FAIL {GATE}: {len(hits)} attribution hit(s) in {n} commit(s) since {RULE_SINCE} "
              f"(rule: decision 16, forward-only; amend/re-mint the commit, never rewrite older history)")
        return 1
    st = hook_status(repo, tracked)
    active = active_hook_path(repo)
    if st == "ok":
        print(f"✓ PASS {GATE} — tracked hook active at {active}; history since {RULE_SINCE}: "
              f"{n} commit(s), 0 attribution hits")
        return 0
    if st == "no-ops":
        print(f"- SKIP {GATE}: ops/ not mounted (no {tracked}); history since {RULE_SINCE}: "
              f"{n} commit(s), 0 attribution hits")
        return 2
    print(f"✗ FAIL {GATE}: commit-msg hook {st} at {active} — run: bash ops/tools/install_hooks.sh")
    return 1


def selftest():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.name", "Vishal Katyal")
        _git(repo, "config", "user.email", "vishal@katyal.ai")
        _git(repo, "config", "commit.gpgsign", "false")
        (repo / "a").write_text("a\n")
        _git(repo, "add", "a")
        assert _git(repo, "commit", "-q", "-m", "feat: clean").returncode == 0
        clean = history_hits(repo) == []
        print(f"  {'✓' if clean else '✗'} clean commit: no hit")
        ok &= clean

        (repo / "b").write_text("b\n"); _git(repo, "add", "b")
        _git(repo, "commit", "-q", "-m", "feat: x\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>")
        h = history_hits(repo)
        caught = any("Co-Authored-By" in why for _s, why in h)
        print(f"  {'✓' if caught else '✗'} planted trailer: {'CAUGHT' if caught else 'MISSED (gate is blind!)'}")
        ok &= caught

        (repo / "c").write_text("c\n"); _git(repo, "add", "c")
        _git(repo, "commit", "-q", "-m", "feat: y",
             env={"GIT_AUTHOR_NAME": "Claude", "GIT_AUTHOR_EMAIL": "noreply@anthropic.com"})
        h = history_hits(repo)
        caught = any("author is a bot" in why for _s, why in h)
        print(f"  {'✓' if caught else '✗'} planted bot author: {'CAUGHT' if caught else 'MISSED'}")
        ok &= caught

        # pre-rule commits are history, not hits (forward-only)
        (repo / "d").write_text("d\n"); _git(repo, "add", "d")
        old = {"GIT_AUTHOR_DATE": "2026-09-01T00:00:00", "GIT_COMMITTER_DATE": "2026-09-01T00:00:00"}
        _git(repo, "commit", "-q", "-m", "old: z\n\nCo-Authored-By: Claude <noreply@anthropic.com>", env=old)
        before = len(history_hits(repo))
        grand = len(history_hits(repo, since="2026-01-01"))
        fwd = grand == before + 1
        print(f"  {'✓' if fwd else '✗'} forward-only: a pre-{RULE_SINCE} trailer is history, not a hit")
        ok &= fwd

        # hook half: missing -> stale -> ok, against a scratch 'tracked' hook
        tracked = pathlib.Path(td) / "tracked-hook"
        tracked.write_text("#!/bin/sh\nexit 0\n")
        st1 = hook_status(repo, tracked)
        active = active_hook_path(repo)
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text("#!/bin/sh\nexit 1\n")
        st2 = hook_status(repo, tracked)
        active.unlink(); active.symlink_to(tracked)
        st3 = hook_status(repo, tracked)
        hooks_ok = (st1, st2, st3) == ("missing", "stale", "ok")
        print(f"  {'✓' if hooks_ok else '✗'} hook status ladder: {st1} → {st2} → {st3} (expected missing → stale → ok)")
        ok &= hooks_ok
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true", help="hermetic kill-tests")
    ap.add_argument("--repo", default=str(ROOT), help="repo to check (default: this clone)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    return run(pathlib.Path(a.repo))


if __name__ == "__main__":
    sys.exit(main())

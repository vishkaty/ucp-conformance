#!/usr/bin/env python3
"""Daily UCP upstream status → email (7 AM ET via launchd).

Self-contained runtime — lives OUTSIDE ~/Documents so a launchd agent (no Full
Disk Access) can read it, and inlines the katyal.ai Gmail send (Keychain +
OAuth + Gmail API) so it has zero dependency on the TCC-protected project dir.
Canonical source is versioned at ucp-conformance/ops/tools/daily_status_email.py;
keep them in sync when the report logic changes.

Sends AS vishal@katyal.ai → katyal.vishal@gmail.com. Read-only vs GitHub.
--preview builds the body without sending.
"""
import base64
import datetime
import email.message
import json
import subprocess
import sys
import urllib.parse
import urllib.request
import zoneinfo

ORG = "Universal-Commerce-Protocol"
ME = "vishkaty"
EMAIL_FROM = "Vishal Katyal <vishal@katyal.ai>"
EMAIL_TO = "katyal.vishal@gmail.com"
GH = "/Users/vishalkatyal/shn/tools/bin/gh"
ET = zoneinfo.ZoneInfo("America/New_York")

# Our OWN repo's CI health (the scheduled conformance-selftest). A red run here
# used to sit unnoticed for days (the 08-03/08-10 reds) because this report only
# watched upstream — so we now surface it, RED-flagged, at the top of the email.
CI_REPO = "vishkaty/ucp-conformance"
CI_WORKFLOW = "conformance-selftest"
_CI_FAIL = {"failure", "timed_out", "startup_failure"}

PIPELINE = [
    ("crates.io v1.4.0 issue", "ready", "Thu AM ET window"),
    ("TikTok nodejs-test issue", "unblocked (#132 merged)", "file — collab"),
    ("RFC 9421 test-vector suite", "on #590 merge", "build + propose"),
    ("C1 error-envelope module", "on #59 merge", "build"),
    ("samples permalink impl", "on #523 merge", "build"),
    ("cart implementation", "on #134 answer", "build"),
    ("nightly-alerting PR", "ready", "Fri/Mon"),
]


def gh(path):
    r = subprocess.run([GH, "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def search(q):
    d = gh(f"search/issues?q={q}&per_page=50&sort=updated")
    return d.get("items", []) if d else []


def et(ts):
    if not ts:
        return "—"
    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET)
    return dt.strftime("%b %-d %-I:%M%p").replace("AM", "a").replace("PM", "p")


def days_since(ts):
    if not ts:
        return None
    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (datetime.datetime.now(datetime.timezone.utc) - dt).days


def last_other_activity(repo, num):
    latest = who = None
    for c in gh(f"repos/{ORG}/{repo}/issues/{num}/comments") or []:
        login = (c.get("user") or {}).get("login", "")
        if login in (ME, "") or login.endswith("[bot]") or "cla" in login.lower():
            continue
        ts = c.get("created_at")
        if ts and (latest is None or ts > latest):
            latest, who = ts, login
    for c in gh(f"repos/{ORG}/{repo}/pulls/{num}/reviews") or []:
        login = (c.get("user") or {}).get("login", "")
        if login in (ME, "") or login.endswith("[bot]"):
            continue
        ts = c.get("submitted_at")
        if ts and (latest is None or ts > latest):
            latest, who = ts, login
    return who, latest


def collect():
    seen = {}
    for q in (f"involves:{ME}+org:{ORG}+state:open",
              f"author:{ME}+org:{ORG}+state:closed+sort:updated"):
        for it in search(q):
            repo = it["repository_url"].rsplit("/", 1)[-1]
            key = (repo, it["number"])
            if key in seen:
                continue
            is_pr = "pull_request" in it
            state = it["state"]
            if is_pr and it.get("pull_request", {}).get("merged_at"):
                state = "merged"
            d = days_since(it.get("closed_at"))
            if state in ("closed", "merged") and (d is None or d > 4):
                continue
            seen[key] = {"repo": repo, "num": it["number"], "pr": is_pr,
                         "state": state, "title": it["title"],
                         "closed_at": it.get("closed_at")}
    return list(seen.values())


# ── Engagement (spck.dev beacon + Cloudflare edge + PyPI) ──
# Bot-free first-party beacon is the truth; edge is shown as context (mostly bots).
CF_ACCT = "5e6f8e127d10ba10542aa25f82af322e"
USERS_NS = "7420e399198949848526538e7921a46e"      # ucp-conformance-USERS KV
ZONE = "7e807e6a30bf8cb65a67d2dac2aaefbc"           # spck.dev


def _sum_suffix(day_map, suffix):
    return sum(v or 0 for k, v in (day_map or {}).items() if k.endswith(suffix))


def engagement(days=7):
    """A 7-day daily table: bot-free human views/checks + edge context + PyPI.
    Never raises — any source that fails is shown as '—' so the email still sends."""
    today = datetime.datetime.now(ET).date()
    dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]

    beacon, edge, pypi = {}, {}, {}
    try:
        tok = secret("cloudflare.api-token")
        stats = _curl(["-H", f"Authorization: Bearer {tok}",
                       f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCT}"
                       f"/storage/kv/namespaces/{USERS_NS}/values/global:stats"])
        beacon = stats.get("dailyActivity", {}) if isinstance(stats, dict) else {}
    except Exception:
        beacon = {}
    try:
        tok = secret("cloudflare.api-token")
        q = ('{"query":"query($z:String!,$s:Date!,$e:Date!){viewer{zones(filter:{zoneTag:$z})'
             '{httpRequests1dGroups(limit:31,filter:{date_geq:$s,date_leq:$e},orderBy:[date_ASC])'
             '{dimensions{date}sum{requests}uniq{uniques}}}}}","variables":{"z":"' + ZONE +
             '","s":"' + dates[0] + '","e":"' + dates[-1] + '"}}')
        r = _curl(["-X", "POST", "https://api.cloudflare.com/client/v4/graphql",
                   "-H", f"Authorization: Bearer {tok}", "-H", "Content-Type: application/json", "-d", q])
        for g in (((r.get("data") or {}).get("viewer") or {}).get("zones") or [{}])[0].get("httpRequests1dGroups") or []:
            edge[g["dimensions"]["date"]] = (g["sum"]["requests"], g["uniq"]["uniques"])
    except Exception:
        edge = {}
    try:
        d = _curl(["-H", "Accept: application/json",
                   "https://pypistats.org/api/packages/spck-conformance/overall?mirrors=false"])
        for row in d.get("data", []):
            pypi[row["date"]] = pypi.get(row["date"], 0) + row["downloads"]
    except Exception:
        pypi = {}

    L = [f"ENGAGEMENT — spck.dev, last {days}d (human = bot-free beacon; edge incl. bots)",
         f"  {'date':<11}{'human':>6}{'ret':>5}{'chk':>5}{'edge_req':>9}{'edge_uq':>8}{'pypi':>6}"]
    tot = [0, 0, 0, 0, 0, 0]
    for d in dates:
        b = beacon.get(d, {})
        hv, rt, ck = _sum_suffix(b, "_view"), _sum_suffix(b, "_return"), (b.get("instantChecks") or 0)
        req, uq = edge.get(d, (None, None))
        dl = pypi.get(d)
        cells = [hv, rt, ck, req or 0, uq or 0, dl or 0]
        tot = [t + c for t, c in zip(tot, cells)]
        fr = "—" if req is None else str(req)
        fu = "—" if uq is None else str(uq)
        fd = "—" if dl is None else str(dl)
        L.append(f"  {d:<11}{hv:>6}{rt:>5}{ck:>5}{fr:>9}{fu:>8}{fd:>6}")
    L.append(f"  {'TOTAL':<11}{tot[0]:>6}{tot[1]:>5}{tot[2]:>5}{tot[3]:>9}{tot[4]:>8}{tot[5]:>6}")
    L.append("  human=page views · ret=returning · chk=instant checks run · edge=Cloudflare (mostly bots)")
    return L


def _ci_runs():
    """Read-only: latest conformance-selftest runs on main (newest first).
    Never raises — returns None on any failure so the email still sends."""
    try:
        r = subprocess.run(
            [GH, "run", "list", "-R", CI_REPO,
             "--workflow", CI_WORKFLOW, "--branch", "main", "--limit", "20",
             "--json", "conclusion,status,createdAt,url,databaseId"],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _days_between(ts, now):
    if not ts:
        return None
    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (now - dt).days


def ci_status_line(runs, now=None):
    """Format the CI-health status from `gh run list --json ...` output.
    PURE + offline (unit-tested by _selftest): `runs` is a list of run dicts
    (newest first) or None when the query failed. Returns body lines — RED-flagged
    with the run URL + how many days main has been red when the latest run failed,
    a normal green line when it passed, and a graceful line when there are no runs."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    header = "CI — conformance-selftest on main"
    if runs is None:
        return [f"{header}: status unavailable (gh query failed) — check manually"]
    completed = sorted(
        [r for r in runs if r.get("status") == "completed" and r.get("conclusion")],
        key=lambda r: r.get("createdAt") or "", reverse=True)
    if not completed:
        return [f"{header}: no completed runs found"]
    latest = completed[0]
    concl = (latest.get("conclusion") or "").lower()
    if concl == "success":
        return [f"{header}: green ✓ (passing · {et(latest.get('createdAt'))})"]
    if concl in _CI_FAIL:
        # First red of the CURRENT streak = the run just after the last success.
        streak = []
        for r in completed:
            if (r.get("conclusion") or "").lower() == "success":
                break
            streak.append(r)
        first_red = streak[-1] if streak else latest
        d = _days_between(first_red.get("createdAt"), now)
        dtxt = f"{d}d" if d is not None else "?"
        url = latest.get("url") or "(no url)"
        return [f"⚠️  CI RED — conformance-selftest on main FAILING for {dtxt}  ({concl})",
                f"           {url}"]
    # cancelled / neutral / skipped etc. — completed but not a red failure.
    return [f"{header}: {concl} (latest · {et(latest.get('createdAt'))})"]


def build_body():
    items = collect()
    merged = [i for i in items if i["state"] in ("merged", "closed")]
    open_prs = [i for i in items if i["state"] == "open" and i["pr"]]
    open_iss = [i for i in items if i["state"] == "open" and not i["pr"]]
    now = datetime.datetime.now(ET)
    L = [f"UCP upstream — daily status  ({now.strftime('%A %b %-d, %-I:%M %p ET')})", ""]

    # Our own repo's CI health first — a red conformance-selftest must not sit unseen.
    L += ci_status_line(_ci_runs())
    L.append("")

    if merged:
        L.append("RECENTLY LANDED")
        for i in sorted(merged, key=lambda x: x["closed_at"] or "", reverse=True):
            tag = "MERGED" if i["state"] == "merged" else "closed"
            L.append(f"  {tag:6} {i['repo']}#{i['num']}  {i['title'][:58]}  ({et(i['closed_at'])})")
        L.append("")

    def block(title, rows):
        L.append(f"{title} ({len(rows)})")
        L.append(f"  {'item':22} {'last (non-us)':22} {'idle':6} note")
        for i in sorted(rows, key=lambda x: (x["repo"], x["num"])):
            who, ts = last_other_activity(i["repo"], i["num"])
            act = f"{who} {et(ts)}" if who else "—"
            d = days_since(ts)
            idle = f"{d}d" if d is not None else "—"
            L.append(f"  {i['repo']+'#'+str(i['num']):22} {act[:22]:22} {idle:6} {i['title'][:34]}")
        L.append("")

    block("OPEN PRs — awaiting review", open_prs)
    block("OPEN ISSUES / OFFERS — awaiting response", open_iss)
    L.append("PIPELINE — next to file / build")
    for name, status, nxt in PIPELINE:
        L.append(f"  {name:26} {status:26} → {nxt}")
    L.append("")
    L += engagement()
    L.append("")
    L.append(f"({len(merged)} landed shown · {len(open_prs)} open PRs · {len(open_iss)} open issues/offers)")
    L.append("Maintainer window ~5–11 AM ET (damaz91). Read-only report; no actions taken.")
    return "\n".join(L)


def secret(name):
    r = subprocess.run(["/usr/bin/security", "find-generic-password",
                        "-s", f"katyal-ai.{name}", "-a", "katyal-ai", "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def _curl(args):
    # system curl uses the macOS CA store — works in the minimal launchd env
    # where the framework Python's SSL can't find a CA bundle.
    r = subprocess.run(["/usr/bin/curl", "-sS", *args], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"non-JSON from curl: {(r.stdout + r.stderr)[:200]}")


def send(subject, body):
    cid = secret("google-oauth.katyal-ai-access.client-id")
    csec = secret("google-oauth.katyal-ai-access.client-secret")
    rt = secret("gmail-katyal.oauth.refresh-token")
    if not (cid and csec and rt):
        raise RuntimeError("keychain secrets unavailable in this context")
    tok = _curl(["-X", "POST", "https://oauth2.googleapis.com/token",
                 "-d", f"client_id={cid}", "-d", f"client_secret={csec}",
                 "-d", f"refresh_token={rt}", "-d", "grant_type=refresh_token"])
    at = tok["access_token"]
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = EMAIL_FROM, EMAIL_TO, subject
    m.set_content(body)
    raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
    resp = _curl(["-X", "POST",
                  "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                  "-H", f"Authorization: Bearer {at}",
                  "-H", "content-type: application/json",
                  "-d", json.dumps({"raw": raw})])
    return resp.get("id")


def _selftest():
    """Offline unit tests for ci_status_line — mocked `gh run list` JSON, no network."""
    fails = []
    now = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.timezone.utc)

    # 1) latest run FAILED — red streak began 08-04 (7 days red by 08-11).
    red = [
        {"status": "completed", "conclusion": "failure", "createdAt": "2026-08-10T06:17:00Z",
         "url": "https://github.com/vishkaty/ucp-conformance/actions/runs/222"},
        {"status": "completed", "conclusion": "failure", "createdAt": "2026-08-04T06:17:00Z",
         "url": "https://github.com/vishkaty/ucp-conformance/actions/runs/111"},
        {"status": "completed", "conclusion": "success", "createdAt": "2026-08-03T06:17:00Z",
         "url": "https://github.com/vishkaty/ucp-conformance/actions/runs/100"},
    ]
    out = "\n".join(ci_status_line(red, now=now))
    if "RED" not in out:
        fails.append(f"failing latest run must be RED-flagged: {out!r}")
    if "runs/222" not in out:
        fails.append(f"RED line must carry the latest run URL: {out!r}")
    if "7d" not in out:
        fails.append(f"RED line must show days-red (7d from 08-04): {out!r}")

    # 2) latest run PASSED — normal green line, never RED.
    green = [
        {"status": "completed", "conclusion": "success", "createdAt": "2026-08-10T06:17:00Z",
         "url": "https://github.com/vishkaty/ucp-conformance/actions/runs/900"},
    ]
    out = "\n".join(ci_status_line(green, now=now))
    if "RED" in out:
        fails.append(f"passing run must NOT be RED: {out!r}")
    if "green" not in out:
        fails.append(f"passing run must read green: {out!r}")

    # 3) no completed runs — graceful, no crash, no false RED.
    out = "\n".join(ci_status_line([], now=now))
    if "RED" in out or "no completed runs" not in out:
        fails.append(f"empty run list must be graceful: {out!r}")

    # 4) gh query failed (None) — graceful, no false RED.
    out = "\n".join(ci_status_line(None, now=now))
    if "RED" in out or "unavailable" not in out:
        fails.append(f"None (query failure) must be graceful: {out!r}")

    # 5) in-progress-only (no completed) — treated as no completed runs.
    out = "\n".join(ci_status_line([{"status": "in_progress", "conclusion": None,
                                     "createdAt": "2026-08-11T06:17:00Z", "url": "u"}], now=now))
    if "RED" in out or "no completed runs" not in out:
        fails.append(f"in-progress-only must be graceful: {out!r}")

    if fails:
        print("daily_status_email selftest: FAIL")
        for f in fails:
            print(f"  x {f}")
        return 1
    print("daily_status_email selftest: PASS (ci_status_line red/green/empty/none/in-progress).")
    return 0


def main():
    if "--selftest" in sys.argv:
        return _selftest()
    body = build_body()
    subject = f"UCP status — {datetime.datetime.now(ET).strftime('%b %-d')}"
    if "--preview" in sys.argv:
        print(f"From: {EMAIL_FROM}\nTo: {EMAIL_TO}\nSubject: {subject}\n---\n{body}")
        return 0
    mid = send(subject, body)
    print(f"sent ✓ as vishal@katyal.ai · id {mid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

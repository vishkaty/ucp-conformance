#!/usr/bin/env python3
"""
official_crosscheck.py — run the OFFICIAL UCP conformance suite against our goldens, nightly,
with a SELF-EXPIRING allowlist (D4-06 / E1). SIGNAL, NOT ORACLE: their main is unpinned and
red at times, so this is never a push gate; the nightly (D4-10) uploads the results as
artifacts and ops/tools/pull_feeds.py (D4-17) pulls them (decision 24).

  per-pin venv     the suite is cloned at --suite-sha into conformance/.crosscheck/<sha>/
                   with a sibling python-sdk checkout at the tag the suite's own CI pins
                   (`v2026-04-08-6` for 016ecbc2, so its `[tool.uv.sources] ../python-sdk`
                   editable path resolves to the intended SDK); `pip show ucp-sdk` must equal
                   the suite's declared pin (0.4.6) or the run is rc 2.
  ports            goldens on :8382 (flower-04-08, the vendored samples server) / :8398
                   (golden-0825, --report-only); absl `--mock_webhook_port=8484
                   --mock_agent_port=8485` (conformance/ci/ports.json), passed per test file
                   (`uv run <file>.py ...` is the form that accepts absl flags; conftest's
                   `FLAGS(["pytest"])` swallows them under bare pytest).
  junit            absltest `--xml_output_file` per file -> merged official_crosscheck.json
                   {run_at, suite_sha, sdk_pin, golden, tests: {name: {status, message}}}.
  allowlist        conformance/ci/official_crosscheck_allowlist.json — USED (its test failed
                   this run) / STALE (test passed while unexpired: red) / EXPIRED (any
                   expires_on condition holds: samples_pin_not != the lock's samples pin,
                   or_pr_merged merged per `gh api` GET, or_date passed: red). Reuses
                   differential.classify_allowlist for used/stale.
  verdict diff     official FAIL not allowlisted -> `crosscheck_diffs.json` rows
                   {date, test, official: fail, ours: not-reached, check_ids: [], ledger_row:
                   "G16b"} (ledger candidates); on the gated golden they also FAIL the run.
  --selftest       hermetic: synthetic junit + allowlist + a STUBBED gh (RV2 T5).
Exit: 0 PASS · 1 stale/expired/unlisted failure · 2 cannot run (clone/venv/golden/pin).
"""
import argparse, datetime, json, os, pathlib, re, shutil, socket, subprocess, sys, tempfile, time
import xml.etree.ElementTree as ET

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from differential import classify_allowlist  # noqa: E402

LOCK = ROOT / "conformance" / "SOURCES.lock.json"
ALLOWLIST = HERE / "official_crosscheck_allowlist.json"
WORK = ROOT / "conformance" / ".crosscheck"
PORTS = json.loads((HERE / "ports.json").read_text())
GOLDENS = {
    "flower-04-08": {"port": PORTS["nightly-golden-a"]["port"], "serve": HERE / "serve_golden.sh",
                     "stop": HERE / "stop_golden.sh", "gated": True},
    "golden-0825": {"port": PORTS["nightly-golden-b"]["port"],
                    "serve": ROOT / "conformance" / "testbed" / "golden-0825" / "serve_golden_0825.sh",
                    "stop": ROOT / "conformance" / "testbed" / "golden-0825" / "stop_golden_0825.sh", "gated": False},
}
MOCK_WEBHOOK_PORT = PORTS["official-mock-a"]["port"]
MOCK_AGENT_PORT = PORTS["official-mock-b"]["port"]
SDK_TAG_FOR_SUITE = {"016ecbc2": "v2026-04-08-6"}     # the suite's own conformance-tests.yml pin (#100)


# ---------------------------------------------------------------------------
# allowlist semantics
# ---------------------------------------------------------------------------
def samples_pin(lock=LOCK):
    return json.loads(pathlib.Path(lock).read_text())["reference_sample_server"]["commit"]


def gh_pr_merged(ref, gh=None):
    """True/False when `gh api` (GET) answers, None offline. ref: 'samples#220'."""
    m = re.fullmatch(r"([\w.-]+)#(\d+)", ref or "")
    if not m:
        return None
    gh = gh or os.environ.get("GH_BIN") or shutil.which("gh")
    if not gh:
        return None
    r = subprocess.run([gh, "api", f"repos/Universal-Commerce-Protocol/{m.group(1)}/pulls/{m.group(2)}",
                        "--jq", ".merged"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip() == "true"


def expired(entry, lock_pin, pr_merged, today):
    """[reasons] for which `expires_on` conditions hold (empty = live)."""
    e = entry.get("expires_on") or {}
    out = []
    want = e.get("samples_pin_not")
    if want and want != lock_pin:
        out.append(f"samples_pin_not: lock samples pin {lock_pin[:8]} != {want[:8]} (the golden moved)")
    pr = e.get("or_pr_merged")
    if pr and pr_merged(pr) is True:
        out.append(f"or_pr_merged: {pr} is merged")
    d = e.get("or_date")
    if d and today > datetime.date.fromisoformat(d):
        out.append(f"or_date: {d} passed")
    return out


def classify(results, entries, golden, lock_pin, pr_merged, today=None):
    """results: {test: status}; entries: allowlist for this golden. Returns dict(used, stale,
    expired, unlisted_failures, untestable)."""
    today = today or datetime.date.today()
    exp = {}
    live = []
    for e in entries:
        if e.get("golden") != golden:
            continue
        why = expired(e, lock_pin, pr_merged, today)
        if why:
            exp[e["id"]] = why
        else:
            live.append(e)
    keys = {(golden, e["test"]): e["id"] for e in live}
    failed = {(golden, t) for t, st in results.items() if st == "fail"}
    probed = {golden} if results else set()
    used, stale = classify_allowlist(set(keys), matched=failed, probed=probed)
    allowed_tests = {t for _g, t in keys}
    unlisted = sorted(t for t, st in results.items() if st == "fail" and t not in allowed_tests)
    untestable = [keys[k] for k in keys if k not in failed and (golden not in probed or k[1] not in results)]
    return {"used": [keys[k] for k in used], "stale": [keys[k] for k in stale if k[1] in results],
            "expired": exp, "unlisted_failures": unlisted, "untestable": untestable}


# ---------------------------------------------------------------------------
# junit
# ---------------------------------------------------------------------------
def parse_junit(path, file_stem=None):
    """{name: {status, message}} with name = '<file>::<Class>::<test>'."""
    out = {}
    root = ET.parse(str(path)).getroot()
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")
        cls = cls.split(".")[-1]
        stem = file_stem or (tc.get("file") or "").split("/")[-1] or root.get("name", "")
        name = f"{stem}::{cls}::{tc.get('name')}"
        st, msg = "pass", ""
        for tag, s in (("failure", "fail"), ("error", "fail"), ("skipped", "skip")):
            el = tc.find(tag)
            if el is not None:
                st, msg = s, (el.get("message") or (el.text or "")).strip()[:300]
                break
        out[name] = {"status": st, "message": msg}
    return out


# ---------------------------------------------------------------------------
# live run
# ---------------------------------------------------------------------------
def _sh(cmd, cwd=None, env=None, timeout=900):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _clone_at(dest, repo, ref):
    if (dest / ".git").exists() and _sh(["git", "-C", str(dest), "rev-parse", "HEAD"]).stdout.strip().startswith(ref.lstrip("v") if False else ""):
        pass
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        for cmd in (["git", "init", "-q"], ["git", "remote", "add", "origin", f"https://github.com/Universal-Commerce-Protocol/{repo}.git"]):
            r = _sh(cmd, cwd=dest)
            if r.returncode:
                raise RuntimeError(r.stderr.strip()[-200:])
    r = _sh(["git", "-C", str(dest), "fetch", "-q", "--depth", "1", "origin", ref])
    if r.returncode:
        raise RuntimeError(f"fetch {repo}@{ref}: {r.stderr.strip()[-200:]}")
    r = _sh(["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"])
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-200:])
    return _sh(["git", "-C", str(dest), "rev-parse", "HEAD"]).stdout.strip()


def prepare_suite(sha):
    """Clone the suite at `sha` + the sibling python-sdk at the suite's pinned tag; uv sync;
    assert the resolved ucp-sdk == the suite's declared pin. Returns (suite_dir, sdk_pin)."""
    suite = WORK / sha
    got = _clone_at(suite, "conformance", sha)
    tag = SDK_TAG_FOR_SUITE.get(sha[:8])
    if not tag:
        raise RuntimeError(f"no python-sdk tag known for suite {sha[:8]} (SDK_TAG_FOR_SUITE)")
    _clone_at(WORK / "python-sdk", "python-sdk", tag)
    # the suite's [tool.uv.sources] editable path is ../python-sdk relative to the suite dir
    link = suite.parent / "python-sdk"
    assert link.exists()
    pyproj = (suite / "pyproject.toml").read_text()
    m = re.search(r'ucp-sdk==([\d.]+)', pyproj)
    pin = m.group(1) if m else None
    r = _sh(["uv", "sync", "-q"], cwd=suite, timeout=900)
    if r.returncode:
        raise RuntimeError(f"uv sync failed: {r.stderr.strip()[-300:]}")
    r = _sh(["uv", "pip", "show", "ucp-sdk"], cwd=suite)
    ver = next((l.split(":", 1)[1].strip() for l in r.stdout.splitlines() if l.startswith("Version:")), None)
    if pin and ver != pin:
        raise RuntimeError(f"SDK PIN DRIFT: suite {sha[:8]} declares ucp-sdk=={pin} but the venv resolved {ver}")
    return suite, pin, got


class Golden:
    def __init__(self, name):
        self.cfg = GOLDENS[name]; self.name = name
        self.port = self.cfg["port"]
        self.db = pathlib.Path(tempfile.mkdtemp(prefix=f"crosscheck_{name}_"))
        self.secret = "crosscheck-secret"
        self.env = dict(os.environ, PORT=str(self.port), DB_DIR=str(self.db), SIM_SECRET=self.secret)

    def __enter__(self):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", self.port)) == 0:
                raise RuntimeError(f":{self.port} already in use — refusing to boot {self.name}")
        r = _sh([str(self.cfg["serve"])], env=self.env, timeout=300)
        if r.returncode:
            raise RuntimeError(f"{self.name} boot failed: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
        return f"http://localhost:{self.port}"

    def __exit__(self, *a):
        _sh([str(self.cfg["stop"])], env=self.env, timeout=60)
        shutil.rmtree(self.db, ignore_errors=True)


def run_suite_files(suite, base, secret, out_dir):
    """Run every *_test.py standalone with absl flags; merge the junit files."""
    results = {}
    files = sorted(p for p in suite.glob("*_test.py"))
    for f in files:
        xml = out_dir / f"{f.stem}.xml"
        _sh(["uv", "run", "-q", f.name, f"--server_url={base}", f"--simulation_secret={secret}",
             f"--mock_webhook_port={MOCK_WEBHOOK_PORT}", f"--mock_agent_port={MOCK_AGENT_PORT}",
             f"--xml_output_file={xml}"], cwd=suite, timeout=900)
        if xml.exists():
            results.update(parse_junit(xml, file_stem=f.name))
        else:
            results[f"{f.name}::(no junit)"] = {"status": "fail", "message": "no junit produced (import/boot error)"}
    return results


def live(golden, sha, report_only, out_dir):
    lines = []
    try:
        suite, pin, got = prepare_suite(sha)
    except Exception as e:  # noqa: BLE001
        print(f"official-crosscheck: SKIP — {e}"); return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Golden(golden) as base:
            t0 = time.monotonic()
            results = run_suite_files(suite, base, GOLDENS[golden] and "crosscheck-secret", out_dir)
            dt = time.monotonic() - t0
    except RuntimeError as e:
        print(f"official-crosscheck: SKIP — {e}"); return 2
    entries = json.loads(ALLOWLIST.read_text())["allow"]
    statuses = {t: r["status"] for t, r in results.items()}
    cls = classify(statuses, entries, golden, samples_pin(), gh_pr_merged)
    n_pass = sum(1 for s in statuses.values() if s == "pass"); n_fail = sum(1 for s in statuses.values() if s == "fail")
    n_skip = sum(1 for s in statuses.values() if s == "skip")
    doc = {"run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "suite_sha": got,
           "sdk_pin": pin, "golden": golden, "report_only": report_only, "seconds": round(dt, 1), "tests": results,
           "allowlist": cls}
    (out_dir / f"official_crosscheck_{golden}.json").write_text(json.dumps(doc, indent=1) + "\n")
    today = datetime.date.today().isoformat()
    diffs = [{"date": today, "golden": golden, "test": t, "official": "fail", "ours": "not-reached", "check_ids": [],
              "message": results[t]["message"], "ledger_row": "G16b"} for t in cls["unlisted_failures"]]
    (out_dir / f"crosscheck_diffs_{golden}.json").write_text(json.dumps({"generated": doc["run_at"], "rows": diffs}, indent=1) + "\n")
    for k in ("stale", "unlisted_failures"):
        for x in cls[k]:
            lines.append(f"  ✗ {k}: {x}" + (f" — {results.get(x, {}).get('message', '')[:120]}" if k == "unlisted_failures" else ""))
    for eid, why in cls["expired"].items():
        lines.append(f"  ✗ expired: {eid}: {'; '.join(why)}")
    ok = not cls["stale"] and not cls["expired"] and (report_only or not cls["unlisted_failures"])
    used_ids = cls["used"]
    lines.append(f"suite {got[:8]} · sdk {pin} · golden {golden} · {len(statuses)} tests: {n_pass} pass / {n_fail} fail / "
                 f"{n_skip} skip · allowlisted {len(used_ids)} ({', '.join(sorted({e['upstream'].rsplit('/', 2)[-2] + '#' + e['upstream'].rsplit('/', 1)[-1] for e in entries if e['id'] in used_ids})) or '-'})"
                 f" · stale {len(cls['stale'])} · expired {len(cls['expired'])}"
                 + (f" · unlisted failures {len(cls['unlisted_failures'])} (report)" if report_only and cls['unlisted_failures'] else "")
                 + f" · {'PASS' if ok else 'FAIL'}" + (" (report-only)" if report_only else ""))
    print("\n".join(lines))
    return 0 if (ok or report_only) else 1


# ---------------------------------------------------------------------------
# selftest (hermetic; stubbed gh)
# ---------------------------------------------------------------------------
def selftest():
    ok = True

    def case(tag, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'✓' if cond else '✗'} {tag}" + (f" — {detail}" if detail else ""))

    xml = """<testsuites><testsuite name="order_test.py"><testcase classname="order_test.OrderTest" name="test_a"><failure message="boom">x</failure></testcase><testcase classname="order_test.OrderTest" name="test_b"/><testcase classname="order_test.OrderTest" name="test_d"><error message="err"/></testcase><testcase classname="order_test.OrderTest" name="test_s"><skipped message="n/a"/></testcase></testsuite></testsuites>"""
    fd, path = tempfile.mkstemp(suffix=".xml"); os.close(fd); pathlib.Path(path).write_text(xml)
    res = parse_junit(path, file_stem="order_test.py"); os.unlink(path)
    st = {t: r["status"] for t, r in res.items()}
    case("junit parsed: A fail, B pass, D fail (error), S skip",
         st == {"order_test.py::OrderTest::test_a": "fail", "order_test.py::OrderTest::test_b": "pass",
                "order_test.py::OrderTest::test_d": "fail", "order_test.py::OrderTest::test_s": "skip"}, str(st))
    pin = "00333a80ebca3efd442d515b261a36e57de815d3"
    today = datetime.date(2026, 9, 11)
    A = {"id": "A", "golden": "g", "test": "order_test.py::OrderTest::test_a", "upstream": "u",
         "expires_on": {"samples_pin_not": pin, "or_pr_merged": "samples#220", "or_date": "2026-11-01"}}
    B = dict(A, id="B", test="order_test.py::OrderTest::test_b")
    C = dict(A, id="C", test="order_test.py::OrderTest::test_c", expires_on={"or_date": "2026-09-01"})
    stub_gh = lambda ref: False   # noqa: E731 — stubbed gh: nothing merged
    c = classify(st, [A, B, C], "g", pin, stub_gh, today)
    case("A used (its test failed this run)", c["used"] == ["A"], str(c))
    case("B stale (its test passed while unexpired) -> red", c["stale"] == ["B"], str(c["stale"]))
    case("C expired by or_date -> red", "C" in c["expired"] and "or_date" in c["expired"]["C"][0], str(c["expired"]))
    case("D is an unlisted official failure (ledger candidate)", c["unlisted_failures"] == ["order_test.py::OrderTest::test_d"])
    merged_gh = lambda ref: ref == "samples#220"   # noqa: E731 — STUBBED gh says #220 merged
    c2 = classify(st, [A], "g", pin, merged_gh, today)
    case("or_pr_merged resolved true via a STUBBED gh -> A expired", "A" in c2["expired"] and "or_pr_merged" in c2["expired"]["A"][0], str(c2["expired"]))
    c3 = classify(st, [A], "g", "deadbeef" * 5, stub_gh, today)
    case("samples_pin_not no longer equals the lock's samples pin (pin moved) -> A expired",
         "A" in c3["expired"] and "samples_pin_not" in c3["expired"]["A"][0], str(c3["expired"]))
    c4 = classify(st, [A], "g", pin, stub_gh, today)
    case("samples_pin_not equal to the lock's samples pin -> A live (used)", c4["used"] == ["A"] and not c4["expired"])
    c5 = classify({}, [A], "g", pin, stub_gh, today)
    case("no results for the golden -> A untestable, never stale", c5["stale"] == [] and c5["untestable"] == ["A"], str(c5))
    real = json.loads(ALLOWLIST.read_text())["allow"]
    case("real allowlist: every entry has upstream + expires_on + golden + test",
         all(e.get("upstream") and e.get("expires_on") and e.get("golden") in GOLDENS and e.get("test") for e in real))
    case("real allowlist: no entry expired against the lock today (offline gh)",
         not any(expired(e, samples_pin(), lambda r: None, datetime.date.today()) for e in real))
    case("ports: goldens :8382/:8398, mocks :8484/:8485 from ports.json, disjoint from the push-gate ports",
         {GOLDENS["flower-04-08"]["port"], GOLDENS["golden-0825"]["port"], MOCK_WEBHOOK_PORT, MOCK_AGENT_PORT} == {8382, 8398, 8484, 8485})
    print("official-crosscheck selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="official-suite cross-checker (D4-06)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--golden", choices=sorted(GOLDENS))
    ap.add_argument("--suite-sha", default="016ecbc22240affdec4429a60d539bedd04ab51b")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--out-dir", default=None, help="where the junit + JSON artifacts go (default RECORD_DIR / tmp)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.golden:
        ap.error("--golden required")
    out = pathlib.Path(a.out_dir or os.environ.get("RUN_SUITE_RECORD_DIR") or tempfile.mkdtemp(prefix="crosscheck_"))
    report_only = a.report_only or not GOLDENS[a.golden]["gated"]
    return live(a.golden, a.suite_sha, report_only, out)


if __name__ == "__main__":
    sys.exit(main())

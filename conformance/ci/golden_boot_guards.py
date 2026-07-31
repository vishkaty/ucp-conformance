#!/usr/bin/env python3
"""
golden_boot_guards.py — deterministic tests for the golden server's boot/teardown guards.

Every behavioral verdict we publish is measured against the golden reference server, so
"which process answered, running which SDK" is not an operational detail — it is the
provenance of the verdict. Two ways that provenance can silently rot, both observed:

  1. FLOATING SDK. From samples 2e5b77c1 the reference server dropped
     [tool.uv.sources], so it installs ucp-sdk from PyPI instead of our SHA-pinned
     vendored copy. A PyPI release could then change behavior under a pinned SHA and
     move a verdict with nothing in the repo changing. serve_golden.sh holds the
     resolved version to SOURCES.lock reference_sdk.pypi_pin.

  2. ZOMBIE GOLDEN. `uv run` forks, so $! is the wrapper, not the process holding the
     port. A teardown that kills the recorded PID can leave the real server listening;
     the next boot then fails to bind, the stale process answers the health probe, and
     the harness reports "golden UP" while validating against stale code. Observed
     2026-07-31: a stale 01-23 server re-read a discovery_profile.json that
     fetch_sources had already replaced, serving a NEW payload through OLD code.
     serve_golden.sh now refuses an occupied port and records the true listener.

These guards only earn trust if they are non-vacuous, so every case here runs twice:
once against the real script (must block / must record), and once against a MUTANT with
the guard excised (must NOT block). A guard that cannot be shown to fail without its
code is decoration.

Hermetic: no network, no uv, no real reference server. The script under test is copied
into a synthetic ROOT (it resolves ROOT from its own path) with a stub `uv` on PATH.

  --selftest   run the cases. Exit 0 pass, 1 fail.
"""
import contextlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVE = ROOT / "conformance" / "ci" / "serve_golden.sh"
STOP = ROOT / "conformance" / "ci" / "stop_golden.sh"

# Stub `uv`. Handles the three invocations serve_golden.sh makes: `uv sync` (no-op),
# `uv pip show ucp-sdk` (reports STUB_SDK_VERSION), and `uv run <script>` — where
# `server.py` becomes a tiny HTTP listener that answers the health path, so boot,
# listener-PID recording and teardown are all exercised end to end without the real
# reference server.
UV_STUB = """#!/usr/bin/env bash
case "$1" in
  sync) exit 0 ;;
  pip)  [ "$2" = "show" ] && { echo "Name: ucp-sdk"; echo "Version: ${STUB_SDK_VERSION}"; exit 0; }; exit 1 ;;
  run)
    shift
    case "$1" in
      import_csv.py) exit 0 ;;
      server.py)
        port=""
        for a in "$@"; do case "$a" in --port=*) port="${a#--port=}" ;; esac; done
        # FORK, do not exec. Real `uv run` spawns the interpreter as a CHILD, which is
        # precisely why $! is the wrapper and not the process holding the port. A stub
        # that exec'd would collapse the two into one pid and make the wrapper-pid
        # mutant undetectable — the harness would "pass" by being unable to fail.
        python3 -c '
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.end_headers(); self.wfile.write(b"{\\"ucp\\":{\\"version\\":\\"stub\\"}}")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
' "$port" &
        wait $!
        ;;
    esac
    exit 0 ;;
esac
exit 0
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _mkroot(tmp, *, pypi_pin, path_dep=False, mutate=None):
    """Build a synthetic ROOT holding a copy of the scripts under test.

    mutate: optional callable(text)->text applied to serve_golden.sh to excise a guard,
    producing the mutant that proves the corresponding case is non-vacuous.
    """
    root = pathlib.Path(tmp)
    ci = root / "conformance" / "ci"
    ci.mkdir(parents=True)
    server = root / "conformance" / ".vendor" / "samples" / "rest" / "python" / "server"
    server.mkdir(parents=True)
    data = root / "conformance" / ".vendor" / "samples" / "rest" / "python" / "test_data" / "flower_shop"
    data.mkdir(parents=True)
    (data / "products.csv").write_text("id,name\n1,rose\n")

    pyproject = '[project]\nname = "ucp-merchant-server"\n'
    if path_dep:
        pyproject += '\n[tool.uv.sources]\nucp-sdk = { path = "../python-sdk/", editable = true }\n'
    (server / "pyproject.toml").write_text(pyproject)

    (root / "conformance" / "SOURCES.lock.json").write_text(
        json.dumps({"reference_sdk": {"pypi_pin": pypi_pin}} if pypi_pin is not None
                   else {"reference_sdk": {}})
    )

    text = SERVE.read_text()
    if mutate:
        text = mutate(text)
    (ci / "serve_golden.sh").write_text(text)
    (ci / "stop_golden.sh").write_text(STOP.read_text())
    for f in ("serve_golden.sh", "stop_golden.sh"):
        (ci / f).chmod(0o755)

    bindir = root / "bin"
    bindir.mkdir()
    (bindir / "uv").write_text(UV_STUB)
    (bindir / "uv").chmod(0o755)
    return root, ci, bindir


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _run(script, *, root, bindir, port, db_dir, sdk_version="0.4.3", timeout=60):
    """Run the script with stdio bound to FILES, never pipes.

    Capturing via pipes would make this harness hostage to the very defect it guards:
    anything the script leaves holding an inherited fd blocks the read until it dies.
    Files decouple us, so a regression shows up as a failed assertion rather than a
    hung test run.
    """
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["STUB_SDK_VERSION"] = sdk_version
    env["PORT"] = str(port)
    env["DB_DIR"] = str(db_dir)
    env["SIM_SECRET"] = "x"
    out_p, err_p = pathlib.Path(root) / "_stdout", pathlib.Path(root) / "_stderr"
    with open(out_p, "w") as o, open(err_p, "w") as e:
        p = subprocess.Popen(["bash", str(script)], stdout=o, stderr=e,
                             stdin=subprocess.DEVNULL, env=env)
        rc = p.wait(timeout=timeout)
    return _Result(rc, out_p.read_text(), err_p.read_text())


# ---- mutants: excise exactly one guard, leaving the rest of the script intact --------
def _mutant_drop_port_guard(text):
    """Remove the occupancy refusal so an occupied port boots anyway (pre-fix behavior)."""
    out = re.sub(
        r'if lsof -nP -iTCP:"\$PORT" -sTCP:LISTEN >/dev/null 2>&1; then.*?\n  exit 3\nfi\n',
        "", text, count=1, flags=re.S)
    assert out != text, "port-guard mutant did not apply — the guard's shape changed"
    return out


def _mutant_drop_sdk_guard(text):
    """Neuter the SDK pin assertion (pre-fix behavior: whatever PyPI gave us is fine)."""
    out = text.replace("sdk_pin_guard || exit 3", "true")
    assert out != text, "sdk-guard mutant did not apply — the call site changed"
    return out


def _mutant_record_wrapper_pid(text):
    """Restore the original bug: record the `uv run` wrapper instead of the listener."""
    out = text.replace(
        'LISTEN_PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"',
        'LISTEN_PID=""')
    assert out != text, "wrapper-pid mutant did not apply — the lookup changed"
    return out


def _selftest():
    fails = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append(f"{name}: {detail}")

    # ---- Case A: SDK pin drift is refused, and the refusal is load-bearing ----------
    with tempfile.TemporaryDirectory() as tmp:
        root, ci, bindir = _mkroot(tmp, pypi_pin="0.4.2")
        port = _free_port()
        r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port,
                 db_dir=root / "db", sdk_version="0.4.3")
        check("A-real", r.returncode == 3 and "SDK PIN DRIFT" in r.stderr,
              f"expected rc=3 + drift message, got rc={r.returncode} err={r.stderr[-200:]}")
    with tempfile.TemporaryDirectory() as tmp:
        root, ci, bindir = _mkroot(tmp, pypi_pin="0.4.2", mutate=_mutant_drop_sdk_guard)
        port = _free_port()
        r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port,
                 db_dir=root / "db", sdk_version="0.4.3")
        check("A-mutant", r.returncode == 0,
              f"guard-less script must boot despite drift (non-vacuity), got rc={r.returncode}")
        subprocess.run(["bash", str(ci / "stop_golden.sh")], capture_output=True,
                       env={**os.environ, "DB_DIR": str(root / "db"), "PORT": str(port)})

    # ---- Case B: matching pin boots, and records the LISTENER, not the wrapper ------
    with tempfile.TemporaryDirectory() as tmp:
        root, ci, bindir = _mkroot(tmp, pypi_pin="0.4.3")
        port, db = _free_port(), root / "db"
        r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port,
                 db_dir=db, sdk_version="0.4.3")
        check("B-boot", r.returncode == 0, f"matching pin must boot, rc={r.returncode} {r.stderr[-200:]}")
        recorded = (db / "server.pid").read_text().strip() if (db / "server.pid").exists() else ""
        listener = _listener_pid(port)
        check("B-pid", recorded and listener and recorded == str(listener),
              f"server.pid must hold the listener {listener}, holds {recorded!r}")
        # teardown must free the port and stay quiet on a healthy stop
        t = subprocess.run(["bash", str(ci / "stop_golden.sh")], capture_output=True, text=True,
                           env={**os.environ, "DB_DIR": str(db), "PORT": str(port)}, timeout=60)
        check("B-teardown", t.returncode == 0, f"healthy teardown must exit 0, got {t.returncode}: {t.stderr[-200:]}")
        check("B-freed", _listener_pid(port) is None, "teardown must leave the port free")

    # ---- Case B-mutant: recording the wrapper leaves the port held after teardown ---
    with tempfile.TemporaryDirectory() as tmp:
        root, ci, bindir = _mkroot(tmp, pypi_pin="0.4.3", mutate=_mutant_record_wrapper_pid)
        port, db = _free_port(), root / "db"
        _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port, db_dir=db)
        subprocess.run(["bash", str(ci / "stop_golden.sh")], capture_output=True, text=True,
                       env={**os.environ, "DB_DIR": str(db), "PORT": str(port)}, timeout=60)
        still = _listener_pid(port)
        check("B-mutant", still is not None,
              "wrapper-pid mutant must strand the listener (proves the fix is load-bearing)")
        if still:
            _kill(still)

    # ---- Case C: an occupied port is refused, and the refusal is load-bearing -------
    # The squatter must ANSWER, not merely bind: the real hazard is a stale golden that
    # still serves 200s, so the health probe passes and the harness grades against it.
    # A silent socket would only prove curl can time out, which is not the defect.
    with _http_squatter() as (squatter, port):
        with tempfile.TemporaryDirectory() as tmp:
            root, ci, bindir = _mkroot(tmp, pypi_pin="0.4.3")
            r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port, db_dir=root / "db")
            check("C-real", r.returncode == 3 and "already in use" in r.stderr,
                  f"occupied port must be refused, got rc={r.returncode} err={r.stderr[-200:]}")

    with _http_squatter() as (squatter, port):
        with tempfile.TemporaryDirectory() as tmp:
            root, ci, bindir = _mkroot(tmp, pypi_pin="0.4.3", mutate=_mutant_drop_port_guard)
            r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port, db_dir=root / "db")
            # Pre-fix behavior: the squatter answers the health probe and the script
            # cheerfully reports the golden is UP. That false green is the whole point.
            check("C-mutant", r.returncode == 0 and "golden UP" in r.stdout,
                  f"guard-less script must false-green on a squatted port, got rc={r.returncode}")

    # ---- Case D: a path-dep server skips the PyPI assertion entirely ----------------
    with tempfile.TemporaryDirectory() as tmp:
        root, ci, bindir = _mkroot(tmp, pypi_pin="0.0.0", path_dep=True)
        port, db = _free_port(), root / "db"
        r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port, db_dir=db,
                 sdk_version="9.9.9")
        check("D", r.returncode == 0 and "path dep" in r.stderr,
              f"path-dep server must skip the PyPI guard, rc={r.returncode} err={r.stderr[-200:]}")
        subprocess.run(["bash", str(ci / "stop_golden.sh")], capture_output=True,
                       env={**os.environ, "DB_DIR": str(db), "PORT": str(port)})

    # ---- Case E: a missing pypi_pin is a hard stop, never a silent pass -------------
    with tempfile.TemporaryDirectory() as tmp:
        root, ci, bindir = _mkroot(tmp, pypi_pin=None)
        port = _free_port()
        r = _run(ci / "serve_golden.sh", root=root, bindir=bindir, port=port, db_dir=root / "db")
        check("E", r.returncode == 3 and "cannot verify" in r.stderr,
              f"absent pin must refuse, got rc={r.returncode} err={r.stderr[-200:]}")

    if fails:
        print("golden-boot-guards: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("golden-boot-guards: PASS — SDK-pin drift, occupied-port and listener-PID "
          "guards each block their failure AND each mutant proves the guard is load-bearing.")
    return 0


@contextlib.contextmanager
def _http_squatter():
    """A live HTTP server holding a port — stands in for a stale golden that answers."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ucp":{"version":"stale"}}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _listener_pid(port):
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                       capture_output=True, text=True)
    out = r.stdout.strip().splitlines()
    return int(out[0]) if out else None


def _kill(pid):
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
validate_schema_oracle_manifest.py — the PER-VERSION ORACLE gate (D4-04 / B6, decision 7b).

The schema-validation oracle is one `ucp-schema` build PER LAYOUT, declared in
conformance/ci/oracle_manifest.json (destined for SOURCES.lock `schema_validator.per_version`;
lane D4 may not edit the lock, so the manifest is cross-checked against it here). This gate
proves the manifest is honest and that the checks never lean on a blind spot:

  manifest   every entry: 40-hex `commit`, `merged: true` (a PR build is never pinned),
             `vendor_dir`/`bin`/`bin_version`/`layout`/`known_blind_spots`/`verdict_diff`;
             aliases resolve (no cycle); the 2026-04-08 entry == SOURCES.lock
             schema_validator.commit (the lock stays the single source for the pinned build);
             the 2026-08-25 build has NO known blind spot.
  vendor     .vendor/<vendor_dir> is checked out at `commit` and `<bin> --version` equals
             `bin_version` (a swapped binary is refused even when the checkout is right).
             Absent dir/binary -> rc 2 (honest skip; CI materializes both via fetch_sources.sh).
  self-root  `--scan-selfroot`: every schema file carrying `"$ref": "#"` per layout (the
             ucp-schema#43/#45 blind-spot class: 2 at 08-25, 1 at 04-08, 1 at 01-23, 0 at 01-11).
  call sites every `--def` call site in the checks (schema_check_04_08* CHECKS + controls,
             golden_check_08_25 `_oracle_against`, the battery's defects_config oracle blocks)
             resolved against its version's binary must not hit a `known_blind_spots` pointer
             unless the dual-oracle referee judges that (schema, def) pair at that version.
  boot guard `--boot-guard <version>` (serve_golden_0825.sh, golden_check_08_25.py): prints
             `✓ oracle <sha8> matches manifest` or exits 3 with `oracle SHA mismatch: ...`.

Exit: 0 PASS · 1 a finding · 2 cannot verify (vendor dir / binary absent) · 3 boot-guard refusal.
--selftest is hermetic (synthetic manifests + a stub verdict runner) and kill-proves each net.
"""
import argparse, json, os, pathlib, re, subprocess, sys, tempfile, copy

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "ci"))

import schema_oracle as so  # noqa: E402

MANIFEST = so.MANIFEST
LOCK = ROOT / "conformance" / "SOURCES.lock.json"
VENDOR = so.VENDOR
DEFECTS_CONFIG = ROOT / "conformance" / "testbed" / "golden-0825" / "server" / "defects_config.json"
SELFROOT_RE = re.compile(r'"\$ref"\s*:\s*"#"')
EXPECTED_SELFROOT = {"2026-08-25": 2, "2026-04-08": 1, "2026-01-23": 1, "2026-01-11": 0}
REQUIRED_FIELDS = ("commit", "ref", "merged", "vendor_dir", "bin", "bin_version", "layout",
                   "known_blind_spots", "verdict_diff")


# ---------------------------------------------------------------------------
# manifest structure
# ---------------------------------------------------------------------------
def check_manifest(manifest, lock_commit):
    """Structural findings (list[str]) for a manifest dict against the lock's pinned commit."""
    f = []
    pv = manifest.get("per_version") or {}
    if not pv:
        return ["manifest has no per_version block"]
    for v, e in pv.items():
        if "alias_of" in e:
            try:
                so.manifest_entry(v, manifest)
            except so.OracleUnavailable as ex:
                f.append(f"{v}: {ex}")
            continue
        for k in REQUIRED_FIELDS:
            if k not in e:
                f.append(f"{v}: missing field {k!r}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(e.get("commit", ""))):
            f.append(f"{v}: commit {e.get('commit')!r} is not a 40-hex SHA")
        if e.get("merged") is not True:
            f.append(f"{v}: entry is not `merged: true` — only merged SHAs or tags may be pinned "
                     f"(decision 7b); a PR build is never an oracle")
        if not isinstance(e.get("known_blind_spots"), list):
            f.append(f"{v}: known_blind_spots must be a list")
    e0408 = pv.get("2026-04-08")
    if e0408 and "alias_of" not in e0408 and lock_commit and e0408.get("commit") != lock_commit:
        f.append(f"2026-04-08: manifest commit {str(e0408.get('commit'))[:12]} != SOURCES.lock "
                 f"schema_validator.commit {lock_commit[:12]} — the lock is the single source for "
                 f"the pinned build; re-derive one of them")
    e0825 = pv.get("2026-08-25")
    if e0825 and "alias_of" not in e0825 and e0825.get("known_blind_spots"):
        f.append(f"2026-08-25: the merged-main build must have NO known blind spot "
                 f"(got {e0825['known_blind_spots']}) — that is the reason for the split")
    return f


def lock_commit(lock=LOCK):
    try:
        return json.loads(pathlib.Path(lock).read_text())["schema_validator"]["commit"]
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# vendor identity (checkout SHA + binary fingerprint)
# ---------------------------------------------------------------------------
def vendor_identity(entry, vendor=VENDOR):
    """(status, detail): status in {ok, sha-mismatch, bin-mismatch, absent}."""
    d = vendor / entry["vendor_dir"]
    if not (d / ".git").exists() and not (d / "HEAD").exists():
        return "absent", f".vendor/{entry['vendor_dir']} absent (run conformance/ci/fetch_sources.sh)"
    r = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True)
    have = r.stdout.strip()
    if have != entry["commit"]:
        return "sha-mismatch", (f".vendor/{entry['vendor_dir']} at {have[:12] or '?'} but the manifest "
                                f"pins {entry['commit'][:12]}")
    b = vendor / entry["bin"]
    if not b.exists():
        return "absent", f"{b} not built (cargo build --release --manifest-path {d}/Cargo.toml)"
    r = subprocess.run([str(b), "--version"], capture_output=True, text=True)
    ver = (r.stdout + r.stderr).strip().splitlines()[:1]
    ver = ver[0] if ver else ""
    if ver != entry["bin_version"]:
        return "bin-mismatch", (f"{b} reports {ver!r} but the manifest fingerprints "
                                f"{entry['bin_version']!r} — swapped or stale binary")
    return "ok", f"oracle {entry['commit'][:8]} matches manifest ({ver})"


def boot_guard(version, manifest=None, vendor=VENDOR):
    """rc 0 + '✓ oracle <sha8> matches manifest', or rc 3 + 'oracle SHA mismatch: ...'."""
    try:
        e = so.manifest_entry(version, manifest)
    except so.OracleUnavailable as ex:
        return 3, f"oracle SHA mismatch: {ex}"
    st, detail = vendor_identity(e, vendor)
    if st == "ok":
        return 0, f"✓ oracle {e['commit'][:8]} matches manifest"
    return 3, f"oracle SHA mismatch: {detail}"


# ---------------------------------------------------------------------------
# self-root scan + --def call sites
# ---------------------------------------------------------------------------
def scan_selfroot(base):
    """Sorted schema-relative paths under <base>/schemas whose text carries `"$ref": "#"`."""
    base = pathlib.Path(base)
    out = []
    for f in sorted((base / "schemas").rglob("*.json")):
        try:
            if SELFROOT_RE.search(f.read_text()):
                out.append(str(f.relative_to(base)))
        except Exception:  # noqa: BLE001
            continue
    return out


def def_call_sites():
    """{version: sorted set of (schema_rel, def_name)} for every `--def` call site the checks
    make: schema_check_04_08* CHECKS (+ controls naming another schema/def),
    golden_check_08_25 `_oracle_against(...)` literals, and the battery's defects_config
    oracle blocks. Mechanical (imports + literal scan), no hand list."""
    import glob, importlib
    sites = {"2026-04-08": set(), "2026-08-25": set()}
    for f in sorted(glob.glob(str(ROOT / "conformance" / "checks" / "schema_check_04_08*.py"))):
        mod = importlib.import_module(pathlib.Path(f).stem)
        for c in getattr(mod, "CHECKS", []) or []:
            if c.def_name:
                sites["2026-04-08"].add((c.schema_rel, c.def_name))
            for ctrl in c.controls:
                if len(ctrl) > 3 and ctrl[3]:
                    sites["2026-04-08"].add((ctrl[2], ctrl[3]))
    gc = (ROOT / "conformance" / "checks" / "golden_check_08_25.py").read_text()
    for m in re.finditer(r'_oracle_against\(\s*"([^"]+)",\s*"([^"]+)"', gc):
        sites["2026-08-25"].add((m.group(1), m.group(2)))
    for m in re.finditer(r'validate_against\([^,]+,\s*"([^"]+)",\s*"([^"]+)"', gc):
        sites["2026-08-25"].add((m.group(1), m.group(2)))
    if DEFECTS_CONFIG.exists():
        d = json.loads(DEFECTS_CONFIG.read_text())
        for arr in ("mutants", "self_referenced_mutants", "behavior_mutants"):
            for mut in d.get(arr, []) or []:
                o = mut.get("oracle") if isinstance(mut, dict) else None
                if o and o.get("schema") and o.get("def"):
                    sites["2026-08-25"].add((o["schema"], o["def"]))
    return {v: sorted(s) for v, s in sites.items()}


def referee_judged(version):
    """(schema_rel, def_name) pairs the dual-oracle gate compares at `version` (its corpus)."""
    import validate_dual_oracle as vdo
    vdo.set_version(version)
    try:
        items = vdo.agreement_corpus() + vdo.divergence_corpus()
    finally:
        vdo.set_version(vdo.DEFAULT_VERSION)
    return {(it.schema_rel, it.def_name) for it in items if it.def_name}


def blind_spot_hits(sites, manifest, judged):
    """Findings: a --def call site whose `<schema_rel>#/$defs/<def>` is a known blind spot of
    the binary serving that version, and which the referee does not judge there."""
    f = []
    for v, pairs in sites.items():
        e = so.manifest_entry(v, manifest)
        spots = set(e.get("known_blind_spots") or [])
        for schema_rel, def_name in pairs:
            ptr = f"{schema_rel}#/$defs/{def_name}"
            if ptr in spots and (schema_rel, def_name) not in judged.get(v, set()):
                f.append(f"{v}: --def call site {ptr} hits a known blind spot of the "
                         f"{e['vendor_dir']} build and the referee does not judge it")
    return f


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def run(scan_only=False):
    manifest = so.load_manifest()
    lines, findings, unavailable = [], [], []
    findings += check_manifest(manifest, lock_commit())
    bases = {v: so.SCHEMA_BASE[v] for v in EXPECTED_SELFROOT if so.SCHEMA_BASE.get(v)}
    scan = {}
    for v, base in bases.items():
        if not (pathlib.Path(base) / "schemas").is_dir():
            unavailable.append(f"{v}: schema base {base} not fetched")
            continue
        scan[v] = scan_selfroot(base)
        lines.append(f"  self-root {v}: {len(scan[v])} file(s) " + ", ".join(scan[v]))
        if len(scan[v]) != EXPECTED_SELFROOT[v]:
            findings.append(f"{v}: {len(scan[v])} self-root file(s), register expects "
                            f"{EXPECTED_SELFROOT[v]} (R24) — re-derive known_blind_spots")
    if scan_only:
        return (1 if findings else 0), lines + [f"scan-selfroot: {'FAIL' if findings else 'PASS'}"] + findings
    entries = {v: e for v, e in manifest["per_version"].items() if "alias_of" not in e}
    for v, e in entries.items():
        st, detail = vendor_identity(e)
        lines.append(f"  {v}: {e['vendor_dir']} @ {e['commit'][:8]} — {detail}")
        if st == "absent":
            unavailable.append(detail)
        elif st != "ok":
            findings.append(f"{v}: {detail}")
    try:
        sites = def_call_sites()
        judged = {v: referee_judged(v) for v in sites}
        hits = blind_spot_hits(sites, manifest, judged)
        findings += hits
        lines.append(f"  --def call sites: " + " · ".join(f"{v} {len(p)}" for v, p in sites.items())
                     + f" · blind-spot hits {len(hits)}")
    except Exception as ex:  # noqa: BLE001
        findings.append(f"call-site scan failed: {ex!r}")
    if findings:
        return 1, lines + [f"  ✗ {x}" for x in findings] + ["oracle-manifest: FAIL"]
    if unavailable:
        return 2, lines + [f"  · {x}" for x in unavailable] + [
            "oracle-manifest: SKIP — a per-version build is not materialized (fetch_sources.sh + cargo build)"]
    per = " · ".join(f"{v} {e['vendor_dir']}@{e['commit'][:8]} ({e['bin_version']})"
                     for v, e in entries.items())
    return 0, lines + [f"oracle-manifest: PASS — {per} · aliases "
                       f"{[v for v, e in manifest['per_version'].items() if 'alias_of' in e]}"]


# ---------------------------------------------------------------------------
# selftest (hermetic kill-tests)
# ---------------------------------------------------------------------------
def selftest():
    ok = True

    def case(tag, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'✓' if cond else '✗'} {tag}" + (f" — {detail}" if detail else ""))

    manifest = so.load_manifest()
    # (a) bin_for resolves per layout, aliases collapse onto the pinned build
    b = so.bin_for("2026-08-25", manifest)
    case("case a: bin_for('2026-08-25') resolves to the merged-main build",
         b.name == "ucp-schema" and "ucp-schema-0825" in str(b)
         and so.bin_for("2026-01-11", manifest) == so.bin_for("2026-04-08", manifest), str(b))
    # (b) an entry that is not merged is rejected
    m2 = copy.deepcopy(manifest); m2["per_version"]["2026-08-25"]["merged"] = False
    f = check_manifest(m2, lock_commit())
    case("case b: entry not `merged: true` rejected", any("not `merged: true`" in x for x in f), "; ".join(f)[:120])
    case("case b': the real manifest has 0 structural findings", not check_manifest(manifest, lock_commit()),
         "; ".join(check_manifest(manifest, lock_commit()))[:200])
    m3 = copy.deepcopy(manifest); m3["per_version"]["2026-04-08"]["commit"] = "0" * 40
    case("case b'': 04-08 commit != SOURCES.lock -> finding",
         any("SOURCES.lock" in x for x in check_manifest(m3, lock_commit())))
    m4 = copy.deepcopy(manifest); m4["per_version"]["2026-01-23"] = {"alias_of": "2026-01-11"}; m4["per_version"]["2026-01-11"] = {"alias_of": "2026-01-23"}
    case("case b''': alias cycle -> finding", any("cycle" in x for x in check_manifest(m4, lock_commit())))
    m5 = copy.deepcopy(manifest); m5["per_version"]["2026-08-25"]["known_blind_spots"] = ["x#/$defs/y"]
    case("case b'''': a blind spot on the 08-25 build -> finding",
         any("NO known blind spot" in x for x in check_manifest(m5, lock_commit())))
    # (c) the scan returns the two 08-25 self-root files
    base = so.SCHEMA_BASE["2026-08-25"]
    if (pathlib.Path(base) / "schemas").is_dir():
        got = scan_selfroot(base)
        case("case c: scan_selfroot(08-25 base) returns the two self-root files",
             got == ["schemas/common/types/constraint_expression.json",
                     "schemas/common/types/payment_instrument.json"], str(got))
    else:
        case("case c: 08-25 schema base absent — cannot scan", False)
    # (d) verdict-diff over a 3-item corpus reports `crash` for rc 134 (stub runner)
    import oracle_verdict_diff as ovd
    rcs = {"a": 0, "b": 1, "c": 134}
    cells = ovd.summarize({lbl: ovd.classify_rc(rc) for lbl, rc in rcs.items()})
    case("case d: verdict-diff on a 3-item corpus reports crash for rc 134",
         cells["crash"] == 1 and cells["valid"] == 1 and cells["invalid"] == 1
         and ovd.classify_rc(2) == "rc2", json.dumps(cells))
    # (e) blind-spot call-site net: a planted --def call site on the 04-08 blind spot, unjudged
    #     by the referee, is a finding; the same site judged by the referee is not
    spot = manifest["per_version"]["2026-04-08"]["known_blind_spots"][0]
    schema_rel, def_name = spot.split("#/$defs/")
    planted = {"2026-04-08": [(schema_rel, def_name)], "2026-08-25": []}
    case("case e: planted --def call site on a blind spot, unjudged -> finding",
         len(blind_spot_hits(planted, manifest, {"2026-04-08": set()})) == 1)
    case("case e': same site judged by the referee -> quiet",
         blind_spot_hits(planted, manifest, {"2026-04-08": {(schema_rel, def_name)}}) == [])
    # (f) boot guard: the real manifest passes when materialized; a swapped fingerprint exits 3
    e = manifest["per_version"]["2026-08-25"]
    st, _ = vendor_identity(e)
    if st == "ok":
        rc, line = boot_guard("2026-08-25", manifest)
        case("case f: boot guard on the real 08-25 build", rc == 0 and line.startswith("✓ oracle b52518f5 matches manifest"), line)
        m6 = copy.deepcopy(manifest); m6["per_version"]["2026-08-25"]["bin_version"] = "ucp-schema 1.4.0"
        rc, line = boot_guard("2026-08-25", m6)
        case("case f': swapped binary fingerprint -> exit 3", rc == 3 and line.startswith("oracle SHA mismatch"), line)
        m7 = copy.deepcopy(manifest); m7["per_version"]["2026-08-25"]["commit"] = "0" * 40
        rc, line = boot_guard("2026-08-25", m7)
        case("case f'': moved manifest commit -> exit 3", rc == 3 and "manifest pins 000000000000" in line, line)
    else:
        case("case f: boot guard (08-25 build not materialized here — skipped, not green)", False, _)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="per-version oracle manifest gate (D4-04)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scan-selfroot", action="store_true", help="list `\"$ref\": \"#\"` files per layout")
    ap.add_argument("--boot-guard", metavar="VERSION", help="rc 0 ✓ / rc 3 refuse (serve_golden_0825.sh)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.boot_guard:
        rc, line = boot_guard(a.boot_guard)
        print(line, file=sys.stdout if rc == 0 else sys.stderr)
        return rc
    rc, lines = run(scan_only=a.scan_selfroot)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())

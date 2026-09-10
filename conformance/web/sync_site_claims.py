#!/usr/bin/env python3
"""
sync_site_claims.py — the GENERATED half of public/site_claims.json (D5-05 / H1, RV2 T9).

The register mixes two kinds of data: hand-reviewed claims (text, evidence, review_by,
the `reviewed` stamps) and product-derived blocks that used to be hand-copied — the
`manifest` (merchant/agent counts, supported versions) and `evidence.per_version` (the
honest evidence-class split). This writer regenerates ONLY the derived blocks from the
engine and NEVER touches `reviewed`, `review_by`, claim text or evidence prose — a human
reviews claims; a machine copies numbers.

  sync_site_claims.py --check [--file F]   # byte-compare: exit 1 naming every drifted field (preflight, deploy.sh step 2, run_suite gate)
  sync_site_claims.py --write [--file F]   # rewrite the derived blocks in place

Sources (the same techniques the gates use, never a second opinion):
  manifest.merchant_checks   MCheck count over conformance/checks/merchant_checks*.py (coverage_gate)
  manifest.agent_checks/agent_defects   len(agent_checks.CHECKS) / non-None DEFECTS (agent_governance)
  manifest.versions          versions whose rule-R-a state is `live` (site_gates._expected_state; supported = live only)
  evidence.per_version       {"check", **evidence_breakdown} per version from a fresh matrix export (validate_evidence_class)
"""
import argparse, glob, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "public" / "site_claims.json"
sys.path.insert(0, str(ROOT / "conformance" / "ci"))
sys.path.insert(0, str(ROOT / "conformance" / "coverage"))


def serialize(doc):
    """The register's canonical serialization (indent 2, ASCII-escaped) — byte-stable."""
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def derived_blocks():
    """(manifest_without_reviewed, per_version) from the engine."""
    import site_gates                       # noqa: E402  (stdlib-only module)
    import matrix                           # noqa: E402
    merchant = 0
    for f in glob.glob(str(ROOT / "conformance" / "checks" / "merchant_checks*.py")):
        merchant += len(re.findall(r"^    MCheck\(", open(f).read(), re.M))
    r = subprocess.run([sys.executable, "-c",
                        "import sys,json;sys.path.insert(0,'conformance/agent');"
                        "import agent_checks,reference_agent;"
                        "print(json.dumps({'agent_checks':len(agent_checks.CHECKS),"
                        "'agent_defects':len([k for k in reference_agent.DEFECTS if k])}))"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"agent registry import failed: {r.stderr[-300:]}")
    ag = json.loads(r.stdout)
    export = matrix.export_json()
    versions = export["versions"]
    live = sorted(v for v, d in versions.items() if site_gates._expected_state(d)[0] == "live")
    manifest = {"merchant_checks": merchant, "agent_checks": ag["agent_checks"],
                "agent_defects": ag["agent_defects"], "versions": live}
    per_version = {v: {"check": d["check"], **d["evidence_breakdown"]} for v, d in versions.items()}
    return manifest, per_version


def synced(doc, manifest, per_version):
    """A copy of `doc` with the derived blocks replaced; every other byte untouched."""
    out = json.loads(json.dumps(doc))                      # deep copy
    m = out.setdefault("manifest", {})
    reviewed = m.get("reviewed")
    m.clear(); m.update(manifest)
    if reviewed is not None:
        m["reviewed"] = reviewed
    ev = out.setdefault("evidence", {})
    ev["per_version"] = per_version                        # _about / reviewed untouched
    return out


def drift(doc, manifest, per_version):
    fails = []
    m = doc.get("manifest") or {}
    for k, v in manifest.items():
        if m.get(k) != v:
            fails.append(f"manifest.{k}: registered {m.get(k)!r}, product says {v!r}")
    for k in m:
        if k not in manifest and k != "reviewed":
            fails.append(f"manifest.{k}: not a product field (remove)")
    ev = (doc.get("evidence") or {}).get("per_version") or {}
    for ver, want in per_version.items():
        if ev.get(ver) != want:
            fails.append(f"evidence.per_version['{ver}']: registered {ev.get(ver)!r}, fresh export says {want!r}")
    for ver in ev:
        if ver not in per_version:
            fails.append(f"evidence.per_version['{ver}']: version unknown to the engine")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(DEFAULT))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="byte-compare (default)")
    g.add_argument("--write", action="store_true", help="rewrite the derived blocks in place")
    a = ap.parse_args()
    path = pathlib.Path(a.file)
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    manifest, per_version = derived_blocks()
    if a.write:
        path.write_text(serialize(synced(doc, manifest, per_version)), encoding="utf-8")
        print(f"site_claims: manifest + evidence.per_version rewritten from the engine -> {os.path.relpath(path, ROOT)}")
        return 0
    fails = drift(doc, manifest, per_version)
    if not fails and raw != serialize(synced(doc, manifest, per_version)):
        fails.append("file is not in the canonical serialization (indent 2, ASCII) — run --write")
    for f in fails:
        print(f"  x {f}")
    if fails:
        print(f"site_claims: OUT OF SYNC ({len(fails)} field(s)) — run sync_site_claims.py --write")
        return 1
    print("site_claims: manifest + evidence in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())

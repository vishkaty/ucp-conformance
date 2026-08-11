#!/usr/bin/env python3
"""
checkset_manifest.py — the STRICT area-module loader shared by the version suite runners.

A suite runner (run_04_08.py; and the same class in run_01_23.py) aggregates its
`checks/area_*.py` modules. The naive `try: import; except: print+continue` pattern is a
silent integrity hole: a syntax error in one area module drops its checks from the run
while the gate still exits 0, and the coverage matrix keeps claiming the ids are covered
(P0-3). "Green" then no longer means "every kill-test ran and could fail."

This loader closes that by holding the module set to a committed MANIFEST (a lock, in the
TEST-INTEGRITY.md sense: it changes only deliberately). Loading RAISES AreaManifestError,
never continues, on any of:
  * a manifest-listed module MISSING from disk (deleted / renamed away),
  * a module that FAILS to import (syntax/import error) — the P0-3 case,
  * a module whose CHECKS count != the manifest's expected count (vanished / shrunk /
    grew — a check silently added or removed without updating the lock),
  * an on-disk area module (matching the glob, minus `exclude_prefixes`) NOT in the
    manifest (a silent addition that would never run),
  * the CORE checkset (v2026_xx.CHECKS, passed as `core_checks`) drifting from the
    manifest's `core_checks` — in particular an EMPTIED core module, which the area
    lock alone did not cover: the areas still loaded and ran, so run_xx stayed exit 0
    while 12 (01-23) / 1 (04-08) core kill-tests silently vanished (P0-4). The
    combined `expected_total` (core + all areas) is enforced too as a belt-and-braces
    cross-check of the manifest's own internal arithmetic.

`exclude_prefixes` lets run_01_23 glob the broad `area_*.py` family while excluding the
04-08 fixture modules (`area_04_08_*`), which belong solely to suite-04-08 — pulling them
into the 01-23 run pollutes its report and, since they need the schema oracle, would even
turn suite-01-23 red purely from the leak when the oracle is unavailable.

The loader's own module name never starts with `area_`, so it is never mistaken for a
check module. On a clean tree each manifest matches its modules exactly — no false red.
"""
import glob
import importlib
import json
import pathlib


class AreaManifestError(RuntimeError):
    """Raised when the loaded area modules do not match the committed manifest."""


def load_manifest(path):
    """Read a manifest JSON: {"areas": {"<module_stem>": <expected CHECKS count>, ...}}."""
    data = json.load(open(path))
    return dict(data["areas"])


def load_core_count(path):
    """The manifest's declared CORE checkset count (len(v2026_xx.CHECKS)). Raises
    KeyError if absent — every checkset manifest MUST declare `core_checks` so an
    emptied/shrunk/grown core module reds deliberately, exactly like the area counts
    (P0-4). Kept a distinct field from `expected_total` so the two enforce independently."""
    return json.load(open(path))["core_checks"]


def load_expected_total(path):
    """The manifest's declared TOTAL (core + every area) check count, or None if the
    manifest does not declare `expected_total`. A cross-check of the manifest's own
    arithmetic against the actually-loaded set."""
    return json.load(open(path)).get("expected_total")


def load_area_checks(here, pattern, manifest, core_checks, exclude_prefixes=(),
                     expected_core=None, expected_total=None):
    """Return `list(core_checks)` + every manifest module's CHECKS, or raise
    AreaManifestError. `here` is the checks directory (Path); `pattern` the glob
    (e.g. "area_04_08_*.py"); `manifest` a {stem: expected_count} dict;
    `exclude_prefixes` stems to ignore when detecting unlisted on-disk modules
    (e.g. ("area_04_08_",) so run_01_23's broad glob does not flag 04-08 modules).

    `expected_core` (when not None) LOCKS the core checkset count: `len(core_checks)`
    must equal it or the load reds (P0-4 — an emptied/drifted core module must not stay
    green while its area siblings keep the run non-vacuous). `expected_total` (when not
    None) is a final cross-check that core+areas loaded to exactly the manifest's total.

    The module stem must be importable from sys.path (the runner inserts the checks
    dir before calling; the hermetic tests insert a temp dir)."""
    here = pathlib.Path(here)
    on_disk = {pathlib.Path(f).stem for f in glob.glob(str(here / pattern))}
    on_disk = {s for s in on_disk
               if not any(s.startswith(p) for p in exclude_prefixes)}
    expected = set(manifest)

    errors = []
    for stem in sorted(expected - on_disk):
        errors.append(f"manifest module '{stem}' is missing from disk "
                      f"(deleted/renamed?) — a vanished module cannot silently pass")
    for stem in sorted(on_disk - expected):
        errors.append(f"area module '{stem}' is on disk but NOT in the manifest — "
                      f"add it deliberately (name + expected CHECKS count)")

    core_list = list(core_checks)
    if expected_core is not None and len(core_list) != expected_core:
        errors.append(f"core checkset exports {len(core_list)} checks; manifest expects "
                      f"{expected_core} (P0-4: an emptied/shrunk/grown core module must "
                      f"not silently vanish while the areas keep the run non-vacuous — "
                      f"update the manifest deliberately if intended)")

    checks = list(core_list)
    for stem in sorted(expected & on_disk):
        try:
            mod = importlib.import_module(stem)
        except Exception as e:                      # noqa: BLE001 — any import failure reds
            errors.append(f"area module '{stem}' FAILED to import: {e!r}")
            continue
        n = len(list(getattr(mod, "CHECKS", []) or []))
        if n != manifest[stem]:
            errors.append(f"area module '{stem}' exports {n} checks; manifest expects "
                          f"{manifest[stem]} (update the manifest deliberately if intended)")
            continue
        checks += list(mod.CHECKS)

    # Belt-and-braces: the manifest's own core+areas arithmetic must match the loaded
    # set. Only meaningful once the per-module checks above are clean (otherwise a
    # dropped module already reds and the total would mismatch redundantly).
    if expected_total is not None and not errors and len(checks) != expected_total:
        errors.append(f"loaded {len(checks)} total checks; manifest expected_total is "
                      f"{expected_total} (manifest arithmetic drift — update deliberately)")

    if errors:
        raise AreaManifestError("; ".join(errors))
    return checks

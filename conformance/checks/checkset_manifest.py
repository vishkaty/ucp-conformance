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
    manifest (a silent addition that would never run).

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


def load_area_checks(here, pattern, manifest, core_checks, exclude_prefixes=()):
    """Return `list(core_checks)` + every manifest module's CHECKS, or raise
    AreaManifestError. `here` is the checks directory (Path); `pattern` the glob
    (e.g. "area_04_08_*.py"); `manifest` a {stem: expected_count} dict;
    `exclude_prefixes` stems to ignore when detecting unlisted on-disk modules
    (e.g. ("area_04_08_",) so run_01_23's broad glob does not flag 04-08 modules).

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

    checks = list(core_checks)
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

    if errors:
        raise AreaManifestError("; ".join(errors))
    return checks

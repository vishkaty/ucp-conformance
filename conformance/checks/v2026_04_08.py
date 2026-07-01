#!/usr/bin/env python3
"""
v2026_04_08.py — Phase 2 core conformance checks for spec 2026-04-08.

2026-04-08 has NO live reference server, so these are fixture-based: each check
validates a hand-built synthetic response through the official ucp-schema oracle,
and the engine's kill-rate corrupts the fixture to prove the check catches defects.
Additional areas load from area_04_08_*.py modules via run_04_08.py.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

SCOPE_STAMP = {
    "spec_version": "2026-04-08",
    "spec_commit": "a2d8bf0b8f5a6fc790f677899c2c7da0684fe33d",
    "tool": "spck.dev conformance (dev)",
    "methodology": "synthetic-fixture validated by the official ucp-schema oracle",
}
DISCLAIMER = ("Independent, unofficial tool. 2026-04-08 has no reference server; these "
             "checks validate synthetic fixtures against the pinned 04-08 schemas via "
             "the official ucp-schema validator. A pass is not proof of compliance.")

CHECKS = [
    fixture_check("catalog.search_response_schema", ["CAT-029"], "MUST", "2026-04-08",
                  "catalog_search.valid.json", "search", "response",
                  ["drop:products", "drop:ucp", "corrupt-json", "empty"]),
]

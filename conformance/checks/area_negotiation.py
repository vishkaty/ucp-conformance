#!/usr/bin/env python3
"""
area_negotiation.py — 2026-01-23 negotiation + REST transport-convention checks.

Covers the header/response-envelope MUSTs of the discovery-negotiation register
(requirements/2026-01-23/discovery-negotiation.json) that are testable against the
LIVE reference server and kill-rate-able with the engine's header/body mutations.

Each check evaluates a real discovery or create-checkout response and declares the
mutations that MUST break that requirement. The engine self-validates every check
by kill-rate (clean-pass + every mutant deviates) before it contributes to a verdict.

Requirements covered:
  NEG-019  REST responses MUST use Content-Type application/json  (discovery + create)
  NEG-012  Business MUST include the `ucp` field (version + active capabilities) in
           every response  (create response envelope: ucp.version + ucp.capabilities)
  NEG-017  Business MUST include the processing version in every response  (ucp.version)
  DISC-001 Service/capability names MUST use {reverse-domain}.{service}.{capability}
  NEG-016  Platform version > business version MUST yield an error (live suite: 400)

Probed live shapes (http://localhost:8182):
  discovery: top-level `version`, `services`, `capabilities`, `payment_handlers`;
             response header content-type: application/json.
  create   : 201, header content-type application/json, envelope carries `ucp`
             with `version` == "2026-01-23" and `capabilities` (dict).
  neg      : UCP-Agent version="2099-01-01" (> business 2026-01-23) -> HTTP 400.
"""
import re
from engine import Check, fetch, CLEAN, DEVIATION  # noqa: F401
import v2026_01_23 as core

# {reverse-domain}.{service}.{capability}: >= 3 dot-separated lowercase labels.
_RDN = re.compile(r"^[a-z0-9]+(\.[a-z0-9_]+){2,}$")


def _ct(r):
    """Case-insensitive Content-Type header lookup; '' if absent."""
    for k, v in r.headers.items():
        if k.lower() == "content-type":
            return v.lower()
    return ""


# ---- NEG-019: Content-Type application/json --------------------------------
def chk_ct_json_discovery(r):
    if r.status != 200:
        return DEVIATION
    return CLEAN if "application/json" in _ct(r) else DEVIATION


def chk_ct_json_create(r):
    if r.status not in (200, 201):
        return DEVIATION
    return CLEAN if "application/json" in _ct(r) else DEVIATION


# ---- NEG-012 / NEG-017: ucp field w/ version (+ capabilities) --------------
def chk_ucp_envelope(r):   # NEG-012
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    ucp = r.json.get("ucp")
    if not isinstance(ucp, dict):
        return DEVIATION
    if not isinstance(ucp.get("version"), str) or not ucp.get("version"):
        return DEVIATION
    return CLEAN if "capabilities" in ucp else DEVIATION


def chk_processing_version(r):   # NEG-017
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    ucp = r.json.get("ucp")
    if not isinstance(ucp, dict):
        return DEVIATION
    return CLEAN if isinstance(ucp.get("version"), str) and ucp.get("version") else DEVIATION


# ---- DISC-001: reverse-domain service + capability names -------------------
def chk_reverse_domain_names(r):
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    services = r.json.get("services")
    capabilities = r.json.get("capabilities")
    if not isinstance(services, dict) or not services:
        return DEVIATION
    if not isinstance(capabilities, dict) or not capabilities:
        return DEVIATION
    names = list(services.keys()) + list(capabilities.keys())
    return CLEAN if all(_RDN.match(n) for n in names) else DEVIATION


# ---- NEG-016: incompatible (higher) platform version -> error --------------
def f_incompatible_version(base):
    h = core._ucp_headers()
    h["UCP-Agent"] = 'profile="https://example.com/platform"; version="2099-01-01"'
    return fetch(base, "/checkout-sessions", "POST", core._create_payload(), h)


def chk_version_unsupported_400(r):   # NEG-016
    return CLEAN if r.status == 400 else DEVIATION


CHECKS = [
    # NOTE: the live server emits the header lowercased as `content-type`; hset is an
    # exact-key set (only hdrop is case-insensitive), so the mutation token must use
    # the server's actual casing to REPLACE it rather than add a duplicate key.
    Check("negotiation.content_type_json_discovery", ["NEG-019"], "MUST",
          core._discovery, chk_ct_json_discovery,
          ["hset:content-type=text/plain", "hdrop:Content-Type", "status:500"]),
    Check("negotiation.content_type_json_create", ["NEG-019"], "MUST",
          core._create, chk_ct_json_create,
          ["hset:content-type=text/plain", "hdrop:Content-Type", "status:500"]),
    Check("negotiation.ucp_envelope", ["NEG-012"], "MUST",
          core._create, chk_ucp_envelope,
          ["status:500", "drop:ucp", "drop:ucp.version", "drop:ucp.capabilities",
           "corrupt-json", "empty"]),
    Check("negotiation.processing_version", ["NEG-017"], "MUST",
          core._create, chk_processing_version,
          ["status:500", "drop:ucp", "drop:ucp.version", "corrupt-json", "empty"]),
    Check("negotiation.reverse_domain_names", ["DISC-001"], "MUST",
          core._discovery, chk_reverse_domain_names,
          ["status:500", "drop:services", "set:services={}",
           "drop:capabilities", "set:capabilities={}", "corrupt-json"]),
    Check("negotiation.version_unsupported_400", ["NEG-016"], "MUST",
          f_incompatible_version, chk_version_unsupported_400,
          ["status:200", "status:201"]),
]


if __name__ == "__main__":
    import sys
    from engine import run_check
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8182"
    for c in CHECKS:
        _, d = run_check(c, base)
        print(f"{c.id:42} clean={d['clean']!s:11} kills={d['kills']:6} "
              f"kill_safe={d['kill_safe']}"
              + (f"  survivors={d['survivors']}" if d.get("survivors") else ""))

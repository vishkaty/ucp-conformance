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

# Reviewed applicable versions for checks below that don't declare their own
# `versions=` — see area_fulfillment.py's identical marker for the full rationale
# (PLAN-0825 "Check conversion phase" doctrine; hand-curated, never a formula).
# negotiation.version_unsupported_error (NEG-001) also matches a 2026-08-25 id;
# its 08-25 shape is not yet reviewed, so it stays off this list until a deliberate
# per-check review adds 2026-08-25 explicitly.
VERSIONS = ("2026-01-11", "2026-01-23", "2026-04-08")

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


# ---- NEG-016 / NEG-001@04-08: incompatible (higher) platform version -> error
def f_incompatible_version(base):
    h = core._ucp_headers()
    h["UCP-Agent"] = 'profile="https://example.com/platform"; version="2099-01-01"'
    return fetch(base, "/checkout-sessions", "POST", core._create_payload(), h)


def chk_version_unsupported_error(r):
    """NEG-016 (01-era, overview.md#L1157): "the business MUST return
    version_unsupported error" — the register never pins an HTTP status. The
    2026-04-08 register renumbers the duty to NEG-001 and maps it to HTTP 422
    (overview.md#L699); the official 01-era suite asserted 400 (AMB-001: the spec
    is authoritative — accept both, they are each a 4xx). The old check asserted
    exactly 400, which was STRONGER than the cited register text and deviated on
    the conformant 04-08 reference (422 + VERSION_UNSUPPORTED envelope).
    Assert what the register asserts: an HTTP 4xx, and — when a messages[] error
    envelope is present — an error message whose code names version_unsupported
    (case-insensitive; the pinned reference emits "VERSION_UNSUPPORTED")."""
    if not (400 <= r.status < 500):
        return DEVIATION
    j = r.json if isinstance(r.json, dict) else {}
    msgs = j.get("messages")
    if isinstance(msgs, list) and msgs:
        return CLEAN if any(
            isinstance(m, dict) and m.get("type") == "error"
            and str(m.get("code", "")).lower() == "version_unsupported"
            for m in msgs) else DEVIATION
    return CLEAN


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
           "drop:capabilities", "set:capabilities={}", "corrupt-json"],
          # DISC-001@2026-04-08 names a DIFFERENT requirement (profile over HTTPS).
          # The 04-08 register carries the naming rule as OVR-001 (needs-receiver);
          # there the duty is graded structurally by the profile-schema check
          # (DISC-000). versions= makes the runner's served-version gate skip this
          # against a 2026-04-08 server instead of reporting a false deviation/
          # UNSAFE; it stays live for 01-era targets and is kill-validated against
          # the controlled 01-23/01-11 goldens via its merchant twin
          # (discovery.reverse_domain_names).
          versions=("2026-01-11", "2026-01-23")),
    Check("negotiation.version_unsupported_error", ["NEG-016"], "MUST",
          f_incompatible_version, chk_version_unsupported_error,
          # kills: a 2xx (the requirement is an ERROR), and a mangled error code on
          # the captured envelope (the requirement is THIS error). No drop:messages
          # mutant on purpose: an 01-era body legitimately carries no messages[].
          ["status:200", "status:201", 'set:messages.0.code="some_other_error"'],
          # NEG-016@01-era == NEG-001@2026-04-08 (renumbered; 422 mapping there) —
          # see chk_version_unsupported_error's citation trail + AMB-001.
          req_ids_map={"2026-04-08": ["NEG-001"], "2026-08-25": ["NEG-001"]}),
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

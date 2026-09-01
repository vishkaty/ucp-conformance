#!/usr/bin/env python3
"""
struct_check_08_25.py — 2026-08-25 structural/algorithmic checks that need neither a
live golden server nor the ucp-schema CLI oracle (pinned pre-#66 at 9b5c3206; crashes
on shipped 08-25 patterns — PLAN-0825 §B/G6; the oracle re-pin is a separate, blocked
workstream). These checks judge conformance with OUR OWN implementation of an
algorithm the released prose (or a vendored schema file) specifies exactly enough to
implement deterministically, proven against:

  (a) verbatim fixtures lifted from the spec's own worked examples (the strongest
      available anchor — an independent oracle for the DECISION, not just the code), and
  (b) the real vendored corpus at pin cd78fb38 (conformance/.vendor/ucp-2026-08-25),
      for the one row a schema file enforces directly (CAP-001).

EVIDENCE CLASS: self-referenced (PLAN-0825 §B/§E) — the judge is our own code, not an
independently-authored implementation or the official validator. This is the honest
evidence ceiling for a NEW algorithm with no live oracle; promotion happens only if/
when ucp-schema or an independent server exercises the same rule (tracked in
GAP-LEDGER-0825.md, not claimed here). Every check below is kill-tested in the
schema_check_04_08.py idiom: a `valid` fixture set that MUST be accepted and a
`negatives` set that MUST be rejected — the negative set IS the kill-rate proof
(P-1, no vacuous green).

Scope note (why these three areas and not others): the mission for this lane was to
convert register rows checkable WITHOUT the golden and WITHOUT the schema-oracle CLI.
The R5/R5a/R8/R9 carry-forward hazards in GAP-LEDGER-0825.md (dormant over-strict
predicates; signing_keys->keys[]; card_credential->pan/network_token) all live in
areas (signatures, payment, fulfillment, consent) whose 04-08 twin checks are
MCheck-idiom behavioral checks (merchant_checks_04_08_*.py) that fetch() a live
merchant/golden — out of scope here by construction, not overlooked. Nothing in this
file touches those areas; they wait for R11 (mutant battery) + the oracle re-pin
(items 11-13, ROADMAP-2026-08-31-unified.md) same as every other golden-reading area.

Areas converted (register: conformance/requirements/2026-08-25/):
  1. capability-namespace-authority.json — CAP-001, CAP-003..CAP-007 (the Authority
     Binding derivation algorithm; NEW at 08-25, wholly self-contained prose+schema).
  2. signals-attribution-eligibility.json / loyalty.json / actions.json — SAE-002,
     SAE-003, LOY-003, LOY-006, ACT-007 (the shared reverse-domain namespace pattern,
     read live from the vendored common/types/reverse_domain_name.json so the check
     tracks the schema rather than duplicating its regex — P-7).
  3. permalink.json — PERM-005, PERM-006, PERM-007, PERM-010, PERM-011, PERM-012 (the
     compact item-identifier encode/decode algorithm and the continue_to destination-
     preference validation algorithm; NEW capability at 08-25, no 04-08 equivalent).
  4. discovery.json — DISC-002, DISC-004, DISC-007, DISC-008 (P3 wave 3: the
     identity-resolution URL fetch-safety rules -- HTTPS-only, no-redirect-follow,
     and the SSRF/special-use-address guard -- each a pure algorithm over a URL/
     status/IP string, no live golden or oracle needed).

Wiring: run_suite.py gate "struct-check-08-25" (hermetic, needs=None — no server, no
oracle). Deliberately NOT wired into checkset_manifest.py / matrix.py / coverage
export / REGISTER_ONLY_VERSIONS: per the lane brief, these checks land EXECUTABLE and
kill-gated but UNATTRIBUTED — the coverage/site flip is a separate, owner-visible step.

Run:  python3 conformance/checks/struct_check_08_25.py
Exit 0 = every check clean-pass + kill-safe; 1 = a check failed or a mutant survived.
"""
import sys
import re
import json
import base64
import pathlib
import urllib.parse
import copy
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = ROOT / "conformance" / ".vendor" / "ucp-2026-08-25"

VERSION = "2026-08-25"
# Module-level VERSIONS marker (the same mechanism schema_check_04_08.py's filename
# token and merchant_checks_04_08_*.py's versions=V0408 use): matrix.py's attribution
# walk and verify_citations.py both read this to scope every check in this file to
# 2026-08-25 ONLY. Without it, a check whose filename carries no recognized version
# token defaults to ALL FOUR pinned versions (matrix._file_targets' fallback) — and
# at least one id here (SAE-003) is REUSED at 2026-04-08 for an unrelated requirement,
# which the citation-soundness gate correctly reds on (divergent requirement text
# under one id). None of these ids exist in the 01-11/01-23/04-08 registers as the
# SAME requirement, so 2026-08-25 is the only correct scope.
VERSIONS = ("2026-08-25",)

# Check(id, req_ids, fn, valid, negatives)
#   fn(*case) -> bool, where True means "our algorithm ACCEPTS/judges-conformant".
#   valid:     list of arg-tuples that MUST make fn return True.
#   negatives: list of arg-tuples that MUST make fn return False (the kill-rate proof:
#              a negative that fn still accepts is a surviving mutant).
Check = namedtuple("Check", "id req_ids fn valid negatives")


# =====================================================================================
# Area 1 — capability-namespace-authority (CAP-001, CAP-003..CAP-007)
# Source: docs/specification/overview/index.md#L845-950 (Authority Binding subsection)
#         + source/schemas/capability.json#L36-40 (business_schema), both @ cd78fb38.
# =====================================================================================

CAPABILITY_SCHEMA_PATH = VENDOR / "source" / "schemas" / "capability.json"
_REAL_CAPABILITY_DOC = json.loads(CAPABILITY_SCHEMA_PATH.read_text())


def _is_ip_literal(host):
    """IPv4 dotted-quad or an IPv6 literal (urlsplit's .hostname strips the brackets,
    so an IPv6 literal survives only as a colon-bearing string here)."""
    octets = host.split(".")
    if len(octets) == 4 and all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        return True
    if ":" in host:
        return True
    return False


def derive_authority_prefix(schema_url):
    """Derivation algorithm steps 1-3 (overview/index.md#L868-892). Returns
    (authority_prefix, None) on success or (None, reason) on rejection."""
    if not isinstance(schema_url, str) or not schema_url:
        return None, "empty/non-string schema URL"
    try:
        parts = urllib.parse.urlsplit(schema_url)
    except ValueError:
        return None, "unparsable URL"
    # Step 1: MUST parse, MUST use https, MUST NOT contain userinfo. Substring
    # matching on the raw URL is explicitly forbidden by the spec (the
    # ucp.dev@evil.example example) — we parse structurally instead.
    if parts.scheme != "https":
        return None, f"scheme is {parts.scheme!r}, not https"
    if "@" in parts.netloc:
        return None, "URL contains userinfo (user:pass@)"
    try:
        host = parts.hostname
    except ValueError:
        return None, "unparsable host"
    if not host:
        return None, "no host"
    # Step 2: registered domain name of >= 2 labels; IP-literal/single-label invalid.
    if _is_ip_literal(host):
        return None, "host is an IP literal"
    host = host.lower().rstrip(".")
    labels = [lbl for lbl in host.split(".") if lbl]
    if len(labels) < 2:
        return None, "host is a single label"
    # Step 3: normalize + reverse labels.
    return ".".join(reversed(labels)), None


def authority_binds(name, authority_prefix):
    """Step 4 (overview/index.md#L893-904): exact match, or a label-aligned prefix
    (the character immediately after authority_prefix in name is a '.')."""
    if name == authority_prefix:
        return True
    return name.startswith(authority_prefix + ".")


def _cap_authority_accepts(name, host):
    """Composed derivation+binding decision for a (name, schema-host) pair, used to
    replay the spec's own worked-example table (CAP-006/007)."""
    prefix, err = derive_authority_prefix(f"https://{host}/schema.json")
    if err:
        return False
    return authority_binds(name, prefix)


def validate_spec_url(url):
    """CAP-003: a `spec` URL MUST be https; MAY be served from any host — no
    authority check (overview/index.md#L868-871)."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https"


def business_schema_requires_schema(doc):
    """CAP-001: capability.json's business_schema.allOf MUST include a branch
    requiring 'schema' (source/schemas/capability.json#L36-40)."""
    node = (doc.get("$defs") or {}).get("business_schema")
    if not isinstance(node, dict):
        return False
    for branch in node.get("allOf") or []:
        if isinstance(branch, dict) and "schema" in (branch.get("required") or []):
            return True
    return False


def _mutant_capability_doc_without_schema_requirement():
    doc = copy.deepcopy(_REAL_CAPABILITY_DOC)
    node = doc["$defs"]["business_schema"]
    node["allOf"] = [b for b in node["allOf"] if "required" not in b]
    return doc


# CAP-006/007 worked-example table, VERBATIM from overview/index.md#L913-921 @ cd78fb38.
_BINDING_TABLE = [
    ("dev.ucp.shopping.checkout", "ucp.dev", True),
    ("dev.ucp.shopping.checkout", "shopping.ucp.dev", True),
    ("com.example.payments.installments", "example.com", True),
    ("com.example.pay", "pay.example.com", True),
    ("com.example.pay", "example.com", True),
    ("com.example.pay", "evil.example", False),
    ("dev.ucp.shopping.checkout", "evil.example", False),
    ("com.examplecorp.pay", "example.com", False),
    ("com.example.pay", "cdn.example.com", False),
]

CAP_CHECKS = [
    Check("capability.business_schema_requires_schema", ["CAP-001"],
          business_schema_requires_schema,
          [(_REAL_CAPABILITY_DOC,)],
          [(_mutant_capability_doc_without_schema_requirement(),),
           ({"$defs": {"business_schema": {}}},),
           ({},)]),
    Check("capability.spec_url_https_only_any_host", ["CAP-003"],
          validate_spec_url,
          [("https://docs.example.com/spec.json",),
           ("https://any-third-party-docs-host.example/x",)],
          [("http://example.com/spec.json",),
           ("ftp://example.com/spec.json",),
           ("",), (None,)]),
    # CAP-004 (step 1: parse/https/no-userinfo) and CAP-005 (step 2: registered
    # domain, >=2 labels, no IP-literal) share the composed parser; each is proven
    # by negatives drawn from ITS OWN step so a regression in either step is caught
    # by the check that names it.
    Check("capability.schema_url_parses_https_no_userinfo", ["CAP-004"],
          lambda url: derive_authority_prefix(url)[1] is None,
          [("https://ucp.dev/x.json",), ("https://shopping.ucp.dev/x.json",)],
          [("https://ucp.dev@evil.example/x.json",),   # the spec's own userinfo example
           ("http://ucp.dev/x.json",),                  # not https
           ("ucp.dev/x.json",),                         # no scheme at all
           ("",)]),
    Check("capability.schema_host_registered_domain_min_two_labels", ["CAP-005"],
          lambda url: derive_authority_prefix(url)[1] is None,
          [("https://ucp.dev/x.json",), ("https://example.com/x.json",)],
          [("https://localhost/x.json",),               # single label
           ("https://203.0.113.10/x.json",),             # IPv4 literal
           ("https://[2001:db8::1]/x.json",)]),          # IPv6 literal
    Check("capability.authority_prefix_derivation", ["CAP-006"],
          lambda url, want: derive_authority_prefix(url)[0] == want,
          [("https://ucp.dev/x.json", "dev.ucp"),
           ("https://shopping.ucp.dev/x.json", "dev.ucp.shopping"),
           ("https://UCP.DEV./x.json", "dev.ucp")],      # normalize: lowercase + strip trailing dot
          [("https://ucp.dev/x.json", "ucp.dev")]),       # NOT reversed — must fail equality
    Check("capability.name_binds_exact_or_label_aligned_prefix", ["CAP-007"],
          _cap_authority_accepts,
          [(n, h) for (n, h, want) in _BINDING_TABLE if want],
          [(n, h) for (n, h, want) in _BINDING_TABLE if not want]),
]


# =====================================================================================
# Area 2 — reverse-domain namespace key conformance (SAE-002, SAE-003, LOY-003,
# LOY-006, ACT-007)
# Source: source/schemas/common/types/reverse_domain_name.json (the pattern + its own
# `examples`, read LIVE from the vendored file so this check tracks the schema instead
# of duplicating its regex — P-7) + docs/specification/overview/index.md#L3322-3323,
# source/schemas/common/loyalty.json#L168, common/extensions/loyalty.md#L211,
# overview/index.md#L721, all @ cd78fb38.
# =====================================================================================

RDN_SCHEMA_PATH = VENDOR / "source" / "schemas" / "common" / "types" / "reverse_domain_name.json"
_RDN_DOC = json.loads(RDN_SCHEMA_PATH.read_text())
_RDN_PATTERN = _RDN_DOC["pattern"]
_RDN_EXAMPLES = _RDN_DOC.get("examples", [])
_RDN_RE = re.compile(_RDN_PATTERN)


def is_reverse_domain(key):
    if not isinstance(key, str) or not key:
        return False
    return _RDN_RE.fullmatch(key) is not None


def independent_programs_are_sibling_keys(loyalty_obj, program_keys):
    """LOY-006: independently-joinable programs MUST be modeled as separate SIBLING
    top-level keys in the loyalty map (not nested under one shared parent key), each
    reverse-domain-named (common/extensions/loyalty.md#L211)."""
    if not isinstance(loyalty_obj, dict) or not program_keys:
        return False
    for k in program_keys:
        if k not in loyalty_obj or not is_reverse_domain(k):
            return False
    return True


NAMESPACE_CHECKS = [
    Check("namespace.reverse_domain_pattern_from_vendored_schema", ["SAE-003"],
          is_reverse_domain,
          [(e,) for e in _RDN_EXAMPLES],   # the schema's OWN verbatim examples
          [("DEV.UCP.SHOPPING.CHECKOUT",),  # uppercase
           ("dev",),                        # single segment (needs >=2)
           ("-dev.ucp.checkout",),          # leading hyphen on first label
           ("dev.-ucp.checkout",),          # leading hyphen on later label
           ("dev.ucp.checkout-",),          # trailing hyphen
           ("dev..ucp",),                   # empty segment
           ("",)]),
    Check("namespace.signal_keys_reverse_domain", ["SAE-002"],
          is_reverse_domain,
          [("dev.ucp.shopping.free_shipping_eligible",),
           ("com.example.loyalty_tier",)],
          [("free_shipping_eligible",), ("Dev.Ucp.Signal",)]),
    Check("namespace.loyalty_keys_reverse_domain", ["LOY-003"],
          is_reverse_domain,
          [("dev.ucp.common.loyalty",), ("com.example.loyalty_gold",)],
          [("loyalty",), ("com_example_loyalty",)]),
    Check("namespace.action_type_keys_reverse_domain", ["ACT-007"],
          is_reverse_domain,
          [("dev.ucp.shopping.reorder",), ("com.example.custom_action",)],
          [("reorder",), ("dev ucp action",)]),
    Check("namespace.loyalty_independent_programs_sibling_keys", ["LOY-006"],
          independent_programs_are_sibling_keys,
          [({"dev.ucp.common.loyalty.gold": {"points": 100},
             "dev.ucp.common.loyalty.silver": {"points": 10}},
            ["dev.ucp.common.loyalty.gold", "dev.ucp.common.loyalty.silver"])],
          [  # nested under one shared parent key instead of top-level siblings
           ({"dev.ucp.common.loyalty": {"gold": {"points": 100},
                                        "silver": {"points": 10}}},
            ["dev.ucp.common.loyalty.gold", "dev.ucp.common.loyalty.silver"]),
           # present but not reverse-domain-named
           ({"gold": {"points": 100}}, ["gold"])]),
]


# =====================================================================================
# Area 3 — permalink compact item-identifier encoding + continue_to validation
# (PERM-005, PERM-006, PERM-007, PERM-010, PERM-011, PERM-012)
# Source: docs/specification/permalink.md#L178-220 (item identifiers),
#         permalink.md#L260-286 (continue_to), both @ cd78fb38. NEW capability at
# 08-25; no 04-08 equivalent existed.
# =====================================================================================

_RAW_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x00, 0x20)) | {chr(0x7F)}

# permalink.md#L200-208 @ cd78fb38: gid://shopify/ProductVariant/70881412 becomes
# /buy/~Z2lkOi8vc2hvcGlmeS9Qcm9kdWN0VmFyaWFudC83MDg4MTQxMg:1 — the spec's OWN worked
# answer, used verbatim as an independent oracle (not derived from our own encoder).
_GID_EXAMPLE = "gid://shopify/ProductVariant/70881412"
_GID_ENCODED_PER_SPEC = "~Z2lkOi8vc2hvcGlmeS9Qcm9kdWN0VmFyaWFudC83MDg4MTQxMg"


def encode_item_id(identifier):
    """permalink.md#L178-193: raw token grammar -> used directly; otherwise
    '~' + base64url_no_padding(utf8(identifier))."""
    if _RAW_TOKEN_RE.fullmatch(identifier):
        return identifier
    return "~" + base64.urlsafe_b64encode(identifier.encode("utf-8")).rstrip(b"=").decode("ascii")


def decode_item_id(token):
    """PERM-006 (permalink.md#L214-216): leading '~' -> decode base64url without
    padding; reject non-canonical base64url, reject a decoded value that is not
    valid UTF-8 or that contains control characters. Returns (identity, err)."""
    if not token.startswith("~"):
        return token, None
    remainder = token[1:]
    if not re.fullmatch(r"[A-Za-z0-9_-]*", remainder):
        return None, "non-canonical base64url characters"
    pad = (-len(remainder)) % 4
    try:
        raw = base64.urlsafe_b64decode(remainder + "=" * pad)
    except Exception:
        return None, "invalid base64url"
    # Canonical-form check: re-encoding must reproduce the same remainder (catches
    # non-canonical trailing bits a naive decoder would silently accept).
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != remainder:
        return None, "non-canonical base64url (re-encode mismatch)"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "decoded value is not valid UTF-8"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        return None, "decoded value contains control characters"
    return text, None


def _decode_accepts(token):
    return decode_item_id(token)[1] is None


def _decoded_matches(raw, tilde_token):
    """PERM-007 (permalink.md#L218): the raw and ~-encoded forms of an identifier
    MUST resolve to the same decoded identity."""
    got, err = decode_item_id(tilde_token)
    return err is None and got == raw


def validate_continue_to(raw_value):
    """permalink.md#L272-282, steps 1-2 (the STATIC, origin-independent rejection
    rules — steps 3-5 are origin-scoped and covered separately by safe_location's
    fallback + no-reflection behavior below). Returns (decoded_path, None) or
    (None, reason)."""
    try:
        decoded = urllib.parse.unquote(raw_value, errors="strict")
    except Exception:
        return None, "percent-decode failed"
    if not decoded.startswith("/") or decoded.startswith("//"):
        return None, "does not start with a single '/'"
    if "\\" in decoded:
        return None, "contains a backslash"
    if any(c.isspace() for c in decoded):
        return None, "contains whitespace"
    if any(c in _CONTROL_CHARS for c in decoded):
        return None, "contains a control character"
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", decoded):
        return None, "contains a URL scheme"
    return decoded, None


def _continue_to_accepts(raw_value):
    return validate_continue_to(raw_value)[1] is None


def safe_location(raw_value, default):
    """PERM-011/012 (permalink.md#L282-286): a Business MUST fall back to its
    default destination for any continue_to value that fails validation, and MUST
    NOT reflect the raw value."""
    decoded, err = validate_continue_to(raw_value)
    return default if err else decoded


PERMALINK_CHECKS = [
    Check("permalink.item_id_raw_token_passthrough", ["PERM-005"],
          lambda raw: encode_item_id(raw) == raw,
          [("sku_123",), ("A1.b_2-c",)],
          [("has space",), ("gid://x/y",), ("slash/here",)]),
    Check("permalink.item_id_tilde_base64url_encoding", ["PERM-005"],
          lambda raw, want: encode_item_id(raw) == want,
          [(_GID_EXAMPLE, _GID_ENCODED_PER_SPEC)],
          [(_GID_EXAMPLE, "~" + urllib.parse.quote(_GID_EXAMPLE, safe="")),  # percent-encoding forbidden
           (_GID_EXAMPLE, _GID_EXAMPLE)]),                                  # raw pass-through forbidden
    Check("permalink.item_id_tilde_decode_rejects_malformed", ["PERM-006"],
          _decode_accepts,
          [(_GID_ENCODED_PER_SPEC,), ("sku_123",)],
          [("~not valid base64 with spaces",),
           ("~" + base64.urlsafe_b64encode(b"\x00\x01control").rstrip(b"=").decode(),),
           ("~AAAA====",)]),
    Check("permalink.item_id_raw_and_tilde_forms_agree", ["PERM-007"],
          _decoded_matches,
          [("sku_123", "~" + base64.urlsafe_b64encode(b"sku_123").rstrip(b"=").decode()),
           (_GID_EXAMPLE, _GID_ENCODED_PER_SPEC)],
          [("sku_123", "~" + base64.urlsafe_b64encode(b"different").rstrip(b"=").decode()),
           ("sku_123", "~not-valid-base64!!!")]),
    Check("permalink.continue_to_static_rejection_rules", ["PERM-010"],
          _continue_to_accepts,
          [("/collections/spring",), ("/a/b/c",)],
          [("//evil.example/phish",),      # protocol-relative
           ("https://evil.example/x",),     # absolute URL with scheme
           ("/a\\b",),                      # backslash
           ("/a\tb",),                      # whitespace/control (TAB)
           ("relative/no/leading/slash",),  # no leading '/'
           ("/a%0d%0ab",)]),                # percent-encoded CRLF -> control chars post-decode
    Check("permalink.continue_to_fallback_no_reflection", ["PERM-011", "PERM-012"],
          lambda raw, default, want: safe_location(raw, default) == want,
          [("/ok/path", "/default", "/ok/path"),                  # valid -> passthrough
           ("https://evil.example/x", "/default", "/default"),     # invalid -> falls back
           ("//evil.example/x", "/default", "/default")],
          [("https://evil.example/x", "/default", "https://evil.example/x")]),  # must NOT reflect raw
    Check("permalink.location_header_no_control_chars", ["PERM-011"],
          lambda dest: not any(c in _CONTROL_CHARS for c in dest),
          [("/collections/spring",), ("/buy/sku_123:1",)],
          [("/a\r\nSet-Cookie: evil=1",), ("/a\x00b",)]),
]


# =====================================================================================
# Area 4 — identity-resolution URL fetch-safety (DISC-002, DISC-004, DISC-007, DISC-008)
# Source: docs/specification/overview/index.md#L2283-2299 @ cd78fb38 -- the numbered
# fetch-safety rules list that binds "any URL dereferenced during identity resolution
# -- the profile, and any jwks_uri or CIMD document a verifier follows": rule 1 (DISC-
# 004's broadened HTTPS-only guard, carried-forward from the narrower 04-08 "profile
# URLs" wording), rule 2 (DISC-002's unchanged "Profile endpoints MUST NOT use
# redirects" + DISC-007's broadened verifier-side companion -- one algorithm, two
# register rows, same PERM-011/012-style combine precedent above), and rule 7
# (DISC-008's SSRF / special-use-address guard). Structural residue beyond wave 1:
# CAP-001..007 (wave 1) was the only capability-namespace area; these are the
# remaining pure algorithms in discovery.json that need neither a live golden nor the
# oracle CLI (DISC-001/003/005 are header/wire behaviors -- MCheck-idiom, out of
# scope here by the same construction struct_check's docstring already states for
# signatures/payment/fulfillment/consent).
# =====================================================================================

import ipaddress


def dereference_url_scheme_ok(url):
    """DISC-004 (rule 1): reject any identity-resolution URL (profile, jwks_uri,
    CIMD document) not served over https."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https"


def dereference_may_proceed(status_code):
    """DISC-002/DISC-007 (rule 2): a compliant implementation dereferencing a
    profile/jwks_uri/CIMD URL during identity resolution MUST NOT follow a 3xx
    redirect -- it treats the response as a rejection instead of re-issuing the
    request against Location. Returns True iff the response status permits the
    implementation to proceed (use the response), False iff it MUST reject
    (any 3xx, read literally per the rule's own unqualified "redirects (3xx)")."""
    code = int(status_code)
    return not (300 <= code < 400)


def dereference_target_allowed(ip_str, verifier_is_loopback=False):
    """DISC-008 (rule 7): reject URLs that resolve to special-use addresses (RFC
    6890 -- loopback, link-local including the cloud-metadata address
    169.254.169.254, private, and other reserved ranges), EXCEPT a loopback
    target when the verifier itself runs on the same loopback interface (local
    development). Python's ipaddress.is_global is the released rule's complement
    for MOST special-use ranges (False for loopback/link-local/private/reserved/
    unspecified) but NOT multicast (a multicast address is still `is_global`
    in this stdlib's own classification -- verified empirically, not assumed --
    so it needs an explicit exclusion; multicast is squarely "other reserved
    ranges" under RFC 6890 and MUST be rejected same as the rest). Fail CLOSED
    (reject) on an unparsable address rather than fail open."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if addr.is_loopback:
        return bool(verifier_is_loopback)
    return addr.is_global and not addr.is_multicast


DISCOVERY_FETCH_SAFETY_CHECKS = [
    Check("discovery.dereference_url_https_only", ["DISC-004"],
          dereference_url_scheme_ok,
          [("https://business.example/.well-known/ucp",),
           ("https://issuer.example/jwks.json",)],
          [("http://business.example/.well-known/ucp",),   # not https
           ("ftp://business.example/x",),                   # not https
           ("business.example/.well-known/ucp",),           # no scheme at all
           ("",), (None,)]),
    Check("discovery.dereference_no_redirect_follow", ["DISC-002", "DISC-007"],
          dereference_may_proceed,
          [(200,), (404,), (500,), (299,), (400,)],           # non-3xx -> may proceed
          [(300,), (301,), (302,), (303,), (304,), (307,), (308,), (399,)]),  # 3xx -> MUST reject
    Check("discovery.dereference_target_not_special_use", ["DISC-008"],
          dereference_target_allowed,
          [("8.8.8.8",), ("1.1.1.1",), ("2001:4860:4860::8888",),
           ("127.0.0.1", True)],                              # loopback exception: verifier on loopback
          [("127.0.0.1",),                                    # loopback, verifier NOT on loopback
           ("127.0.0.1", False),
           ("169.254.169.254",),                               # cloud-metadata address, named in the rule
           ("169.254.1.1",),                                   # link-local
           ("10.0.0.1",), ("192.168.1.1",), ("172.16.0.5",),   # RFC 1918 private
           ("::1",),                                           # IPv6 loopback, no exception granted
           ("fc00::1",),                                       # IPv6 unique-local (reserved)
           ("224.0.0.1",),                                     # multicast
           ("not-an-ip",)]),                                   # unparsable -- fail closed
]


CHECKS = CAP_CHECKS + NAMESPACE_CHECKS + PERMALINK_CHECKS + DISCOVERY_FETCH_SAFETY_CHECKS


def run():
    """Run every check; return [(check, killed_all, detail)]. killed_all = every
    valid case accepted AND every negative case rejected (the kill-rate proof)."""
    results = []
    for c in CHECKS:
        try:
            valid_ok = [bool(c.fn(*case)) for case in c.valid]
            neg_ok = [bool(c.fn(*case)) for case in c.negatives]   # True = mutant SURVIVED
        except Exception as e:                                     # noqa: BLE001
            results.append((c, False, f"error: {e!r}"))
            continue
        surviving = sum(1 for x in neg_ok if x)
        rejected_valid = sum(1 for x in valid_ok if not x)
        killed_all = rejected_valid == 0 and surviving == 0
        detail = ("clean-pass + kill-safe" if killed_all
                  else f"{rejected_valid}/{len(valid_ok)} valid cases REJECTED, "
                       f"{surviving}/{len(neg_ok)} mutants SURVIVED")
        results.append((c, killed_all, detail))
    return results


def main():
    res = run()
    allok = True
    for c, ok, detail in res:
        print(f"  {'✓' if ok else '✗'} {c.id} ({','.join(c.req_ids)}): {detail}")
        allok = allok and ok
    print("PASS" if allok else "FAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())

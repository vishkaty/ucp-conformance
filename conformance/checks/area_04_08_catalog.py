#!/usr/bin/env python3
"""
area_04_08_catalog.py — 2026-04-08 fixture-based conformance checks for the CATALOG
LOOKUP and PAGINATION schema MUSTs, validated through the official ucp-schema oracle.

Scope note: the search_response required[] (CAT-029) is already covered by
v2026_04_08.py::catalog.search_response_schema and is NOT duplicated here. The
pagination `has_next_page`/`cursor` if/then rule (CAT-001/002/003) is structurally
enforced ONLY on search_response (pagination is a property of search_response, not
lookup_response), so its check uses op "search" but a distinct fixture + mutations
that exercise the cross-field constraint the existing check does not.

Requirements cited from conformance/requirements/2026-04-08/catalog.json (CAT-*):
  CAT-001/002/003  pagination.response: has_next_page required; has_next_page=true => cursor
  CAT-017/018      lookup_variant allOf requires non-empty `inputs` (minItems 1)
  CAT-028          lookup ids required (request-side; not response-testable — see note)
  CAT-030          lookup_response required[]: ucp, products
  CAT-031          get_product_response required[]: ucp, product
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

_V = "2026-04-08"
_LOOKUP = "catalog_lookup_response.valid.json"
_GETPRODUCT = "catalog_getproduct_response.valid.json"
_PAGE = "catalog_search_pagination.valid.json"

CHECKS = [
    # CAT-001/002/003 — pagination.response cross-field rule. The valid fixture carries
    # has_next_page:true WITH cursor. drop:pagination.cursor violates the if/then
    # (has_next_page const true => required cursor); drop:pagination.has_next_page
    # violates the base required[]. Pagination lives on search_response only, so op is
    # "search" — but this is the pagination constraint, not the search required[] check.
    fixture_check("catalog.pagination_next_page_requires_cursor",
                  ["CAT-001", "CAT-002", "CAT-003"], "MUST", _V,
                  _PAGE, "search", "response",
                  ["drop:pagination.cursor", "drop:pagination.has_next_page",
                   "corrupt-json", "empty"]),

    # CAT-030 — lookup_response MUST include ucp and products.
    fixture_check("catalog.lookup_response_required_fields", ["CAT-030"], "MUST", _V,
                  _LOOKUP, "lookup", "response",
                  ["drop:ucp", "drop:products", "corrupt-json", "empty"]),

    # CAT-017/018 — a variant present in a lookup response MUST carry a non-empty
    # `inputs` correlation array (lookup_variant allOf adds required inputs minItems:1).
    # drop removes it; set:[]=empty violates minItems:1; dropping the correlation's own
    # required `id` proves the entry is validated too (input_correlation requires id).
    fixture_check("catalog.lookup_variant_requires_inputs", ["CAT-017", "CAT-018"], "MUST", _V,
                  _LOOKUP, "lookup", "response",
                  ["drop:products.0.variants.0.inputs",
                   "set:products.0.variants.0.inputs=[]",
                   "drop:products.0.variants.0.inputs.0.id",
                   "corrupt-json", "empty"]),

    # CAT-031 — get_product_response MUST include ucp and a singular `product` object.
    fixture_check("catalog.getproduct_response_required_fields", ["CAT-031"], "MUST", _V,
                  _GETPRODUCT, "get_product", "response",
                  ["drop:ucp", "drop:product", "corrupt-json", "empty"]),
]

# CAT-028 (lookup ids array required, minItems 1) is a REQUEST-side constraint
# (lookup_request.required=["ids"]); these fixture checks validate response bodies, so
# it is not exercised here. It would require a request-direction fixture/op.

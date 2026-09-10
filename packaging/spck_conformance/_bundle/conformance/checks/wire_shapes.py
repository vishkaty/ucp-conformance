#!/usr/bin/env python3
"""
wire_shapes.py — the ONE place that knows how a request body/header differs between
the reviewed spec versions (PLAN-v3 §2.1, D1-01/D1-02).

Every merchant check builds its requests through `shapes_for(ctx.version)`; the
per-version delta table below is the whole difference. Before this module,
merchant_checks._create_payload sent a 2026-04-08 `destinations[]` to every server —
at 2026-08-25 `shopping/types/fulfillment_destination.json` makes `type` a required
discriminator (`required: ["type","id"]`), so every fulfillment-bearing create 422'd on
golden-0825 and seven checks reported false deviations about a conformant server.

Fail-closed: a version outside SHAPE_VERSIONS raises ShapeUnsupported — an unreviewed
version never silently gets the 04-08 shape (the same doctrine as spec_versions.py).
Frozen: conformance/selfcheck/validate_wire_shapes.py pins the three older versions'
bodies byte-for-byte to what the checks sent before this module existed, so the 08-25
delta can never leak backwards. Kill-proof: gate `probe-shape-0825` (D1-04) goes red on
golden-0825 when the 08-25 delta is removed.

Interface (all builders read only ctx.version / ctx.config / ctx.product_id):
  shapes_for(version) -> Shapes
  Shapes.headers(idem=None, *, version_param=None, signer=None)
      the base UCP headers; `version_param` appends `; version="…"` to UCP-Agent;
      `signer(headers) -> dict` is the D3-23 hook — a callable (typically a closure
      over the request's method/path/body) returning headers to merge (e.g.
      Signature / Signature-Input / Content-Digest). None → no signing headers, ever.
  Shapes.checkout_create(ctx, with_fulfillment=False)
  Shapes.fulfillment_block(ctx, option_id=None, line_item_ids=("li_1",))
  Shapes.destinations(ctx)              08-25 adds `type: shipping_address`
  Shapes.complete_body(ctx)             the merchant's configured success payment
  Shapes.consent(purposes)              {name: bool} → booleans (≤04-08) | Purpose objects (08-25)
  Shapes.keys_field()                   "signing_keys" (≤04-08) | "keys" (08-25, SIG-008)
  Shapes.schema_path(logical)           relative schema path under the version's source/schemas
  Shapes.completion_ok(body)            CHK-025 branches (async accepted only at 08-25)
  Shapes.update_carries_id              top-level `id` on update requests (01-era only)
  Shapes.placeholders(ctx)              {"$DESTS","$FUL","$KEYS"} expanded by _expand_mut
"""
import json
import uuid

SHAPE_VERSIONS = ("2026-01-11", "2026-01-23", "2026-04-08", "2026-08-25")
AGENT_PROFILE = "https://spck.dev/agent"

_SCHEMA_PATHS = {
    "checkout": "shopping/checkout.json",
    "fulfillment_destination": "shopping/types/fulfillment_destination.json",
    "shipping_destination": "shopping/types/shipping_destination.json",
    "buyer_consent": "shopping/buyer_consent.json",
}


class ShapeUnsupported(ValueError):
    """The served version has no reviewed wire shape — refuse rather than guess."""


def base_headers(idem=None):
    """The version-independent request headers every check has always sent
    (merchant_checks._hdr's exact output; frozen by validate_wire_shapes.py)."""
    return {"UCP-Agent": f'profile="{AGENT_PROFILE}"', "request-signature": "test",
            "idempotency-key": idem or str(uuid.uuid4()), "request-id": str(uuid.uuid4()),
            "Content-Type": "application/json"}


class Shapes:
    def __init__(self, version, *, typed_destinations, async_completion, keys_field,
                 consent_objects, update_carries_id, schema_paths):
        self.version = version
        self._typed_destinations = typed_destinations
        self._async_completion = async_completion
        self._keys_field = keys_field
        self._consent_objects = consent_objects
        self.update_carries_id = update_carries_id
        self._schema_paths = schema_paths

    # ---- headers ---------------------------------------------------------------
    def headers(self, idem=None, *, version_param=None, signer=None):
        h = base_headers(idem)
        if version_param:
            h["UCP-Agent"] = f'profile="{AGENT_PROFILE}"; version="{version_param}"'
        if signer is not None:
            extra = signer(dict(h))
            if extra:
                h.update(extra)
        return h

    # ---- bodies ----------------------------------------------------------------
    def destinations(self, ctx):
        d = {"id": "d1", "address_country": "US"}
        # 08-25 sends the discriminator (belt-and-braces: fulfillment_destination.json
        # marks it ucp_request:optional — the business MUST default it, C3b/D3-04). The
        # probe-shape-0825 gate's omit mode (ctx.omit_destination_type) leaves it out.
        if self._typed_destinations and not getattr(ctx, "omit_destination_type", False):
            d["type"] = "shipping_address"
        return [d]

    def fulfillment_block(self, ctx, option_id=None, line_item_ids=("li_1",)):
        ids = list(line_item_ids)
        return {"methods": [{"id": "m1", "type": "shipping",
                             "destinations": self.destinations(ctx), "line_item_ids": ids,
                             "selected_destination_id": "d1",
                             "groups": [{"id": "g1", "line_item_ids": ids,
                                         "selected_option_id": option_id or "std"}]}]}

    def checkout_create(self, ctx, with_fulfillment=False):
        p = {"id": str(uuid.uuid4()), "currency": ctx.config.get("currency", "USD"),
             "line_items": [{"id": "li_1", "quantity": 1,
                             "item": {"id": ctx.product_id, "price": 1000}, "totals": []}],
             "payment": {"instruments": [], "handlers": ctx.config.get("payment_handlers", [])},
             "status": "incomplete", "ucp": {"version": ctx.version}, "totals": [], "links": []}
        if with_fulfillment:
            p["fulfillment"] = self.fulfillment_block(ctx)
        return p

    def complete_body(self, ctx):
        return ctx.config.get("complete_payment")

    def consent(self, purposes):
        """purposes: {short_name: granted}. ≤04-08 buyer_consent.json is a flat
        boolean map; 08-25 (buyer-consent-v2, CNST-001) keys reverse-DNS purposes to
        Purpose objects carrying granted + source (description is ucp_request:omit)."""
        if not self._consent_objects:
            return {k: bool(v) for k, v in purposes.items()}
        return {f"dev.ucp.consent.{k}": {"granted": bool(v), "source": "platform"}
                for k, v in purposes.items()}

    def keys_field(self):
        return self._keys_field

    def schema_path(self, logical):
        return self._schema_paths[logical]            # KeyError = unknown logical name

    # ---- predicates ------------------------------------------------------------
    def completion_ok(self, body):
        """CHK-025. Synchronous: status completed AND order present (every version).
        Asynchronous (08-25 only): status complete_in_progress AND no order."""
        if not isinstance(body, dict):
            return False
        status, order = body.get("status"), body.get("order")
        if status == "completed":
            return bool(order)
        if status == "complete_in_progress" and self._async_completion:
            return not order
        return False

    # ---- mutation placeholders -------------------------------------------------
    def placeholders(self, ctx):
        """Version-keyed JSON fragments for mutation strings, expanded by
        merchant_checks._expand_mut BEFORE $PRODUCT… (D1-02)."""
        return {"$DESTS": json.dumps(self.destinations(ctx)),
                "$FUL": json.dumps(self.fulfillment_block(ctx)),
                "$KEYS": self._keys_field}


def _old(version):
    return Shapes(version, typed_destinations=False, async_completion=False,
                  keys_field="signing_keys", consent_objects=False,
                  update_carries_id=version in ("2026-01-11", "2026-01-23"),
                  schema_paths=dict(_SCHEMA_PATHS))


_TABLE = {
    "2026-01-11": _old("2026-01-11"),
    "2026-01-23": _old("2026-01-23"),
    "2026-04-08": _old("2026-04-08"),
    # 2026-08-25 delta (each line cites the register row it serves):
    #   destinations[].type required — FUL-030 / fulfillment_destination.json L7-13
    #   async completion branch      — CHK-025 (checkout/index.md L1094-1103)
    #   keys[] at the profile top    — SIG-008 / profile.json L97-101
    #   Purpose objects              — CNST-001 / buyer_consent.json L11
    "2026-08-25": Shapes("2026-08-25", typed_destinations=True, async_completion=True,
                         keys_field="keys", consent_objects=True, update_carries_id=False,
                         schema_paths={**_SCHEMA_PATHS, "profile": "profile.json"}),
}


def shapes_for(version):
    if version not in _TABLE:
        raise ShapeUnsupported(f"no reviewed wire shape for spec version {version!r} "
                               f"(reviewed: {', '.join(SHAPE_VERSIONS)})")
    return _TABLE[version]

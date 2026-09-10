#!/usr/bin/env python3
"""
validate_wire_shapes.py — the version-keyed request builder (checks/wire_shapes.py)
must send each spec version the shape THAT version's schemas require, and must keep
the three older versions' bodies byte-identical to what the checks sent before it
existed.

Why this exists (PLAN-v3 §2.1, D1-01/D1-02): merchant_checks._create_payload built a
2026-04-08 `destinations[]` (no `type`) for every version. Against golden-0825 the
2026-08-25 schemas make `destinations[].type` a required discriminator
(shopping/types/fulfillment_destination.json `required: ["type","id"]`), so every
fulfillment-bearing create 422'd and 7 checks reported false deviations about a
conformant server. The fix is one place that knows the per-version delta —
`shapes_for(version)` — with a frozen-body test so the delta can never leak backwards.

Cases (each carries the mutant that would make it red):
  test_0825_destinations_carry_type   08-25 destinations carry `type: shipping_address`;
                                      04-08 do not (a global `type` would alter 04-08)
  test_unknown_version_fails_closed   shapes_for() on an unreviewed version raises
                                      ShapeUnsupported (never a silent 04-08 default)
  test_completion_ok_branches         CHK-025: completed+order at every version; the
                                      complete_in_progress/no-order branch ONLY at 08-25
  test_headers_signer_hook            a signer's headers appear when given, never otherwise
                                      (the D3-23 contract — no forked _hdr)
  test_frozen_bodies_01_04            _create_payload/_hdr/checkout_create at 01-11/01-23/
                                      04-08 are byte-identical to the pre-wire_shapes output
  test_keys_field_and_consent         keys_field(): signing_keys|keys; consent(): booleans
                                      at 04-08, Purpose objects at 08-25
  test_expand_mut_dests_08_25         (D1-02) _expand_mut expands $DESTS/$FUL/$KEYS from
                                      shapes_for(ctx.version) BEFORE $PRODUCT…: the FUL-026
                                      mutant carries typed destinations at 08-25, untyped
                                      at 04-08 (a 04-08-shaped mutant would 422 at 08-25 and
                                      "kill" vacuously)

    --selftest   hermetic, no server. Exit 0 pass, 1 fail.
"""
import json
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))

OLD = ("2026-01-11", "2026-01-23", "2026-04-08")
NEW = "2026-08-25"
FIXED_UUID = uuid.UUID("00000000-0000-4000-8000-000000000001")

# What merchant_checks._create_payload produced on 2026-09-10 (main f9cc470) with
# uuid4 pinned to FIXED_UUID, product "prod_1", handlers [{"id":"h1"}] — sort_keys=True.
# Version-independent apart from ucp.version. NEVER edit to make a test pass: any drift
# here is a change to what every 01-era/04-08 merchant is sent.
FROZEN_PLAIN = ('{"currency": "USD", "id": "00000000-0000-4000-8000-000000000001", '
                '"line_items": [{"id": "li_1", "item": {"id": "prod_1", "price": 1000}, '
                '"quantity": 1, "totals": []}], "links": [], "payment": {"handlers": '
                '[{"id": "h1"}], "instruments": []}, "status": "incomplete", "totals": [], '
                '"ucp": {"version": "%s"}}')
FROZEN_FUL = ('{"currency": "USD", "fulfillment": {"methods": [{"destinations": '
              '[{"address_country": "US", "id": "d1"}], "groups": [{"id": "g1", '
              '"line_item_ids": ["li_1"], "selected_option_id": "std"}], "id": "m1", '
              '"line_item_ids": ["li_1"], "selected_destination_id": "d1", "type": '
              '"shipping"}]}, "id": "00000000-0000-4000-8000-000000000001", "line_items": '
              '[{"id": "li_1", "item": {"id": "prod_1", "price": 1000}, "quantity": 1, '
              '"totals": []}], "links": [], "payment": {"handlers": [{"id": "h1"}], '
              '"instruments": []}, "status": "incomplete", "totals": [], "ucp": '
              '{"version": "%s"}}')
FROZEN_HDR = ('{"Content-Type": "application/json", "UCP-Agent": "profile=\\"https://spck.dev/agent\\"", '
              '"idempotency-key": "k1", "request-id": "00000000-0000-4000-8000-000000000001", '
              '"request-signature": "test"}')


class Ctx:
    """The slice of merchant.MerchantCtx the builders read."""
    def __init__(self, version, product="prod_1", config=None):
        self.version = version
        self.product_id = product
        self.config = config if config is not None else {"currency": "USD",
                                                          "payment_handlers": [{"id": "h1"}]}


def _dump(o):
    return json.dumps(o, sort_keys=True)


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    try:
        import wire_shapes as ws
    except ImportError as e:
        print(f"  ✗ import — ImportError: {e}")
        print("wire-shapes: FAIL (ImportError: wire_shapes)")
        return 1
    import merchant_checks as mc

    uuid.uuid4 = lambda: FIXED_UUID          # pin ids so bodies are comparable

    # --- test_0825_destinations_carry_type ---------------------------------------
    s25 = ws.shapes_for(NEW)
    d25 = s25.destinations(Ctx(NEW))
    check("test_0825_destinations_carry_type",
          d25 and all(d.get("type") == "shipping_address" for d in d25), f"got {d25}")
    body = s25.checkout_create(Ctx(NEW), with_fulfillment=True)
    dests = body["fulfillment"]["methods"][0]["destinations"]
    check("test_0825_create_body_destinations_typed",
          all(d.get("type") == "shipping_address" for d in dests), f"got {dests}")
    check("test_0825_fulfillment_block_typed",
          all(d.get("type") == "shipping_address"
              for d in s25.fulfillment_block(Ctx(NEW))["methods"][0]["destinations"]))
    d08 = ws.shapes_for("2026-04-08").destinations(Ctx("2026-04-08"))
    check("test_0408_destinations_untyped", d08 and all("type" not in d for d in d08), f"got {d08}")
    check("test_shape_versions_are_the_four_reviewed",
          tuple(ws.SHAPE_VERSIONS) == OLD + (NEW,), f"got {ws.SHAPE_VERSIONS}")

    # --- test_unknown_version_fails_closed ----------------------------------------
    for bad in ("2027-01-01", None, ""):
        try:
            ws.shapes_for(bad)
            check(f"test_unknown_version_fails_closed[{bad!r}]", False, "no exception")
        except ws.ShapeUnsupported:
            check(f"test_unknown_version_fails_closed[{bad!r}]", True)
        except Exception as e:                                       # noqa: BLE001
            check(f"test_unknown_version_fails_closed[{bad!r}]", False, f"{type(e).__name__}: {e}")

    # --- test_completion_ok_branches (CHK-025) -----------------------------------
    order = {"id": "ord_1", "permalink_url": "https://m.example/o/1"}
    sync_ok = {"status": "completed", "order": order}
    async_ok = {"status": "complete_in_progress"}
    async_bad = {"status": "complete_in_progress", "order": order}   # order MUST be absent
    sync_bad = {"status": "completed"}                               # order MUST be present
    other = {"status": "incomplete", "order": order}
    for v in OLD + (NEW,):
        s = ws.shapes_for(v)
        check(f"completion_ok[{v}] completed+order", s.completion_ok(sync_ok) is True)
        check(f"completion_ok[{v}] completed w/o order", s.completion_ok(sync_bad) is False)
        check(f"completion_ok[{v}] other status", s.completion_ok(other) is False)
        check(f"completion_ok[{v}] non-dict", s.completion_ok(None) is False)
    check("completion_ok[08-25] complete_in_progress w/o order", s25.completion_ok(async_ok) is True)
    check("completion_ok[08-25] complete_in_progress WITH order", s25.completion_ok(async_bad) is False)
    for v in OLD:
        check(f"completion_ok[{v}] complete_in_progress is NOT ok (no async branch before 08-25)",
              ws.shapes_for(v).completion_ok(async_ok) is False)
    check("test_completion_ok_branches", all(f"completion_ok" not in x for x in fails))

    # --- test_headers_signer_hook --------------------------------------------------
    seen = {}

    def signer(headers):
        seen["base"] = dict(headers)
        return {"Signature": "sig=:abc:", "Signature-Input": 'sig=("@method")'}
    h_signed = s25.headers("k1", signer=signer)
    h_plain = s25.headers("k1")
    check("test_headers_signer_hook signer headers present",
          h_signed.get("Signature") == "sig=:abc:" and "Signature-Input" in h_signed, f"{h_signed}")
    check("test_headers_signer_hook signer saw the base headers",
          seen.get("base", {}).get("UCP-Agent", "").startswith("profile="))
    check("test_headers_signer_hook none without signer",
          "Signature" not in h_plain and "Signature-Input" not in h_plain, f"{h_plain}")
    check("test_headers_signer_hook base headers intact",
          h_signed.get("idempotency-key") == "k1" and "UCP-Agent" in h_signed
          and h_signed.get("Content-Type") == "application/json")
    hv = s25.headers("k1", version_param=NEW)
    check("test_headers_version_param",
          hv["UCP-Agent"] == f'profile="https://spck.dev/agent"; version="{NEW}"', hv["UCP-Agent"])
    check("test_headers_signer_hook", all("test_headers_signer_hook" not in x for x in fails))

    # --- test_frozen_bodies_01_04 -------------------------------------------------
    for v in OLD:
        c = Ctx(v)
        s = ws.shapes_for(v)
        check(f"frozen[{v}] _create_payload plain", _dump(mc._create_payload(c)) == FROZEN_PLAIN % v)
        check(f"frozen[{v}] _create_payload fulfillment",
              _dump(mc._create_payload(c, with_fulfillment=True)) == FROZEN_FUL % v)
        check(f"frozen[{v}] checkout_create plain", _dump(s.checkout_create(c)) == FROZEN_PLAIN % v)
        check(f"frozen[{v}] checkout_create fulfillment",
              _dump(s.checkout_create(c, with_fulfillment=True)) == FROZEN_FUL % v)
        check(f"frozen[{v}] headers", _dump(s.headers("k1")) == FROZEN_HDR, _dump(s.headers("k1")))
    check("frozen _hdr wrapper", _dump(mc._hdr("k1")) == FROZEN_HDR, _dump(mc._hdr("k1")))
    check("test_frozen_bodies_01_04", all("frozen" not in x for x in fails))

    # --- keys_field / consent / schema_path / complete_body ------------------------
    check("keys_field 04-08 = signing_keys", ws.shapes_for("2026-04-08").keys_field() == "signing_keys")
    check("keys_field 08-25 = keys", s25.keys_field() == "keys")
    c08 = ws.shapes_for("2026-04-08").consent({"marketing": True})
    c25 = s25.consent({"marketing": True})
    check("consent 04-08 booleans", c08 == {"marketing": True}, f"{c08}")
    check("consent 08-25 purpose objects",
          c25 == {"dev.ucp.consent.marketing": {"granted": True, "source": "platform"}}, f"{c25}")
    check("schema_path destination", s25.schema_path("fulfillment_destination")
          == "shopping/types/fulfillment_destination.json")
    try:
        s25.schema_path("no_such_logical_name")
        check("schema_path unknown fails closed", False, "no exception")
    except KeyError:
        check("schema_path unknown fails closed", True)
    pay = {"payment": {"instruments": [{"id": "i1"}]}, "risk_signals": {}}
    check("complete_body passes the config payment through",
          s25.complete_body(Ctx(NEW, config={"complete_payment": pay})) == pay)
    check("test_keys_field_and_consent", all(x not in fails for x in
          ("keys_field 04-08 = signing_keys", "keys_field 08-25 = keys",
           "consent 04-08 booleans", "consent 08-25 purpose objects")))

    # --- test_expand_mut_dests_08_25 (D1-02) ---------------------------------------
    probe = 'set:fulfillment={"methods":[{"destinations":$DESTS,"item":$PRODUCT}]}'
    exp25 = mc._expand_mut(probe, Ctx(NEW))
    exp08 = mc._expand_mut(probe, Ctx("2026-04-08"))
    check("test_expand_mut_dests_08_25 $DESTS expanded (typed) at 08-25",
          "$DESTS" not in exp25 and '"type": "shipping_address"' in exp25, exp25[:200])
    check("test_expand_mut_dests_08_25 $DESTS expanded (untyped) at 04-08",
          "$DESTS" not in exp08 and "shipping_address" not in exp08, exp08[:200])
    check("test_expand_mut_dests_08_25 $PRODUCT still expanded after", '"prod_1"' in exp25, exp25[:200])
    ful026 = next(c for c in mc.CHECKS if c.id == "fulfillment.single_group_default")
    mut = next(m for m in ful026.mutations if m.startswith("set:fulfillment="))
    check("test_expand_mut_dests_08_25 FUL-026 mutant uses $DESTS (no 04-08 literal)",
          "$DESTS" in mut and '"address_country":"US"' not in mut, mut[:160])
    ph = s25.placeholders(Ctx(NEW))
    check("placeholders carry $DESTS/$FUL/$KEYS",
          set(ph) >= {"$DESTS", "$FUL", "$KEYS"} and ph["$KEYS"] == "keys", f"{sorted(ph)}")
    check("placeholder values are JSON", all(json.loads(ph[k]) is not None for k in ("$DESTS", "$FUL")))
    check("test_expand_mut_dests_08_25", all("test_expand_mut_dests_08_25" not in x for x in fails))

    print(f"wire-shapes: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--selftest" not in sys.argv:
        print("usage: validate_wire_shapes.py --selftest"); sys.exit(2)
    sys.exit(selftest())

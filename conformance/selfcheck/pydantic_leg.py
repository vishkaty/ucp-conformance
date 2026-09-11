#!/usr/bin/env python3
"""
pydantic_leg.py — the PYDANTIC third leg of the dual-oracle gate (D4-05 / B5b).

The python-sdk ships generated pydantic models for every 2026-08-25 schema (ucp-sdk 0.5.0 =
tag v2026-08-25 on PyPI; main carries constraint fixes not yet cut). Those models are what
every Python implementer validates with, so where they are LAXER than the schema the
ecosystem silently accepts what the spec forbids (L4 N24/N25: 7/14 negatives dropped by 0.5.0).
This leg validates the same corpus the Rust oracle and the referee validate, through the
SDK models, in a SEPARATE venv per SDK cut (two ucp_sdk versions cannot coexist in one
interpreter; conformance/ci/make_sdk_venvs.sh builds them from known_sdk_drops.json `sdk`).

  PydanticLeg(venv).validate_many([(label, model_path, payload), ...]) -> {label: (ok, faults)}
  model_for(schema_rel, def_name, op, direction) -> "shopping.checkout.Checkout" | None
      An UNMAPPED (schema, def) is `not-judged`, never `agree` (a leg that cannot judge must
      not vote). The map is seeded from the corpus + probe families; extend deliberately.

Faults are instance-path pointers ("/keys/0/crv") built from pydantic's error `loc`, the same
semantic signature the referee reports — never message text.
"""
import json, pathlib, subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VENVS = ROOT / "conformance" / ".sdk-venvs"
TAG = VENVS / "ucp-sdk-tag"
MAIN = VENVS / "ucp-sdk-main"

# (schema_rel, def_name, direction) -> model dotted path under ucp_sdk.models.schemas; the
# request side is keyed by op because the SDK generates <schema>_<op>_request modules.
MODEL_MAP = {
    ("schemas/shopping/checkout.json", None, "response"): "shopping.checkout.Checkout",
    ("schemas/shopping/checkout.json", None, "request:create"): "shopping.checkout_create_request.CheckoutCreateRequest",
    ("schemas/shopping/checkout.json", None, "request:complete"): "shopping.checkout_complete_request.CheckoutCompleteRequest",
    ("schemas/shopping/order.json", None, "response"): "shopping.order.Order",
    ("schemas/common/types/payment_instrument.json", "selected_payment_instrument", "response"):
        "common.types.payment_instrument.SelectedPaymentInstrument",
    ("schemas/common/types/payment_instrument.json", "selected_payment_instrument", "request:complete"):
        "common.types.payment_instrument_complete_request.SelectedPaymentInstrument",
    ("schemas/profile.json", "business_schema", "response"): "profile.BusinessSchema",
    ("schemas/profile.json", "jwk_public_key", "response"): "profile.JwkPublicKey",
    ("schemas/common/types/unit.json", None, "response"): "common.types.unit.Unit",
    ("schemas/common/types/time_interval.json", None, "response"): "common.types.time_interval.TimeInterval",
    ("schemas/common/types/location_serves.json", None, "response"): "common.types.location_serves.LocationServes",
    ("schemas/shopping/types/fulfillment_method.json", None, "response"): "shopping.types.fulfillment_method.FulfillmentMethod",
    ("schemas/capability.json", "base", "response"): "capability.Base",
}


def model_for(schema_rel, def_name, op="read", direction="response"):
    key_dir = f"request:{op}" if direction == "request" else "response"
    return MODEL_MAP.get((schema_rel, def_name, key_dir))


_RUNNER = r'''
import sys, json, importlib
from pydantic import ValidationError
reqs = json.load(sys.stdin)
out = {}
for r in reqs:
    mod, cls = r["model"].rsplit(".", 1)
    try:
        M = getattr(importlib.import_module("ucp_sdk.models.schemas." + mod), cls)
    except Exception as e:
        out[r["label"]] = {"error": f"{type(e).__name__}: {e}"}; continue
    try:
        M.model_validate(r["payload"])
        out[r["label"]] = {"ok": True, "faults": []}
    except ValidationError as e:
        faults = sorted({"/" + "/".join(str(p) for p in err["loc"]) for err in e.errors()})
        out[r["label"]] = {"ok": False, "faults": faults}
import importlib.metadata as m
out["__version__"] = m.version("ucp-sdk")
print(json.dumps(out))
'''


class LegUnavailable(RuntimeError):
    """The venv is not built / the SDK does not import -> the gate SKIPs (rc 2), never green."""


class PydanticLeg:
    def __init__(self, venv=TAG, name="pydantic-tag"):
        self.venv = pathlib.Path(venv)
        self.name = name
        self.python = self.venv / "bin" / "python"

    def available(self):
        return self.python.exists()

    def version(self):
        if not self.available():
            raise LegUnavailable(f"{self.name}: venv not built at {self.venv} "
                                 f"(run conformance/ci/make_sdk_venvs.sh)")
        r = subprocess.run([str(self.python), "-c",
                            "import importlib.metadata as m; print(m.version('ucp-sdk'))"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise LegUnavailable(f"{self.name}: ucp-sdk not importable in {self.venv}: {r.stderr[-200:]}")
        return r.stdout.strip()

    def validate_many(self, reqs):
        """reqs: [(label, model_path, payload)] -> {label: (ok, faults)}; raises LegUnavailable."""
        if not self.available():
            raise LegUnavailable(f"{self.name}: venv not built at {self.venv} "
                                 f"(run conformance/ci/make_sdk_venvs.sh)")
        payload = [{"label": l, "model": m, "payload": p} for l, m, p in reqs]
        r = subprocess.run([str(self.python), "-c", _RUNNER], input=json.dumps(payload),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise LegUnavailable(f"{self.name}: runner failed: {r.stderr[-300:]}")
        out = json.loads(r.stdout)
        res = {}
        for l, _m, _p in reqs:
            v = out[l]
            if "error" in v:
                raise LegUnavailable(f"{self.name}: {l}: {v['error']}")
            res[l] = (v["ok"], v["faults"])
        return res

    def validate(self, payload, model_path):
        return self.validate_many([("one", model_path, payload)])["one"]

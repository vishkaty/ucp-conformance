#!/usr/bin/env python3
"""
sdk_constraints_probe.py — assert the installed ucp-sdk enforces the schema
constraints added by python-sdk#57 (contains/minContains/maxContains, 0.4.4) and
python-sdk#59 (uniqueItems, 0.4.4).

Run by validate_sdk_constraints.py inside two interpreters: the golden's venv
(must enforce, exit 0) and a throwaway ucp-sdk==0.4.3 venv (must NOT enforce,
nonzero exit — proving this probe can fail). Prints one line per case.

Exit 0 = all constraints enforced; 1 = at least one unenforced (or a positive
control was wrongly rejected).
"""
import sys

from pydantic import TypeAdapter, ValidationError
from ucp_sdk.models.schemas.shopping.types.context import Context
from ucp_sdk.models.schemas.shopping.types.totals_create_request import (
    TotalsCreateRequest,
)

failures = []


def case(name, fn, expect_reject):
    try:
        fn()
        rejected = False
    except ValidationError:
        rejected = True
    ok = rejected == expect_reject
    print(f"  {'ok' if ok else 'FAIL'} {name}: "
          f"{'rejected' if rejected else 'accepted'} "
          f"(want {'reject' if expect_reject else 'accept'})")
    if not ok:
        failures.append(name)


TOTALS = TypeAdapter(TotalsCreateRequest)
SUBTOTAL = {"type": "subtotal", "display_text": "Subtotal", "amount": 100}
TOTAL = {"type": "total", "display_text": "Total", "amount": 100}

# python-sdk#57: totals MUST contain exactly one subtotal and one total.
case("contains: totals without subtotal rejected",
     lambda: TOTALS.validate_python([TOTAL]), expect_reject=True)
case("contains: totals with duplicate total rejected",
     lambda: TOTALS.validate_python([SUBTOTAL, TOTAL, TOTAL]), expect_reject=True)
case("contains: exactly one subtotal + one total accepted",
     lambda: TOTALS.validate_python([SUBTOTAL, TOTAL]), expect_reject=False)

# python-sdk#59: uniqueItems on context.eligibility.
case("uniqueItems: duplicate eligibility rejected",
     lambda: Context(eligibility=["com.example.loyalty_gold",
                                  "com.example.loyalty_gold"]),
     expect_reject=True)
case("uniqueItems: distinct eligibility accepted",
     lambda: Context(eligibility=["com.example.loyalty_gold",
                                  "com.example.loyalty_silver"]),
     expect_reject=False)

sys.exit(1 if failures else 0)

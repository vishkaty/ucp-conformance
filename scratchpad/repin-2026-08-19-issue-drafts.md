# DRAFTS for owner approval, 2026-08-19. Two samples issues. NOT filed, READ-ONLY session.
# Dedup swept open+closed issues/PRs (cart 500, currency, internal server error, multiple values,
# ValidationError, TypeError, cart in:title, 500 in:title, full issue listing): NO prior report of
# either crash. Nearest neighbors, all distinct: #134 (cart feature request), #159 (its
# implementation), #156 (checkout lane, currency read on ABSENCE, fixed 08-03, in the old pin),
# #167 (request supplied checkout id), #174/#187 (Node error envelopes).
# Every wire fact below re-verified against vendored samples main HEAD bd06e290 on 2026-08-19.

---

## ISSUE 1

**Suggested title:** Cart create returns 500 when the request body includes currency, a field cart.json marks ucp_request omit

**Body:**

## What I ran into

A cart create that includes a currency member draws a bare 500 from the Python sample server. The generated request models set extra allow, so the member crosses the boundary cleanly and the failure happens inside response construction, out of reach of the error envelope.

## Observed against main (bd06e290)

Fresh clone, documented setup, seeded flower shop database:

    POST /carts
    Content-Type: application/json
    UCP-Agent: profile="https://example.com/agent"
    Request-Signature: test
    Idempotency-Key: 5f2f9a3c-4b1d-4e8a-9c6d-2a7b8e1f0d3a
    Request-Id: 9c1e4b7a-2d3f-4a5b-8c9d-0e1f2a3b4c5d

    {"line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 2}], "currency": "USD"}

    HTTP 500  Internal Server Error

Server log:

    File "services/cart_service.py", line 119, in create_cart
        cart = Cart(
    TypeError: models.UnifiedCart() got multiple values for keyword argument 'currency'

The same body without the currency member returns 201 with a conformant cart whose currency is USD, which is the negative control that isolates the member.

## Expected

`source/schemas/shopping/cart.json` annotates currency with `ucp_request: omit` and describes it as an ISO 4217 code the merchant determines from context or geo IP, while requiring it on the response object. So the merchant owns the value either way: a body without currency succeeds today, and a body carrying it should get the tolerant treatment the extra allow boundary implies, or an in band envelope error, never a bare 500. The checkout lane already determines currency server side (#156).

## Root cause

`create_cart` at `services/cart_service.py` line 119 passes an explicit currency keyword and also splats the full request dump built at line 113, which still carries the request supplied currency because the SDK CartCreateRequest accepts it as an extra member, so construction raises TypeError ahead of any error shaping.

## Where it comes from

The cart lane is new in #159. This is a pitfall that travels with extra allow generated models: a member the schema assigns to the merchant can still arrive from the wire, and any construction site that also sets it explicitly is exposed to exactly this collision. The checkout lane met the mirror image in #156, where the conformant body was the one that failed.

## Why CI did not catch it

`cart_test.py` builds every create body through CartCreateRequest, which declares no currency field, and dumps with exclude_none, so no test request ever carries currency and the collision path never runs.

---

## ISSUE 2

**Suggested title:** Checkout create returns 500 instead of an in band error when a merchant determined member arrives with a wrong JSON type

**Body:**

## What I ran into

A checkout create whose currency member is a number draws a bare 500 from the Python sample server. Members the schema assigns to the merchant are extras under the extra allow request models, so a wrong typed value crosses the boundary unvalidated and fails deep inside response construction. A schema fuzz sweep surfaced 8 request variants that draw the same bare 500, wrong typed currency and wrong typed line_items[].id across the JSON types; the smallest is below.

## Observed against main (bd06e290)

Fresh clone, documented setup, seeded flower shop database:

    POST /checkout-sessions
    Content-Type: application/json
    UCP-Agent: profile="https://example.com/agent"
    Request-Signature: test
    Idempotency-Key: 7a3b5c9d-1e2f-4a6b-8c0d-3e5f7a9b1c2d
    Request-Id: 2d4f6a8b-0c1d-4e3f-9a5b-7c9d1e3f5a6b

    {"line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}], "currency": 123}

    HTTP 500  Internal Server Error

Server log:

    File "services/checkout_service.py", line 366, in create_checkout
        checkout = Checkout(
    pydantic_core._pydantic_core.ValidationError: 1 validation error for UnifiedCheckout
    currency
      Input should be a valid string [type=string_type, input_value=123, input_type=int]

The same body without the currency member returns 201, which is the negative control that isolates the member.

## Expected

An invalid request member should draw an in band 4xx the platform can act on, the shape `docs/specification/checkout-rest.md` gives protocol errors, a JSON body carrying code and content. A bare text 500 gives the caller nothing.

## Root cause

`create_checkout` reads currency from the request through getattr at `services/checkout_service.py` line 213, which resolves for any JSON type because the SDK request models set extra allow and declare no currency field, and feeds it into the response Checkout construction at line 366, where the resulting ValidationError is uncaught and surfaces as a bare 500.

## Where it comes from

The tolerant read arrived with the cart conversion flow in #159, which needs a currency source for both entry paths. Under extra allow it quietly widens the trust boundary: a member the schema hands to the merchant can arrive from the wire with any type, and its first validation happens outside the request error path. This is the same ecosystem pitfall as the cart report filed alongside this one.

## Why CI did not catch it

The integration tests always send well typed values for these members, so a wrong typed extra never reaches response construction.

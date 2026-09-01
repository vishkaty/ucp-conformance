# Class negative for signature_example_coverage

Synthetic, never shipped upstream. Proves the predicate does NOT constant-FIRE
on any signed example: every gate header these blocks carry is also covered.

**Conformant request (all three gate headers present AND covered):**

```http
POST /checkout-sessions HTTP/1.1
Host: merchant.example.invalid
Content-Type: application/json
UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
Signature-Agent: sig1="https://platform.example.invalid/.well-known/ucp";type=jwks_uri
Idempotency-Key: 00000000-0000-4000-8000-000000000000
Content-Digest: sha-256=:XTS0000000000000000000000000000000000000000:
Signature-Input: sig1=("@method" "@authority" "@path" "ucp-agent" "signature-agent" "idempotency-key" "content-digest" "content-type");keyid="fixture-key"
Signature: sig1=:XTS0000000000000000000000000000000000000000:

{"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
```

**Conformant request (gate header ABSENT, so nothing to cover):**

```http
GET /orders/order_fixture HTTP/1.1
Host: merchant.example.invalid
Signature-Input: sig1=("@method" "@authority" "@path");keyid="fixture-key"
Signature: sig1=:XTS0000000000000000000000000000000000000000:
```

**Elided covered set (must be excluded, not judged):**

```text
Signature-Agent: sig1="https://platform.example.invalid/.well-known/ucp";type=jwks_uri
Signature-Input: sig1=("@method" "@authority" ...);...
```

**Elided covered set on a REQUEST block.** This is the case that makes the
`elided` guard load-bearing rather than decorative: the block IS an HTTP request
and DOES carry a gate header, so only the `...` in its component list keeps the
predicate silent. Remove the elided guard and this fires a false finding, which
is exactly what the gate's class negative must catch.

```http
POST /checkout-sessions HTTP/1.1
Host: merchant.example.invalid
UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
Idempotency-Key: 00000000-0000-4000-8000-000000000001
Signature-Input: sig1=("@method" "@authority" "@path" ...);keyid="fixture-key"
Signature: sig1=:XTS0000000000000000000000000000000000000000:

{"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
```

**Response example (the gate governs request headers, so not judged):**

```http
HTTP/1.1 200 OK
Content-Type: application/json
UCP-Agent: profile="https://merchant.example.invalid/.well-known/ucp"
Signature-Input: sig1=("@status" "content-digest" "content-type");keyid="fixture-key"
Signature: sig1=:XTS0000000000000000000000000000000000000000:

{"id":"order_fixture"}
```

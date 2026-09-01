# Hardening cases for signature_example_coverage

Synthetic, never shipped upstream. Each block encodes a way the first version of
the parser failed toward GREEN (a real violation it could not see) or toward a
false clean. The gate asserts the exact expected finding set over this file, so a
regression in any of them turns speclint red.

Case A is not hypothetical: `docs/specification/shopping/order/rest.md` ships a
4-space-indented `http` fence inside a MkDocs `=== "Request"` tab, and the
original parser could not see it at all.

## A. VIOLATION inside an indented (MkDocs tab) fence — MUST be found

=== "Request"

    ```http
    POST /checkout-sessions HTTP/1.1
    Host: merchant.example.invalid
    UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
    Signature-Input: sig1=("@method" "@authority" "@path");keyid="fixture-key"
    Signature: sig1=:XTS0000000000000000000000000000000000000000:

    {"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
    ```

## B. VIOLATION where a parameter VALUE looks like the component — MUST be found

A quoted parameter value must never count as coverage. Here `keyid` is literally
`ucp-agent`, but the component list does not name it.

```http
POST /checkout-sessions HTTP/1.1
Host: merchant.example.invalid
UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
Signature-Input: sig1=("@method" "@authority" "@path");keyid="ucp-agent"
Signature: sig1=:XTS0000000000000000000000000000000000000000:

{"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
```

## C. VIOLATION with non-canonical header case — MUST be found

HTTP header names are case insensitive, so a lowercased example is still an
example.

```http
POST /checkout-sessions HTTP/1.1
host: merchant.example.invalid
ucp-agent: profile="https://platform.example.invalid/.well-known/ucp"
signature-input: sig1=("@method" "@authority" "@path");keyid="fixture-key"
signature: sig1=:XTS0000000000000000000000000000000000000000:

{"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
```

## D. VIOLATION with an ellipsis OUTSIDE the component list — MUST be found

The component list is complete and omits `ucp-agent`; the `...` sits in a
parameter. Judging elision on the whole line would wrongly excuse this block from
judgement and hide a real violation, so this case is what makes the
elision-is-per-list rule load-bearing rather than decorative.

```http
GET /orders/order_fixture HTTP/1.1
Host: merchant.example.invalid
UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
Signature-Input: sig1=("@method" "@authority" "@path");created=...;keyid="fixture-key"
Signature: sig1=:XTS0000000000000000000000000000000000000000:
```

## E. CLEAN across two signatures — MUST NOT fire

`sig2` covers `ucp-agent`, so the message can authenticate on a signature that
covers it. A per-signature reading that ignored `sig2` would false-flag here.

```http
POST /checkout-sessions HTTP/1.1
Host: merchant.example.invalid
UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
Signature-Input: sig1=("@method" "@authority" "@path");keyid="k1", sig2=("@method" "@authority" "@path" "ucp-agent");keyid="k2"
Signature: sig1=:XTS0000000000000000000000000000000000000000:, sig2=:XTS0000000000000000000000000000000000000001:

{"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
```

## F. VIOLATION across two signatures where NEITHER covers — MUST be found

```http
POST /checkout-sessions HTTP/1.1
Host: merchant.example.invalid
UCP-Agent: profile="https://platform.example.invalid/.well-known/ucp"
Signature-Input: sig1=("@method" "@authority" "@path");keyid="k1", sig2=("@method" "@authority");keyid="k2"
Signature: sig1=:XTS0000000000000000000000000000000000000000:, sig2=:XTS0000000000000000000000000000000000000001:

{"line_items":[{"item":{"id":"fixture_item"},"quantity":1}]}
```

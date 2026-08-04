#!/usr/bin/env python3
"""
fuzz_corpus.py — a SCHEMA-GUIDED, DETERMINISTIC boundary/mutation corpus for the UCP
request schemas (the bug-discovery generator behind the fuzz gate, fuzz_gate.py).

WHY. #156 (the currency-omit 500 we filed) was found by luck. A conformance suite that
only fires hand-authored happy/negative payloads can only find the bugs someone thought
to write down. This module finds the #156 CLASS systematically: it enumerates the
constraint points of the pinned 2026-04-08 request schemas and emits one boundary
payload per point, so a maintainer gets a finite, explainable, reproducible corpus
rather than a random walk.

DETERMINISM (the whole point — a finding must be stable and reproducible). There is no
RNG. The corpus is a pure function of (schema, product_id, version): every case is a
named, structural mutation of a curated VALID baseline, enumerated over the baseline's
own fields in a fixed (sorted) order. Same inputs -> byte-identical corpus. build_corpus
is import-only and does no I/O beyond reading the vendored schemas.

WHAT IT ENUMERATES (per op = create / update). Starting from a spec-VALID baseline
request, for every field reachable to a bounded depth it emits:
  * drop            — omit the field. Covers BOTH the required-omit case (server MUST
                      4xx) AND the ucp_request:omit/optional-omit case (server MUST NOT
                      5xx). The currency-omit #156 shape is exactly a drop of an
                      omit-in-request field, and is tagged as the positive control.
  * null            — set the field to JSON null.
  * wrongtype       — set the field to a value of each other JSON type.
  * empty           — [] / "" / {} for array / string / object fields.
  * number-boundary — 0, -1, and an oversized integer for numeric fields (e.g. quantity
                      has minimum:1, so 0 and -1 are boundary violations).
  * oversize        — a very long string / a very large array (resource-exhaustion probe).
  * cardinality     — an array where an object is expected and vice-versa.
  * enum-break      — an out-of-enum value for every enum-constrained field.
Plus whole-body degenerate shapes ({}, null, [], a bare scalar, a deeply-nested blob)
and the named known-dangerous shapes (currency-omit, empty line_items).

The corpus does NOT hand-label validity. Expected validity is assigned at classify time
by the INDEPENDENT Python referee (dual_oracle_referee.Referee), the same second oracle
the dual-oracle gate trusts. The operators here only MUTATE; the referee JUDGES. That
keeps the generator honest (it cannot encode a wrong expectation) and is why a
spec-contradicting ACCEPT is detectable at all.

This module is pure/hermetic. The live firing + classification + gate live in
conformance/ci/fuzz_gate.py.
"""
import copy, json, pathlib
from dataclasses import dataclass, field
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = ROOT / "conformance" / ".vendor"

# A fixed, deterministic product id placeholder. The gate overrides this with the
# golden's real seeded product (differential_flower.config.json) so the happy-path
# baseline actually reaches the currency-determination code path; against an arbitrary
# server it is passed via --product. The corpus STRUCTURE does not depend on the value.
DEFAULT_PRODUCT = "bouquet_roses"

# Bounded traversal depth: the request-validated constraint points of a checkout create
# live at the checkout root, line_items[0], line_items[0].item and payment — depth 3 is
# enough to reach every one while keeping the corpus finite and each case explainable.
MAX_DEPTH = 3

# The wrong-type palette: one representative value per OTHER JSON type. Deterministic.
_WRONGTYPE = {
    "string":  '"a-string"',      # placeholders; real values built in _wrongtype_values
    "integer": "12345",
    "boolean": "true",
    "array":   "[]",
    "object":  "{}",
    "null":    "null",
}
_OVERSIZE_STR = "A" * 100_000
_OVERSIZE_INT = 10 ** 18
_OVERSIZE_ARR = list(range(1000))


@dataclass
class FuzzCase:
    cid: str            # stable, unique, explainable id: "<op>/<category>/<pointer>[/detail]"
    op: str             # create | update
    method: str
    path_template: str  # "/checkout-sessions" ; update templates {id}
    body: Any           # the payload to send (dict, or a degenerate whole-body value)
    category: str       # drop|null|wrongtype|empty|number|oversize|cardinality|enum|wholebody|baseline|known-dangerous
    mutation: str       # human description
    tags: list = field(default_factory=list)


# ---- baselines (curated, spec-VALID request shapes; verified 2xx on the golden) -------
def _create_baseline(product_id, version):
    """A spec-correct create request. Includes response-only fields (currency, status,
    ucp, totals, links, id) that are ucp_request:omit — a conformant server ignores them,
    and dropping each is how we reach the omit-field cases (incl. #156 currency-omit)."""
    return {
        "id": "co_fuzz_baseline",
        "currency": "USD",
        "line_items": [{
            "id": "li_1",
            "quantity": 1,
            "item": {"id": product_id, "price": 1000},
            "totals": [],
        }],
        "payment": {"instruments": [], "handlers": []},
        "status": "incomplete",
        "ucp": {"version": version},
        "totals": [],
        "links": [],
    }


def _update_baseline(product_id, version):
    """A spec-correct update request. line_items[].id is required on update (omit on
    create); top-level id is ucp_request:omit at 04-08 (required pre-04-08)."""
    b = {
        "currency": "USD",
        "line_items": [{
            "id": "li_1",
            "quantity": 3,
            "item": {"id": product_id},
        }],
        "payment": {"instruments": []},
    }
    if version != "2026-04-08":
        b["id"] = "co_fuzz_baseline"
    return b


# ---- deterministic structural traversal ----------------------------------------------
def _walk(node, prefix="", depth=0):
    """Yield (json_pointer, value, kind) for every mutable location in `node`, to
    MAX_DEPTH. kind is 'field' (object member) or 'elem' (array element). Deterministic:
    object keys are visited in sorted order, array elements in index order."""
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for k in sorted(node.keys()):
            ptr = f"{prefix}/{k}"
            yield (ptr, node[k], "field")
            yield from _walk(node[k], ptr, depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            ptr = f"{prefix}/{i}"
            yield (ptr, v, "elem")
            yield from _walk(v, ptr, depth + 1)


def _ptr_parts(ptr):
    return [p for p in ptr.split("/") if p != ""]


def _get(obj, parts):
    cur = obj
    for p in parts:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur


def _set(obj, parts, value):
    cur = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _delete(obj, parts):
    cur = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        del cur[int(last)]
    else:
        del cur[last]


def _mutant(baseline, ptr, op):
    """Deep-copy the baseline and return (copy, parts) for mutating at ptr."""
    m = copy.deepcopy(baseline)
    return m, _ptr_parts(ptr)


def _json_kind(v):
    if isinstance(v, bool): return "boolean"
    if isinstance(v, int): return "integer"
    if isinstance(v, float): return "number"
    if isinstance(v, str): return "string"
    if isinstance(v, list): return "array"
    if isinstance(v, dict): return "object"
    return "null"


def _wrongtype_values(current_kind):
    """Return {label: value} of one value per JSON type OTHER than current_kind."""
    palette = {
        "string": "not-the-right-type",
        "integer": 987654321,
        "boolean": True,
        "array": ["wrong"],
        "object": {"wrong": True},
        "null": None,
    }
    # integer/number are interchangeable enough that we skip integer->number noise
    skip = {current_kind}
    if current_kind == "number":
        skip.add("integer")
    if current_kind == "integer":
        skip.add("number")
    return {k: v for k, v in palette.items() if k not in skip}


# ---- known enum locations (schema-derived) -------------------------------------------
# status is the only enum reachable in a request body; it is ucp_request:omit (a server
# ignores it), so an out-of-enum status is a NEGATIVE control the server may accept — the
# referee labels it, we do not. Kept explicit so enum coverage is auditable.
_ENUM_POINTERS = {
    "/status": ["incomplete", "requires_escalation", "ready_for_complete",
                "complete_in_progress", "completed", "canceled"],
}


def _emit_for_baseline(baseline, op, method, path_template):
    cases = []

    def add(cid, body, category, mutation, tags=None):
        cases.append(FuzzCase(cid=f"{op}/{cid}", op=op, method=method,
                              path_template=path_template, body=body,
                              category=category, mutation=mutation, tags=tags or []))

    # 0) the valid baseline itself (a spec-correct request MUST be accepted)
    add("baseline", copy.deepcopy(baseline), "baseline",
        "spec-correct baseline request (must be accepted)")

    # structural mutations over every reachable field/elem
    for ptr, val, kind in _walk(baseline):
        parts = _ptr_parts(ptr)
        vkind = _json_kind(val)
        safe = ptr.strip("/").replace("/", ".")

        # 1) drop
        m, p = _mutant(baseline, ptr, op); _delete(m, p)
        tags = []
        if ptr == "/currency":
            tags = ["#156", "currency-omit", "known-dangerous"]
        add(f"drop/{safe}", m, "drop", f"omit field {ptr}", tags)

        # 2) null
        m, p = _mutant(baseline, ptr, op); _set(m, p, None)
        add(f"null/{safe}", m, "null", f"set {ptr} = null")

        # 3) wrongtype (one per other JSON type)
        for tlabel, tval in _wrongtype_values(vkind).items():
            m, p = _mutant(baseline, ptr, op); _set(m, p, copy.deepcopy(tval))
            add(f"wrongtype/{safe}/{tlabel}", m, "wrongtype",
                f"set {ptr} ({vkind}) to a {tlabel}")

        # 4) empty container / empty string
        if vkind == "array":
            m, p = _mutant(baseline, ptr, op); _set(m, p, [])
            t = ["known-dangerous"] if ptr == "/line_items" else []
            add(f"empty/{safe}", m, "empty", f"set {ptr} = [] (empty array)", t)
        elif vkind == "object":
            m, p = _mutant(baseline, ptr, op); _set(m, p, {})
            add(f"empty/{safe}", m, "empty", f"set {ptr} = {{}} (empty object)")
        elif vkind == "string":
            m, p = _mutant(baseline, ptr, op); _set(m, p, "")
            add(f"empty/{safe}", m, "empty", f"set {ptr} = \"\" (empty string)")

        # 5) numeric boundaries
        if vkind in ("integer", "number"):
            for label, num in (("zero", 0), ("negative", -1), ("oversize", _OVERSIZE_INT)):
                m, p = _mutant(baseline, ptr, op); _set(m, p, num)
                add(f"number/{safe}/{label}", m, "number", f"set {ptr} = {num}")

        # 6) oversize string
        if vkind == "string":
            m, p = _mutant(baseline, ptr, op); _set(m, p, _OVERSIZE_STR)
            add(f"oversize/{safe}", m, "oversize", f"set {ptr} to a 100k-char string")

        # 7) cardinality swap (array<->object) at container fields
        if vkind == "array":
            m, p = _mutant(baseline, ptr, op); _set(m, p, {"0": val[0] if val else None})
            add(f"cardinality/{safe}", m, "cardinality", f"send {ptr} as an object not an array")
        elif vkind == "object":
            m, p = _mutant(baseline, ptr, op); _set(m, p, [val])
            add(f"cardinality/{safe}", m, "cardinality", f"send {ptr} as an array not an object")

    # 8) enum-break at known enum locations present in the baseline
    for ptr, allowed in _ENUM_POINTERS.items():
        parts = _ptr_parts(ptr)
        try:
            _get(baseline, parts)
        except (KeyError, IndexError):
            continue
        m = copy.deepcopy(baseline); _set(m, parts, "not_a_valid_enum_value")
        safe = ptr.strip("/").replace("/", ".")
        add(f"enum/{safe}", m, "enum", f"set {ptr} to a value outside its enum")

    # 9) whole-body degenerate shapes
    add("wholebody/empty-object", {}, "wholebody", "empty object body {}")
    add("wholebody/null", None, "wholebody", "null body")
    add("wholebody/array", [], "wholebody", "array body []")
    add("wholebody/scalar", "not-an-object", "wholebody", "bare string body")
    deep = cur = {}
    for _ in range(200):
        cur["n"] = {}; cur = cur["n"]
    add("wholebody/deep-nest", copy.deepcopy(deep), "wholebody",
        "200-level deeply-nested object (parser-depth probe)")
    big = copy.deepcopy(baseline); big["line_items"] = [big["line_items"][0]] * 500
    add("wholebody/huge-array", big, "wholebody", "500 line_items (resource-exhaustion probe)")

    return cases


def build_corpus(product_id=DEFAULT_PRODUCT, version="2026-04-08", ops=("create", "update")):
    """Return the full deterministic list[FuzzCase]. Pure function of the inputs."""
    corpus = []
    if "create" in ops:
        corpus += _emit_for_baseline(_create_baseline(product_id, version),
                                     "create", "POST", "/checkout-sessions")
    if "update" in ops:
        corpus += _emit_for_baseline(_update_baseline(product_id, version),
                                     "update", "PUT", "/checkout-sessions/{id}")
    return corpus


def corpus_digest(corpus):
    """A stable content hash of the corpus — proves determinism across runs/machines."""
    import hashlib
    blob = json.dumps([[c.cid, c.op, c.method, c.path_template, c.category, c.body]
                       for c in corpus], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--product", default=DEFAULT_PRODUCT)
    ap.add_argument("--version", default="2026-04-08")
    ap.add_argument("--op", default="all", choices=["create", "update", "all"])
    ap.add_argument("--json", action="store_true", help="dump full corpus as JSON")
    ap.add_argument("--list", action="store_true", help="list case ids + categories")
    args = ap.parse_args()
    ops = ("create", "update") if args.op == "all" else (args.op,)
    corpus = build_corpus(args.product, args.version, ops)
    if args.json:
        print(json.dumps([c.__dict__ for c in corpus], indent=2, default=str))
        return 0
    from collections import Counter
    by_cat = Counter(c.category for c in corpus)
    by_op = Counter(c.op for c in corpus)
    print(f"corpus: {len(corpus)} cases   digest={corpus_digest(corpus)[:16]}")
    print(f"  by op:       {dict(by_op)}")
    print(f"  by category: {dict(sorted(by_cat.items()))}")
    if args.list:
        for c in corpus:
            tag = f"  {c.tags}" if c.tags else ""
            print(f"  {c.cid:52} [{c.category}]{tag}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

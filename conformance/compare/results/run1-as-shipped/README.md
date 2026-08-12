# Run 1 — the spck suite AS SHIPPED, before the comparison's findings were fixed

Measured with the SAME final harness (fresh reseeded golden per suite run,
sticky-proxy injection, strict catch rules) but with
`merchant_checks_04_08_totals.py` temporarily removed so the check set matches
tree `1deda31` — the suite as it shipped before this comparison existed. The
`+dirty` marker in `results.json` meta reflects exactly that temporary removal.

**Result: spck 12/18, official 17/18 on the shared surface.** The official
suite won against our then-shipped suite: our TOT-005/006/014/015/020
invariants ran only cart-gated (a capability the flower golden does not
declare) and in the fixture lane (which never observes a live server), and
ORD-020's confirmation fields were unchecked on complete.

It is committed deliberately: the head-to-head in `../results.md` reflects the
suite AFTER those six gaps were closed (see the disclosed origin note in
`conformance/checks/merchant_checks_04_08_totals.py`), and an honest
before/after needs the before to be inspectable. Single repeat; the
determinism gate applies to the final run in `../`.

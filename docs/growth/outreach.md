# Outreach — targets + ready-to-send emails (#5)

One mention from someone who already has an audience beats a cold community post.
These are people/sites **already publishing about UCP** — so linking a free conformance
tool is genuinely useful to their readers, not spam. Send from your **personal** email
(more credible than a brand). I draft; you approve each send.

## Target list

| target | why | how to reach |
|---|---|---|
| **wearepresta.com** | Publishes UCP developer tutorials / "how to set up UCP" guides | contact form / author byline; their guides are the perfect place for a "verify it" tool |
| **digitalapplied.com** | "UCP vs ACP vs AP2" merchant guide — covers conformance-adjacent topics | site contact / author |
| **MetaRouter blog** | "What is UCP" explainer — audience of commerce engineers | blog contact |
| **ucptools.dev / ucpchecker.com** | Competitors — but small ecosystem; possible cross-link / awareness | site contact |
| **Agentic-commerce / dev-tool newsletters** | e.g. any "agentic commerce weekly" / Shopify-dev newsletters | newsletter "submit a tool/tip" links |
| **Shopify dev community** (shopify.dev/agents, forums) | Millions of Shopify merchants on UCP | forum post / community, not email |

**How to find the email:** site "Contact"/"Write for us" page, author byline, or the
domain's `hello@`/`team@`. Keep it to 5–6 quality sends, not a blast.

---

## Email A — to a blogger who writes UCP guides

**Subject:** A free UCP conformance checker for your readers

> Hi [name],
>
> I read your piece on [their UCP article] — genuinely useful, especially [one specific point].
>
> I built a free, open-source UCP conformance checker: **spck.dev/check**. Paste a store URL and it validates the `/.well-known/ucp` profile against the spec; there's also a `pip` CLI and a GitHub Action for the full suite (checkout, catalog, cart, totals) in CI.
>
> One thing that might interest your readers: testing it against the *official* reference samples surfaced real deviations (e.g. the Node.js sample serves `capabilities` as an array where the schema requires a keyed object). Every check is designed to prove it can catch its bug, so results are trustworthy.
>
> If it's a fit, a mention/link in your UCP guides could save your readers a lot of debugging. Happy to answer anything — and no ask beyond that.
>
> Thanks for the good writing,
> [your name] · spck.dev · unofficial & open source

## Email B — to a newsletter / tool directory

**Subject:** Free open-source UCP conformance checker (tool submission)

> Hi [name],
>
> Sharing a free, open-source tool your audience might find useful: **spck.dev** — a conformance checker for the Universal Commerce Protocol. Browser check at spck.dev/check, plus `pip install spck-conformance` and a GitHub Action.
>
> What's different: every check is kill-rate-tested (proven to catch the defect it's for), so a pass is trustworthy — it even caught bugs in the official reference samples.
>
> MIT-licensed, unofficial, no signup. Repo: github.com/vishkaty/ucp-conformance
>
> Thanks!
> [your name]

---

## Get-listed targets (I can prep the submissions)

- **awesome-ucp** — create it (seed ready in `awesome-ucp.md`); list spck-conformance.
- **Shopify agents/UCP docs "community tools"** — if/when such a page exists.
- **PyPI / GitHub topics** — done (`ucp`, `conformance`, `agentic-commerce`, …).
- General dev-tool directories (once there's a Show HN/PH link to point at).

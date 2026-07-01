# awesome-ucp — create it (nobody has yet)

There's no `awesome-ucp` list. That's an opportunity: **creating the canonical
community list is a credibility + SEO play** (it ranks for "UCP resources", it's linkable
from the spec/community, and spck-conformance is naturally listed on it — as the author,
tastefully, not spammily).

## Do this

1. Create a public repo `spck-dev/awesome-ucp` (or `vishkaty/awesome-ucp`).
2. Seed it with the README below.
3. Submit it to the awesome ecosystem (sindresorhus/awesome has an addition process) and
   link it from the spck.dev footer + your launch posts.
4. Invite PRs — a list the community contributes to becomes *the* list, and you own it.

## README seed

````markdown
# Awesome UCP [![Awesome](https://awesome.re/badge.svg)](https://awesome.re)

A curated list of resources for the **Universal Commerce Protocol (UCP)** — the open
standard for agentic and headless commerce.

> Community-maintained. Not affiliated with the official UCP project.

## Contents
- [Official](#official)
- [Specification](#specification)
- [SDKs & servers](#sdks--servers)
- [Tools](#tools)
- [Guides & articles](#guides--articles)

## Official
- [ucp.dev](https://ucp.dev) — protocol home & documentation.
- [Universal-Commerce-Protocol](https://github.com/Universal-Commerce-Protocol) — spec, schemas, samples, conformance suite.
- [Official conformance suite](https://github.com/Universal-Commerce-Protocol/conformance) — the reference test suite (targets 2026-01-23).

## Specification
- [Spec overview](https://ucp.dev/2026-04-08/specification/overview/)
- [Checkout (REST)](https://ucp.dev/2026-04-08/specification/checkout/) · [Checkout (MCP)](https://ucp.dev/2026-04-08/specification/checkout-mcp/)
- [ucp-schema](https://github.com/Universal-Commerce-Protocol/ucp-schema) — the schema validator.

## SDKs & servers
- [python-sdk](https://github.com/Universal-Commerce-Protocol/python-sdk) — official Python SDK.
- [samples](https://github.com/Universal-Commerce-Protocol/samples) — reference REST servers (Python, Node.js) + A2A.

## Tools
- [spck-conformance](https://github.com/vishkaty/ucp-conformance) — kill-rate-validated conformance checker (browser / pip / GitHub Action; REST + MCP). Web: [spck.dev/check](https://spck.dev/check).
- *(add yours via PR)*

## Guides & articles
- *(add via PR)*

## Contributing
PRs welcome — add a resource with a one-line, neutral description. Keep it useful.
````

## If someone else creates awesome-ucp first

Submit a single PR adding the **Tools** entry above. Neutral one-liner, no marketing.

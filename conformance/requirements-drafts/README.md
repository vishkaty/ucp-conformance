# Requirements Register Drafts (unreleased spec versions)

Pre-built requirement registers for capabilities that exist on UCP spec `main`
but are **not yet in any tagged release**. This directory is deliberately a
sibling of `requirements/` — the selfcheck gates (`verify_register.py`,
`verify_register_completeness.py`) sweep only `requirements/<version>/`, and
that is correct: rows here are preparation, not certification claims. Nothing
in this directory feeds the build, the coverage page, or any published number.

## Why this exists

The next UCP release is visibly building on `main` (100+ commits past
v2026-04-08: buyer consent, split payments, identity OAuth foundation,
delegated IdP, Web Bot Auth, policies[], Actions, permalink,
fulfillment-methods-on-catalog, loyalty, webhook headers, keys[], idempotency
contract). Extracting the normative requirements from the drafts now means
day-one conformance coverage when the release tags, instead of starting to
read the changelog that day.

## Layout

`requirements-drafts/next/<area>.json` — same row schema as
`requirements/README.md`, with two differences:

- `_spec_commit` is the **main-branch SHA the rows were extracted at** (drafts
  move; every promotion re-verifies).
- `versions` is `["unreleased"]` until promotion.

## Promotion protocol (when the release tags)

1. Diff each draft area against the tagged release (`git diff <draft-sha>..<tag>`
   on the source files); update any row whose text moved. Quotes must be
   re-verified **verbatim at the tagged SHA** — a draft quote is never trusted.
2. Move the area file to `requirements/<new-version>/`, set `versions`, update
   `_spec_commit` to the tagged SHA.
3. Wire tests or documented waivers until `verify_register_completeness` is
   green — promotion is not complete while any MUST row is unmapped.
4. Delete the draft file. This directory should be empty between release
   cycles.

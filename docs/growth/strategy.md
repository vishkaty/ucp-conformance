# spck.dev — growth & marketing playbook

Goal: **1000 developers using it**, zero ad budget, organic and self-sustaining, with a
brand presence *beyond* the founder's personal accounts, and (optionally) revenue that
recycles into promotion.

Guiding truth: **you already have a neutral brand — the `spck.dev` domain.** The whole
strategy is to make `spck.dev` the credible home, seed it with the founder's reach, and
let compounding loops fill it.

---

## 0) The model in one line

> Seed with the founder's voice → funnel everyone to the `spck.dev` brand home → the
> badge / CI / SEO loops compound → monetize the operator side to fund more promotion.

Launches spike and fade. **Loops** are what get you to 1000. Everything below either
*seeds* a loop or *is* one.

---

## 1) Stand up the brand (one afternoon, all free)

Beyond your personal accounts, create the brand home. Match the name/handle everywhere.

| surface | action | note |
|---|---|---|
| **LinkedIn** | Create a **Company Page** "spck.dev" | LinkedIn ToS = personal profiles are humans; a Company Page is the legit brand presence, admin'd from your profile |
| **X / Twitter** | `@spckdev` (or nearest free) | product account |
| **GitHub** | *(optional)* org `spck-dev`, move the repo | looks like "a project," not a side-repo. Do it **before** more links accumulate; GitHub redirects old URLs, but update the Action/pip/PyPI refs |
| **dev.to** | Create an **Organization** | post launch content under it |
| **Reddit / Bluesky / Mastodon / YouTube** | reserve the handle | fill later |
| **Email** | `hello@spck.dev` | for PyPI/GitHub/press contact |

**Brand kit — paste this identically into every bio/description:**

- **Name:** spck.dev — UCP Conformance
- **One-liner:** An honest, kill-rate-validated conformance checker for the Universal Commerce Protocol (UCP). Free & open source. Unofficial.
- **Description:** Point it at any UCP server — browser, `pip`, or CI — for a capability-scoped report that proves every check catches the bug it's for. REST + MCP.
- **Links:** https://spck.dev · https://github.com/vishkaty/ucp-conformance · https://pypi.org/project/spck-conformance
- **Always include:** "Independent/unofficial. Not affiliated with the UCP project."

---

## 2) The compounding loops (already built — now leverage them)

1. **Badge loop** *(live)*. Every merchant who passes embeds
   `[![UCP conformance](https://spck.dev/api/badge?server=…)](https://spck.dev/check?server=…)`
   → their README/site links back → new visitors. **Push the badge hard** in the CLI
   output, the web result, the README, and the launch posts. This is your #1 free
   distribution channel.
2. **CI loop** *(live)*. Once the GitHub Action is in a repo, it runs every push and
   shows up as a PR check in front of the whole team. Near-100% retention, and each
   teammate is a new impression.
3. **Shareable-result loop** *(live)*. `spck.dev/check?server=…` is a shareable report.
   Encourage sharing ("tweet your result").
4. **Content/SEO loop** *(to seed)*. Evergreen posts rank for the queries below and pull
   steady traffic as UCP grows.
5. **Ecosystem loop** *(to seed)*. Being linked from official/community resources = trusted
   discovery.

---

## 3) Seed the loops — the launch (week 1)

Order matters. **Lead with the findings, not the tool** — the bugs are the credible hook.

1. **Publish the writeup** (`docs/launch/writeup.md`) on the dev.to org + spck.dev/blog;
   cross-post to the LinkedIn Company Page *and* share from your personal profile.
2. **Show HN** — weekday morning US time; reply to every comment for the first 3 hours.
3. **Ecosystem PRs** *(highest credibility)*: file the real deviations you found (Node.js
   sample: capabilities-as-array, services-as-object) as issues/PRs on
   `Universal-Commerce-Protocol/samples`, with repro. The core team noticing you is worth
   more than any post. Also submit to / create **awesome-ucp**.
4. **Short social** on X + LinkedIn (personal → brand), tagging the agentic-commerce
   conversation.
5. **Dogfood**: add the badge + a great README to the repo before you post.

---

## 4) Where the UCP developers actually are (channels)

- **Hacker News** — Show HN (methodology + findings angle).
- **Reddit** — r/programming, r/webdev, r/ecommerce, r/shopify, r/aiagents.
- **dev.to / Hashnode / Medium** — the long-form writeup + a methodology deep-dive.
- **LinkedIn** — retail/commerce-tech audience; the "agentic commerce" narrative is hot there.
- **X** — follow & reply in the UCP / agentic-commerce / Shopify-dev threads.
- **Shopify dev community** (shopify.dev/agents, forums) — millions of Shopify merchants are on UCP; "is my Shopify store UCP-ready" is a real query.
- **GitHub** — Discussions on the UCP repos; answer conformance questions with the tool as a natural (non-spammy) solution.
- **Google/UCP community** — any Discord/Slack/forum; dev-advocate outreach once you have the findings post as a hook.

---

## 5) SEO — own the searches (ongoing)

Target queries: **"UCP conformance"**, "test UCP server", "UCP compliance checker",
"is my Shopify store UCP ready", "Universal Commerce Protocol testing".

- README H1 + first paragraph and the PyPI/landing `<title>`/meta lead with these terms.
- One canonical guide page ("How to test your UCP server for conformance") that ranks and
  funnels to `/check` + `pip`.
- GitHub repo **topics**: `ucp`, `universal-commerce-protocol`, `conformance`,
  `agentic-commerce`, `shopify`, `mcp`.

---

## 6) Money that funds growth (design now, build later)

Keep the **developer surface free forever** — CLI, Action, web check, basic badge. That's
the growth engine; never gate it. Monetize the **operator/platform** side, where
willingness-to-pay actually is:

- **Continuous monitoring + alerts** — "email/Slack me when my live UCP server starts
  deviating." Recurring value → recurring revenue. (Small paid tier.)
- **Platform / multi-merchant dashboard** — anyone integrating *many* merchants
  (marketplaces, agents, aggregators) pays to check them in bulk with history.
- **Verified badge tier** — free badge for everyone; a *monitored/verified* badge for a fee.

Note the badge is simultaneously the growth loop *and* the upsell. Any revenue → hosting +
sponsoring a UCP/agentic-commerce newsletter or community (which seeds more loops). Do
**not** monetize before there's a user base; the free tool is the funnel.

---

## 7) Metrics — define "1000 developers"

Vanity (page views) ≠ adoption. Track the funnel:

- **North star:** GitHub Action adoptions + PyPI installs (weekly).
- PyPI download stats (pypistats), GitHub stars/forks, `/check` runs and badge requests
  (you already have `global:stats` analytics), repos using the Action (GitHub code search
  for `vishkaty/ucp-conformance`).
- Optimize whichever step leaks. "1000" = ~1000 distinct installs/Action-repos, not visits.

---

## 8) 30 / 60 / 90 (zero budget)

- **Days 1–14:** brand home + kit; launch writeup + Show HN; ecosystem PRs + awesome-ucp;
  README/SEO polish; dogfood the badge.
- **Days 15–45:** content cadence (1 post/week — findings, methodology, spec-update
  notes); engage in channels; chase the badge into a few real merchant READMEs; keep the
  tool the *most current* as the spec moves (that's the moat).
- **Days 46–90:** ship the first paid hook (continuous monitoring) once there's usage;
  reinvest any revenue into a community sponsorship; measure the north star and double down
  on the best channel.

---

## 9) The one honest caveat

Adoption is coupled to **UCP's own growth** — it's ~6 months old. You can't force the
market bigger; you *can* own the "conformance" niche within it so that as UCP grows,
you're the default. The loops above are how you win that. Being the **most rigorous and
the most current** tool is the durable advantage — and you already are.

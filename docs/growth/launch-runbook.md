# Launch runbook — fire in one ~20-min sitting

Everything is written. Do these in order on a weekday morning (US Eastern). Your only
job is paste + submit + reply to comments. I own everything else.

**Prereq (I'll do / you unblock):** file the samples issue first (credibility anchor).

---

## 1. Hacker News — Show HN  (highest reach; no API, must be you)

**Post at:** https://news.ycombinator.com/submit  (Tue–Thu, ~8–10am ET)

**Title** (80 char max — this fits):
```
Show HN: A UCP conformance checker that proves its checks catch bugs
```

**URL:** `https://spck.dev/check`

**First comment** (post immediately after submitting — this is where the substance goes):
```
I built spck.dev, an unofficial conformance checker for the Universal Commerce Protocol (UCP — the Google/Shopify agentic-commerce standard).

The thing I cared about: a conformance tool that can false-pass is worse than none. So every check has to prove it fails when the server is wrong. Each check is kill-rate tested (I inject the exact defect it should catch; if the check still passes, it's blocked from release), anchored to the official ucp-schema validator, and traced to a verbatim spec clause. The whole suite self-validates in CI.

Pointing it at the reference implementations already surfaced real deviations — e.g. the official Node.js sample serves `capabilities` as an array where the profile schema requires a keyed object (the Python sample and production Shopify stores get it right). Reported upstream with a repro.

- Web: https://spck.dev/check (paste a store URL)
- CLI: pip install spck-conformance
- CI: a GitHub Action
- Code: https://github.com/vishkaty/ucp-conformance

Happy to answer questions about the kill-rate methodology or the findings. It's unofficial and never claims "certified."
```

**Then:** stay for ~2–3 hrs and reply to every comment. Engagement in the first hour decides front page.

---

## 2. Reddit — r/shopify  (your beachhead)

**Post at:** https://www.reddit.com/r/shopify/submit  (check the sub's self-promo rules first; frame as helpful, not an ad)

**Title:**
```
I built a free tool to check if your Shopify store's UCP profile is set up correctly
```

**Body:**
```
Shopify turned on UCP (the agentic-commerce standard) for every store, so every store now has a /.well-known/ucp profile — but "is mine actually correct?" isn't obvious.

I made a free, open-source checker: paste your store URL and it fetches the profile and validates it (version, capabilities, service transports) against the spec. https://spck.dev/check

It's unofficial and open source (pip/CLI/GitHub Action too if you want deeper checks in CI). Not affiliated with Shopify or the UCP project — just something I built while working with UCP. Feedback welcome.
```

---

## 3. Reddit — r/programming or r/webdev  (the methodology angle)

**Title:**
```
I built a conformance checker and made every check prove it can catch the bug it's for
```

**Body:**
```
Working with the Universal Commerce Protocol (UCP), I wanted a conformance checker I could actually trust — one that can't silently pass a broken server.

So every check is kill-rate tested: I inject the exact defect it should catch, and if the check still passes it's blocked from release. Checks are anchored to the official schema validator and cite a spec clause, and the whole suite self-validates in CI (goes red if any check loses its ability to catch defects).

Testing it against the reference implementations found real deviations. Writeup + code: https://github.com/vishkaty/ucp-conformance — web version: https://spck.dev/check

Curious what folks think of the kill-rate approach to trusting a test suite.
```

---

## 4. Product Hunt  (optional, same day or next)

**Post at:** https://www.producthunt.com/posts/new

- **Name:** spck.dev
- **Tagline (60 char):** `The honest UCP conformance checker`
- **Description:** `Point it at any Universal Commerce Protocol server — browser, pip, or CI — for a capability-scoped report where every check is proven to catch the bug it's for. REST + MCP. Free & open source. Unofficial.`
- **Links:** https://spck.dev/check · https://github.com/vishkaty/ucp-conformance
- **First comment (maker):** the Show HN first-comment text works here too.

---

## Timing & etiquette
- One weekday morning. HN first, then Reddit within the hour, PH same day.
- Read each subreddit's self-promotion rules; lead with usefulness.
- Reply to everything for the first few hours — that's what compounds.
- Measure: `/check` runs, `pip` installs (pypistats), GitHub stars over 48h.

## What I own around this
Content (above), the samples issue, awesome-ucp, the SEO guide page, and outreach
emails to UCP writers (drafted for your approval). The badge loop is already live to
catch the traffic.

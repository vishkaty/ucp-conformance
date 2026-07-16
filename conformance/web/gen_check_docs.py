#!/usr/bin/env python3
"""
gen_check_docs.py — generate the published check register (SITE-R-026).

Emits, deterministically, from public/coverage.json (the SAME source /coverage
renders, so the pages can never say something the product doesn't):

    public/checks/index.html      one register browser, grouped by area
    public/checks/<ID>.html       one page per covered requirement id (status=check)

Deterministic by construction: sorted iteration, no timestamps, content derived
only from coverage.json. The checkdocs gate (site_gates.py) regenerates into a
temp dir and byte-compares, so a stale or hand-edited page fails CI.

Usage:  python3 conformance/web/gen_check_docs.py [--out DIR]   (default public/checks)
"""
import argparse
import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
COVERAGE = ROOT / "public" / "coverage.json"

AREA_NAMES = {
    "discovery": "Discovery", "checkout": "Checkout", "cart": "Cart",
    "order": "Order", "payment": "Payment", "signatures": "Signatures",
    "errors": "Errors", "negotiation": "Version negotiation",
    "identity-linking": "Identity linking", "catalog": "Catalog",
    "fulfillment": "Fulfillment", "discount": "Discounts",
    "transports": "Transports", "security": "Security", "totals": "Totals",
    "validation": "Validation", "idempotency": "Idempotency",
}

TESTABILITY_NOTES = {
    "hosted-testable": "runs in the hosted web check and the CLI",
    "needs-receiver": "needs a receiver the suite controls, so it runs in the CLI/full suite",
    "needs-config": "unlocks when a merchant config supplies real product/discount data",
}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _page(title, description, body, depth=1):
    """Shared page shell — links /site.css (the site's single design system)."""
    prefix = "../" * 0  # absolute URLs are used throughout
    del prefix
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23059669'/><text x='16' y='22' text-anchor='middle' fill='white' font-family='system-ui' font-weight='700' font-size='16'>S</text></svg>">
<link rel="stylesheet" href="/site.css">
<style>
  .wrap {{ max-width:820px; margin:0 auto; padding:40px 24px 8px; }}
  .wrap h1 {{ font-size:clamp(24px,4vw,32px); font-weight:800; letter-spacing:-.8px; margin:0 0 10px; color:var(--ink); }}
  .wrap .sub {{ color:var(--muted); margin:0 0 24px; font-size:15px; line-height:1.65; max-width:660px; }}
  .wrap h2 {{ font-size:19px; font-weight:800; letter-spacing:-.3px; margin:36px 0 6px; color:var(--ink); }}
  .meta-grid {{ display:grid; grid-template-columns:130px 1fr; gap:8px 16px; margin:18px 0; font-size:14.5px; }}
  .meta-grid dt {{ color:var(--muted); }}
  .meta-grid dd {{ margin:0; color:var(--ink); }}
  .req-quote {{ border-left:3px solid var(--primary); padding:10px 14px; margin:14px 0; background:var(--card,rgba(5,150,105,.05)); font-size:15px; line-height:1.6; }}
  .chip {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:12.5px; border:1px solid var(--muted); color:var(--muted); margin-right:6px; }}
  .idx-area {{ margin:26px 0 4px; }}
  .idx-list {{ list-style:none; padding:0; margin:6px 0 0; }}
  .idx-list li {{ margin:7px 0; font-size:14.5px; line-height:1.55; }}
  .idx-list .rid {{ font-family:ui-monospace,monospace; font-size:13px; margin-right:8px; }}
  .note {{ font-size:13px; color:var(--muted); line-height:1.6; margin:10px 0 0; }}
</style>
</head>
<body>
<nav class="site-nav"><a class="brand" href="/"><span class="mk">S</span>spck</a>
<div class="links"><a href="/check">Merchants</a><a href="/agent">Agents</a>
<a href="/coverage">Coverage</a><a href="/docs">Docs</a>
<a href="https://github.com/vishkaty/ucp-conformance" rel="noopener">GitHub ↗</a></div></nav>
<div class="wrap">
{body}
<footer class="page-foot"><p class="note">spck.dev is an independent, unofficial project — not affiliated with,
endorsed by, or a substitute for the official UCP conformance suite; the official suite is authoritative.
Requirement text is quoted from the UCP specification (Apache-2.0), cited to the exact pinned source line.</p></footer>
</div>
</body>
</html>
"""


def _citation_url(source, pin):
    repo_alias, rest = source.split(":", 1)
    path, _, line = rest.partition("#")
    frag = f"#{line}" if line else ""
    return f"https://github.com/Universal-Commerce-Protocol/ucp/blob/{pin}/{path}{frag}"


def load():
    d = json.loads(COVERAGE.read_text())
    pins = d["spec_pins"]
    by_id = {}
    for ver in sorted(d["versions"]):
        for row in d["versions"][ver]["rows"]:
            if row.get("status") != "check":
                continue
            by_id.setdefault(row["id"], []).append({**row, "version": ver})
    return by_id, pins


def render_detail(rid, rows, pins):
    first = rows[0]
    versions = [r["version"] for r in rows]
    area = AREA_NAMES.get(first["area"], first["area"].title())
    title = f"{rid} — {area} conformance check | spck.dev"
    desc = (f"UCP requirement {rid}: what the spck.dev conformance suite verifies, "
            f"the spec citation, and the versions it applies to.")

    # Requirement text per version — collapse when identical across versions.
    texts = {r["version"]: r["requirement"] for r in rows}
    unique = sorted(set(texts.values()))
    if len(unique) == 1:
        quote_html = f'<blockquote class="req-quote">{esc(unique[0])}</blockquote>'
    else:
        quote_html = "".join(
            f'<p class="note">{esc(v)}:</p>'
            f'<blockquote class="req-quote">{esc(texts[v])}</blockquote>'
            for v in sorted(texts))

    cites = "".join(
        f'<dd><a href="{esc(_citation_url(r["source"], pins[r["version"]]))}" rel="noopener">'
        f'{esc(r["source"])}</a> <span class="chip">{esc(r["version"])}</span></dd>'
        for r in rows)

    testability = first.get("testability", "")
    tnote = TESTABILITY_NOTES.get(testability, testability)

    body = f"""<header class="page-head">
  <div class="eyebrow"><a href="/checks/">Check register</a> / {esc(area)}</div>
  <h1>{esc(rid)}</h1>
  <p class="sub">A requirement the spck.dev suite checks on UCP servers. The quoted text is
the normative requirement; the citation links to the exact line of the pinned spec source
it was taken from.</p>
</header>
{quote_html}
<dl class="meta-grid">
  <dt>Keyword</dt><dd>{esc(first["keyword"])}</dd>
  <dt>Area</dt><dd>{esc(area)}</dd>
  <dt>Versions</dt><dd>{" ".join(f'<span class="chip">{esc(v)}</span>' for v in versions)}</dd>
  <dt>Testability</dt><dd>{esc(tnote)}</dd>
  <dt>Spec source</dt>{cites}
</dl>
<h2>How this is graded</h2>
<p class="sub">A deviation on a MUST fails the run and shows the requirement next to the
actual response. Checks the suite cannot validate soundly are reported inconclusive or
not-tested — never a silent pass. The full verdict semantics are on the
<a href="/rubric">grading rubric</a>.</p>
<h2>Run it against your store</h2>
<p class="sub">The <a href="/check">hosted check</a> covers the read-only surface; the
<a href="/docs">CLI and GitHub Action</a> run the full suite, including this check when
your store declares the capability it belongs to.</p>
"""
    return _page(title, desc, body)


def render_index(by_id, pins):
    del pins
    count = len(by_id)
    areas = {}
    for rid, rows in by_id.items():
        areas.setdefault(rows[0]["area"], []).append((rid, rows))

    sections = []
    for area in sorted(areas, key=lambda a: AREA_NAMES.get(a, a)):
        name = AREA_NAMES.get(area, area.title())
        items = "".join(
            f'<li><span class="rid"><a href="/checks/{esc(rid)}">{esc(rid)}</a></span>'
            f'{esc(rows[0]["requirement"][:140])}{"…" if len(rows[0]["requirement"]) > 140 else ""}</li>'
            for rid, rows in sorted(areas[area]))
        sections.append(f'<h2 class="idx-area">{esc(name)} '
                        f'<span class="chip">{len(areas[area])}</span></h2>'
                        f'<ul class="idx-list">{items}</ul>')

    body = f"""<header class="page-head">
  <div class="eyebrow">Check register</div>
  <h1>Every requirement the suite checks, cited to the spec</h1>
  <p class="sub">The spck.dev conformance suite is built from a requirements register scraped
from the UCP specification and quote-verified against SHA-pinned sources. This page lists the
{count} requirements the suite currently covers; each links to the requirement text and its
exact spec citation. For the full picture including requirements not yet covered, see the
<a href="/coverage">coverage matrix</a>; for how verdicts are graded, the
<a href="/rubric">rubric</a>.</p>
</header>
{"".join(sections)}
"""
    title = "UCP check register — every covered requirement, spec-cited | spck.dev"
    desc = (f"The {count} UCP specification requirements the spck.dev conformance suite "
            f"covers, each with requirement text and a pinned spec citation.")
    return _page(title, desc, body)


def generate(outdir):
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    by_id, pins = load()
    written = []
    for rid in sorted(by_id):
        f = outdir / f"{rid}.html"
        f.write_text(render_detail(rid, by_id[rid], pins))
        written.append(f.name)
    (outdir / "index.html").write_text(render_index(by_id, pins))
    written.append("index.html")
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "public" / "checks"))
    args = ap.parse_args()
    written = generate(args.out)
    print(f"check-docs: wrote {len(written)} page(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

/**
 * /api/badge?server=<url> — an embeddable SVG preview badge (shields-style).
 *
 * Reflects the discovery + profile-structure PREVIEW (same logic as /api/conformance):
 * the message is `preview N/M`, never a conformance verdict.
 * Heavily cached so a popular README doesn't hammer the merchant's server. Links to the
 * shareable report at /check?server=<url>. This is the growth loop: every merchant who
 * passes embeds the badge, which points back here.
 *
 * Unofficial. Not affiliated with or endorsed by the UCP project.
 */
import { preview, bumpStat } from "./conformance.js";

const LABEL = "UCP conformance";

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Approx text width at 11px Verdana (shields uses precise metrics; this is close enough).
function textWidth(s) {
  let w = 0;
  for (const c of s) w += "ilj.,:;'|!".includes(c) ? 3.2 : "mwMW@".includes(c) ? 9.5 : 6.6;
  return w;
}

function svg(message, color) {
  const label = LABEL;
  const pad = 12;
  const lw = Math.round(textWidth(label) + pad);
  const mw = Math.round(textWidth(message) + pad);
  const w = lw + mw;
  const lx = (lw / 2) * 10;
  const mx = (lw + mw / 2) * 10;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="20" role="img" aria-label="${esc(label)}: ${esc(message)}">
<title>${esc(label)}: ${esc(message)}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="${w}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)">
<rect width="${lw}" height="20" fill="#555"/>
<rect x="${lw}" width="${mw}" height="20" fill="${color}"/>
<rect width="${w}" height="20" fill="url(#s)"/>
</g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110" transform="scale(.1)">
<text x="${lx}" y="150" fill="#010101" fill-opacity=".3">${esc(label)}</text>
<text x="${lx}" y="140">${esc(label)}</text>
<text x="${mx}" y="150" fill="#010101" fill-opacity=".3">${esc(message)}</text>
<text x="${mx}" y="140">${esc(message)}</text>
</g>
</svg>`;
}

function badgeResponse(body) {
  return new Response(body, {
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      // cache at the edge + client so README views don't re-probe the merchant each time
      "Cache-Control": "public, max-age=600, s-maxage=600",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

export async function onRequestGet(context) {
  const server = new URL(context.request.url).searchParams.get("server");
  if (!server) return badgeResponse(svg("no server", "#9f9f9f"));
  let out;
  try { out = await preview(server); } catch { out = { error: "error" }; }
  context.waitUntil(bumpStat(context.env, "badgeHits", out.server));
  let message, color;
  if (out.error) { message = "unreachable"; color = "#9f9f9f"; }
  else {
    // PREVIEW, never a verdict (D5-20, PLAN-v3 §2.18): the four structural discovery
    // checks are a preview of the CLI/Action suite, so the badge reads `preview N/M`
    // (N passed of M preview checks) and NEVER the word "conformant" — a registered
    // wording (conformance/web/doc_claims.json DOC-BADGE-001) pinned by the web-unit
    // badge test. M = the COUNTED discovery-stage ids of conformance/web/preview_parity.json
    // (the badge inherits the id map through summary; D5-11).
    const s = out.summary || {};
    const passed = Number.isFinite(s.passed) ? s.passed : 0;
    const total = Number.isFinite(s.total) ? s.total : 0;
    message = `preview ${passed}/${total}`;
    color = total > 0 && passed === total ? "#3fb950" : "#e05d44";
  }
  return badgeResponse(svg(message, color));
}

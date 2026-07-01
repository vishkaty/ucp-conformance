/**
 * /api/badge?server=<url> — an embeddable SVG conformance badge (shields-style).
 *
 * Reflects the discovery + profile-structure preview (same logic as /api/conformance).
 * Heavily cached so a popular README doesn't hammer the merchant's server. Links to the
 * shareable report at /check?server=<url>. This is the growth loop: every merchant who
 * passes embeds the badge, which points back here.
 *
 * Unofficial. Not affiliated with or endorsed by the UCP project.
 */
import { preview } from "./conformance.js";

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
  let message, color;
  if (out.error) { message = "unreachable"; color = "#9f9f9f"; }
  else if ((out.summary?.deviations || 0) > 0) {
    message = `${out.summary.deviations} deviation${out.summary.deviations === 1 ? "" : "s"}`;
    color = "#e05d44";
  } else { message = "conformant"; color = "#3fb950"; }
  return badgeResponse(svg(message, color));
}

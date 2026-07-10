/**
 * /api/track?event=<name> — anonymous, cookieless page-engagement beacon.
 *
 * The agent-side surfaces (/agent, /sandbox) need adoption to be visible in admin,
 * exactly like /check does via bumpStat. This records ONLY an allow-listed event
 * name into global:stats (USERS KV) — no URL, no PII, no body. Analytics must never
 * break the page, so every failure is swallowed and still returns 204.
 */
import { bumpStat } from "./conformance.js";

// allow-list — an unknown event is ignored, so the counter can't be polluted.
const EVENTS = new Set([
  "agent_view",       // /agent offering page viewed
  "sandbox_view",     // /sandbox interop demo opened
  "sandbox_case",     // a defect case selected in the demo
  "agent_cta",        // clicked through from an agent-side CTA
  // depth signal: a RETURNING visitor (localStorage flag set on a prior visit). A return is
  // worth far more than a first-view — it's the "they came back / rely on it" signal.
  "agent_return",     // /agent viewed by a returning visitor
  "sandbox_return",   // /sandbox opened by a returning visitor
  // page-view funnel (SITE-R-022) — every public page's view + return, so the
  // home→check/agent→depth path is visible end to end in admin
  "home_view", "home_return",
  "check_view", "check_return",
  "docs_view", "docs_return",
  "coverage_view", "coverage_return",
]);

function done() {
  return new Response(null, {
    status: 204,
    headers: { "Access-Control-Allow-Origin": "*", "Cache-Control": "no-store" },
  });
}

async function handle(context) {
  try {
    const url = new URL(context.request.url);
    const ev = url.searchParams.get("event");
    if (ev && EVENTS.has(ev)) await bumpStat(context.env, ev);
  } catch { /* never break on a beacon */ }
  return done();
}

export const onRequestPost = handle;
export const onRequestGet = handle;

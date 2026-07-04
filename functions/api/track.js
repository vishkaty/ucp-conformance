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

/**
 * /api/conformance — server-side UCP discovery + profile-structure preview.
 *
 * The browser can't fetch an arbitrary merchant's /.well-known/ucp (CORS), so this
 * Cloudflare Pages Function does it server-side and runs the DISCOVERY + PROFILE-
 * STRUCTURE subset of the spck conformance methodology. It is a PREVIEW: the full
 * 37 kill-rate-validated checks (checkout, order, discount, catalog, cart, totals…)
 * run only in the CLI / GitHub Action. Kept intentionally small + stable so it does
 * not drift from the authoritative Python engine.
 *
 * Unofficial. Not affiliated with or endorsed by the UCP project.
 */

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const REV_DOMAIN_RE = /^[a-z0-9]+(\.[a-z0-9_]+)+$/;

// Reject obvious SSRF targets; the tool is for PUBLIC merchant profiles.
function blockedHost(host) {
  const h = (host || "").toLowerCase();
  return (
    h === "localhost" || h === "0.0.0.0" || h.endsWith(".local") ||
    /^127\./.test(h) || /^10\./.test(h) || /^192\.168\./.test(h) ||
    /^169\.254\./.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h) || h === "[::1]"
  );
}

// Pure, testable: run the discovery/profile-structure checks over a fetched profile.
export function runPreviewChecks(profile, contentType) {
  const ucp = (profile && profile.ucp) || profile || {};
  const version = ucp.version;
  const caps = ucp.capabilities;
  const svc = (ucp.services && ucp.services["dev.ucp.shopping"]) || null;
  const transports = Array.isArray(svc)
    ? svc.map((s) => s && s.transport).filter(Boolean)
    : svc && typeof svc === "object" ? ["(non-conformant: object, not array)"] : [];

  const checks = [];
  const add = (id, requirement, ok, observed) =>
    checks.push({ id, requirement, status: ok ? "pass" : "deviation", observed });

  add("discovery.version", "Profile MUST declare a dated version (YYYY-MM-DD).",
      typeof version === "string" && DATE_RE.test(version), `version = ${JSON.stringify(version)}`);

  add("discovery.content_type", "The profile SHOULD be served as application/json.",
      /application\/json/i.test(contentType || ""), `Content-Type: ${contentType || "(none)"}`);

  const capsOk = caps && !Array.isArray(caps) && typeof caps === "object" &&
    Object.keys(caps).length > 0 && Object.keys(caps).every((k) => REV_DOMAIN_RE.test(k));
  add("discovery.capabilities_object",
      "capabilities MUST be a keyed object of reverse-domain names (DISC-001).",
      capsOk,
      Array.isArray(caps) ? "capabilities is an ARRAY (should be a keyed object)"
        : caps && typeof caps === "object" ? `keys: ${Object.keys(caps).join(", ")}`
        : `capabilities = ${JSON.stringify(caps)}`);

  add("discovery.services_array",
      "services.<name> MUST be an array of {transport, endpoint} entries (DISC-007).",
      Array.isArray(svc) && svc.length > 0,
      Array.isArray(svc) ? `transports: ${transports.join(", ")}`
        : `dev.ucp.shopping = ${svc ? "object (should be an array)" : "(absent)"}`);

  const passed = checks.filter((c) => c.status === "pass").length;
  const deviations = checks.filter((c) => c.status === "deviation").length;
  return {
    version: version || null,
    capabilities: caps && !Array.isArray(caps) ? Object.keys(caps) : caps || [],
    transports,
    checks,
    summary: { passed, deviations, total: checks.length },
  };
}

const DISCLAIMER =
  "Preview only — discovery + profile structure. Unofficial; not affiliated with or " +
  "endorsed by the UCP project. Run the spck-conformance CLI for the full, " +
  "kill-rate-validated check suite (checkout, order, discount, catalog, cart, totals…).";

export async function preview(serverUrl) {
  let u;
  try { u = new URL(serverUrl); } catch { return { error: "invalid server URL" }; }
  if (!/^https?:$/.test(u.protocol)) return { error: "server must be http(s)" };
  if (blockedHost(u.hostname)) return { error: "refusing to probe a private/loopback host" };

  const wk = `${u.protocol}//${u.host}/.well-known/ucp`;
  let resp, text;
  try {
    resp = await fetch(wk, {
      headers: { "User-Agent": "spck-conformance-preview/0.1", Accept: "application/json" },
      redirect: "manual",
      cf: { cacheTtl: 30 },
      signal: AbortSignal.timeout(10000),
    });
    text = (await resp.text()).slice(0, 100000);
  } catch (e) {
    return { server: u.host, error: `could not fetch ${wk}: ${e}` };
  }
  if (resp.status >= 300 && resp.status < 400)
    return { server: u.host, error: `discovery MUST NOT redirect (got ${resp.status})` };
  if (resp.status !== 200)
    return { server: u.host, error: `discovery returned HTTP ${resp.status}` };

  let profile;
  try { profile = JSON.parse(text); }
  catch { return { server: u.host, error: "profile is not valid JSON" }; }

  const report = runPreviewChecks(profile, resp.headers.get("content-type"));
  return { server: u.host, well_known: wk, ...report, disclaimer: DISCLAIMER };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

export async function onRequestGet(context) {
  const server = new URL(context.request.url).searchParams.get("server");
  if (!server) return json({ error: "pass ?server=<url>" }, 400);
  const out = await preview(server);
  return json(out, out.error && !out.checks ? 400 : 200);
}

export async function onRequestPost(context) {
  let body = {};
  try { body = await context.request.json(); } catch {}
  if (!body.server) return json({ error: "POST { server: <url> }" }, 400);
  const out = await preview(body.server);
  return json(out, out.error && !out.checks ? 400 : 200);
}

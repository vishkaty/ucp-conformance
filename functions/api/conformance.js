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

// Optional caller-supplied headers (e.g. x-firmly-host for multi-tenant staging
// routing). Strictly sanitized: x-* names only, no proxy-identity headers, ≤3
// headers, values ≤200 chars, no CR/LF injection.
const HEADER_NAME_RE = /^x-[a-z0-9-]{1,40}$/;
const HEADER_DENY = new Set(["x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip"]);
export function cleanHeaders(h) {
  const out = {};
  if (!h || typeof h !== "object" || Array.isArray(h)) return out;
  for (const [k, v] of Object.entries(h)) {
    const name = String(k).trim().toLowerCase();
    if (!HEADER_NAME_RE.test(name) || HEADER_DENY.has(name)) continue;
    out[name] = String(v).replace(/[\r\n]/g, "").slice(0, 200);
    if (Object.keys(out).length >= 3) break;
  }
  return out;
}

// Anonymous engagement counter (global:stats in USERS KV) — the public surfaces
// (/check, badge) previously left no trace, so adoption was invisible in admin.
export async function bumpStat(env, field, host) {
  try {
    const s = (await env.USERS.get("global:stats", "json")) || {};
    s[field] = (s[field] || 0) + 1;
    const day = new Date().toISOString().slice(0, 10);
    s.dailyActivity = s.dailyActivity || {};
    const d = (s.dailyActivity[day] = s.dailyActivity[day] || {});
    d[field] = (d[field] || 0) + 1;
    if (host) {
      s.instantDomains = s.instantDomains || {};
      if (s.instantDomains[host] != null || Object.keys(s.instantDomains).length < 300)
        s.instantDomains[host] = (s.instantDomains[host] || 0) + 1;
    }
    await env.USERS.put("global:stats", JSON.stringify(s));
  } catch { /* analytics must never break a check */ }
}

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
  "Preview — discovery + profile structure (+ read-only catalog probes when declared). " +
  "Unofficial; not affiliated with or endorsed by the UCP project. Run the " +
  "spck-conformance CLI for the full, kill-rate-validated check suite " +
  "(checkout, order, discount, catalog, cart, totals…).";

// Read-only catalog probes — the ONLY live checks safe to run anonymously online:
// catalog search/lookup are read-only by semantics. Write-path checks (checkout
// create/complete, cart) stay CLI-only where the merchant runs them consciously.
async function catalogChecks(profile, extraHeaders) {
  const ucp = (profile && profile.ucp) || profile || {};
  const caps = (ucp.capabilities && !Array.isArray(ucp.capabilities)) ? ucp.capabilities : {};
  const svc = (ucp.services && ucp.services["dev.ucp.shopping"]) || [];
  const rest = Array.isArray(svc) ? svc.find((s) => s && s.transport === "rest") : null;
  if (!rest || !rest.endpoint || !("dev.ucp.shopping.catalog.search" in caps)) return [];
  let base;
  try {
    base = new URL(rest.endpoint);
    if (!/^https?:$/.test(base.protocol) || blockedHost(base.hostname)) return [];
  } catch { return []; }
  const headers = { "User-Agent": "spck-conformance-preview/0.1",
                    "UCP-Agent": 'profile="https://spck.dev/agent"',
                    "Content-Type": "application/json", ...extraHeaders };
  const post = (path, body) =>
    fetch(base.href.replace(/\/$/, "") + path, {
      method: "POST", headers, body: JSON.stringify(body),
      redirect: "manual", signal: AbortSignal.timeout(10000),
    }).then(async (r) => ({ status: r.status, json: await r.json().catch(() => null) }))
      .catch((e) => ({ status: 0, json: null, err: String(e) }));

  const checks = [];
  const add = (id, requirement, ok, observed) =>
    checks.push({ id, requirement, status: ok ? "pass" : "deviation", observed });

  const s = await post("/catalog/search", { query: "*" });
  const prods = s.json && Array.isArray(s.json.products) ? s.json.products : null;
  const shapeOk = s.status === 200 && prods !== null && prods.every((p) =>
    p && p.id && p.title && Array.isArray(p.variants) && p.variants.length);
  add("catalog.search_shape",
      "Search returns a products array; each product carries id, title, variants (CAT-012).",
      shapeOk, `HTTP ${s.status}, products: ${prods === null ? "(absent)" : prods.length}`);

  const e = await post("/catalog/search", { query: "zzz_no_such_product_zzz" });
  const eOk = e.status === 200 && e.json && Array.isArray(e.json.products) &&
    e.json.products.length === 0 && !(e.json.messages || []).length;
  add("catalog.empty_search",
      "A no-match search returns products: [] without error messages (CAT-012).",
      eOk, `HTTP ${e.status}, products: ${e.json && e.json.products ? e.json.products.length : "(absent)"}`);

  if ("dev.ucp.shopping.catalog.lookup" in caps && prods && prods.length) {
    const id = prods[0].id;
    const l = await post("/catalog/lookup", { ids: [id] });
    const lp = l.json && Array.isArray(l.json.products) ? l.json.products : null;
    const inputsOk = l.status === 200 && lp && lp.length && lp.every((p) =>
      (p.variants || []).every((v) => Array.isArray(v.inputs) && v.inputs.length &&
        v.inputs.every((i) => i && i.id)));
    add("catalog.lookup_inputs",
        "Lookup variants carry a non-empty inputs correlation array (CAT-017/018).",
        inputsOk, `HTTP ${l.status}, looked up ${JSON.stringify(id)}`);
  }
  return checks;
}

export async function preview(serverUrl, opts = {}) {
  let u;
  try { u = new URL(serverUrl); } catch { return { error: "invalid server URL" }; }
  if (!/^https?:$/.test(u.protocol)) return { error: "server must be http(s)" };
  if (blockedHost(u.hostname)) return { error: "refusing to probe a private/loopback host" };
  const extra = cleanHeaders(opts.headers);

  // Preserve the caller's query string (multi-tenant gateways route the merchant
  // via e.g. ?domain=store.example.com on the well-known URL).
  const wk = `${u.protocol}//${u.host}/.well-known/ucp${u.search || ""}`;
  let resp, text;
  try {
    resp = await fetch(wk, {
      headers: { "User-Agent": "spck-conformance-preview/0.1", Accept: "application/json", ...extra },
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
  if (opts.deep) {                       // /api/conformance runs the live probes;
    const live = await catalogChecks(profile, extra);   // the badge stays shallow/fast
    report.checks.push(...live);
    report.summary.passed += live.filter((c) => c.status === "pass").length;
    report.summary.deviations += live.filter((c) => c.status === "deviation").length;
    report.summary.total += live.length;
  }
  const out = { server: u.host, well_known: wk, ...report, disclaimer: DISCLAIMER };
  if (Object.keys(extra).length) out.custom_headers_sent = Object.keys(extra);
  return out;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const server = url.searchParams.get("server");
  if (!server) return json({ error: "pass ?server=<url> [&header=Name:Value up to 3, x-* only]" }, 400);
  const headers = {};
  for (const hv of url.searchParams.getAll("header")) {
    const i = hv.indexOf(":");
    if (i > 0) headers[hv.slice(0, i).trim()] = hv.slice(i + 1).trim();
  }
  const out = await preview(server, { deep: true, headers });
  context.waitUntil(bumpStat(context.env, "instantChecks", out.server));
  return json(out, out.error && !out.checks ? 400 : 200);
}

export async function onRequestPost(context) {
  let body = {};
  try { body = await context.request.json(); } catch {}
  if (!body.server) return json({ error: "POST { server: <url>, headers?: { \"x-…\": \"value\" } }" }, 400);
  const out = await preview(body.server, { deep: true, headers: body.headers });
  context.waitUntil(bumpStat(context.env, "instantChecks", out.server));
  return json(out, out.error && !out.checks ? 400 : 200);
}

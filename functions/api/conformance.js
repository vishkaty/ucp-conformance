/**
 * /api/conformance — server-side UCP discovery + profile-structure preview.
 *
 * The browser can't fetch an arbitrary merchant's /.well-known/ucp (CORS), so this
 * Cloudflare Pages Function does it server-side and runs the DISCOVERY + PROFILE-
 * STRUCTURE subset of the spck conformance methodology. It is a PREVIEW: the full
 * kill-rate-validated check suite (checkout, order, discount, catalog, cart, totals…)
 * runs only in the CLI / GitHub Action — no count is advertised here (site_gates docclaims). Kept intentionally small + stable so it does
 * not drift from the authoritative Python engine: every row is mapped 1:1 to engine checks by
 * conformance/web/preview_parity.json and the parity gate (conformance/ci/preview_parity.py, D5-11)
 * grades a frozen capture set on both sides.
 *
 * Unofficial. Not affiliated with or endorsed by the UCP project.
 */

import { idsFor } from "./preview_ids.js";   // GENERATED from conformance/web/preview_parity.json (D5-11)

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const REV_DOMAIN_RE = /^[a-z0-9]+(\.[a-z0-9_]+)+$/;

// Every rendered row cites its engine twin(s) and requirement ids for the profile's
// version from the committed id map — never a hard-coded "(DISC-001)" in prose — and
// `counted:false` rows (a SHOULD) render but never enter summary / the badge N/M.
function rowFactory(version) {
  const map = idsFor(version);
  return (checks) => (id, requirement, ok, observed, status) => {
    const m = map[id] || { counted: false, engine: [], req_ids: [] };
    checks.push({ id, requirement, status: status || (ok ? "pass" : "deviation"), observed,
                  engine: m.engine, req_ids: m.req_ids, counted: m.counted });
  };
}
function summarize(checks) {
  const counted = checks.filter((c) => c.counted);
  return { passed: counted.filter((c) => c.status === "pass").length,
           deviations: counted.filter((c) => c.status === "deviation").length,
           total: counted.length };
}

// Optional caller-supplied headers (e.g. x-tenant-host for multi-tenant staging
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
  const add = rowFactory(version)(checks);
  const legacy0111 = version === "2026-01-11";   // array capabilities + service OBJECT are canonical there

  add("discovery.version", "Profile MUST declare a dated version (YYYY-MM-DD).",
      typeof version === "string" && DATE_RE.test(version), `version = ${JSON.stringify(version)}`);

  add("discovery.content_type", "The profile SHOULD be served as application/json (report-only).",
      /application\/json/i.test(contentType || ""), `Content-Type: ${contentType || "(none)"}`);

  // 2026-01-11: capabilities is an ARRAY of {name} entries (capability.json $defs/discovery);
  // 01-23 and later: a keyed object of reverse-domain names — the engine's p_reverse_domain.
  const capsOk = legacy0111
    ? Array.isArray(caps) && caps.length > 0 &&
      caps.every((e) => e && typeof e.name === "string" && REV_DOMAIN_RE.test(e.name))
    : caps && !Array.isArray(caps) && typeof caps === "object" &&
      Object.keys(caps).length > 0 && Object.keys(caps).every((k) => REV_DOMAIN_RE.test(k));
  add("discovery.capabilities_object",
      legacy0111 ? "capabilities MUST be an array of reverse-domain-named entries at 2026-01-11."
                 : "capabilities MUST be a keyed object of reverse-domain names.",
      capsOk,
      Array.isArray(caps) ? (legacy0111 ? `entries: ${caps.map((e) => e && e.name).join(", ")}`
                                        : "capabilities is an ARRAY (should be a keyed object)")
        : caps && typeof caps === "object" ? (legacy0111 ? "capabilities is a keyed object (2026-01-11 expects an array)"
                                                         : `keys: ${Object.keys(caps).join(", ")}`)
        : `capabilities = ${JSON.stringify(caps)}`);

  // 2026-01-11: services.<name> is an OBJECT whose `rest` member carries the endpoint
  // (service_schema.json); 01-23 and later: an array of {transport, endpoint} entries.
  const svcOk = legacy0111
    ? !!(svc && !Array.isArray(svc) && typeof svc === "object" && svc.rest && svc.rest.endpoint)
    : Array.isArray(svc) && svc.length > 0;
  add("discovery.services_array",
      legacy0111 ? "services.<name> MUST be an object whose rest member carries the endpoint at 2026-01-11."
                 : "services.<name> MUST be an array of {transport, endpoint} entries with a REST endpoint.",
      svcOk,
      Array.isArray(svc) ? `transports: ${transports.join(", ")}`
        : `dev.ucp.shopping = ${svc ? (legacy0111 ? "object" : "object (should be an array)") : "(absent)"}`);

  return {
    version: version || null,
    capabilities: caps && !Array.isArray(caps) ? Object.keys(caps)
      : Array.isArray(caps) ? caps.map((e) => (e && e.name) || e) : [],
    transports: legacy0111 && svc && !Array.isArray(svc) ? Object.keys(svc) : transports,
    checks,
    summary: summarize(checks),
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
async function catalogChecks(profile, extraHeaders, query) {
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
  const add = rowFactory(ucp.version)(checks);

  const q = (typeof query === "string" && query.trim().slice(0, 80)) || "*";
  const s = await post("/catalog/search", { query: q });
  // Requiring signed/authenticated agents is spec-LEGITIMATE, not a deviation —
  // production stores commonly gate even reads. Report honestly as not-tested.
  const AUTH_CODES = new Set(["agent_signature_required", "requires_sign_in", "requires_identity_linking"]);
  const authMsg = ((s.json && s.json.messages) || []).find((m) => m && AUTH_CODES.has(m.code));
  if (s.status === 401 || s.status === 403 || authMsg) {
    add("catalog.live_probes",
        "Read-only catalog probes (search shape, empty search, lookup input correlation).", false,
        `server requires request authentication (HTTP ${s.status}` +
          (authMsg ? `, ${authMsg.code}` : "") +
          `) — anonymous probes can't run; test with a registered agent via the CLI`,
        "not-tested");
    return checks;
  }
  const prods = s.json && Array.isArray(s.json.products) ? s.json.products : null;
  const shapeOk = s.status === 200 && prods !== null && prods.every((p) =>
    p && p.id && p.title && Array.isArray(p.variants) && p.variants.length);
  add("catalog.search_shape",
      "Search returns a products array; each product carries id, title, variants.",
      shapeOk, `HTTP ${s.status}, query ${JSON.stringify(q)}, products: ${prods === null ? "(absent)" : prods.length}`);

  const e = await post("/catalog/search", { query: "zzz_no_such_product_zzz" });
  const eOk = e.status === 200 && e.json && Array.isArray(e.json.products) &&
    e.json.products.length === 0 && !(e.json.messages || []).length;
  add("catalog.empty_search",
      "A no-match search returns products: [] without error messages.",
      eOk, `HTTP ${e.status}, products: ${e.json && e.json.products ? e.json.products.length : "(absent)"}`);

  if ("dev.ucp.shopping.catalog.lookup" in caps && prods && prods.length) {
    const id = prods[0].id;
    const l = await post("/catalog/lookup", { ids: [id] });
    const lp = l.json && Array.isArray(l.json.products) ? l.json.products : null;
    const inputsOk = l.status === 200 && lp && lp.length && lp.every((p) =>
      (p.variants || []).every((v) => Array.isArray(v.inputs) && v.inputs.length &&
        v.inputs.every((i) => i && i.id)));
    add("catalog.lookup_inputs",
        "Lookup variants carry a non-empty inputs correlation array.",
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
    const live = await catalogChecks(profile, extra, opts.query);   // the badge stays shallow/fast
    report.checks.push(...live);
    report.summary = summarize(report.checks);   // counted rows only (id map)
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
  const out = await preview(server, { deep: true, headers, query: url.searchParams.get("query") });
  context.waitUntil(bumpStat(context.env, "instantChecks", out.server));
  return json(out, out.error && !out.checks ? 400 : 200);
}

export async function onRequestPost(context) {
  let body = {};
  try { body = await context.request.json(); } catch {}
  if (!body.server) return json({ error: "POST { server: <url>, headers?: { \"x-…\": \"value\" } }" }, 400);
  const out = await preview(body.server, { deep: true, headers: body.headers, query: body.query });
  context.waitUntil(bumpStat(context.env, "instantChecks", out.server));
  return json(out, out.error && !out.checks ? 400 : 200);
}

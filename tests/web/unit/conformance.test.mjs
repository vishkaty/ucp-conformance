// Unit tests for functions/api/conformance.js — the /check + badge engine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { stubFetch, jsonResp } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const { cleanHeaders, runPreviewChecks, preview } =
  await import(pathToFileURL(path.join(ROOT, "functions/api/conformance.js")).href);
import fs from "node:fs";
// SITE-R-034 (D5-11): the committed id map conformance/web/preview_parity.json is the single
// source of which preview ids are COUNTED, their engine twins and requirement ids per version.
const ID_MAP = JSON.parse(fs.readFileSync(path.join(ROOT, "conformance/web/preview_parity.json"), "utf8")).preview;
const countedIds = (stage) => Object.entries(ID_MAP).filter(([, m]) => m.counted && m.stage === stage).map(([id]) => id);
const SHALLOW = countedIds("discovery").length;          // badge / shallow preview denominator
const DEEP = SHALLOW + countedIds("catalog").length;     // /api/conformance with catalog probes

// ── cleanHeaders: the sanitizer between users and outbound requests ──────────
test("cleanHeaders keeps only sane x-* headers", () => {
  const out = cleanHeaders({
    "x-tenant-host": "staging.example.com",
    "Authorization": "Bearer evil",           // not x-*
    "x-forwarded-for": "1.2.3.4",             // proxy-identity: denied
    "X-Ok": "line1\r\ninjected",              // CRLF stripped
    "x-a": 1, "x-b": 2, "x-c": 3, "x-d": 4,   // capped at 3 total
  });
  assert.equal(out["x-tenant-host"], "staging.example.com");
  assert.ok(!("authorization" in out) && !("Authorization" in out));
  assert.ok(!("x-forwarded-for" in out));
  assert.equal(out["x-ok"], "line1injected");
  assert.equal(Object.keys(out).length, 3);
});

test("cleanHeaders tolerates junk input", () => {
  assert.deepEqual(cleanHeaders(null), {});
  assert.deepEqual(cleanHeaders("x-a: b"), {});
  assert.deepEqual(cleanHeaders(["x-a"]), {});
  assert.equal(cleanHeaders({ "x-long": "v".repeat(999) })["x-long"].length, 200);
});

// ── runPreviewChecks: profile-structure grading ───────────────────────────────
const GOOD_PROFILE = {
  ucp: {
    version: "2026-04-08",
    capabilities: { "dev.ucp.shopping.checkout": [{}] },
    services: { "dev.ucp.shopping": [{ transport: "rest", endpoint: "https://m.example.com" }] },
  },
};

test("conformant profile passes every counted structure check (denominator from the id map)", () => { // SITE-R-034
  const r = runPreviewChecks(GOOD_PROFILE, "application/json");
  assert.equal(r.summary.total, SHALLOW);
  assert.equal(r.summary.passed, SHALLOW);
  assert.equal(r.summary.deviations, 0);
});

test("summary counts only counted ids — discovery.content_type (SHOULD) is report-only", () => { // SITE-R-034
  const r = runPreviewChecks(GOOD_PROFILE, "text/html");
  const ct = r.checks.find((c) => c.id === "discovery.content_type");
  assert.ok(ct, "the content-type row still renders");
  assert.equal(ct.status, "deviation");
  assert.equal(ct.counted, false);
  assert.equal(r.summary.total, SHALLOW, "report-only rows never enter the denominator");
  assert.equal(r.summary.deviations, 0, "a report-only deviation is not counted");
  assert.equal(ID_MAP["discovery.content_type"].counted, false);
});

test("every preview row carries its engine twin(s) and requirement ids from the id map, per version", () => { // SITE-R-034
  const r = runPreviewChecks(GOOD_PROFILE, "application/json");      // 2026-04-08
  const by = Object.fromEntries(r.checks.map((c) => [c.id, c]));
  assert.deepEqual(by["discovery.capabilities_object"].req_ids, ["OVR-001"]);   // was mis-cited DISC-001
  assert.deepEqual(by["discovery.capabilities_object"].engine, ["profile.reverse_domain_names"]);
  assert.deepEqual(by["discovery.services_array"].req_ids, ["DISC-005"]);       // was mis-cited DISC-007
  assert.deepEqual(by["discovery.version"].req_ids, ["OVR-010"]);
  for (const c of r.checks) {
    assert.deepEqual(c.engine, ID_MAP[c.id].engine["2026-04-08"] || []);
    assert.deepEqual(c.req_ids, ID_MAP[c.id].req_ids["2026-04-08"] || []);
    assert.doesNotMatch(c.requirement, /\((DISC|OVR|CAT)-\d{3}[^)]*\)/, "no hard-coded citation in the prose");
  }
  const old = runPreviewChecks({ ucp: { ...GOOD_PROFILE.ucp, version: "2026-01-23" } }, "application/json");
  const byOld = Object.fromEntries(old.checks.map((c) => [c.id, c]));
  assert.deepEqual(byOld["discovery.capabilities_object"].req_ids, ["DISC-001"]);
  assert.deepEqual(byOld["discovery.services_array"].req_ids, ["DISC-007"]);
});

test("2026-01-11 profiles are graded in their own shape (array capabilities with name; service object with rest.endpoint)", () => { // SITE-R-034
  const p0111 = { ucp: { version: "2026-01-11",
    capabilities: [{ name: "dev.ucp.shopping.checkout", version: "2026-01-11" }],
    services: { "dev.ucp.shopping": { rest: { endpoint: "https://m.example.com" } } } } };
  const r = runPreviewChecks(p0111, "application/json");
  const by = Object.fromEntries(r.checks.map((c) => [c.id, c.status]));
  assert.equal(by["discovery.capabilities_object"], "pass");
  assert.equal(by["discovery.services_array"], "pass");
  assert.equal(r.summary.deviations, 0);
  const keyed = runPreviewChecks({ ucp: { ...p0111.ucp, capabilities: { "dev.ucp.shopping.checkout": [{}] } } }, "application/json");
  assert.equal(keyed.checks.find((c) => c.id === "discovery.capabilities_object").status, "deviation",
    "the keyed object is the WRONG shape at 2026-01-11 (engine parity)");
});

test("array capabilities and object services are deviations", () => {
  const bad = { ucp: { version: "2026-04-08", capabilities: ["a"],
    services: { "dev.ucp.shopping": { rest: { endpoint: "x" } } } } };
  const r = runPreviewChecks(bad, "text/html");
  const by = Object.fromEntries(r.checks.map((c) => [c.id, c.status]));
  assert.equal(by["discovery.capabilities_object"], "deviation");
  assert.equal(by["discovery.services_array"], "deviation");
  assert.equal(by["discovery.content_type"], "deviation");
});

// ── preview(): fetch behavior, SSRF, redirects, deep probes ──────────────────
test("preview refuses private hosts and bad schemes", async () => {
  assert.match((await preview("http://127.0.0.1:8080")).error, /private|loopback/);
  assert.match((await preview("ftp://x.example.com")).error, /http/);
  assert.match((await preview("not a url")).error, /invalid/);
});

test("preview preserves the caller's query string (?domain= gateways)", async () => {
  const calls = stubFetch([["/.well-known/ucp", () => jsonResp(GOOD_PROFILE)]]);
  const out = await preview("https://gw.example.com?domain=shop.example.com");
  assert.equal(out.well_known, "https://gw.example.com/.well-known/ucp?domain=shop.example.com");
  assert.ok(calls[0].url.includes("?domain=shop.example.com"));
});

test("preview flags redirects as an error (discovery MUST NOT redirect)", async () => {
  stubFetch([["/.well-known/ucp", () => new Response("", { status: 302,
    headers: { location: "https://www.example.com" } })]]);
  assert.match((await preview("https://m.example.com")).error, /redirect/);
});

test("shallow preview (badge path) runs no catalog probes", async () => {
  const calls = stubFetch([["/.well-known/ucp", () => jsonResp({ ucp: {
    ...GOOD_PROFILE.ucp,
    capabilities: { "dev.ucp.shopping.catalog.search": [{}] } } })]]);
  const out = await preview("https://m.example.com");
  assert.equal(out.summary.total, SHALLOW);
  assert.equal(calls.length, 1);            // only the well-known fetch
});

const CATALOG_PROFILE = { ucp: {
  version: "2026-04-08",
  capabilities: { "dev.ucp.shopping.catalog.search": [{}],
                  "dev.ucp.shopping.catalog.lookup": [{}] },
  services: { "dev.ucp.shopping": [{ transport: "rest", endpoint: "https://m.example.com" }] } } };
const PRODUCT = { id: "p1", title: "T", variants: [{ id: "v1", inputs: [{ id: "p1" }] }] };

test("deep preview runs catalog probes, forwards headers + custom query", async () => {
  const calls = stubFetch([
    ["/.well-known/ucp", () => jsonResp(CATALOG_PROFILE)],
    ["/catalog/search", (u, o) => {
      const q = JSON.parse(o.body).query;
      return jsonResp({ products: q === "sunglasses" ? [PRODUCT] : [] });
    }],
    ["/catalog/lookup", () => jsonResp({ products: [PRODUCT] })],
  ]);
  const out = await preview("https://m.example.com",
    { deep: true, headers: { "x-tenant-host": "s.example.com" }, query: "sunglasses" });
  assert.equal(out.summary.total, DEEP);
  assert.equal(out.summary.deviations, 0);
  assert.deepEqual(out.custom_headers_sent, ["x-tenant-host"]);
  for (const c of calls)                     // header forwarded to EVERY request
    assert.equal(c.opts.headers["x-tenant-host"], "s.example.com");
  assert.equal(JSON.parse(calls[1].opts.body).query, "sunglasses");
});

test("auth-gated catalog (401 agent_signature_required) is not-tested, never a deviation", async () => {
  stubFetch([
    ["/.well-known/ucp", () => jsonResp(CATALOG_PROFILE)],
    ["/catalog/search", () => jsonResp({ messages: [{ type: "error",
      code: "agent_signature_required", severity: "recoverable" }] }, 401)],
  ]);
  const out = await preview("https://m.example.com", { deep: true });
  const live = out.checks.find((c) => c.id === "catalog.live_probes");
  assert.equal(live.status, "not-tested");
  assert.match(live.observed, /agent_signature_required/);
  assert.equal(out.summary.deviations, 0);
});

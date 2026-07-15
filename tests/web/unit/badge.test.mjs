// SITE-R-025 — public opt-in conformance badge: /api/badge/<reportId>.svg is an
// unauthenticated SVG derived from the saved report's summary; missing ids get a
// 200 "unknown" badge (embedded <img> never breaks); every interpolated value is
// XML-escaped; responses are publicly cacheable. Driven through the REAL router.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { mockEnv, ctx, get } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const { onRequest } =
  await import(pathToFileURL(path.join(ROOT, "functions/api/[[path]].js")).href);

const B = "https://spck.dev";

async function call(env, req) {
  const resp = await onRequest(ctx(req, env));
  return { status: resp.status, text: await resp.text(), resp };
}

function seedReport(env, id, summary) {
  env.REPORTS.store.set(
    `report:${id}`,
    JSON.stringify({ id, userId: "u@example.com", date: "2026-07-15T12:00:00.000Z",
      summary, config: {}, deviations: [], tests: [] })
  );
}

test("badge for a saved report is an SVG carrying grade and counts", async () => {   // SITE-R-025
  const env = mockEnv();
  seedReport(env, "11111111-1111-4111-8111-111111111111",
    { pass: 5, fail: 1, skip: 2, total: 8, grade: "B" });
  const r = await call(env, get(`${B}/api/badge/11111111-1111-4111-8111-111111111111.svg`));
  assert.equal(r.status, 200);
  assert.equal(r.resp.headers.get("Content-Type"), "image/svg+xml");
  assert.match(r.resp.headers.get("Cache-Control") || "", /public/);
  assert.match(r.text, /<svg/);
  assert.match(r.text, /UCP conformance/);
  assert.match(r.text, /B/);
  assert.match(r.text, /5\/8/);
});

test("badge requires no authentication", async () => {                              // SITE-R-025
  const env = mockEnv();
  seedReport(env, "22222222-2222-4222-8222-222222222222",
    { pass: 8, fail: 0, skip: 0, total: 8, grade: "A" });
  // No session token anywhere in the request.
  const r = await call(env, get(`${B}/api/badge/22222222-2222-4222-8222-222222222222.svg`));
  assert.equal(r.status, 200);
  assert.match(r.text, /8\/8/);
});

test("missing or expired report id yields a 200 'unknown' badge", async () => {     // SITE-R-025
  const env = mockEnv();
  const r = await call(env, get(`${B}/api/badge/99999999-9999-4999-8999-999999999999.svg`));
  assert.equal(r.status, 200);
  assert.equal(r.resp.headers.get("Content-Type"), "image/svg+xml");
  assert.match(r.text, /unknown/i);
  assert.doesNotMatch(r.text, /undefined|null|NaN/);
});

test("interpolated values are XML-escaped (no markup injection via report data)", async () => { // SITE-R-025
  const env = mockEnv();
  seedReport(env, "33333333-3333-4333-8333-333333333333",
    { pass: 1, fail: 0, skip: 0, total: 1, grade: '"><script>alert(1)</script>' });
  const r = await call(env, get(`${B}/api/badge/33333333-3333-4333-8333-333333333333.svg`));
  assert.equal(r.status, 200);
  assert.doesNotMatch(r.text, /<script>/);
  assert.match(r.text, /&lt;script&gt;|&quot;/);
});

test("a malformed id (not a uuid) is refused without a KV lookup", async () => {     // SITE-R-025
  const env = mockEnv();
  let looked = false;
  const inner = env.REPORTS.get.bind(env.REPORTS);
  env.REPORTS.get = async (...a) => { looked = true; return inner(...a); };
  const r = await call(env, get(`${B}/api/badge/..%2F..%2Fetc.svg`));
  assert.equal(r.status, 200);              // still an image, still never breaks
  assert.match(r.text, /unknown/i);
  assert.equal(looked, false);
});

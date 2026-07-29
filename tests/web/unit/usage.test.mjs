// SITE-R-024 — honest "real usage" summary: bot-free first-party funnel, and a
// test/internal-vs-real merchant-domain split so a dashboard can never read a QA
// store (e.g. ascent-testing.myshopify.com) as confirmed external adoption.
// Pure functions are unit-tested directly; the wiring is checked through the router.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { mockEnv, ctx, post, get, stubFetch, jsonResp, MockKV } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const mod = await import(pathToFileURL(path.join(ROOT, "functions/api/[[path]].js")).href);
const { onRequest, classifyDomain, summarizeUsage } = mod;

const B = "https://spck.dev";
const SUPER = "katyal.vishal@gmail.com";

// ── classifyDomain ──────────────────────────────────────────────────────────
test("classifyDomain flags obvious test/internal hosts", () => {
  for (const h of [
    "ascent-testing.myshopify.com", "example.com", "www.example.org",
    "localhost", "localhost:8788", "shop.test", "acme.local",
    "demo.acme.com", "staging.acme.com", "sandbox.foo.com", "",
  ]) assert.equal(classifyDomain(h), "test", `${h} should be test`);
});

test("classifyDomain treats a real merchant domain as real", () => {
  for (const h of ["shop.gymshark.com", "store.allbirds.com", "burrow.com"])
    assert.equal(classifyDomain(h), "real", `${h} should be real`);
});

// ── summarizeUsage ──────────────────────────────────────────────────────────
test("summarizeUsage builds a bot-free funnel and real/test domain split", () => {
  const u = summarizeUsage({
    home_view: 58, agent_view: 29, sandbox_view: 21, check_view: 11,
    coverage_view: 9, docs_view: 6,
    home_return: 9, agent_return: 7, check_return: 3, coverage_return: 3,
    sandbox_return: 2, docs_return: 1,
    instantChecks: 7, report_saved: 1, totalUsers: 5, totalTestRuns: 20,
    instantDomains: { "ascent-testing.myshopify.com": 1 },
    domainsTested: { "burrow.com": 3, "ascent-testing.myshopify.com": 2 },
  });
  assert.equal(u.humanViews, 134);
  assert.equal(u.returns, 25);
  assert.equal(u.instantChecks, 7);
  assert.equal(u.reportsSaved, 1);
  assert.equal(u.registeredUsers, 5);
  // union of instant + registered domains, deduped:
  assert.equal(u.domainsTotal, 2);          // ascent-testing.* and burrow.com
  assert.equal(u.domainsReal, 1);           // only burrow.com
  assert.equal(u.domainsTest, 1);
  assert.equal(u.allTest, false);
  assert.equal(u.realDomains[0].host, "burrow.com");
  assert.equal(u.realDomains[0].runs, 3);
});

test("summarizeUsage sets allTest when every checked domain is test/internal", () => {
  const u = summarizeUsage({ instantChecks: 7, instantDomains: { "ascent-testing.myshopify.com": 1 } });
  assert.equal(u.domainsTotal, 1);
  assert.equal(u.domainsReal, 0);
  assert.equal(u.allTest, true);
});

test("summarizeUsage is safe on empty/undefined stats", () => {
  for (const s of [undefined, null, {}]) {
    const u = summarizeUsage(s);
    assert.equal(u.humanViews, 0);
    assert.equal(u.domainsTotal, 0);
    assert.equal(u.allTest, false);        // no domains → not "all test"
  }
});

// ── wired through /api/admin/stats ────────────────────────────────────────────
async function call(env, req, waits = []) {
  const resp = await onRequest(ctx(req, env, waits));
  let body = null; try { body = await resp.clone().json(); } catch {}
  return { status: resp.status, body };
}
async function loginSuper(env) {
  stubFetch([["api.resend.com", () => jsonResp({ id: "email_1" })]]);
  await call(env, post(`${B}/api/auth/send-otp`, { email: SUPER }));
  const otp = await env.OTP_STORE.get(`otp:${SUPER}`, "json");
  const r = await call(env, post(`${B}/api/auth/verify-otp`, { email: SUPER, code: otp.code }));
  return r.body.token;
}

test("/api/admin/stats returns the usage summary alongside raw stats", async () => {
  const env = mockEnv({
    USERS: new MockKV({
      "global:stats": JSON.stringify({
        home_view: 10, instantChecks: 4, dailyActivity: {},
        instantDomains: { "burrow.com": 4, "ascent-testing.myshopify.com": 1 },
      }),
    }),
  });
  const token = await loginSuper(env);
  const r = await call(env, get(`${B}/api/admin/stats`, { Authorization: `Bearer ${token}` }));
  assert.equal(r.status, 200);
  assert.ok(r.body.usage, "response must include a usage summary");
  assert.equal(r.body.usage.domainsReal, 1);
  assert.equal(r.body.usage.domainsTest, 1);
  assert.equal(r.body.usage.humanViews, 10);
});

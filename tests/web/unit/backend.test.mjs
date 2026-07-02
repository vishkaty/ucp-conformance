// Unit tests for functions/api/[[path]].js — driven through the REAL exported
// router (onRequest) with an in-memory KV env, so routing + handlers + auth are
// all exercised exactly as deployed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { MockKV, mockEnv, ctx, post, get, stubFetch, jsonResp } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const { onRequest } =
  await import(pathToFileURL(path.join(ROOT, "functions/api/[[path]].js")).href);

const B = "https://spck.dev";
const ADMIN = "katyal.vishal@gmail.com";

async function call(env, req, waits = []) {
  const resp = await onRequest(ctx(req, env, waits));
  let body = null;
  try { body = await resp.clone().json(); } catch {}
  return { status: resp.status, body, resp };
}

async function login(env, email) {
  stubFetch([["api.resend.com", () => jsonResp({ id: "email_1" })]]);
  await call(env, post(`${B}/api/auth/send-otp`, { email }));
  const otp = await env.OTP_STORE.get(`otp:${email}`, "json");
  const r = await call(env, post(`${B}/api/auth/verify-otp`, { email, code: otp.code }));
  return r.body.token;
}

// ── OTP / auth ────────────────────────────────────────────────────────────────
test("send-otp: 500 when email delivery unconfigured", async () => {
  const env = mockEnv();                                  // no RESEND_API_KEY
  const r = await call(env, post(`${B}/api/auth/send-otp`, { email: "a@b.co" }));
  assert.equal(r.status, 500);
});

test("send-otp: surfaces Resend failure as 502 (no silent ok)", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  stubFetch([["api.resend.com", () => jsonResp({ error: "bad key" }, 401)]]);
  const r = await call(env, post(`${B}/api/auth/send-otp`, { email: "a@b.co" }));
  assert.equal(r.status, 502);
});

test("send-otp stores the code; verify-otp enforces attempts and issues a session", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  stubFetch([["api.resend.com", () => jsonResp({ id: "email_1" })]]);
  const email = "user@example.com";
  const r1 = await call(env, post(`${B}/api/auth/send-otp`, { email }));
  assert.equal(r1.status, 200);
  const otp = await env.OTP_STORE.get(`otp:${email}`, "json");
  assert.match(otp.code, /^\d{6}$/);

  const bad = await call(env, post(`${B}/api/auth/verify-otp`, { email, code: "000000" }));
  assert.equal(bad.status, 401);

  const ok = await call(env, post(`${B}/api/auth/verify-otp`, { email, code: otp.code }));
  assert.equal(ok.status, 200);
  assert.ok(ok.body.token);
  assert.equal(ok.body.user.isAdmin, false);

  const me = await call(env, get(`${B}/api/auth/me`,
    { Authorization: `Bearer ${ok.body.token}` }));
  assert.equal(me.status, 200);
  assert.equal(me.body.user.email, email);
});

test("verify-otp: 429 after too many attempts", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  stubFetch([["api.resend.com", () => jsonResp({ id: "e" })]]);
  const email = "brute@example.com";
  await call(env, post(`${B}/api/auth/send-otp`, { email }));
  for (let i = 0; i < 5; i++)
    await call(env, post(`${B}/api/auth/verify-otp`, { email, code: "999999" }));
  const r = await call(env, post(`${B}/api/auth/verify-otp`, { email, code: "999999" }));
  assert.equal(r.status, 429);
});

// ── admin gating ──────────────────────────────────────────────────────────────
test("admin endpoints reject anonymous and non-admin users", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  assert.equal((await call(env, get(`${B}/api/admin/stats`))).status, 403);
  const token = await login(env, "notadmin@example.com");
  const r = await call(env, get(`${B}/api/admin/stats`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(r.status, 403);
});

test("admin/stats returns defaults for the admin", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const token = await login(env, ADMIN);
  const r = await call(env, get(`${B}/api/admin/stats`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(r.status, 200);
  assert.equal(typeof r.body.stats.totalUsers, "number");
});

// ── admin/metrics: source degradation + the PyPI last-known-good fallback ─────
test("admin/metrics serves last-known-good PyPI marked stale when pypistats 429s", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  await env.REPORTS.put("metrics:pypi:last",
    JSON.stringify({ last_month: 99, as_of: "2026-07-02T02:33:43Z" }));
  const token = await login(env, ADMIN);
  stubFetch([
    ["api.resend.com", () => jsonResp({ id: "e" })],
    ["pypistats.org", () => new Response("<html>rate limited</html>",
      { status: 429, headers: { "Content-Type": "text/html" } })],
    ["api.github.com", () => jsonResp({ stargazers_count: 1, forks_count: 0 })],
  ]);
  const r = await call(env, get(`${B}/api/admin/metrics?refresh=1`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(r.status, 200);
  assert.equal(r.body.pypi.last_month, 99);
  assert.equal(r.body.pypi.stale, true);
  assert.match(r.body.cloudflare.error, /CF_ANALYTICS_TOKEN/);   // unconfigured → honest error
});

test("admin/metrics serves the 30-min cache unless refresh=1", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  await env.REPORTS.put("metrics:cache", JSON.stringify({ pypi: { last_month: 42 } }));
  const token = await login(env, ADMIN);
  const r = await call(env, get(`${B}/api/admin/metrics`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(r.body.cached, true);
  assert.equal(r.body.pypi.last_month, 42);
});

// ── settings round-trip (the custom-headers persistence path) ─────────────────
test("settings save/load round-trips custom headers", async () => {
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const token = await login(env, "user2@example.com");
  const save = await call(env, post(`${B}/api/settings`,
    { headers: { "x-firmly-host": "staging.example.com" }, defaultBase: "https://gw.example.com" },
    { Authorization: `Bearer ${token}` }));
  assert.equal(save.status, 200);
  const load = await call(env, get(`${B}/api/settings`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(load.body.settings.headers["x-firmly-host"], "staging.example.com");
});

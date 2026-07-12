// SITE-R-023 — the /check save-report flow's backend contract: auth-gated save,
// owner-walled retrieval, email→domain mapping on the user, best-effort report
// email that never blocks, and the conversion beacon allow-listed.
// SITE-R-024 — admin visibility: users listing exposes the recorded domains.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { mockEnv, ctx, post, get, stubFetch, jsonResp } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const { onRequest } =
  await import(pathToFileURL(path.join(ROOT, "functions/api/[[path]].js")).href);
const track =
  await import(pathToFileURL(path.join(ROOT, "functions/api/track.js")).href);

const B = "https://spck.dev";
const SUPER = "katyal.vishal@gmail.com";

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

// the shape /check sends after an instant run
const INSTANT = {
  config: { domain: "store.example.com", server: "https://store.example.com" },
  summary: { pass: 5, fail: 1, skip: 2, total: 8, grade: "B" },
  tests: [{ id: "discovery.reachable", status: "pass" },
          { id: "profile.schema", status: "fail", observed: "missing services" }],
};

test("saving requires a session (401 unauthenticated)", async () => {        // SITE-R-023
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const r = await call(env, post(`${B}/api/reports`, INSTANT));
  assert.equal(r.status, 401);
});

test("authed save stores the report; owner reads it back; others are walled", async () => { // SITE-R-023
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const token = await login(env, "merchant@example.com");
  stubFetch([["api.resend.com", () => jsonResp({ id: "email_2" })]]);
  const save = await call(env, post(`${B}/api/reports`, INSTANT,
    { Authorization: `Bearer ${token}` }));
  assert.equal(save.status, 200);
  assert.ok(save.body.id);

  const mine = await call(env, get(`${B}/api/reports/${save.body.id}`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(mine.status, 200);
  assert.equal(mine.body.report.summary.grade, "B");

  const other = await login(env, "stranger@example.com");
  const theirs = await call(env, get(`${B}/api/reports/${save.body.id}`,
    { Authorization: `Bearer ${other}` }));
  assert.equal(theirs.status, 403, "another user must not read someone's report");
});

test("each save records the store domain on the user (email→domain mapping)", async () => { // SITE-R-023
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const token = await login(env, "merchant@example.com");
  stubFetch([["api.resend.com", () => jsonResp({ id: "e" })]]);
  await call(env, post(`${B}/api/reports`, INSTANT, { Authorization: `Bearer ${token}` }));
  await call(env, post(`${B}/api/reports`, INSTANT, { Authorization: `Bearer ${token}` })); // dup domain
  const second = { ...INSTANT, config: { domain: "other.example.org", server: "https://other.example.org" } };
  await call(env, post(`${B}/api/reports`, second, { Authorization: `Bearer ${token}` }));

  const user = await env.USERS.get("merchant@example.com", "json");
  assert.deepEqual(user.domains, ["store.example.com", "other.example.org"],
    "unique, in first-seen order");
});

test("report email is attempted (link included) but a Resend failure never blocks the save", async () => { // SITE-R-023
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const token = await login(env, "merchant@example.com");

  // happy: capture the outbound email
  const sends = stubFetch([["api.resend.com", () => jsonResp({ id: "e" })]]);
  const waits = [];
  const ok = await call(env, post(`${B}/api/reports`, INSTANT,
    { Authorization: `Bearer ${token}` }), waits);
  await Promise.all(waits);
  assert.equal(ok.status, 200);
  const mail = sends.find(c => c.url.includes("api.resend.com"));
  assert.ok(mail, "a report email must be attempted");
  const body = JSON.parse(mail.opts.body);
  assert.ok(body.html.includes(`/check?report=${ok.body.id}`), "email carries the permalink");
  assert.ok(body.to === "merchant@example.com");

  // failure path: Resend down → save still succeeds
  stubFetch([["api.resend.com", () => jsonResp({ error: "down" }, 500)]]);
  const waits2 = [];
  const still = await call(env, post(`${B}/api/reports`, INSTANT,
    { Authorization: `Bearer ${token}` }), waits2);
  await Promise.all(waits2);
  assert.equal(still.status, 200, "email failure must never block the save");
});

test("report_saved conversion beacon is allow-listed", async () => {         // SITE-R-023
  const env = mockEnv();
  const r = await track.onRequestPost(ctx(post(`${B}/api/track?event=report_saved`, {}), env));
  assert.equal(r.status, 204);
  assert.equal((await env.USERS.get("global:stats", "json")).report_saved, 1);
});

test("admin users listing exposes each user's recorded domains", async () => { // SITE-R-024
  const env = mockEnv({ RESEND_API_KEY: "re_test" });
  const token = await login(env, "merchant@example.com");
  stubFetch([["api.resend.com", () => jsonResp({ id: "e" })]]);
  await call(env, post(`${B}/api/reports`, INSTANT, { Authorization: `Bearer ${token}` }));

  const sup = await login(env, SUPER);
  const r = await call(env, get(`${B}/api/admin/users`, { Authorization: `Bearer ${sup}` }));
  assert.equal(r.status, 200);
  const u = (r.body.users || []).find(x => x.email === "merchant@example.com");
  assert.ok(u, "user present");
  assert.deepEqual(u.domains, ["store.example.com"], "domains surfaced to admin");
});

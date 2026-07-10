// SITE-R-020 — admin access control: /api/admin/* auth walls, the dynamic admin
// allowlist manageable ONLY by a super-admin, super-admin undeletable, audit trail.
// Driven through the REAL router like backend.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { mockEnv, ctx, post, get, stubFetch, jsonResp } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const { onRequest } =
  await import(pathToFileURL(path.join(ROOT, "functions/api/[[path]].js")).href);

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
  return r.body;
}

function del(url, body, headers = {}) {
  return new Request(url, { method: "DELETE", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...headers } });
}

// ── auth walls ────────────────────────────────────────────────────────────────
test("admin routes reject unauthenticated requests", async () => {          // SITE-R-020
  const env = mockEnv();
  for (const p of ["stats", "users", "activity", "metrics", "admins", "audit"]) {
    const r = await call(env, get(`${B}/api/admin/${p}`));
    assert.ok([401, 403].includes(r.status), `${p} must be walled, got ${r.status}`);
  }
});

test("a plain logged-in user is NOT an admin (403 everywhere)", async () => { // SITE-R-020
  const env = mockEnv();
  const { token, user } = await login(env, "someone@example.com");
  assert.equal(user.isAdmin, false);
  for (const p of ["stats", "admins"]) {
    const r = await call(env, get(`${B}/api/admin/${p}`,
      { Authorization: `Bearer ${token}` }));
    assert.equal(r.status, 403, `${p} must 403 for non-admin`);
  }
});

test("the super-admin is admin+super by construction", async () => {        // SITE-R-020
  const env = mockEnv();
  const { token, user } = await login(env, SUPER);
  assert.equal(user.isAdmin, true);
  assert.equal(user.isSuperAdmin, true);
  const r = await call(env, get(`${B}/api/admin/stats`,
    { Authorization: `Bearer ${token}` }));
  assert.equal(r.status, 200);
});

// ── allowlist management (super-admin only) ──────────────────────────────────
test("super-admin grants an admin; the grantee gains admin (not super)", async () => { // SITE-R-020
  const env = mockEnv();
  const sup = await login(env, SUPER);
  const add = await call(env, post(`${B}/api/admin/admins`, { email: "Ops@Example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  assert.equal(add.status, 200);

  const who = await login(env, "ops@example.com");           // lowercased on both sides
  assert.equal(who.user.isAdmin, true);
  assert.equal(who.user.isSuperAdmin, false);
  const r = await call(env, get(`${B}/api/admin/stats`,
    { Authorization: `Bearer ${who.token}` }));
  assert.equal(r.status, 200);

  // list shows both tiers
  const list = await call(env, get(`${B}/api/admin/admins`,
    { Authorization: `Bearer ${sup.token}` }));
  assert.deepEqual(list.body.super_admins, [SUPER]);
  assert.ok(list.body.admins.includes("ops@example.com"));
});

test("an ADMIN (non-super) cannot manage the allowlist", async () => {      // SITE-R-020
  const env = mockEnv();
  const sup = await login(env, SUPER);
  await call(env, post(`${B}/api/admin/admins`, { email: "ops@example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  const adm = await login(env, "ops@example.com");
  const r1 = await call(env, post(`${B}/api/admin/admins`, { email: "friend@x.co" },
    { Authorization: `Bearer ${adm.token}` }));
  assert.equal(r1.status, 403);
  const r2 = await call(env, del(`${B}/api/admin/admins`, { email: SUPER },
    { Authorization: `Bearer ${adm.token}` }));
  assert.equal(r2.status, 403);
});

test("revoking an admin removes access; removing the super-admin is refused", async () => { // SITE-R-020
  const env = mockEnv();
  const sup = await login(env, SUPER);
  await call(env, post(`${B}/api/admin/admins`, { email: "ops@example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  const adm = await login(env, "ops@example.com");

  const rm = await call(env, del(`${B}/api/admin/admins`, { email: "ops@example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  assert.equal(rm.status, 200);
  const after = await call(env, get(`${B}/api/admin/stats`,
    { Authorization: `Bearer ${adm.token}` }));
  assert.equal(after.status, 403, "revoked admin must lose access immediately");

  const nope = await call(env, del(`${B}/api/admin/admins`, { email: SUPER },
    { Authorization: `Bearer ${sup.token}` }));
  assert.equal(nope.status, 400, "super-admin must be undeletable");
});

test("garbage emails are rejected; add is idempotent", async () => {        // SITE-R-020
  const env = mockEnv();
  const sup = await login(env, SUPER);
  const bad = await call(env, post(`${B}/api/admin/admins`, { email: "not-an-email" },
    { Authorization: `Bearer ${sup.token}` }));
  assert.equal(bad.status, 400);
  for (let i = 0; i < 2; i++)
    await call(env, post(`${B}/api/admin/admins`, { email: "ops@example.com" },
      { Authorization: `Bearer ${sup.token}` }));
  const list = await call(env, get(`${B}/api/admin/admins`,
    { Authorization: `Bearer ${sup.token}` }));
  assert.equal(list.body.admins.filter((e) => e === "ops@example.com").length, 1);
});

test("grants/revokes land in the audit log (super-only view)", async () => { // SITE-R-020
  const env = mockEnv();
  const sup = await login(env, SUPER);
  await call(env, post(`${B}/api/admin/admins`, { email: "ops@example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  await call(env, del(`${B}/api/admin/admins`, { email: "ops@example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  const audit = await call(env, get(`${B}/api/admin/audit`,
    { Authorization: `Bearer ${sup.token}` }));
  assert.equal(audit.status, 200);
  const actions = audit.body.audit.map((a) => a.action);
  assert.ok(actions.includes("grant") && actions.includes("revoke"));
  assert.ok(audit.body.audit.every((a) => a.by === SUPER && a.at));

  // an ordinary admin cannot read the audit trail
  await call(env, post(`${B}/api/admin/admins`, { email: "ops@example.com" },
    { Authorization: `Bearer ${sup.token}` }));
  const adm = await login(env, "ops@example.com");
  const r = await call(env, get(`${B}/api/admin/audit`,
    { Authorization: `Bearer ${adm.token}` }));
  assert.equal(r.status, 403);
});

// Unit tests for functions/api/track.js — the anonymous engagement beacon.
// Verifies: allow-listed events increment global:stats; unknown/missing events are
// ignored; every response is 204; GET and POST both work; analytics never throws.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { mockEnv, ctx, post, get } from "./helpers.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const { onRequestGet, onRequestPost } =
  await import(pathToFileURL(path.join(ROOT, "functions/api/track.js")).href);

const B = "https://spck.dev";
const stats = (env) => env.USERS.get("global:stats", "json");

test("POST an allow-listed event → 204 and increments global:stats", async () => {
  const env = mockEnv();
  const r = await onRequestPost(ctx(post(`${B}/api/track?event=agent_view`, {}), env));
  assert.equal(r.status, 204);
  assert.equal((await stats(env)).agent_view, 1);
});

test("GET works too (beacon may use GET)", async () => {
  const env = mockEnv();
  const r = await onRequestGet(ctx(get(`${B}/api/track?event=sandbox_view`), env));
  assert.equal(r.status, 204);
  assert.equal((await stats(env)).sandbox_view, 1);
});

test("repeated events accumulate", async () => {
  const env = mockEnv();
  for (let i = 0; i < 3; i++)
    await onRequestPost(ctx(post(`${B}/api/track?event=sandbox_case`, {}), env));
  assert.equal((await stats(env)).sandbox_case, 3);
});

test("every allow-listed event is accepted (incl. the returning-visitor depth signals)", async () => {
  const env = mockEnv();
  const events = ["agent_view", "sandbox_view", "sandbox_case", "agent_cta",
                  "agent_return", "sandbox_return"];
  for (const ev of events)
    await onRequestPost(ctx(post(`${B}/api/track?event=${ev}`, {}), env));
  const s = await stats(env);
  for (const ev of events)
    assert.equal(s[ev], 1, `${ev} should be recorded`);
});

test("unknown event is ignored — no write, still 204 (allow-list can't be polluted)", async () => {
  const env = mockEnv();
  const r = await onRequestPost(ctx(post(`${B}/api/track?event=evil_inject`, {}), env));
  assert.equal(r.status, 204);
  assert.equal(await stats(env), null, "nothing should be written for an unknown event");
});

test("missing event param → 204, no write", async () => {
  const env = mockEnv();
  const r = await onRequestPost(ctx(post(`${B}/api/track`, {}), env));
  assert.equal(r.status, 204);
  assert.equal(await stats(env), null);
});

test("response is bodyless with no-store cache header", async () => {
  const env = mockEnv();
  const r = await onRequestGet(ctx(get(`${B}/api/track?event=agent_view`), env));
  assert.equal(r.status, 204);
  assert.match(r.headers.get("cache-control") || "", /no-store/);
});

// Shared test doubles for the Pages-functions unit suite. No dependencies:
// node:test + the Request/Response/fetch globals of Node >= 18.

export class MockKV {
  constructor(seed = {}) { this.store = new Map(Object.entries(seed)); }
  async get(key, type) {
    const v = this.store.has(key) ? this.store.get(key) : null;
    if (v === null) return null;
    return type === "json" ? JSON.parse(v) : v;
  }
  async put(key, value) { this.store.set(key, String(value)); }
  async delete(key) { this.store.delete(key); }
  async list({ prefix = "", limit = 1000 } = {}) {
    const keys = [...this.store.keys()].filter((k) => k.startsWith(prefix))
      .slice(0, limit).map((name) => ({ name }));
    return { keys };
  }
}

export function mockEnv(overrides = {}) {
  return {
    USERS: new MockKV(), REPORTS: new MockKV(),
    SESSIONS: new MockKV(), OTP_STORE: new MockKV(),
    ...overrides,
  };
}

export function ctx(request, env, waits = []) {
  // Pages catch-all functions receive params.path = URL segments after /api/
  const segs = new URL(request.url).pathname.split("/").filter(Boolean);
  const params = { path: segs[0] === "api" ? segs.slice(1) : segs };
  return { request, env, params, waitUntil: (p) => waits.push(p) };
}

export function post(url, body, headers = {}) {
  return new Request(url, { method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...headers } });
}

export function get(url, headers = {}) {
  return new Request(url, { headers });
}

// A fetch stub driven by URL-substring routes: [[substring, responderFn], ...].
// Unmatched URLs throw so a test can never silently hit the network.
export function stubFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, opts });
    for (const [needle, responder] of routes) {
      if (u.includes(needle)) return responder(u, opts);
    }
    throw new Error("unstubbed fetch: " + u);
  };
  return calls;
}

export const jsonResp = (obj, status = 200, headers = {}) =>
  new Response(JSON.stringify(obj), { status,
    headers: { "Content-Type": "application/json", ...headers } });

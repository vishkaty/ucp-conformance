/**
 * UCP Conformance Tool — Cloudflare Pages Function
 * Catch-all for /api/* routes.
 *
 * KV Bindings: OTP_STORE, SESSIONS, USERS, REPORTS
 * Env: RESEND_API_KEY, FROM_EMAIL, ADMIN_EMAILS (comma-separated)
 */

const ADMIN_EMAILS = ['katyal.vishal@gmail.com'];

export async function onRequest(context) {
  const { request, env, params } = context;
  const path = '/api/' + (params.path?.join('/') || '');

  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors() });

  try {
    // Auth
    if (path === '/api/auth/send-otp' && request.method === 'POST') return await sendOtp(request, env);
    if (path === '/api/auth/verify-otp' && request.method === 'POST') return await verifyOtp(request, env);
    if (path === '/api/auth/me' && request.method === 'GET') return await me(request, env);
    if (path === '/api/auth/logout' && request.method === 'POST') return await logout(request, env);

    // API Keys
    if (path === '/api/keys' && request.method === 'POST') return await createApiKey(request, env);
    if (path === '/api/keys' && request.method === 'GET') return await listApiKeys(request, env);
    if (path.match(/^\/api\/keys\/[^/]+$/) && request.method === 'DELETE') return await deleteApiKey(request, env, path.split('/').pop());

    // Settings
    if (path === '/api/settings' && request.method === 'POST') return await saveSettings(request, env);
    if (path === '/api/settings' && request.method === 'GET') return await getSettings(request, env);

    // Reports
    if (path === '/api/reports' && request.method === 'POST') return await saveReport(request, env);
    if (path === '/api/reports' && request.method === 'GET') return await listReports(request, env);
    if (path.match(/^\/api\/reports\/[^/]+$/) && request.method === 'GET') return await getReport(request, env, path.split('/').pop());
    if (path.match(/^\/api\/reports\/[^/]+$/) && request.method === 'DELETE') return await deleteReport(request, env, path.split('/').pop());

    // Admin
    if (path === '/api/admin/stats' && request.method === 'GET') return await adminStats(request, env);
    if (path === '/api/admin/users' && request.method === 'GET') return await adminUsers(request, env);
    if (path === '/api/admin/activity' && request.method === 'GET') return await adminActivity(request, env);

    return json({ error: 'Not found' }, 404);
  } catch (e) {
    return json({ error: e.message }, 500);
  }
}

// ── Auth ──

async function sendOtp(request, env) {
  const { email } = await request.json();
  if (!email || !email.includes('@')) return json({ error: 'Valid email required' }, 400);

  const otp = String(Math.floor(100000 + Math.random() * 900000));
  await env.OTP_STORE.put(`otp:${email.toLowerCase()}`, JSON.stringify({
    code: otp, email: email.toLowerCase(), attempts: 0, created: Date.now()
  }), { expirationTtl: 600 });

  const resendKey = env.RESEND_API_KEY || 're_AnEWccmB_2YFkt4yBC75KvNXBTFHQz1u2';
  await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${resendKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: env.FROM_EMAIL || 'UCP Conformance <noreply@spck.dev>',
      to: email,
      subject: `Your verification code: ${otp}`,
      html: `<div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:20px">
        <div style="font-size:18px;font-weight:700;color:#5b5bd6;margin-bottom:16px">UCP Conformance</div>
        <p>Your verification code:</p>
        <div style="font-size:32px;font-weight:700;letter-spacing:8px;text-align:center;padding:16px;background:#f0f0ff;border-radius:8px;margin:16px 0">${otp}</div>
        <p style="color:#888;font-size:13px">Expires in 10 minutes.</p></div>`
    })
  });
  return json({ ok: true, message: 'OTP sent' });
}

async function verifyOtp(request, env) {
  const { email, code } = await request.json();
  if (!email || !code) return json({ error: 'Email and code required' }, 400);

  const key = `otp:${email.toLowerCase()}`;
  const stored = await env.OTP_STORE.get(key, 'json');
  if (!stored) return json({ error: 'OTP expired or not found' }, 401);
  if (stored.attempts >= 5) { await env.OTP_STORE.delete(key); return json({ error: 'Too many attempts' }, 429); }
  if (stored.code !== code) {
    stored.attempts++;
    await env.OTP_STORE.put(key, JSON.stringify(stored), { expirationTtl: 600 });
    return json({ error: 'Invalid code' }, 401);
  }

  await env.OTP_STORE.delete(key);
  const userId = email.toLowerCase();
  const existing = await env.USERS.get(userId, 'json');
  const user = existing || { email: userId, created: new Date().toISOString(), reportCount: 0, testRuns: 0 };
  user.lastLogin = new Date().toISOString();
  user.loginCount = (user.loginCount || 0) + 1;
  await env.USERS.put(userId, JSON.stringify(user));

  const token = crypto.randomUUID() + '-' + crypto.randomUUID();
  await env.SESSIONS.put(`session:${token}`, JSON.stringify({ userId, created: Date.now() }), { expirationTtl: 2592000 });

  // Track login event
  await trackEvent(env, 'login', userId);

  const isAdmin = ADMIN_EMAILS.includes(userId);
  return json({ ok: true, token, user: { email: user.email, created: user.created, isAdmin } });
}

async function me(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const user = await env.USERS.get(s.userId, 'json');
  if (!user) return json({ error: 'User not found' }, 404);
  const isAdmin = ADMIN_EMAILS.includes(s.userId);
  return json({ user: { ...user, isAdmin } });
}

async function logout(request, env) {
  const t = getToken(request);
  if (t) await env.SESSIONS.delete(`session:${t}`);
  return json({ ok: true });
}

// ── Reports ──

async function saveReport(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const report = await request.json();
  const id = crypto.randomUUID();
  const entry = { id, userId: s.userId, date: new Date().toISOString(), config: report.config, summary: report.summary, deviations: report.deviations, tests: report.tests, apiLog: report.api_log };
  await env.REPORTS.put(`report:${id}`, JSON.stringify(entry), { expirationTtl: 7776000 });
  const idx = await env.REPORTS.get(`index:${s.userId}`, 'json') || [];
  idx.unshift({ id, date: entry.date, summary: entry.summary, config: entry.config });
  if (idx.length > 50) idx.length = 50;
  await env.REPORTS.put(`index:${s.userId}`, JSON.stringify(idx));

  // Update user stats
  const user = await env.USERS.get(s.userId, 'json');
  if (user) { user.reportCount = (user.reportCount || 0) + 1; await env.USERS.put(s.userId, JSON.stringify(user)); }

  // Track test run event
  await trackEvent(env, 'test_run', s.userId, {
    domain: report.config?.domain,
    base: report.config?.base,
    version: report.config?.version,
    pass: report.summary?.pass,
    fail: report.summary?.fail,
    total: report.summary?.total,
    api_calls: report.summary?.api_calls
  });

  return json({ ok: true, id });
}

async function listReports(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  return json({ reports: await env.REPORTS.get(`index:${s.userId}`, 'json') || [] });
}

async function getReport(request, env, id) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const r = await env.REPORTS.get(`report:${id}`, 'json');
  if (!r) return json({ error: 'Not found' }, 404);
  if (r.userId !== s.userId && !ADMIN_EMAILS.includes(s.userId)) return json({ error: 'Not authorized' }, 403);
  return json({ report: r });
}

async function deleteReport(request, env, id) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const r = await env.REPORTS.get(`report:${id}`, 'json');
  if (!r || r.userId !== s.userId) return json({ error: 'Not found' }, 404);
  await env.REPORTS.delete(`report:${id}`);
  const idx = (await env.REPORTS.get(`index:${s.userId}`, 'json') || []).filter(x => x.id !== id);
  await env.REPORTS.put(`index:${s.userId}`, JSON.stringify(idx));
  return json({ ok: true });
}

// ── API Keys ──

async function createApiKey(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const { name } = await request.json();
  const key = 'spck_' + crypto.randomUUID().replace(/-/g, '');
  const keyData = { key, name: name || 'CLI Key', userId: s.userId, created: new Date().toISOString() };
  // Store key -> userId mapping (no expiry)
  await env.SESSIONS.put(`apikey:${key}`, JSON.stringify({ userId: s.userId }));
  // Store in user's key list
  const listKey = `apikeys:${s.userId}`;
  const keys = await env.USERS.get(listKey, 'json') || [];
  keys.push({ key: key.slice(0, 10) + '...', name: keyData.name, created: keyData.created, id: key.slice(-8) });
  await env.USERS.put(listKey, JSON.stringify(keys));
  return json({ ok: true, key, name: keyData.name });
}

async function listApiKeys(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const keys = await env.USERS.get(`apikeys:${s.userId}`, 'json') || [];
  return json({ keys });
}

async function deleteApiKey(request, env, keyId) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const listKey = `apikeys:${s.userId}`;
  const keys = await env.USERS.get(listKey, 'json') || [];
  // Find the full key by last 8 chars
  const entry = keys.find(k => k.id === keyId);
  if (!entry) return json({ error: 'Key not found' }, 404);
  // Delete from sessions
  const allKeys = await env.SESSIONS.list({ prefix: 'apikey:spck_', limit: 100 });
  for (const k of allKeys.keys) {
    if (k.name.endsWith(keyId)) { await env.SESSIONS.delete(k.name); break; }
  }
  const updated = keys.filter(k => k.id !== keyId);
  await env.USERS.put(listKey, JSON.stringify(updated));
  return json({ ok: true });
}

// ── Settings ──

async function saveSettings(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  await env.USERS.put(`settings:${s.userId}`, JSON.stringify(await request.json()));
  return json({ ok: true });
}

async function getSettings(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  return json({ settings: await env.USERS.get(`settings:${s.userId}`, 'json') || { headers: {}, defaultBase: '', defaultDomain: '' } });
}

// ── Analytics Tracking ──

async function trackEvent(env, type, userId, meta = {}) {
  const now = new Date();
  const dayKey = now.toISOString().slice(0, 10); // 2026-04-23

  // Append to daily activity log
  const actKey = `activity:${dayKey}`;
  const activity = await env.USERS.get(actKey, 'json') || [];
  activity.push({ type, userId, time: now.toISOString(), ...meta });
  await env.USERS.put(actKey, JSON.stringify(activity), { expirationTtl: 7776000 }); // 90 days

  // Update global stats
  const stats = await env.USERS.get('global:stats', 'json') || {
    totalUsers: 0, totalLogins: 0, totalTestRuns: 0, totalReports: 0,
    domainsTested: {}, endpointsTested: {}, dailyActivity: {}
  };

  if (type === 'login') {
    stats.totalLogins++;
    // Check if new user
    const existing = await env.USERS.get(userId, 'json');
    if (existing?.loginCount <= 1) stats.totalUsers++;
  }
  if (type === 'test_run') {
    stats.totalTestRuns++;
    stats.totalReports++;
    if (meta.domain) stats.domainsTested[meta.domain] = (stats.domainsTested[meta.domain] || 0) + 1;
    if (meta.base) stats.endpointsTested[meta.base] = (stats.endpointsTested[meta.base] || 0) + 1;
  }

  // Daily counts
  if (!stats.dailyActivity[dayKey]) stats.dailyActivity[dayKey] = { logins: 0, testRuns: 0 };
  if (type === 'login') stats.dailyActivity[dayKey].logins++;
  if (type === 'test_run') stats.dailyActivity[dayKey].testRuns++;

  // Keep last 90 days only
  const cutoff = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
  for (const d of Object.keys(stats.dailyActivity)) { if (d < cutoff) delete stats.dailyActivity[d]; }

  await env.USERS.put('global:stats', JSON.stringify(stats));
}

// ── Admin ──

async function requireAdmin(request, env) {
  const s = await getSession(request, env);
  if (!s) return null;
  if (!ADMIN_EMAILS.includes(s.userId)) return null;
  return s;
}

async function adminStats(request, env) {
  if (!await requireAdmin(request, env)) return json({ error: 'Not authorized' }, 403);
  const stats = await env.USERS.get('global:stats', 'json') || {
    totalUsers: 0, totalLogins: 0, totalTestRuns: 0, totalReports: 0,
    domainsTested: {}, endpointsTested: {}, dailyActivity: {}
  };
  return json({ stats });
}

async function adminUsers(request, env) {
  if (!await requireAdmin(request, env)) return json({ error: 'Not authorized' }, 403);
  // List all users from KV (prefix scan)
  const list = await env.USERS.list({ prefix: '', limit: 200 });
  const users = [];
  for (const key of list.keys) {
    // Skip non-user keys
    if (key.name.startsWith('settings:') || key.name.startsWith('global:') || key.name.startsWith('activity:')) continue;
    if (!key.name.includes('@')) continue;
    const user = await env.USERS.get(key.name, 'json');
    if (user) users.push(user);
  }
  users.sort((a, b) => (b.lastLogin || '').localeCompare(a.lastLogin || ''));
  return json({ users, total: users.length });
}

async function adminActivity(request, env) {
  if (!await requireAdmin(request, env)) return json({ error: 'Not authorized' }, 403);

  const url = new URL(request.url);
  const days = parseInt(url.searchParams.get('days') || '7');

  const activity = [];
  const now = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(now - i * 86400000).toISOString().slice(0, 10);
    const events = await env.USERS.get(`activity:${d}`, 'json') || [];
    activity.push({ date: d, events });
  }

  return json({ activity, days });
}

// ── Helpers ──

function getToken(req) { const a = req.headers.get('Authorization') || ''; return a.startsWith('Bearer ') ? a.slice(7) : null; }
async function getSession(req, env) {
  const t = getToken(req); if (!t) return null;
  // Try session token first
  const session = await env.SESSIONS.get(`session:${t}`, 'json');
  if (session) return session;
  // Try API key (spck_...)
  if (t.startsWith('spck_')) {
    const apiKey = await env.SESSIONS.get(`apikey:${t}`, 'json');
    if (apiKey) return apiKey;
  }
  return null;
}
function json(data, status = 200) { return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...cors() } }); }
function cors() { return { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, Authorization', 'Access-Control-Max-Age': '86400' }; }

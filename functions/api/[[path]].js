/**
 * Catch-all Pages Function for /api/* routes.
 * Routes to the appropriate handler based on the path.
 *
 * KV Bindings (set in Cloudflare Pages dashboard):
 *   OTP_STORE, SESSIONS, USERS, REPORTS
 *
 * Environment Variables:
 *   RESEND_API_KEY, FROM_EMAIL
 */

export async function onRequest(context) {
  const { request, env, params } = context;
  const url = new URL(request.url);
  const path = '/api/' + (params.path?.join('/') || '');

  // CORS
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: cors() });
  }

  try {
    // Auth
    if (path === '/api/auth/send-otp' && request.method === 'POST') return await sendOtp(request, env);
    if (path === '/api/auth/verify-otp' && request.method === 'POST') return await verifyOtp(request, env);
    if (path === '/api/auth/me' && request.method === 'GET') return await me(request, env);
    if (path === '/api/auth/logout' && request.method === 'POST') return await logout(request, env);

    // Settings
    if (path === '/api/settings' && request.method === 'POST') return await saveSettings(request, env);
    if (path === '/api/settings' && request.method === 'GET') return await getSettings(request, env);

    // Reports
    if (path === '/api/reports' && request.method === 'POST') return await saveReport(request, env);
    if (path === '/api/reports' && request.method === 'GET') return await listReports(request, env);
    if (path.match(/^\/api\/reports\/[^/]+$/) && request.method === 'GET') return await getReport(request, env, path.split('/').pop());
    if (path.match(/^\/api\/reports\/[^/]+$/) && request.method === 'DELETE') return await deleteReport(request, env, path.split('/').pop());

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
  {
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
  const user = existing || { email: userId, created: new Date().toISOString(), reportCount: 0 };
  user.lastLogin = new Date().toISOString();
  await env.USERS.put(userId, JSON.stringify(user));

  const token = crypto.randomUUID() + '-' + crypto.randomUUID();
  await env.SESSIONS.put(`session:${token}`, JSON.stringify({ userId, created: Date.now() }), { expirationTtl: 2592000 });

  return json({ ok: true, token, user: { email: user.email, created: user.created } });
}

async function me(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const user = await env.USERS.get(s.userId, 'json');
  return user ? json({ user }) : json({ error: 'User not found' }, 404);
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
  return json({ ok: true, id });
}

async function listReports(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const idx = await env.REPORTS.get(`index:${s.userId}`, 'json') || [];
  return json({ reports: idx });
}

async function getReport(request, env, id) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const r = await env.REPORTS.get(`report:${id}`, 'json');
  if (!r) return json({ error: 'Not found' }, 404);
  if (r.userId !== s.userId) return json({ error: 'Not authorized' }, 403);
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

// ── Settings ──

async function saveSettings(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const settings = await request.json();
  await env.USERS.put(`settings:${s.userId}`, JSON.stringify(settings));
  return json({ ok: true });
}

async function getSettings(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const settings = await env.USERS.get(`settings:${s.userId}`, 'json') || { headers: {}, defaultBase: '', defaultDomain: '' };
  return json({ settings });
}

// ── Helpers ──

function getToken(req) { const a = req.headers.get('Authorization') || ''; return a.startsWith('Bearer ') ? a.slice(7) : null; }
async function getSession(req, env) { const t = getToken(req); return t ? await env.SESSIONS.get(`session:${t}`, 'json') : null; }
function json(data, status = 200) { return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...cors() } }); }
function cors() { return { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, Authorization', 'Access-Control-Max-Age': '86400' }; }

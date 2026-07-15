/**
 * UCP Conformance Tool — Cloudflare Pages Function
 * Catch-all for /api/* routes.
 *
 * KV Bindings: OTP_STORE, SESSIONS, USERS, REPORTS
 * Env: RESEND_API_KEY, FROM_EMAIL
 *
 * Roles (SITE-R-020): SUPER_ADMINS is the code-constant root of trust — never
 * removable via any API. Regular admins live in the dynamic allowlist
 * (USERS['admin:allowlist']), managed only by a super-admin from /admin;
 * every grant/revoke is audit-logged (USERS['admin:audit'], capped).
 */

const SUPER_ADMINS = ['katyal.vishal@gmail.com'];

async function adminAllowlist(env) {
  return (await env.USERS.get('admin:allowlist', 'json')) || [];
}

async function isAdminEmail(env, email) {
  if (SUPER_ADMINS.includes(email)) return true;
  return (await adminAllowlist(env)).includes(email);
}

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

    // Badge (SITE-R-025) — public, no auth: the unguessable report id is the
    // owner's deliberate share (they embed the badge); nothing is enumerable.
    if (path.match(/^\/api\/badge\/[^/]+\.svg$/) && request.method === 'GET') {
      return await badge(env, path.slice('/api/badge/'.length, -'.svg'.length));
    }

    // Admin
    if (path === '/api/admin/stats' && request.method === 'GET') return await adminStats(request, env);
    if (path === '/api/admin/users' && request.method === 'GET') return await adminUsers(request, env);
    if (path === '/api/admin/activity' && request.method === 'GET') return await adminActivity(request, env);
    if (path === '/api/admin/metrics' && request.method === 'GET') return await adminMetrics(request, env);
    if (path === '/api/admin/admins' && request.method === 'GET') return await adminList(request, env);
    if (path === '/api/admin/admins' && request.method === 'POST') return await adminGrant(request, env);
    if (path === '/api/admin/admins' && request.method === 'DELETE') return await adminRevoke(request, env);
    if (path === '/api/admin/audit' && request.method === 'GET') return await adminAudit(request, env);

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

  const resendKey = env.RESEND_API_KEY;
  if (!resendKey) return json({ error: 'Email delivery not configured' }, 500);
  const sent = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${resendKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: env.FROM_EMAIL || 'UCP Conformance <noreply@spck.dev>',
      to: email,
      subject: `Your verification code: ${otp}`,
      html: `<div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:20px">
        <div style="font-size:18px;font-weight:700;color:#059669;margin-bottom:16px">spck.dev</div>
        <p>Your verification code:</p>
        <div style="font-size:32px;font-weight:700;letter-spacing:8px;text-align:center;padding:16px;background:#ecfdf5;border-radius:8px;margin:16px 0">${otp}</div>
        <p style="color:#888;font-size:13px">Expires in 10 minutes.</p></div>`
    })
  });
  // Surface delivery failures instead of a silent ok (a bad/rotated Resend key or a
  // rejected sender would otherwise look like "OTP sent" while nothing arrives).
  if (!sent.ok) return json({ error: 'Email delivery failed — please try again shortly' }, 502);
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

  const isAdmin = await isAdminEmail(env, userId);
  const isSuperAdmin = SUPER_ADMINS.includes(userId);
  return json({ ok: true, token, user: { email: user.email, created: user.created, isAdmin, isSuperAdmin } });
}

async function me(request, env) {
  const s = await getSession(request, env);
  if (!s) return json({ error: 'Not authenticated' }, 401);
  const user = await env.USERS.get(s.userId, 'json');
  if (!user) return json({ error: 'User not found' }, 404);
  const isAdmin = await isAdminEmail(env, s.userId);
  const isSuperAdmin = SUPER_ADMINS.includes(s.userId);
  return json({ user: { ...user, isAdmin, isSuperAdmin } });
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

  // Update user stats + the email→domain mapping (SITE-R-023): every save ties
  // this account to the store it checked — first-seen order, unique, capped.
  const user = await env.USERS.get(s.userId, 'json');
  if (user) {
    user.reportCount = (user.reportCount || 0) + 1;
    const dom = String(report.config?.domain || '').toLowerCase();
    if (dom) {
      user.domains = user.domains || [];
      if (!user.domains.includes(dom)) user.domains.push(dom);
      if (user.domains.length > 50) user.domains.length = 50;
    }
    await env.USERS.put(s.userId, JSON.stringify(user));
  }

  // Best-effort report email with the permalink — a delivery failure must never
  // block the save (SITE-R-023).
  try { await sendReportEmail(env, s.userId, entry); } catch { /* never block */ }

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

async function sendReportEmail(env, to, entry) {
  if (!env.RESEND_API_KEY) return;
  const dom = entry.config?.domain || 'your store';
  const s = entry.summary || {};
  const line = [s.pass !== undefined ? `${s.pass} passed` : null,
                s.fail !== undefined ? `${s.fail} deviations` : null,
                s.skip !== undefined ? `${s.skip} not tested` : null]
    .filter(Boolean).join(' · ');
  await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: env.FROM_EMAIL || 'UCP Conformance <noreply@spck.dev>',
      to,
      subject: `Your UCP check report — ${dom}`,
      html: `<div style="font-family:sans-serif;max-width:440px;margin:0 auto;padding:20px">
        <div style="font-size:18px;font-weight:700;color:#059669;margin-bottom:16px">spck.dev</div>
        <p>Your saved conformance check for <b>${String(dom).replace(/[<>&]/g, '')}</b>${line ? ` — ${line}` : ''}.</p>
        <p><a href="https://spck.dev/check?report=${entry.id}" style="display:inline-block;background:#059669;color:#fff;padding:10px 22px;border-radius:21px;text-decoration:none;font-weight:600">View your report</a></p>
        <p style="color:#888;font-size:13px">Sign in with this email address to open it. Reports are kept for 90 days.</p></div>`,
    }),
  });
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
  if (r.userId !== s.userId && !await isAdminEmail(env, s.userId)) return json({ error: 'Not authorized' }, 403);
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

// ── Badge (SITE-R-025) ──

async function badge(env, id) {
  // Refuse anything that isn't a v4-uuid report id before touching KV.
  const report = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
    ? await env.REPORTS.get(`report:${id}`, 'json')
    : null;

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const s = report?.summary || null;
  const known = s && Number.isFinite(s.pass) && Number.isFinite(s.total);
  const value = known
    ? `${esc(s.grade ?? '')}${s.grade != null ? ' · ' : ''}${esc(s.pass)}/${esc(s.total)}`
    : 'unknown';
  const color = !known ? '#9f9f9f' : (s.fail === 0 ? '#2da44e' : '#d1242f');
  const date = known && report.date ? esc(String(report.date).slice(0, 10)) : '';
  const title = known
    ? `UCP conformance: ${value}${date ? ` (checked ${date})` : ''}`
    : 'UCP conformance: unknown';

  const label = 'UCP conformance';
  const labelW = 108, valueW = Math.max(9 * String(value).replace(/&[^;]+;/g, 'x').length + 12, 48);
  const w = labelW + valueW;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="20" role="img" aria-label="${esc(title)}">` +
    `<title>${esc(title)}</title>` +
    `<rect width="${labelW}" height="20" fill="#555"/>` +
    `<rect x="${labelW}" width="${valueW}" height="20" fill="${color}"/>` +
    `<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">` +
    `<text x="${labelW / 2}" y="14">${label}</text>` +
    `<text x="${labelW + valueW / 2}" y="14">${value}</text>` +
    `</g></svg>`;

  return new Response(svg, { status: 200, headers: {
    'Content-Type': 'image/svg+xml',
    'Cache-Control': 'public, max-age=3600',
    ...cors(),
  } });
}

// ── Analytics Tracking ──

async function trackEvent(env, type, userId, meta = {}) {
  // Defense-in-depth: clamp length and strip control/angle chars from user-supplied
  // values before they are stored and later rendered in the admin dashboard.
  const clean = (s, n) => String(s == null ? "" : s).replace(/[\x00-\x1f<>]/g, "").slice(0, n);
  if (meta.domain != null) meta.domain = clean(meta.domain, 120);
  if (meta.base != null) meta.base = clean(meta.base, 200);

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
  if (!await isAdminEmail(env, s.userId)) return null;
  return s;
}

async function requireSuperAdmin(request, env) {
  const s = await getSession(request, env);
  if (!s) return null;
  if (!SUPER_ADMINS.includes(s.userId)) return null;
  return s;
}

// audit trail for allowlist changes — newest first, capped so the key stays small
async function auditLog(env, by, action, email) {
  const log = (await env.USERS.get('admin:audit', 'json')) || [];
  log.unshift({ at: new Date().toISOString(), by, action, email });
  await env.USERS.put('admin:audit', JSON.stringify(log.slice(0, 200)));
}

async function adminList(request, env) {
  if (!await requireAdmin(request, env)) return json({ error: 'Not authorized' }, 403);
  return json({ super_admins: SUPER_ADMINS, admins: await adminAllowlist(env) });
}

async function adminGrant(request, env) {
  const s = await requireSuperAdmin(request, env);
  if (!s) return json({ error: 'Super-admin only' }, 403);
  const { email } = await request.json();
  const e = String(email || '').trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) return json({ error: 'Valid email required' }, 400);
  const list = await adminAllowlist(env);
  if (!list.includes(e) && !SUPER_ADMINS.includes(e)) {
    list.push(e);
    await env.USERS.put('admin:allowlist', JSON.stringify(list));
    await auditLog(env, s.userId, 'grant', e);
  }
  return json({ ok: true, admins: list });
}

async function adminRevoke(request, env) {
  const s = await requireSuperAdmin(request, env);
  if (!s) return json({ error: 'Super-admin only' }, 403);
  const { email } = await request.json();
  const e = String(email || '').trim().toLowerCase();
  if (SUPER_ADMINS.includes(e)) {
    return json({ error: 'A super-admin cannot be removed' }, 400);
  }
  const list = await adminAllowlist(env);
  if (list.includes(e)) {
    await env.USERS.put('admin:allowlist', JSON.stringify(list.filter((x) => x !== e)));
    await auditLog(env, s.userId, 'revoke', e);
  }
  return json({ ok: true, admins: await adminAllowlist(env) });
}

async function adminAudit(request, env) {
  if (!await requireSuperAdmin(request, env)) return json({ error: 'Super-admin only' }, 403);
  return json({ audit: (await env.USERS.get('admin:audit', 'json')) || [] });
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

// ── Growth / performance metrics (admin) ──
// Live-fetches PyPI (public), GitHub (stars public; traffic needs GH_METRICS_TOKEN),
// and Cloudflare spck.dev analytics (needs CF_ANALYTICS_TOKEN + CF_ZONE_ID). Cached in
// KV for 30 min; ?refresh=1 bypasses. Each source degrades gracefully if unconfigured.
const METRICS_REPOS = ['vishkaty/ucp-conformance', 'vishkaty/awesome-ucp'];
const METRICS_PYPI = 'spck-conformance';

async function adminMetrics(request, env) {
  if (!await requireAdmin(request, env)) return json({ error: 'Not authorized' }, 403);
  const refresh = new URL(request.url).searchParams.get('refresh') === '1';
  if (!refresh) {
    const cached = await env.REPORTS.get('metrics:cache', 'json');
    if (cached) return json({ ...cached, cached: true });
  }
  const out = { generated_at: new Date().toISOString(), cached: false };

  // PyPI (public — but pypistats.org rate-limits datacenter IPs with an HTML 429,
  // so keep a last-known-good snapshot and serve it, marked stale, when live fails)
  try {
    const r = await fetch(`https://pypistats.org/api/packages/${METRICS_PYPI}/recent`,
      { headers: { 'User-Agent': 'spck-metrics' } });
    let d = null;
    try { d = JSON.parse(await r.text()); } catch {}
    if (r.ok && d && d.data) {
      out.pypi = d.data;
      await env.REPORTS.put('metrics:pypi:last',
        JSON.stringify({ ...d.data, as_of: out.generated_at }));
    } else {
      const last = await env.REPORTS.get('metrics:pypi:last', 'json');
      out.pypi = last ? { ...last, stale: true }
                      : { error: `pypistats unavailable (HTTP ${r.status})` };
    }
  } catch (e) {
    const last = await env.REPORTS.get('metrics:pypi:last', 'json').catch(() => null);
    out.pypi = last ? { ...last, stale: true } : { error: String(e) };
  }

  // GitHub — stars/forks are public; traffic (views/clones/referrers) needs a token
  const ght = env.GH_METRICS_TOKEN;
  out.github = {};
  const ghHeaders = { 'User-Agent': 'spck-metrics', 'Accept': 'application/vnd.github+json' };
  if (ght) ghHeaders['Authorization'] = 'Bearer ' + ght;
  const gh = (p) => fetch('https://api.github.com/' + p, { headers: ghHeaders })
    .then(r => r.ok ? r.json() : null).catch(() => null);
  for (const repo of METRICS_REPOS) {
    const info = await gh('repos/' + repo);
    const entry = { stars: info?.stargazers_count, forks: info?.forks_count };
    if (ght) {
      const [views, clones, refs] = await Promise.all([
        gh('repos/' + repo + '/traffic/views'),
        gh('repos/' + repo + '/traffic/clones'),
        gh('repos/' + repo + '/traffic/popular/referrers'),
      ]);
      entry.views_14d = views?.count; entry.views_uniq = views?.uniques;
      entry.clones_14d = clones?.count; entry.clones_uniq = clones?.uniques;
      entry.referrers = (refs || []).slice(0, 5).map(x => ({ src: x.referrer, count: x.count, uniq: x.uniques }));
    }
    out.github[repo] = entry;
  }
  if (!ght) out.github.error = 'set GH_METRICS_TOKEN (repo scope) as a Pages secret for views/clones/referrers (stars shown above are public)';

  // Cloudflare spck.dev traffic
  const cft = env.CF_ANALYTICS_TOKEN, zid = env.CF_ZONE_ID;
  if (cft && zid) {
    try {
      const since = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
      const q = { query: `query{viewer{zones(filter:{zoneTag:"${zid}"}){httpRequests1dGroups(limit:7,orderBy:[date_DESC],filter:{date_geq:"${since}"}){dimensions{date} sum{requests} uniq{uniques}}}}}` };
      const r = await fetch('https://api.cloudflare.com/client/v4/graphql',
        { method: 'POST', headers: { 'Authorization': 'Bearer ' + cft, 'Content-Type': 'application/json' }, body: JSON.stringify(q) });
      const d = await r.json();
      const g = d?.data?.viewer?.zones?.[0]?.httpRequests1dGroups;
      if (g) out.cloudflare = {
        requests_7d: g.reduce((a, x) => a + x.sum.requests, 0),
        uniques_7d: g.reduce((a, x) => a + x.uniq.uniques, 0),
        days: g.map(x => ({ date: x.dimensions.date, req: x.sum.requests, uniq: x.uniq.uniques })),
      };
      else out.cloudflare = { error: 'analytics query failed — check CF_ANALYTICS_TOKEN scope (Zone Analytics:Read) and CF_ZONE_ID' };
    } catch (e) { out.cloudflare = { error: String(e) }; }
  } else {
    out.cloudflare = { error: 'set CF_ANALYTICS_TOKEN + CF_ZONE_ID as Pages secrets to enable spck.dev traffic' };
  }

  await env.REPORTS.put('metrics:cache', JSON.stringify(out), { expirationTtl: 1800 });
  return json(out);
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

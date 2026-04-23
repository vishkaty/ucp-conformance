/**
 * UCP Conformance Tool — Cloudflare Worker Backend
 *
 * Handles:
 * - POST /api/auth/send-otp     — send 6-digit OTP to email
 * - POST /api/auth/verify-otp   — verify OTP, return session token
 * - GET  /api/auth/me            — get current user from session
 * - POST /api/reports            — save a test report
 * - GET  /api/reports            — list user's reports
 * - GET  /api/reports/:id        — get a specific report
 * - DELETE /api/reports/:id      — delete a report
 *
 * KV Namespaces required:
 * - OTP_STORE    — temporary OTP storage (TTL 10 min)
 * - SESSIONS     — session tokens (TTL 30 days)
 * - USERS        — user profiles { email, created, lastLogin }
 * - REPORTS      — saved reports { userId, date, config, summary, ... }
 *
 * Environment variables:
 * - RESEND_API_KEY — API key for Resend (email delivery)
 * - FROM_EMAIL     — sender email (e.g., otp@ucp-conformance.firmly.ai)
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    try {
      // Auth routes
      if (path === '/api/auth/send-otp' && request.method === 'POST') {
        return await handleSendOtp(request, env);
      }
      if (path === '/api/auth/verify-otp' && request.method === 'POST') {
        return await handleVerifyOtp(request, env);
      }
      if (path === '/api/auth/me' && request.method === 'GET') {
        return await handleMe(request, env);
      }
      if (path === '/api/auth/logout' && request.method === 'POST') {
        return await handleLogout(request, env);
      }

      // Report routes (authenticated)
      if (path === '/api/reports' && request.method === 'POST') {
        return await handleSaveReport(request, env);
      }
      if (path === '/api/reports' && request.method === 'GET') {
        return await handleListReports(request, env);
      }
      if (path.startsWith('/api/reports/') && request.method === 'GET') {
        return await handleGetReport(request, env, path.split('/').pop());
      }
      if (path.startsWith('/api/reports/') && request.method === 'DELETE') {
        return await handleDeleteReport(request, env, path.split('/').pop());
      }

      return json({ error: 'Not found' }, 404);
    } catch (e) {
      return json({ error: e.message }, 500);
    }
  }
};

// ═══════════════════════════════════════════════════
// AUTH
// ═══════════════════════════════════════════════════

async function handleSendOtp(request, env) {
  const { email } = await request.json();
  if (!email || !email.includes('@')) {
    return json({ error: 'Valid email required' }, 400);
  }

  const otp = String(Math.floor(100000 + Math.random() * 900000));
  const key = `otp:${email.toLowerCase()}`;

  // Store OTP with 10-minute TTL
  await env.OTP_STORE.put(key, JSON.stringify({
    code: otp,
    email: email.toLowerCase(),
    attempts: 0,
    created: Date.now(),
  }), { expirationTtl: 600 });

  // Send email via Resend
  if (env.RESEND_API_KEY) {
    await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: env.FROM_EMAIL || 'UCP Conformance <noreply@ucp-conformance.dev>',
        to: email,
        subject: `Your verification code: ${otp}`,
        html: `
          <div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:20px">
            <div style="font-size:20px;font-weight:700;color:#5b5bd6;margin-bottom:16px">UCP Conformance</div>
            <p>Your UCP Conformance Tool verification code:</p>
            <div style="font-size:32px;font-weight:700;letter-spacing:8px;text-align:center;
                        padding:16px;background:#f5f0ff;border-radius:8px;margin:16px 0">${otp}</div>
            <p style="color:#888;font-size:13px">This code expires in 10 minutes. If you didn't request this, ignore this email.</p>
          </div>`
      })
    });
  }

  return json({ ok: true, message: 'OTP sent' });
}

async function handleVerifyOtp(request, env) {
  const { email, code } = await request.json();
  if (!email || !code) return json({ error: 'Email and code required' }, 400);

  const key = `otp:${email.toLowerCase()}`;
  const stored = await env.OTP_STORE.get(key, 'json');

  if (!stored) return json({ error: 'OTP expired or not found' }, 401);

  if (stored.attempts >= 5) {
    await env.OTP_STORE.delete(key);
    return json({ error: 'Too many attempts. Request a new code.' }, 429);
  }

  if (stored.code !== code) {
    stored.attempts++;
    await env.OTP_STORE.put(key, JSON.stringify(stored), { expirationTtl: 600 });
    return json({ error: 'Invalid code' }, 401);
  }

  // OTP valid — delete it
  await env.OTP_STORE.delete(key);

  // Create/update user
  const userId = email.toLowerCase();
  const existing = await env.USERS.get(userId, 'json');
  const user = existing || { email: userId, created: new Date().toISOString(), reportCount: 0 };
  user.lastLogin = new Date().toISOString();
  await env.USERS.put(userId, JSON.stringify(user));

  // Create session token (30-day TTL)
  const token = crypto.randomUUID() + '-' + crypto.randomUUID();
  await env.SESSIONS.put(`session:${token}`, JSON.stringify({
    userId,
    created: Date.now(),
  }), { expirationTtl: 2592000 }); // 30 days

  return json({ ok: true, token, user: { email: user.email, created: user.created } });
}

async function handleMe(request, env) {
  const session = await getSession(request, env);
  if (!session) return json({ error: 'Not authenticated' }, 401);
  const user = await env.USERS.get(session.userId, 'json');
  if (!user) return json({ error: 'User not found' }, 404);
  return json({ user });
}

async function handleLogout(request, env) {
  const token = getToken(request);
  if (token) await env.SESSIONS.delete(`session:${token}`);
  return json({ ok: true });
}

// ═══════════════════════════════════════════════════
// REPORTS
// ═══════════════════════════════════════════════════

async function handleSaveReport(request, env) {
  const session = await getSession(request, env);
  if (!session) return json({ error: 'Not authenticated' }, 401);

  const report = await request.json();
  const reportId = crypto.randomUUID();

  const entry = {
    id: reportId,
    userId: session.userId,
    date: new Date().toISOString(),
    config: report.config,
    summary: report.summary,
    deviations: report.deviations,
    tests: report.tests,
    apiLog: report.api_log,
  };

  // Save report
  await env.REPORTS.put(`report:${reportId}`, JSON.stringify(entry), { expirationTtl: 7776000 }); // 90 days

  // Update user's report index
  const indexKey = `index:${session.userId}`;
  const index = await env.REPORTS.get(indexKey, 'json') || [];
  index.unshift({ id: reportId, date: entry.date, summary: entry.summary, config: entry.config });
  if (index.length > 50) index.length = 50; // Keep last 50
  await env.REPORTS.put(indexKey, JSON.stringify(index));

  // Update user report count
  const user = await env.USERS.get(session.userId, 'json');
  if (user) {
    user.reportCount = (user.reportCount || 0) + 1;
    await env.USERS.put(session.userId, JSON.stringify(user));
  }

  return json({ ok: true, id: reportId });
}

async function handleListReports(request, env) {
  const session = await getSession(request, env);
  if (!session) return json({ error: 'Not authenticated' }, 401);

  const index = await env.REPORTS.get(`index:${session.userId}`, 'json') || [];
  return json({ reports: index });
}

async function handleGetReport(request, env, reportId) {
  const session = await getSession(request, env);
  if (!session) return json({ error: 'Not authenticated' }, 401);

  const report = await env.REPORTS.get(`report:${reportId}`, 'json');
  if (!report) return json({ error: 'Report not found' }, 404);
  if (report.userId !== session.userId) return json({ error: 'Not authorized' }, 403);

  return json({ report });
}

async function handleDeleteReport(request, env, reportId) {
  const session = await getSession(request, env);
  if (!session) return json({ error: 'Not authenticated' }, 401);

  const report = await env.REPORTS.get(`report:${reportId}`, 'json');
  if (!report || report.userId !== session.userId) return json({ error: 'Not found' }, 404);

  await env.REPORTS.delete(`report:${reportId}`);

  // Update index
  const indexKey = `index:${session.userId}`;
  const index = await env.REPORTS.get(indexKey, 'json') || [];
  const updated = index.filter(r => r.id !== reportId);
  await env.REPORTS.put(indexKey, JSON.stringify(updated));

  return json({ ok: true });
}

// ═══════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════

function getToken(request) {
  const auth = request.headers.get('Authorization') || '';
  return auth.startsWith('Bearer ') ? auth.slice(7) : null;
}

async function getSession(request, env) {
  const token = getToken(request);
  if (!token) return null;
  return await env.SESSIONS.get(`session:${token}`, 'json');
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders() },
  });
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '86400',
  };
}

// site_smoke.mjs — behavioral assertions for the spck.dev IA redesign, one block per
// SITE-R requirement (conformance/web/site_requirements.json). TDD baseline: this file
// is written BEFORE the page edits and is EXPECTED RED against the current site.
//
// Conventions match responsive_smoke.mjs: CHROME_PATH env, BASE default :8189
// (web_gates.py browser() serves public/ there itself), puppeteer-core,
// domcontentloaded + fixed settle. Each assertion block carries its SITE-R id tag
// comment — the site-tdd gate greps these for register traceability.
//
// A missing page (e.g. docs.html before Task 11) is a FAILURE reported per assertion,
// never a crash. /api/track POSTs are fulfilled 204 during plain loads (the local
// python http.server would 501 them and pollute console-clean; production returns ok).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const CHROME = process.env.CHROME_PATH;
const BASE = process.env.BASE || "http://127.0.0.1:8189";
if (!CHROME) { console.error("CHROME_PATH not set"); process.exit(2); }

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const FOLD = 812;                      // "above the fold" = boundingRect.top < 812
const SETTLE = 700;                    // lets async JSON-driven UI (sandbox cases, stats) lay out
// the six IA pages (docs.html replaces guide.html per the redesign; /tool is retired)
const PAGES = ["index.html", "check.html", "agent.html", "sandbox.html", "coverage.html", "docs.html"];

const results = [];
function report(name, pageName, ok, detail = "") {
  results.push({ ok, name, page: pageName });
  console.log(`${ok ? "ok" : "not ok"} - ${name} [${pageName}]${ok ? "" : "  # " + detail}`);
}
const settle = ms => new Promise(r => setTimeout(r, ms));
// normalize an href for comparison: /check, /check.html, http://…/check → "/check"
const normHref = h => {
  try { h = new URL(h, BASE).pathname; } catch { /* keep as-is */ }
  return h.replace(/\.html$/, "").replace(/\/index$/, "/") || "/";
};

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
});

// open a fresh page with console capture; onRequest lets a caller intercept.
// Returns { page, status, consoleErrors } — status null means navigation itself failed.
async function openPage(url, onRequest) {
  const page = await browser.newPage();
  await page.setViewport({ width: 375, height: FOLD, isMobile: true, deviceScaleFactor: 2 });
  const consoleErrors = [];
  page.on("console", m => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", e => consoleErrors.push(`pageerror: ${e.message}`));
  await page.setRequestInterception(true);
  page.on("request", req => {
    if (onRequest && onRequest(req)) return;                    // caller handled it
    const u = req.url();
    if (u.includes("/api/track")) return req.respond({ status: 204, body: "" });
    if (u.endsWith("/favicon.ico")) return req.respond({ status: 204, body: "" });
    req.continue();
  });
  let status = null;
  try {
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
    status = resp ? resp.status() : null;
    await settle(SETTLE);
  } catch (e) {
    consoleErrors.push(`navigation failed: ${e.message}`);
  }
  return { page, status, consoleErrors };
}

try {
  // ---------------------------------------------------------------------------
  // Pass 1 — load each of the six pages once; collect everything page-scoped.
  // ---------------------------------------------------------------------------
  const perPage = {};   // name -> { status, consoleErrors, nav, primaryAboveFold, ogTitle, twitterCard, ladder, caseBtns, hook }
  for (const p of PAGES) {
    const { page, status, consoleErrors } = await openPage(`${BASE}/${p}`);
    let data = null;
    if (status !== null && status < 400) {
      data = await page.evaluate(fold => {
        const q = s => document.querySelector(s);
        const nav = q("nav.site-nav");
        const primaries = [...document.querySelectorAll(".btn-primary")]
          .filter(e => e.getBoundingClientRect().top < fold);
        const heroCtas = [...document.querySelectorAll("a.btn-primary, a.btn-secondary")]
          .filter(a => a.getBoundingClientRect().top < fold)
          .map(a => a.getAttribute("href") || "");
        return {
          nav: nav ? nav.innerHTML : null,
          primaryAboveFold: primaries.length,
          ogTitle: !!q('meta[property="og:title"]'),
          twitterCard: !!q('meta[name="twitter:card"]'),
          ladder: [...document.querySelectorAll("[data-ladder-step]")]
            .map(e => e.getAttribute("data-ladder-step")),
          caseBtns: document.querySelectorAll(".case-btn").length,
          hook: {
            badge: (q(".badge") || {}).textContent || "",
            h1: (q("h1") || {}).textContent || "",
            heroCtas,
          },
        };
      }, FOLD);
    }
    perPage[p] = { status, consoleErrors, data };
    await page.close();
  }
  const missing = p => `page not served (HTTP ${perPage[p].status ?? "no response"})`;

  // SITE-R-001 site_smoke:nav-identical — one shared <nav class="site-nav"> block,
  // byte-identical innerHTML on all six pages; links /check /agent /coverage /docs, no /tool.
  {
    const navs = {};
    for (const p of PAGES) {
      const d = perPage[p].data;
      if (!d) { report("nav-identical", p, false, missing(p)); continue; }
      if (d.nav === null) { report("nav-identical", p, false, "no <nav class=\"site-nav\"> element"); continue; }
      const needed = ["/check", "/agent", "/coverage", "/docs"].filter(h => !d.nav.includes(`href="${h}"`));
      if (needed.length) { report("nav-identical", p, false, `nav missing links: ${needed.join(", ")}`); continue; }
      if (d.nav.includes("/tool")) { report("nav-identical", p, false, "nav links to retired /tool"); continue; }
      navs[p] = d.nav;
      report("nav-identical", p, true);
    }
    const vals = Object.values(navs);
    const identical = vals.length === PAGES.length && vals.every(v => v === vals[0]);
    report("nav-identical", "all-pages", identical,
      vals.length !== PAGES.length
        ? `only ${vals.length}/${PAGES.length} pages have a valid site-nav`
        : "site-nav innerHTML differs between pages");
  }

  // SITE-R-003 site_smoke:primary-cta — exactly one .btn-primary above the fold per page.
  for (const p of PAGES) {
    const d = perPage[p].data;
    if (!d) { report("primary-cta", p, false, missing(p)); continue; }
    report("primary-cta", p, d.primaryAboveFold === 1,
      `${d.primaryAboveFold} .btn-primary above the fold (want exactly 1)`);
  }

  // SITE-R-004 site_smoke:ladder-order — /check depth ladder: instant → cli → ci in DOM order.
  {
    const d = perPage["check.html"].data;
    if (!d) report("ladder-order", "check.html", false, missing("check.html"));
    else {
      const want = ["instant", "cli", "ci"];
      const ok = JSON.stringify(d.ladder) === JSON.stringify(want);
      report("ladder-order", "check.html", ok,
        `[data-ladder-step] order is [${d.ladder.join(", ")}] (want [${want.join(", ")}])`);
    }
  }

  // SITE-R-006 site_smoke:social-meta — og:title + twitter:card on every page.
  for (const p of PAGES) {
    const d = perPage[p].data;
    if (!d) { report("social-meta", p, false, missing(p)); continue; }
    const gaps = [!d.ogTitle && 'meta[property="og:title"]', !d.twitterCard && 'meta[name="twitter:card"]'].filter(Boolean);
    report("social-meta", p, gaps.length === 0, `missing: ${gaps.join(" + ")}`);
  }

  // SITE-R-012 site_smoke:sandbox-cases — /sandbox renders ≥6 demo case buttons after settle.
  {
    const d = perPage["sandbox.html"].data;
    if (!d) report("sandbox-cases", "sandbox.html", false, missing("sandbox.html"));
    else report("sandbox-cases", "sandbox.html", d.caseBtns >= 6,
      `${d.caseBtns} .case-btn rendered (want >= 6)`);
  }

  // SITE-R-014 site_smoke:console-clean — zero console errors on load of every page.
  for (const p of PAGES) {
    const { status, consoleErrors } = perPage[p];
    if (status === null || status >= 400) { report("console-clean", p, false, missing(p)); continue; }
    report("console-clean", p, consoleErrors.length === 0,
      `console errors: ${consoleErrors.slice(0, 3).join(" | ").slice(0, 300)}`);
  }

  // SITE-R-018 site_smoke:home-hook — hook-first homepage above the fold:
  // .badge mentions reliability, H1 carries the "passes conformance" hook,
  // exactly two hero CTAs linking /check and /agent.
  {
    const d = perPage["index.html"].data;
    if (!d) report("home-hook", "index.html", false, missing("index.html"));
    else {
      const probs = [];
      if (!/reliability/i.test(d.hook.badge))
        probs.push(`.badge text lacks "reliability" (got "${d.hook.badge.trim().slice(0, 60)}")`);
      if (!/passes conformance/i.test(d.hook.h1))
        probs.push(`h1 lacks "passes conformance" (got "${d.hook.h1.trim().slice(0, 60)}")`);
      const ctas = d.hook.heroCtas.map(normHref);
      if (!(ctas.length === 2 && ctas.includes("/check") && ctas.includes("/agent")))
        probs.push(`hero CTAs above fold = [${ctas.join(", ")}] (want exactly /check + /agent)`);
      report("home-hook", "index.html", probs.length === 0, probs.join("; "));
    }
  }

  // ---------------------------------------------------------------------------
  // SITE-R-013 site_smoke:graceful-degrade — with every *coverage*.json fetch
  // aborted, / still renders the H1 hook and a non-blank main.
  // ---------------------------------------------------------------------------
  {
    const { page, status } = await openPage(`${BASE}/index.html`, req => {
      if (/coverage[^/]*\.json/.test(req.url())) { req.abort("failed"); return true; }
      return false;
    });
    if (status === null || status >= 400) report("graceful-degrade", "index.html", false, missing("index.html"));
    else {
      const o = await page.evaluate(() => {
        const h1 = document.querySelector("h1");
        const main = document.querySelector("main") || document.body;
        return { h1: h1 ? h1.textContent : "", mainLen: (main.innerText || "").trim().length };
      });
      const probs = [];
      if (!/passes conformance/i.test(o.h1)) probs.push(`h1 hook absent (got "${o.h1.trim().slice(0, 60)}")`);
      if (o.mainLen < 200) probs.push(`main nearly blank (${o.mainLen} chars of text)`);
      report("graceful-degrade", "index.html", probs.length === 0, probs.join("; "));
    }
    await page.close();
  }

  // ---------------------------------------------------------------------------
  // SITE-R-016 site_smoke:beacons — /api/track POSTs (captured via request
  // interception, aborted after recording) carry home_view on /, check_view on
  // /check, docs_view on /docs.
  // ---------------------------------------------------------------------------
  for (const [p, want] of [["index.html", "home_view"], ["check.html", "check_view"], ["docs.html", "docs_view"]]) {
    const events = [];
    const { page, status } = await openPage(`${BASE}/${p}`, req => {
      if (req.url().includes("/api/track") && req.method() === "POST") {
        try { events.push(new URL(req.url()).searchParams.get("event") || "(none)"); } catch { events.push("(unparseable)"); }
        req.abort("failed");                                    // recorded — never reaches a backend
        return true;
      }
      return false;
    });
    await settle(400);                                          // keepalive beacons can trail the load
    if (status === null || status >= 400) report("beacons", p, false, missing(p));
    else report("beacons", p, events.includes(want),
      `want ${want}; /api/track events seen: [${events.join(", ") || "none"}]`);
    await page.close();
  }

  // ---------------------------------------------------------------------------
  // SITE-R-010 site_smoke:redirects — /tool→/check and /guide→/docs. Browser-level
  // when the server honors _redirects; the local python static server does not, so
  // fall back to asserting the exact _redirects file rows (the preview curl in
  // Task 13 covers the live 301).
  // ---------------------------------------------------------------------------
  {
    const page = await browser.newPage();
    await page.goto(`${BASE}/index.html`, { waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
    for (const [from, to] of [["/tool", "/check"], ["/guide", "/docs"]]) {
      const r = await page.evaluate(async (from) => {
        try {
          const manual = await fetch(from, { redirect: "manual" });
          if (manual.type === "opaqueredirect" || (manual.status >= 301 && manual.status <= 308)) {
            const followed = await fetch(from, { redirect: "follow" });
            return { redirected: true, ok: followed.ok, finalPath: new URL(followed.url).pathname };
          }
          return { redirected: false, status: manual.status };
        } catch (e) { return { redirected: false, error: String(e) }; }
      }, from);
      if (r.redirected) {
        report("redirects", from, r.ok && normHref(r.finalPath) === to,
          `redirects to ${r.finalPath} (want ${to}, HTTP ok=${r.ok})`);
      } else {
        // static server gave no redirect (status ${r.status}) — assert the file rows
        const redirFile = path.join(ROOT, "public", "_redirects");
        const body = fs.existsSync(redirFile) ? fs.readFileSync(redirFile, "utf8") : null;
        const row = new RegExp(`^${from.replace("/", "\\/")}\\s+${to.replace("/", "\\/")}\\s+301\\s*$`, "m");
        report("redirects", `${from}→${to}`, body !== null && row.test(body),
          body === null ? "no server redirect and public/_redirects missing"
                        : `no server redirect and _redirects lacks row "${from}  ${to}  301"`);
      }
    }
    await page.close();
  }
} finally {
  await browser.close();
}

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} site checks passed`);
process.exit(failed.length ? 1 : 0);

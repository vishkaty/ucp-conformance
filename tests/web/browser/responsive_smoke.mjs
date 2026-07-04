// Responsive smoke suite — the committed regression net for the mobile layout bug
// (hero stats + nav overflowed the viewport → the page scrolled sideways into white
// space and content clipped). For every public page, at a real phone width, the page
// MUST NOT overflow horizontally: document.scrollWidth <= viewport (a wide data table
// is fine ONLY inside its own overflow-x:auto scroll container, which keeps the PAGE
// scrollWidth clamped). Any element whose right edge exceeds the viewport is reported.
//
// Env: CHROME_PATH (executable), BASE (default http://127.0.0.1:8189).
import puppeteer from "puppeteer-core";

const CHROME = process.env.CHROME_PATH;
const BASE = process.env.BASE || "http://127.0.0.1:8189";
if (!CHROME) { console.error("CHROME_PATH not set"); process.exit(2); }

// the content/marketing pages (the /tool SPA has its own gate: tool_smoke.mjs — and
// its long-lived connections never reach network-idle, so it's excluded here)
const PAGES = ["index.html", "check.html", "guide.html", "coverage.html", "agent.html", "sandbox.html"];
const WIDTHS = [375, 320];   // iPhone SE / small Android — the tightest common widths

const results = [];
const browser = await puppeteer.launch({
  executablePath: CHROME, headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
});
try {
  const page = await browser.newPage();
  for (const p of PAGES) {
    for (const w of WIDTHS) {
      await page.setViewport({ width: w, height: 812, isMobile: true, deviceScaleFactor: 2 });
      // domcontentloaded (not networkidle) — robust against pages with long-lived
      // connections; a fixed settle lets the async coverage bars lay out.
      await page.goto(`${BASE}/${p}`, { waitUntil: "domcontentloaded", timeout: 20000 });
      await new Promise(r => setTimeout(r, 600));
      const o = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const sw = document.documentElement.scrollWidth;
        const over = [];
        for (const e of document.querySelectorAll("body *")) {
          const r = e.getBoundingClientRect();
          // ignore elements inside an intentional horizontal-scroll container
          let inScroll = false;
          for (let n = e.parentElement; n; n = n.parentElement) {
            const ov = getComputedStyle(n).overflowX;
            if (ov === "auto" || ov === "scroll") { inScroll = true; break; }
          }
          if (!inScroll && r.right > vw + 1) {
            over.push(`${e.tagName}.${String(e.className).slice(0, 24)}@${Math.round(r.right)}`);
          }
        }
        return { vw, sw, over: over.slice(0, 6) };
      });
      const ok = o.sw <= o.vw + 1 && o.over.length === 0;
      results.push({ ok, name: `${p} @${w}px`,
        detail: ok ? `scrollWidth ${o.sw}=vw` :
          `OVERFLOW scrollWidth ${o.sw} > vw ${o.vw}; offenders: ${o.over.join(", ") || "(page-level)"}` });
      console.log(`${ok ? "ok" : "not ok"} - ${p} @${w}px${ok ? "" : "  # " + results.at(-1).detail}`);
    }
  }
} finally {
  await browser.close();
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} responsive checks passed`);
process.exit(failed.length ? 1 : 0);

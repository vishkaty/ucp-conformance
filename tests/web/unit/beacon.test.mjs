// SITE-R-025 — Cloudflare Web Analytics beacon present on every served page, and the
// CSP actually permits it. The "automatic" install does not work for Pages (CF only
// injects for proxied origins), so the beacon must be in the HTML AND script-src must
// allow static.cloudflareinsights.com or the browser blocks it (the reason the CF
// dashboard read 0 despite real traffic).
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const PUB = path.join(ROOT, "public");
const TOKEN = "3bebef55335e4229a5c7807ce207fa13";

const pages = fs.readdirSync(PUB).filter((f) => f.endsWith(".html"));

test("every public page carries the Web Analytics beacon with the spck.dev token", () => {
  assert.ok(pages.length >= 8, "expected the full page set");
  for (const p of pages) {
    const html = fs.readFileSync(path.join(PUB, p), "utf8");
    assert.match(html, /static\.cloudflareinsights\.com\/beacon\.min\.js/,
      `${p} is missing the beacon script`);
    assert.ok(html.includes(TOKEN), `${p} beacon has the wrong/missing token`);
    // exactly once — no double-count
    assert.equal((html.match(/data-cf-beacon/g) || []).length, 1,
      `${p} must have exactly one beacon`);
  }
});

test("the CSP permits the beacon script host", () => {
  const headers = fs.readFileSync(path.join(PUB, "_headers"), "utf8");
  const csp = headers.split("\n").find((l) => /content-security-policy/i.test(l)) || "";
  const scriptSrc = (csp.match(/script-src([^;]*)/i) || [, ""])[1];
  assert.match(scriptSrc, /static\.cloudflareinsights\.com/,
    "script-src must allow static.cloudflareinsights.com or the beacon is CSP-blocked");
});

// Browser smoke suite for the /tool SPA — runs the page's REAL functions in a real
// headless Chromium against the CONTROLLED FIXTURE (no external services, no
// third-party staging). Serves as the committed regression net for the flows that
// broke in the wild: settings headers on the wire, discovery endpoint derivation,
// product discovery incl. query:<term> and manual-id override.
//
// Env: CHROME_PATH (executable), PAGE_URL (default http://127.0.0.1:8189/tool.html),
//      FIXTURE (default http://127.0.0.1:8184 — the controlled 04-08 fixture).
import puppeteer from "puppeteer-core";

const CHROME = process.env.CHROME_PATH;
const PAGE = process.env.PAGE_URL || "http://127.0.0.1:8189/tool.html";
const FIXTURE = process.env.FIXTURE || "http://127.0.0.1:8184";
if (!CHROME) { console.error("CHROME_PATH not set"); process.exit(2); }

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "ok" : "not ok"} - ${name}${detail ? "  # " + detail : ""}`);
}

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: "new",
  // required on CI runners (no user namespaces / small /dev/shm)
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
});
try {
  const page = await browser.newPage();
  await page.goto(PAGE, { waitUntil: "domcontentloaded", timeout: 30000 });

  // 1. settings custom headers reach the wire (fixture /__echo echoes them back)
  const hdr = await page.evaluate(async (fixture) => {
    userSettings = { headers: { "x-test-hdr": "abc123" }, defaultBase: "", defaultDomain: "" };
    loadSettingsToUI();
    const { status, data } = await http("GET", fixture + "/__echo");
    return { status, echoed: data.headers && data.headers["x-test-hdr"] };
  }, FIXTURE);
  check("settings headers sent on the wire", hdr.status === 200 && hdr.echoed === "abc123",
        JSON.stringify(hdr));

  // 2. discovery derives the endpoint from the DECLARED profile (not synthesis)
  const disc = await page.evaluate(async (fixture) => {
    document.getElementById("cfg-base").value = fixture;
    document.getElementById("cfg-domain").value = "";
    document.getElementById("cfg-product").value = "";
    sessionData = {};
    const ucp = await discover();
    return { version: ucp.version, ep: ep(ucp.version) };
  }, FIXTURE);
  check("discovery: version + declared endpoint", disc.version === "2026-04-08"
        && disc.ep.replace(/\/$/, "").startsWith(FIXTURE), JSON.stringify(disc));

  // 3. auto product discovery finds the seeded catalog product
  const auto = await page.evaluate(async () => {
    document.getElementById("cfg-product").value = "";
    await discoverProduct(sessionData.specVersion);
    return sessionData.productId;
  });
  check("auto product discovery", auto === "teapot_ceramic_v1", String(auto));

  // 4. query:<term> drives the search
  const q = await page.evaluate(async () => {
    document.getElementById("cfg-product").value = "query:mug";
    await discoverProduct(sessionData.specVersion);
    return sessionData.productId;
  });
  check("query:<term> product discovery", q === "mug_enamel_v1", String(q));

  // 5. manual product id skips catalog entirely
  const manual = await page.evaluate(async () => {
    document.getElementById("cfg-product").value = "kettle_copper";
    await discoverProduct(sessionData.specVersion);
    return sessionData.productId;
  });
  check("manual product id override", manual === "kettle_copper", String(manual));

  // 6. an unmatched custom query is RESCUED by the fallback queries ('*', ...)
  const rescued = await page.evaluate(async () => {
    document.getElementById("cfg-product").value = "query:zzz_nothing_zzz";
    await discoverProduct(sessionData.specVersion);
    return sessionData.productId;
  });
  check("fallback queries rescue an unmatched custom query", !!rescued, String(rescued));
} finally {
  await browser.close();
}

const fails = results.filter((r) => !r.ok);
console.log(`\n${results.length - fails.length}/${results.length} browser smoke checks passed`);
process.exit(fails.length ? 1 : 0);

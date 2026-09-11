// parity_runner.mjs — the JS side of the preview-parity gate (D5-11, SITE-R-034).
// For every frozen capture under conformance/web/preview_parity/captures/ it stubs
// global fetch with the captured responses (the frozen-capture idiom of
// tests/web/unit/conformance.test.mjs), runs the REAL preview (deep: catalog probes
// included) through functions/api/conformance.js, and prints one JSON document:
//   { "<capture>": { verdicts: { "<preview id>": "pass|deviation|not-tested|absent" }, summary } }
// No network: a capture whose fetch stub is asked for anything it does not carry gets a
// 404 body — that is itself a verdict the gate compares.
//   node parity_runner.mjs <captures-dir> [<preview-module-path>]   (PREVIEW_JS overrides for kill-tests)
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const dir = process.argv[2] || path.join(ROOT, "conformance/web/preview_parity/captures");
const modPath = process.argv[3] || process.env.PREVIEW_JS || path.join(ROOT, "functions/api/conformance.js");
const { preview } = await import(pathToFileURL(modPath).href);
const MAP = JSON.parse(fs.readFileSync(process.env.PARITY_MAP || path.join(ROOT, "conformance/web/preview_parity.json"), "utf8"));
const HOST = MAP.capture_host;

function resp(entry) {
  if (!entry) return new Response(JSON.stringify({ error: "not captured" }), { status: 404, headers: { "content-type": "application/json" } });
  const body = typeof entry.body === "string" ? entry.body : JSON.stringify(entry.body);
  return new Response(body, { status: entry.status, headers: { "content-type": entry.content_type || "application/json" } });
}

const out = {};
for (const f of fs.readdirSync(dir).filter((n) => n.endsWith(".json")).sort()) {
  const cap = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  const cat = cap.catalog || {};
  globalThis.fetch = async (url, opts = {}) => {
    const u = new URL(String(url));
    if (u.pathname === "/.well-known/ucp") return resp(cap.well_known);
    let body = {};
    try { body = JSON.parse(opts.body || "{}"); } catch {}
    if (u.pathname.endsWith("/catalog/search"))
      return resp(body.query === "zzz_no_such_product_zzz" ? cat.empty_search : cat.search);
    if (u.pathname.endsWith("/catalog/lookup")) return resp(cat.lookup);
    return resp(null);
  };
  const r = await preview(HOST, { deep: true });
  const verdicts = {};
  for (const id of Object.keys(MAP.preview)) verdicts[id] = "absent";
  for (const c of r.checks || []) verdicts[c.id] = c.status;
  out[cap.name] = { verdicts, summary: r.summary || null, error: r.error || null };
}
process.stdout.write(JSON.stringify(out, null, 1) + "\n");

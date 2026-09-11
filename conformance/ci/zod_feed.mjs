#!/usr/bin/env node
// zod_feed.mjs — the js-sdk ZOD leg of the dual-oracle gate at 2026-08-25 (D4-05 / B5b), REPORT-ONLY.
//
// Reads a JSON list [{label, zod, payload, referee_ok}] (the same corpus items the Rust oracle,
// the referee and the pydantic legs validate, with the referee's verdict and the exported zod
// schema name), runs `<zod>.safeParse(payload)` from the pinned @ucp-js/sdk (conformance/ci/
// zod_feed/package.json; node_modules gitignored, `npm install` there first) and writes rows
// for every verdict that disagrees with the referee:
//   {date, sdk_cut, schema_name, payload_label, zod, referee, class: "lax" | "over-strict"}
// Output {generated, sdk_cut, compared, rows} goes to --out (default stdout). The gate prints the
// count; nothing here ever fails a build — rows feed ledger candidates (decision 24: the owner
// commits ops/feeds/zod_divergences.json after pull_feeds/local runs).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
// resolve @ucp-js/sdk from the pinned package dir next to this script (zod_feed/node_modules)
const require = createRequire(path.join(path.dirname(fileURLToPath(import.meta.url)), "zod_feed", "package.json"));
const args = process.argv.slice(2);
const arg = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const inPath = arg("--in"); const outPath = arg("--out");
if (!inPath) { console.error("usage: zod_feed.mjs --in items.json [--out rows.json]"); process.exit(2); }
let sdk;
try { sdk = require("@ucp-js/sdk"); } catch (e) { console.error("zod leg unavailable: " + e.message); process.exit(2); }
const version = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "zod_feed", "node_modules", "@ucp-js", "sdk", "package.json"), "utf8")).version;
const items = JSON.parse(fs.readFileSync(inPath, "utf8"));
const rows = []; let compared = 0, notJudged = 0;
const today = new Date().toISOString().slice(0, 10);
for (const it of items) {
  const schema = sdk[it.zod];
  if (!schema || typeof schema.safeParse !== "function") { notJudged++; continue; }
  compared++;
  const ok = schema.safeParse(it.payload).success;
  if (ok !== it.referee_ok) {
    rows.push({ date: today, sdk_cut: "@ucp-js/sdk " + version, schema_name: it.zod, payload_label: it.label,
      zod: ok ? "valid" : "invalid", referee: it.referee_ok ? "valid" : "invalid",
      class: ok && !it.referee_ok ? "lax" : "over-strict" });
  }
}
const doc = { generated: new Date().toISOString(), sdk_cut: "@ucp-js/sdk " + version, compared, not_judged: notJudged, rows };
if (outPath) fs.writeFileSync(outPath, JSON.stringify(doc, null, 1) + "\n"); else process.stdout.write(JSON.stringify(doc, null, 1) + "\n");
console.error(`zod ${version}: compared ${compared} · divergences ${rows.length} (lax ${rows.filter(r => r.class === "lax").length}, over-strict ${rows.filter(r => r.class === "over-strict").length}) · not judged ${notJudged}`);

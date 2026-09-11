// SITE-R-035 — the rubric's MUST-only scope statement (decision 8) is a registered claim
// rendered verbatim: airtight = MUST / MUST NOT / REQUIRED / SHALL at the 2026-08-25 pin;
// SHOULD-class obligations are censused report-only and link to the census.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const RAW = fs.readFileSync(path.join(ROOT, "public/rubric.html"), "utf8");
const HTML = RAW.replace(/\s+/g, " ");
const TEXT = RAW.replace(/<[^>]+>/g, "").replace(/\s+/g, " ");   // what the reader (and the claims audit) sees
const CLAIMS = JSON.parse(fs.readFileSync(path.join(ROOT, "public/site_claims.json"), "utf8")).claims;

test("rubric carries CLAIM-RUB-016 verbatim with a census link", () => {   // SITE-R-035
  const c = CLAIMS.find((x) => x.id === "CLAIM-RUB-016");
  assert.ok(c && c.text, "CLAIM-RUB-016 registered in public/site_claims.json");
  assert.equal(c.page, "rubric.html");
  assert.ok(TEXT.includes(c.text.replace(/\s+/g, " ")), "registered text rendered verbatim on rubric.html (visible text)");
  assert.match(HTML, /cd78fb38/);
  assert.match(HTML, /href="\/coverage[^"]*"[^>]*>[^<]*census/i);
});

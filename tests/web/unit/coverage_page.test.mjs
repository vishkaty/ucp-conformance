// SITE-R-028 — the coverage page's rendered sentences are registered claims: the retired
// ceiling sentence ("no independently-authored 08-25 implementation exists") never renders,
// the replacement renders VERBATIM from the registered CLAIM-COV-002 text, and the 04-08 tab
// carries the tag re-point footnote (CLAIM-COV-003) — data-driven off the real export.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { runPage, jsonFetch } from "./dom_stub.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const HTML = fs.readFileSync(path.join(ROOT, "public/coverage.html"), "utf8");
const COV = JSON.parse(fs.readFileSync(path.join(ROOT, "public/coverage.json"), "utf8"));
const AGC = JSON.parse(fs.readFileSync(path.join(ROOT, "public/agent-coverage.json"), "utf8"));
const CLAIMS = JSON.parse(fs.readFileSync(path.join(ROOT, "public/site_claims.json"), "utf8")).claims;
const claim = (id) => CLAIMS.find((c) => c.id === id);

const RETIRED = /no independently-authored 08-25 implementation exists/;

async function render(cov = COV) {
  return runPage(HTML, { fetch: jsonFetch({ "/coverage.json": cov, "/agent-coverage.json": AGC }) });
}
function clickTab(doc, ver) {
  const tab = doc.getElementById("tabs").children.find((t) => t.dataset.ver === ver);
  assert.ok(tab, `tab ${ver} rendered`); tab.dispatch("click");
}

test("08-25 summary never contains the retired ceiling sentence", async () => {   // SITE-R-028
  const doc = await render();
  clickTab(doc, "2026-08-25");
  const text = doc.getElementById("summary").textContent;
  assert.doesNotMatch(text, RETIRED);
  assert.doesNotMatch(HTML, RETIRED, "the literal must not survive anywhere in the page source");
});

test("08-25 ceiling sentence renders verbatim from registered CLAIM-COV-002", async () => { // SITE-R-028
  const c = claim("CLAIM-COV-002");
  assert.ok(c && c.text, "CLAIM-COV-002 registered in public/site_claims.json");
  const doc = await render();
  clickTab(doc, "2026-08-25");
  const text = doc.getElementById("summary").textContent;
  assert.ok(text.includes(c.text), `rendered summary carries the registered text\n${text}`);
  // data-driven: with live-wire evidence present the sentence retires itself
  const cov2 = JSON.parse(JSON.stringify(COV));
  cov2.versions["2026-08-25"].evidence_breakdown["live-wire"] = 1;
  const doc2 = await render(cov2);
  clickTab(doc2, "2026-08-25");
  assert.ok(!doc2.getElementById("summary").textContent.includes(c.text));
});

test("04-08 tab carries the tag re-point footnote (CLAIM-COV-003) from the pinned SHA", async () => { // SITE-R-028
  const c = claim("CLAIM-COV-003");
  assert.ok(c && c.text, "CLAIM-COV-003 registered in public/site_claims.json");
  const doc = await render();
  clickTab(doc, "2026-04-08");
  const text = doc.getElementById("summary").textContent;
  assert.ok(text.includes(c.text), `04-08 summary carries the registered footnote\n${text}`);
  assert.match(text, /a2d8bf0b/);
  assert.match(text, /ucp#813/);
  clickTab(doc, "2026-08-25");
  assert.ok(!doc.getElementById("summary").textContent.includes(c.text), "footnote is 04-08 only");
});

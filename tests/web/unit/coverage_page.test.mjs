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
  // W1 integration: the committed export now carries discovery_live {stores > 0} (D4-08's smoke),
  // which D5-13 binds to the CLAIM-COV-007 form instead of this ceiling (tests below); the
  // ceiling is asserted on a copy with the slot nulled — the W0 shape of the export.
  const cov0 = JSON.parse(JSON.stringify(COV));
  cov0.versions["2026-08-25"].discovery_live = null;
  const doc = await render(cov0);
  clickTab(doc, "2026-08-25");
  const text = doc.getElementById("summary").textContent;
  assert.ok(text.includes(c.text), `rendered summary carries the registered text\n${text}`);
  // data-driven: with live-wire evidence present the sentence retires itself
  const cov2 = JSON.parse(JSON.stringify(cov0));
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
  assert.match(text, /a25a4a24/);                 // the re-pinned 04-08 commit (D2-15, decision 3), rendered from spec_pins
  assert.doesNotMatch(text, /re-pointed/);     // the tag-move clause retired with the re-pin
  assert.match(text, /ucp#813/);
  clickTab(doc, "2026-08-25");
  assert.ok(!doc.getElementById("summary").textContent.includes(c.text), "footnote is 04-08 only");
});

// ── D5-03 / SITE-R-029: the `converting` state renders honestly ─────────────
function converting(ver, extra = {}) {
  const cov = JSON.parse(JSON.stringify(COV));
  const v = cov.versions[ver];
  v.state = "converting";
  v.gap_by_testability = { testable: 5, "needs-receiver": 3, "needs-oauth": 1, manual: 4, ...extra };
  return cov;
}

test("converting export renders the converting note with N of M testable-tier MUSTs", async () => { // SITE-R-029
  const cov = converting("2026-08-25");
  // the control tab is PLANTED live: the committed 2026-04-08 state is the owner's open
  // ruling (rule R-a reads it `converting` on SIG-039/OVR-002, needs-receiver) — the
  // control must not depend on that ruling (W0-integration 2026-09-10).
  cov.versions["2026-04-08"].state = "live";
  const doc = await render(cov);
  clickTab(doc, "2026-08-25");
  const text = doc.getElementById("summary").textContent;
  assert.match(text, /checks are landing/);
  assert.match(text, /9 of \d+ testable-tier MUSTs still open/);     // 5 + 3 + 1 open
  assert.match(text, /never blended/);
  const tab = doc.getElementById("tabs").children.find((t) => t.dataset.ver === "2026-08-25");
  assert.match(tab.textContent, /converting/, "the state word renders on the tab");
  // a live tab carries neither the note nor the word
  clickTab(doc, "2026-04-08");
  assert.doesNotMatch(doc.getElementById("summary").textContent, /checks are landing/);
});

test("legacy converting versions render the data-driven 'no further work planned' line (CLAIM-COV-005)", async () => { // SITE-R-029
  const c = claim("CLAIM-COV-005");
  assert.ok(c && c.text, "CLAIM-COV-005 registered in public/site_claims.json");
  const cov = converting("2026-01-11", { testable: 0, "needs-oauth": 0, "needs-receiver": 17 });
  cov.versions["2026-01-11"].no_further_work = true;          // D2-06 export field
  const doc = await render(cov);
  clickTab(doc, "2026-01-11");
  const text = doc.getElementById("summary").textContent;
  assert.ok(text.includes(c.text), `01-11 summary carries the registered line\n${text}`);
  assert.match(text, /17 webhook-receiver rows ungraded/);
  clickTab(doc, "2026-08-25");                                  // current version: never the legacy line
  assert.ok(!doc.getElementById("summary").textContent.includes(c.text));
});


// ── D5-13 / SITE-R-035: the 08-25 evidence sentence is bound to discovery_live ──────────
test("08-25 with discovery_live stores > 0 renders the registered CLAIM-COV-007 form (count + as_of), not the ceiling", async () => { // SITE-R-035
  const c = claim("CLAIM-COV-007");
  assert.ok(c && c.text, "CLAIM-COV-007 registered in public/site_claims.json");
  const cov = JSON.parse(JSON.stringify(COV));
  cov.versions["2026-08-25"].discovery_live = { stores: 3, as_of: "2026-09-10" };
  const doc = await render(cov);
  clickTab(doc, "2026-08-25");
  const text = doc.getElementById("summary").textContent;
  assert.ok(text.includes(c.text), `summary carries the registered discovery-live text\n${text}`);
  assert.match(text, /\b3 independently-operated/);
  assert.match(text, /as of 2026-09-10/);
  assert.ok(!text.includes(claim("CLAIM-COV-002").text), "the W0 ceiling sentence retires when stores > 0");
});

test("08-25 with discovery_live null or stores 0 renders the W0 ceiling sentence (CLAIM-COV-002)", async () => { // SITE-R-035
  for (const dl of [null, { stores: 0, as_of: null }]) {
    const cov = JSON.parse(JSON.stringify(COV));
    cov.versions["2026-08-25"].discovery_live = dl;
    const doc = await render(cov);
    clickTab(doc, "2026-08-25");
    const text = doc.getElementById("summary").textContent;
    assert.ok(text.includes(claim("CLAIM-COV-002").text), `fallback renders for discovery_live=${JSON.stringify(dl)}`);
    assert.doesNotMatch(text, /independently-operated stores/);
  }
});


// ── D5-16 / SITE-R-036: the evidence legend and the role split are data-driven ─────────
test("evidence line iterates coverage.json.evidence_classes — a fifth class renders with its count", async () => { // SITE-R-036
  const cov = JSON.parse(JSON.stringify(COV));
  cov.evidence_classes["register-selfcheck"] = "a struct check whose oracle is the register itself";
  const v = cov.versions["2026-04-08"];
  v.evidence_classes = Object.keys(cov.evidence_classes);
  v.evidence_breakdown["register-selfcheck"] = 5;
  const doc = await render(cov);
  clickTab(doc, "2026-04-08");
  const text = doc.getElementById("summary").textContent;
  assert.match(text, /register-selfcheck 5/, `fifth class rendered from the export\n${text}`);
  for (const k of Object.keys(cov.evidence_classes)) assert.match(text, new RegExp(k.replace(/[-]/g, "\\-") + " \\d+"));
});

test("roles.summary renders the per-role MUST split and a role toggle re-renders the bar", async () => { // SITE-R-036
  const cov = JSON.parse(JSON.stringify(COV));
  const v = cov.versions["2026-08-25"];
  v.roles = { summary: { merchant: 500, agent: 220, both: 40, other: 100 },
              merchant: { musts: 500, check: 60, exempt: 10, gap: 430 },
              agent: { musts: 220, check: 0, exempt: 3, gap: 217 } };
  const doc = await render(cov);
  clickTab(doc, "2026-08-25");
  const text = doc.getElementById("summary").textContent;
  assert.match(text, /merchant 500/); assert.match(text, /agent 220/); assert.match(text, /both 40/); assert.match(text, /other 100/);
  const toggle = doc.getElementById("role-toggle");
  assert.ok(toggle, "role toggle rendered when roles.summary is present");
  const btn = toggle.children.find((b) => b.dataset.role === "merchant");
  assert.ok(btn, "merchant role button"); btn.dispatch("click");
  const after = doc.getElementById("summary").textContent;
  assert.match(after, /500/); assert.match(after, /430/);
  // without roles (an export with the D2-08 block stripped) nothing role-related renders
  const cov2 = JSON.parse(JSON.stringify(COV));
  for (const vv of Object.values(cov2.versions)) delete vv.roles;
  const doc2 = await render(cov2);
  clickTab(doc2, "2026-08-25");
  assert.ok(!doc2.getElementById("role-toggle"), "no role toggle without roles in the export");
});

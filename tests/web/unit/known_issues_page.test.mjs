// SITE-R-033 — the published KNOWN ISSUES page is a faithful projection of the single
// source conformance/ci/known_issues.json: every source row is published (JSON + HTML row
// carrying its id and symptom), every row is registered as site claim KI-<id> with the
// row's own review clock, the summary counts are recomputed from the rows, and no refuted
// row ever renders (S8d stays in the ledger).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");
const unesc = (h) => h.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&#39;/g, "'")
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const SRC = JSON.parse(read("conformance/ci/known_issues.json"));
const PUB = JSON.parse(read("public/known-issues.json"));
const HTML = read("public/known-issues.html");
const CLAIMS = JSON.parse(read("public/site_claims.json")).claims;

test("every source row is published in known-issues.json with its symptom and clocks", () => {   // SITE-R-033
  assert.equal(PUB.summary.rows, SRC.issues.length);
  for (const row of SRC.issues) {
    const pub = PUB.issues.find((r) => r.id === row.id);
    assert.ok(pub, `${row.id} published`);
    assert.equal(pub.symptom, row.symptom);
    assert.equal(pub.re_verified, row.re_verified);
    assert.equal(pub.review_by, row.review_by);
    assert.equal(pub.refuted, false);
  }
  assert.equal(PUB.spec_pin, SRC.spec_pin);
});

test("every row renders on known-issues.html: an element with id KI-xxx carrying the symptom", () => {   // SITE-R-033
  const flat = unesc(HTML);
  for (const row of SRC.issues) {
    assert.ok(new RegExp(`id="${row.id}"`).test(HTML), `${row.id} anchor rendered`);
    assert.ok(flat.includes(row.symptom.replace(/\s+/g, " ")), `${row.id} symptom rendered verbatim`);
  }
  assert.doesNotMatch(HTML, /refuted: true/);
});

test("every row is registered as site claim KI-<id> with the row's review clock", () => {   // SITE-R-033
  for (const row of SRC.issues) {
    const c = CLAIMS.find((x) => x.id === row.id);
    assert.ok(c, `${row.id} registered in public/site_claims.json`);
    assert.equal(c.page, "known-issues.html");
    assert.equal(c.text, row.symptom);
    assert.equal(c.review_by, row.review_by);
    assert.ok(c.evidence && c.evidence.includes(row.ledger_row), `${row.id} evidence cites ledger row ${row.ledger_row}`);
  }
  const stray = CLAIMS.filter((c) => /^KI-\d{3}$/.test(c.id) && !SRC.issues.some((r) => r.id === c.id));
  assert.deepEqual(stray.map((c) => c.id), [], "no KI claim without a source row");
});

test("summary counts are recomputed from the rows; nothing refuted", () => {   // SITE-R-033
  const rows = SRC.issues;
  const open = rows.filter((r) => r.status_by_cut.some((c) => c.status === "open")).length;
  const fixed = rows.filter((r) => r.status_by_cut.some((c) => c.status === "fixed")).length;
  const unfixed = rows.filter((r) => !r.status_by_cut.some((c) => c.status === "fixed")).length;
  assert.equal(PUB.summary.open_on_some_cut, open);
  assert.equal(PUB.summary.fixed_on_some_cut, fixed);
  assert.equal(PUB.summary.unfixed_everywhere, unfixed);
  assert.equal(PUB.summary.refuted, 0);
  const byArt = {};
  for (const r of rows) byArt[r.artifact] = (byArt[r.artifact] || 0) + 1;
  assert.deepEqual(PUB.summary.by_artifact, byArt);
  assert.equal(PUB.summary.latest_re_verified, rows.map((r) => r.re_verified).sort().at(-1));
});

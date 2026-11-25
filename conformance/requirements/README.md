# Requirements Register

Machine-readable, **version-scoped** record of every normative UCP requirement the
tester checks. The register is the certification backbone: every test cites a row
here, and every MUST row must map to a test or a documented exemption (see the
traceability matrix). **Every row quotes the actual spec/schema text at the pinned
commit** in `conformance/SOURCES.lock.json` — no summaries, no paraphrase as source.

Layout: `requirements/<spec-version>/<area>.json`. Areas are merged by the build into
a single per-version register. One file per area keeps extraction reviewable.

## Row schema
```
{
  "id":            "AREA-NNN",            // stable id, cited by tests
  "requirement":   "one-line normative statement",
  "keyword":       "MUST | MUST NOT | SHOULD | SHOULD NOT | MAY",
  "source":        "repo:path#Lnn",       // pinned-SHA file + line/anchor
  "quote":         "verbatim text from the source (the normative sentence)",
  "versions":      ["2026-04-08"],         // version(s) this applies to
  "transport":     ["rest"],               // rest | mcp | a2a | embedded | any
  "testability":   "testable | needs-receiver | needs-oauth | manual | untestable",
  "role":          "business | platform | both | handler | spec-author | host",   // who the obligation binds (D2-08; lane per decision 27)
  "role_provenance": "agent-lock | not-agent-bound | client-bound-exemption | subject | direction | backported | review:<batch>",
  "normative_basis": "sentence | table | bullet | pseudocode | schema | definition | inferred",   // what carries the MUST (D2-11a; decision 29)
  "normative_basis_justification": "[<batch>] why an inferred/definition/bullet/table MUST stands",  // review outcomes only
  "official_oracle": true|false,           // does the official 01-23 suite corroborate?
  "browser_capable": true|false,           // can the CORS-limited web tool check it?
  "schema_enforced": true|false,           // caught by ucp-schema, or needs coded check?
  "notes":         "discrepancies / version deltas / why untestable"
}
```

`role` is REQUIRED on every mandatory (MUST / MUST NOT / SHALL / SHALL NOT / REQUIRED)
row and is the single field both lanes read: the merchant lane counts `business | both |
handler`, the agent lane `platform | both | host`, `spec-author` rows are speclint's
(`register-selfcheck`). Seeded by `requirements/tools/assign_roles.py` (idempotent: only
rows without a role are touched); rows it cannot resolve go to
`requirements/<v>/role_review_queue.json`, and the `register` gate is red until that
queue is empty — a reviewer sets `role` + `role_provenance: review:<batch>` (the batch
names a `coverage/review_signoffs.json` entry carrying the >=10% human sample, decision
13) or records the decision in `requirements/role_adjudications.json`. The agent
denominator lock (`agent/agent_denominator_lock.json`) must agree with the roles both
ways; regenerate it deliberately with `agent/agent_matrix.py --snapshot-lock "<note>"`.

`normative_basis` is REQUIRED on every mandatory row unless the row is listed in
`requirements/<v>/normative_basis_review_queue.json` (the nokw review set, D2-11b).
`requirements/tools/assign_normative_basis.py --apply` fills `schema` (source under
`source/`), `sentence` (a MUST-class keyword in the quote) and `pseudocode` (every cited
line inside a fence) mechanically and queues the rest; reviewed outcomes (`table`,
`bullet`, `definition`, `inferred`) are recorded in
`requirements/normative_basis_adjudications.json` with a justification, and
`definition|inferred` rows must be named by a `coverage/review_signoffs.json` batch
(decision 29: an inferred MUST is kept only with a written, signed-off justification).

The seven values, which partition by WHERE THE MANDATORY FORCE LIVES rather than by
what construct the quote sits in. That distinction is the whole taxonomy and it is
easy to get backwards, so it is written out here rather than left in the tool:

| value | what carries the MUST | assigned |
| --- | --- | --- |
| `sentence` | a mandatory keyword (MUST / MUST NOT / SHALL / SHALL NOT / REQUIRED) in the quote itself | mechanically |
| `schema` | the quote is a schema constraint, source under `source/` | mechanically |
| `pseudocode` | every cited line sits inside a code fence, an algorithm the prose specifies | mechanically |
| `table` | a `Yes` or `MUST` cell in a column headed Required or Cond. | review outcome |
| `bullet` | a list item under a keyword bearing lead-in | review outcome |
| `definition` | a definitional sentence; a downgrade candidate | review outcome, needs a sign-off |
| `inferred` | a MUST inferred from context, kept only with a written justification | review outcome, needs a sign-off |

Two consequences worth stating, because both have been got wrong in review. `table` does
NOT mean "the quote is a table row": a row of an operations table with no Required
column is a `definition`, which is why CHK-001 to CHK-005 and IDM-005 all carry that
value at the two 2026-01 pins. The same ids are not a fixed answer across versions:
CHK-001 and CHK-004 are `sentence` at 2026-04-08 and 2026-08-25, where the text carries
a keyword, and the other four do not exist there at all. And `bullet` does NOT mean "the
quote is a list item": the great majority of list-item quotes are `sentence`, and
exactly six rows are `bullet`, namely IDM-002, IDM-003 and IDM-004 at each of the two
2026-01 pins. What matters is whether the lead-in carries the keyword, not whether the
quote is a bullet. The six are enumerated rather than counted because a bare count goes
stale on the next relabel, as one did here: CHK-017 left this class in the same change
that added this table. Widening either one to key on construct would move rows into a
class that needs no sign-off, routing them around decision 29.

## Verdict rule reminder
A MUST row → `deviation` when violated (blocks aggregate green). SHOULD → `advisory`.
MAY → `informational`. A row not exercised → `not-tested` (also blocks aggregate green).
Never collapse a transient/timeout into pass or fail; that is `inconclusive`.

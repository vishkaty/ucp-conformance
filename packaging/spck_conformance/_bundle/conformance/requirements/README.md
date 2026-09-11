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

## Verdict rule reminder
A MUST row → `deviation` when violated (blocks aggregate green). SHOULD → `advisory`.
MAY → `informational`. A row not exercised → `not-tested` (also blocks aggregate green).
Never collapse a transient/timeout into pass or fail — that is `inconclusive`.

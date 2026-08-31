# WAIVERS-PROPOSED — P2b (lane/p2b-extract)

Per the P2b brief: `lane/w1a-versionmap` may be touching
`conformance/coverage/register_completeness_waivers.json`'s mechanics concurrently, so this
lane does not edit that file directly. Everything below is a proposed addition for whoever
merges both lanes. Each entry is written in the exact shape
`verify_register_completeness.py` expects (`scope_exclusions[]` / `waivers[]` objects), ready
to paste into `register_completeness_waivers.json`.

All entries validated against `validate_waiver()` / `validate_scope()` (class enum, minimum
reason length, `duplicate_of`/`row_id` presence) by hand before listing.

## Verification baseline

```
Before this lane (main 2c14e8d):
  2026-08-25:   922 kw    416 covered      0 scope-excl     0 waived   506 missed  (report mode)

After this lane's 3 new/edited area files (rows only, no waiver file touched):
  2026-08-25:   922 kw    509 covered      0 scope-excl     0 waived   413 missed  (report mode)

If the 14 line-waivers below (tokenization.md x2 + identity-linking x12) are applied:
  -> 399 missed for the three target areas' own gap (matches the ~399 the brief anticipated)

If the 4 scope-exclusions + 5 guide.md line-waivers below are ALSO applied (bonus finding,
see "Tokenization handler guide/template/examples" below):
  -> 361 missed overall
```

`verify_register.py 2026-08-25`: **748/748 verified, 0 line-warnings, 0 FAILED** (up from
726/726 pre-lane; the 22 new rows/edits all resolve against the pinned vendor tree).

---

## 1. Scope exclusions (whole-file, `non-normative-doc`)

The mission flagged "the ~33 handler examples/template/guide hits" as scope-exclusion
candidates. Honest count once existing `payment.json` coverage is subtracted: **38** raw
occurrences across 5 files, but **only 4 of those files are cleanly whole-file-excludable**
(see the guide.md exception directly below the table). These 4 are simply the 2026-08-25
successors of files the register already scope-excludes at 04-08/01-23/01-11 (paths moved
under `payment/`, and `encrypted-credential-handler.md` was renamed to
`encrypted-credential-payment-handler.md`) — same document type, same non-normative nature,
verified by reading each file's actual MUST lines (not just filename-matching the old
entries).

```json
{
 "file": "docs/specification/payment/template.md",
 "versions": ["2026-08-25"],
 "class": "non-normative-doc",
 "reason": "2026-08-25 successor of docs/specification/payment-handler-template.md (already scope-excluded for 04-08/01-23/01-11 as versions:\"*\", now stale for this path since the file moved). Still a fill-in-the-blank TEMPLATE ('{Handler Name} Payment Handler', '{participants} MUST complete', '{If using tokens: Tokens MUST expire after {duration}}') for authors WRITING a new payment-handler specification. Its MUST clauses bind the future handler being templated, not the merchant server under test; the literal '{...}' placeholder syntax on 4 of its 9 keyword lines confirms none of this is live, checkable text. Verified by reading all 9 keyword lines (L91,168,186,243,349,373,375,376,377) individually before excluding, not by filename match alone."
},
{
 "file": "docs/specification/payment/examples/processor-tokenizer-payment-handler.md",
 "versions": ["2026-08-25"],
 "class": "non-normative-doc",
 "reason": "2026-08-25 successor of docs/specification/examples/processor-tokenizer-payment-handler.md (already scope-excluded for 04-08/01-23, now stale for this path since the file moved under payment/examples/). A worked, illustrative example of one Business-or-PSP tokenization+processing configuration; its 6 MUST/MUST NOT lines narrate what THIS example scenario does (e.g. 'the Tokenizer/Processor MUST verify that the binding submitted...'), not independent normative obligations on a generic UCP server. Examples are non-normative by spec convention, matching every prior version's treatment of this same file."
},
{
 "file": "docs/specification/payment/examples/platform-tokenizer-payment-handler.md",
 "versions": ["2026-08-25"],
 "class": "non-normative-doc",
 "reason": "2026-08-25 successor of docs/specification/examples/platform-tokenizer-payment-handler.md (already scope-excluded for 04-08/01-23/01-11 as versions:\"*\", now stale for this path since the file moved under payment/examples/). A worked, illustrative example of a Platform-side tokenizer configuration; its 12 MUST/MUST NOT lines (mostly a per-example compliance table: 'Compliance (Receivers)', 'Secure transmission', 'Issued to participant', etc.) narrate this one scenario's design choices, not independent normative obligations. Non-normative by spec convention, matching the prior versions' treatment."
},
{
 "file": "docs/specification/payment/examples/encrypted-credential-payment-handler.md",
 "versions": ["2026-08-25"],
 "class": "non-normative-doc",
 "reason": "2026-08-25 successor of docs/specification/examples/encrypted-credential-handler.md (RENAMED, not just moved -> encrypted-credential-payment-handler.md; already scope-excluded for 04-08/01-23/01-11 as versions:\"*\", now stale for the old path). A worked, illustrative example of an encryption-instead-of-tokenizing pattern; its 5 MUST/MUST NOT lines are the same per-example compliance-table narration as its siblings above. Non-normative by spec convention, matching the prior versions' treatment."
}
```

### `payment/guide.md` — NOT whole-file excluded (partially normative)

`payment/guide.md` is the 2026-08-25 successor of `payment-handler-guide.md`, and that file
was **never** given a whole-file scope exclusion at any prior version either — only 5
individual line-level waivers (all `non-normative`, subject = "Specifications"/"the
specification", i.e. spec-authoring guidance). Blanket-excluding the whole file would have
been wrong: **`payment.json` already carries real rows extracted straight from this file**
(`PAY-006` cites `guide.md#L189`, `PAY-008` cites `guide.md#L283`, `PAY-009` cites
`guide.md#L377-L378`) — proof that some of its content genuinely binds Businesses/Platforms,
not just handler-spec authors, and is already correctly registered. Read all 12 keyword
lines individually; 6 are already covered by those PAY-* rows, 5 match the exact
non-normative spec-authoring pattern already established for this file's predecessor, and
**1 (`L221`) is a genuine duplicate of an already-registered `request-constraints.json` row**
(`RC-014`) rather than either a fresh normative gap or spec-authoring guidance — filed as a
`duplicate` waiver, not `non-normative`.

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/guide.md",
 "line": 92,
 "class": "non-normative",
 "reason": "Subject is 'Specifications' (payment-handler spec authors): 'Specifications MUST explicitly document these field mappings.' Same exact pattern as the already-waived 2026-04-08 payment-handler-guide.md#L92 (identical sentence, file just moved/renamed). Binds document authors of downstream handler specs, not a runtime server obligation."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/guide.md",
 "line": 221,
 "class": "duplicate",
 "reason": "'the Business MUST include an explicit path because the available instrument's response Normalized Path does not identify submitted instruments' restates the general request_constraints explicit-path rule already registered as RC-014 ('A Business MUST provide an explicit path when the Normalized Path of that structured response object does not identify the intended request objects', overview/index.md#L271), applied here specifically to the available_instruments case. Same substantive obligation, guide-specific worked context.",
 "duplicate_of": "RC-014"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/guide.md",
 "line": 851,
 "class": "non-normative",
 "reason": "Subject is 'The specification' (payment-handler spec authors): 'The specification MUST define a mapping for common failures...' Identical pattern to the already-waived 2026-04-08 payment-handler-guide.md#L746 (same sentence, file moved). Documentation obligation on handler-spec authors, not runtime-observable."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/guide.md",
 "line": 867,
 "class": "non-normative",
 "reason": "Under '## Specification Template': 'Sections marked [REQUIRED] MUST be present; sections marked [CONDITIONAL]...' Identical pattern to the already-waived 2026-04-08 payment-handler-guide.md#L762 (same sentence, file moved). Constrains the structure of downstream handler-specification documents authored by spec authors, not server behavior. Covers both keyword occurrences on this line (REQUIRED and MUST)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/guide.md",
 "line": 879,
 "class": "non-normative",
 "reason": "Bullet under '## Conformance Checklist for Spec Authors': '[ ] All [REQUIRED] sections are present'. Identical pattern to the already-waived 2026-04-08 payment-handler-guide.md#L774 (same sentence, file moved). Explicit spec-author checklist item, not a server obligation."
}
```

Not proposed as scope exclusions or waivers because they are already fully covered by
existing rows (no action needed): `guide.md` L50 (-> covered), L283 (-> `PAY-008`), L377
(x2 keywords -> `PAY-009`), L697, L700 (-> covered; both are the same "the
specification/schema MUST define/include X" spec-authoring pattern as L92/L851 above, and
the completeness checker already resolves them against the existing PAY-005/006 quotes'
line spans — verified no `duplicate`/`non-normative` entry is needed for them).

---

## 2. Tokenization.md — 2 line waivers (duplicate, boundary restatement)

Both lines are the file's own "Security Requirements" summary table restating rules already
given their own rows above in the "Binding" section (`TOK-001`..`TOK-005`).

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/tokenization.md",
 "line": 241,
 "class": "duplicate",
 "reason": "Security-Requirements table cell 'Credentials MUST be bound to a binding resource (type and id) and issued to exactly one participant to prevent reuse' restates the binding-required half already covered by TOK-001..004 and the participant-issuance half already covered by TOK-005, in one summary-table sentence.",
 "duplicate_of": "TOK-005"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/payment/tokenization.md",
 "line": 242,
 "class": "duplicate",
 "reason": "Security-Requirements table cell 'Tokenizer MUST verify binding matches before returning credentials' restates the exact-equality binding-verification rule already covered by TOK-001, in one summary-table sentence.",
 "duplicate_of": "TOK-001"
}
```

---

## 3. Identity-linking — 12 line waivers

Two distinct sub-classes, both genuinely already covered — never treated as fresh gaps:

**(a) Quote-fragment/line-wrap boundary artifacts (4 lines).** The completeness checker's
`covered_lines_for` matches a quote fragment against a file line only if the fragment (or the
line) is a clean substring of the other. Where an *existing, already-verified* row's `"..."`
elision lands mid-physical-line (the fragment starts/stops partway through a hard-wrapped
markdown line that also carries unrelated leading/trailing text), that one physical line
fails the substring test on both sides even though `verify_register.py`'s whole-file
substring check (which ignores line boundaries) already confirms the row's quote is present
verbatim. This is a checker artifact, not a missed requirement — flagged here rather than
silently left as a false "gap", and worth flagging generally: the same class likely affects
other already-carried rows elsewhere in the 2026-08-25 register wherever an existing row's
`"..."` boundary falls mid-line; not swept beyond this file (out of this lane's scope).

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 175,
 "class": "duplicate",
 "reason": "Already covered by IDL-012 (source L170-L176, quote spans this exact text: '...If the values do not match, the platform MUST abort and discard the authorization response.'). The row's second quote fragment begins mid-line (right after 'metadata).' on the same physical line 175), so the per-line substring matcher cannot mark this line even though the row's own cited range and quote content include it, and verify_register.py's whole-file check already confirms the text is present verbatim.",
 "duplicate_of": "IDL-012"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 278,
 "class": "duplicate",
 "reason": "Already covered by IDL-030 (source L271-L279, quote spans '...The business MUST publish this metadata when the authorization server does not live on the business domain.'). Same mid-line quote-boundary artifact as L175/IDL-012: the fragment begins right after 'domain conventions.' on the same physical line 278.",
 "duplicate_of": "IDL-030"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 979,
 "class": "duplicate",
 "reason": "Already covered by IDL-050 (source L976-L983, quote spans '...cannot keep a client_secret confidential and MUST use none... Businesses MUST NOT require client_secret_basic...'). Same mid-line quote-boundary artifact: the first quote fragment ends right at 'none' while the actual line 979 continues '; for' onto line 980, so the per-line matcher cannot mark line 979 even though the row's quote content covers it.",
 "duplicate_of": "IDL-050"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 981,
 "class": "duplicate",
 "reason": "Already covered by IDL-050 (source L976-L983) — the second half of the same quote ('Businesses MUST NOT require client_secret_basic as the only method...'). Same boundary-artifact class as L979.",
 "duplicate_of": "IDL-050"
}
```

**(b) Genuine second textual occurrence elsewhere in the doc (8 lines).** The
"Security Considerations" section (L966-1029) is a summary that restates several rules
already given their own row earlier in the document — the same pattern already established
in this exact file at 04-08 (e.g. old L581/L583/L586/L593/L602 were waived against
IDL-050/IDL-012/IDL-026/IDL-020 respectively; see the existing entries in
`register_completeness_waivers.json`). At 08-25 the summary section moved further down the
reorganized doc, and 4 of its restatements now have no companion boundary issue (clean
duplicates), plus 4 more genuine restatements found elsewhere (error-handling
error_description guidance, and the scopes_supported metadata rule).

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 401,
 "class": "duplicate",
 "reason": "'Self-listing forbidden. Businesses MUST NOT list their own authorization server in config.providers.' restates the General-Guidelines statement of the same rule at L255-256 (new row IDL-065), in the more detailed Identity-Providers section with added rationale.",
 "duplicate_of": "IDL-065"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 524,
 "class": "duplicate",
 "reason": "'UCP tightens the JWT authorization grant beyond what the base RFCs mandate: aud MUST be a single-valued URI plus a unique jti' is a preview sentence restating the formal JWT Authorization Grant constraints given their own row at L572-579 (new row IDL-074).",
 "duplicate_of": "IDL-074"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 632,
 "class": "duplicate",
 "reason": "'platforms MUST NOT treat error_description as machine-readable' (Chaining Errors section) restates the general error_description non-machine-readable rule already covered by IDL-051 ('error_description MUST NOT be used for control-flow decisions', L994-997).",
 "duplicate_of": "IDL-051"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 640,
 "class": "duplicate",
 "reason": "'platforms MUST NOT parse it for automated recovery' (Missing-claims sub-section, third occurrence of this rule in the file) restates the same error_description non-machine-readable rule as IDL-051 and the L632 waiver above.",
 "duplicate_of": "IDL-051"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 984,
 "class": "duplicate",
 "reason": "'Mix-Up Attack prevention. Platforms MUST validate the iss parameter in the authorization response' (Security Considerations summary) restates the primary Mix-Up-Attack rule already covered by IDL-012 (L170-176). Same class as the 2026-04-08 precedent (old L586 duplicate_of IDL-012).",
 "duplicate_of": "IDL-012"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 991,
 "class": "duplicate",
 "reason": "'Authentication challenges. Businesses MUST emit WWW-Authenticate: Bearer challenges...' (Security Considerations summary) restates IDL-026 (L241-244). Same class as the 2026-04-08 precedent (old L593 duplicate_of IDL-026).",
 "duplicate_of": "IDL-026"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 1000,
 "class": "duplicate",
 "reason": "'redirect_uri exactness. Businesses MUST enforce exact string matching for redirect_uri.' (Security Considerations summary) restates IDL-020 (L215-218). Same class as the 2026-04-08 precedent (old L602 duplicate_of IDL-020).",
 "duplicate_of": "IDL-020"
},
{
 "version": "2026-08-25",
 "file": "docs/specification/common/identity-linking/index.md",
 "line": 1011,
 "class": "duplicate",
 "reason": "'scopes_supported. Businesses MUST populate scopes_supported in RFC 8414 metadata.' (Security Considerations summary) restates IDL-017 (L205-208) verbatim.",
 "duplicate_of": "IDL-017"
}
```

---

## Ambiguous / flagged honestly

- **`guide.md` L700** ("If using token credentials, the schema **MUST** include an
  expiration field...") reads on first pass like it could bind a real schema constraint
  rather than pure spec-authoring guidance, but its subject is "the schema" being
  *authored* by a handler-spec writer (parallel structure to L697's "The specification MUST
  define which credential types are accepted" immediately above it, already
  non-normative by the established pattern) — judged non-normative on the same basis as
  L92/L851/L867/L879, and it turned out to already be `covered` by the completeness checker
  against an existing PAY-00x row's quote span, so no waiver entry was even needed. Flagged
  here in case a reviewer disagrees with that call.
- The **boundary-artifact class** (L175, L278, L979, L981) is very likely not unique to
  identity-linking — any existing row anywhere in the 2026-08-25 register whose `"..."`
  quote elision falls mid-physical-line could produce the same false "missed" line. This
  lane did not sweep the rest of the register for the pattern (out of scope for a
  three-area extraction pass); worth a dedicated pass before relying on the 08-25
  register-completeness percentage as precise.
- **`terms.md` PT-022/PT-023** (the "Platform responsibilities"/"Business responsibilities"
  checklists) restate several already-rowed obligations verbatim inside their own bullets.
  Filed as two combined rows rather than per-bullet duplicate waivers (matching the
  established `IDL-040`-style bundled-checklist convention already used elsewhere in this
  register for "on X, the business MUST return: <list>" patterns) — flagged here in case a
  reviewer prefers per-bullet duplicate waivers instead.

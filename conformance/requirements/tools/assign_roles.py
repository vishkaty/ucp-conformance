#!/usr/bin/env python3
"""
assign_roles.py — seed + heuristic for the register's `role` field (D2-08, PLAN-v3 §2.5,
D2 design §A4). Idempotent: writes `role` / `role_provenance` ONLY where a row has none;
every row it cannot resolve with confidence goes to `role_review_queue.json` next to the
register, and the `register` gate is red until that queue is empty (a reviewer sets the
row's `role` + `role_provenance: review:<batch>` and deletes the queue entry).

Algorithm (per row, first rule that fires wins; a CONFLICT still seeds the best guess so
the dry-run can be read, but is queued and the row keeps NO role until reviewed):
  1. id in agent_denominator_lock[v]  -> platform (`agent-lock`); id in AGENT_EXTRA -> both
  2. id in NOT_AGENT_BOUND            -> business (`not-agent-bound`)
  3. id has a client-bound exemption  -> platform (`client-bound-exemption`)
  4. subject scan (requirement, then quote): strip **/`; text BEFORE the first mandatory
     keyword; the LAST actor mention wins; "X and Y" with both parties right before the
     keyword -> both. Vocabularies: business / platform / handler / spec-author / host.
  5. passive rows: direction — the last request-/response-side marker before the keyword,
     else the first after it: request-side -> platform, response-side -> business.
  6. queue: lock AND subject=business (lock-but-business); subject or direction = platform
     AND not locked (platform-but-unlocked); handler / host / spec-author (lane per
     decision 27 — always a human call); neither subject nor direction resolves.

  python3 conformance/requirements/tools/assign_roles.py --dry-run [--version V]
  python3 conformance/requirements/tools/assign_roles.py --apply   [--version V]   # writes rows + queue
  python3 conformance/requirements/tools/assign_roles.py --selftest
"""
import argparse, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.dirname(HERE)                                  # conformance/requirements
CONF = os.path.dirname(REQ)                                  # conformance/
sys.path.insert(0, CONF)
from common.keywords import MANDATORY, KW_RE      # noqa: E402
from common.roles import ROLES                    # noqa: E402

AGENT_LOCK = os.path.join(CONF, "agent", "agent_denominator_lock.json")
OVERRIDES = os.path.join(CONF, "agent", "agent_lane_overrides.json")     # retired by D2-08 (seed source only)
EXEMPT = os.path.join(CONF, "coverage", "exemptions.json")

# --- vocabularies (A4 step 4): word-boundary, case-insensitive, longest alternative first
VOCAB = {
    "business": [r"business agents?", r"businesses", r"business", r"merchants?", r"servers?"],
    "platform": [r"mcp clients?", r"platforms?", r"agents?", r"clients?", r"consumers?",
                 r"callers?", r"requesters?", r"senders?"],
    "handler": [r"payment handlers?", r"handlers?", r"psps?", r"tokenization services?",
                r"tokenizers?", r"issuers?", r"credential providers?"],
    "spec-author": [r"spec(?:ification)? authors?", r"extension authors?", r"schema authors?",
                    r"specifications?"],
    "host": [r"host pages?", r"hosts?", r"iframes?", r"webviews?", r"embedded ui"],
}
_ACTOR_RE = re.compile(
    r"\b(?:" + "|".join(f"(?P<{k.replace('-', '_')}>{'|'.join(v)})" for k, v in VOCAB.items()) + r")\b",
    re.I)
_COORD_RE = re.compile(r"\b(?:and|or|/|,)\b|/|,", re.I)

# --- direction (A4 step 5) for passive rows: WHICH message the sentence is about, and
# WHO ACTS on it. Request-side nouns -> the message is a request (producer = platform,
# receiver = business); response-side nouns -> a response (producer = business, receiver
# = platform). The verb right after the keyword decides the party: a RECEIVER verb
# (reject / validate / verify / accept / ignore / deduplicate / process / honor ...)
# binds the message's receiver; a shape/sender verb (carry / include / send / submit /
# be present / match ...) binds its producer. "Duplicate identifiers in the request MUST
# be deduplicated" therefore binds the Business, "Requests MUST carry an Idempotency-Key"
# the Platform. No message noun at all -> None (queued).
REQUEST_NOUNS = r"requests?|submissions?|ucp_request|idempotency-key|ucp-agent|callers?|invocations?|token-request"
RESPONSE_NOUNS = r"responses?|response bod(?:y|ies)|webhooks?|notifications?"
_MSG_RE = re.compile(r"\b(?P<req>" + REQUEST_NOUNS + r")\b|\b(?P<resp>" + RESPONSE_NOUNS + r")\b", re.I)
RECEIVER_VERBS = r"reject(?:ed|s)?|validat(?:e|ed|es)|verif(?:y|ied|ies)|accept(?:ed|s)?|ignor(?:e|ed|es)|dedup(?:e|ed|licate|licated)|process(?:ed|es)?|treat(?:ed|s)?|honou?r(?:ed|s)?|enforc(?:e|ed|es)|resolv(?:e|ed|es)|appl(?:y|ied|ies)|fail(?:ed|s)?|result(?:s)?|respond(?:s|ed)?|return(?:s|ed)?|deriv(?:e|ed|es)|comput(?:e|ed|es)|check(?:ed|s)?|discard(?:ed|s)?|reply|answer(?:ed|s)?|handl(?:e|ed|es)|interpret(?:ed|s)?|consider(?:ed|s)?|skip(?:ped|s)?|locat(?:e|ed|es)|extract(?:ed|s)?|parse(?:d|s)?"
_VERB_RE = re.compile(r"^\s*(?:NOT\s+)?(?:be\s+|also\s+|still\s+|only\s+|then\s+)?(?P<verb>[a-z][a-z-]*)", re.I)
_RECEIVER_RE = re.compile(r"^(?:" + RECEIVER_VERBS + r")$", re.I)


def direction_of(text):
    """Passive rows -> 'platform' | 'business' | None (see the comment above)."""
    before, after = _split_at_keyword(_clean(text))
    if not after:
        return None
    msgs = [m.lastgroup for m in _MSG_RE.finditer(before)]
    msg = msgs[-1] if msgs else None
    if msg is None:
        # the message may be named after the keyword ("... MUST NOT appear in lookup responses")
        tail = [m.lastgroup for m in _MSG_RE.finditer(after[:160])]
        msg = tail[0] if tail else None
    if msg is None:
        return None
    vm = _VERB_RE.match(after)
    receiver_acts = bool(vm and _RECEIVER_RE.match(vm.group("verb") or ""))
    producer = "platform" if msg == "req" else "business"
    receiver = "business" if msg == "req" else "platform"
    return receiver if receiver_acts else producer


def _clean(text):
    return (text or "").replace("**", "").replace("`", "")


def _split_at_keyword(text):
    """(before, after) around the FIRST mandatory keyword; (text, '') when none."""
    m = KW_RE.search(text)
    if not m:
        return text, ""
    return text[:m.start()], text[m.end():]


def subject_of(text):
    """The role of the last actor mentioned before the first mandatory keyword, or None
    for a passive row. `both` when business and platform are coordinated right before
    the keyword ("the Platform and the Business MUST")."""
    before, _ = _split_at_keyword(_clean(text))
    hits = [(m.start(), m.end(), m.lastgroup.replace("_", "-")) for m in _ACTOR_RE.finditer(before)]
    if not hits:
        return None
    if len(hits) >= 2:
        (s1, e1, r1), (s2, e2, r2) = hits[-2], hits[-1]
        gap = before[e1:s2]
        if {r1, r2} == {"business", "platform"} and len(gap) <= 12 and _COORD_RE.search(gap):
            return "both"
    return hits[-1][2]


ADJUDICATIONS = os.path.join(REQ, "role_adjudications.json")


def load_adjudications(path=ADJUDICATIONS):
    """{(version, id): (role, batch)} from role_adjudications.json (reviewed decisions;
    applied with provenance `review:<batch>`). Missing file -> {}.

    An entry MAY carry its own `batch`, which overrides the file-level one: a decision
    superseded later must not stay attributed to the batch that got it wrong. The
    file's `corrections` block defines every such batch (who corrected it, when, on
    what authority, and the prior role) — see the 2026-09-22 fast-follow F1 batch."""
    if not os.path.exists(path):
        return {}
    d = json.load(open(path))
    batch = d.get("batch", "role-adjudication")
    return {(e["version"], e["id"]): (e["role"], e.get("batch") or batch)
            for e in d.get("adjudications", [])}


def assign(row, lock_ids, agent_extra, nab, cb, merchant_check_ids=frozenset(), adjudicated=None):
    """-> (role, provenance, queue_reason|None). A queued row's role is the heuristic's
    best guess for the dry-run; --apply leaves the row unroled until reviewed.
    `adjudicated` = (role, batch) from role_adjudications.json for this (version, id),
    which wins outright (provenance review:<batch>). `merchant_check_ids` = ids the
    merchant axis CHECKs at this version: a locked id that is ALSO merchant-graded binds
    both parties (CAP-004/CAP-006/CAT-043/DISC-004 at 2026-08-25) — `both`, never a
    platform-only row with a merchant CHECK on it (the one-lane rule)."""
    rid = row.get("id")
    if adjudicated:
        return adjudicated[0], f"review:{adjudicated[1]}", None
    text = row.get("requirement", "") + ". " + row.get("quote", "")
    subj = subject_of(text)
    if rid in lock_ids or rid in agent_extra:
        role = "both" if (rid in merchant_check_ids or subj == "both") else "platform"
        if subj == "business" and role != "both":
            return role, "agent-lock", f"lock-but-business: locked in the agent denominator, subject scan reads business"
        if subj in ("handler", "host", "spec-author"):
            return role, "agent-lock", f"lock-but-{subj}: locked in the agent denominator, subject scan reads {subj} (decision 27 lane)"
        return role, "agent-lock", None
    if rid in nab:
        return "business", "not-agent-bound", None
    if rid in cb:
        if subj == "business":
            return "platform", "client-bound-exemption", "client-bound-but-business: exempt as client-bound, subject scan reads business"
        return "platform", "client-bound-exemption", None
    if subj in ("handler", "host", "spec-author"):
        return subj, "subject", f"{subj} row: lane per decision 27 (host -> platform lane, handler -> merchant lane, spec-author -> speclint register-selfcheck)"
    if subj in ("business", "both"):
        return subj, "subject", None
    if subj == "platform":
        return subj, "subject", f"platform-but-unlocked: subject scan reads platform, id not in the agent denominator lock"
    d = direction_of(text)
    if d in ("business", "platform"):
        return d, "direction", None
    return None, None, "unresolved: no actor and no message direction in the quote"


# --- seed sources -------------------------------------------------------------------
def load_seed_sources():
    lock = json.load(open(AGENT_LOCK))["versions"] if os.path.exists(AGENT_LOCK) else {}
    lock = {v: set(ids) for v, ids in lock.items()}
    extra, nab = set(), set()
    if os.path.exists(OVERRIDES):
        d = json.load(open(OVERRIDES))
        extra = {e["id"] for e in d.get("agent_extra", [])}
        nab = {e["id"] for e in d.get("not_agent_bound", [])}
    cb = set()
    if os.path.exists(EXEMPT):
        for k, v in json.load(open(EXEMPT)).items():
            for e in (v if isinstance(v, list) else [v]):
                if isinstance(e, dict) and e.get("class") == "client-bound":
                    cb.add(k)
    return lock, extra, nab, cb


def register_files(ver):
    return [f for f in sorted(glob.glob(os.path.join(REQ, ver, "*.json")))
            if not os.path.basename(f).startswith("_")]


def dump_like(path, doc):
    """Write in the byte style the file already uses (same idiom as add_expiry_clocks)."""
    raw = open(path).read()
    orig = json.loads(raw)
    for ind in (1, 2, 4):
        for ea in (True, False):
            if json.dumps(orig, indent=ind, ensure_ascii=ea) + "\n" == raw:
                open(path, "w").write(json.dumps(doc, indent=ind, ensure_ascii=ea) + "\n")
                return
    open(path, "w").write(json.dumps(doc, indent=2) + "\n")


def merchant_check_ids(ver):
    """Ids the merchant axis CHECKs at `ver` (matrix.coverage_map(), imported from
    conformance/coverage — the same walk the export uses)."""
    sys.path.insert(0, os.path.join(CONF, "coverage"))
    import importlib
    try:
        mx = importlib.import_module("matrix")
        return set(mx.coverage_map().get(ver, {}).keys())
    except Exception as e:                       # noqa: BLE001 — surfaced, never silent
        print(f"  WARN  merchant axis unavailable ({e!r}); locked+merchant-CHECK -> both cannot be seeded")
        return set()


def run_version(ver, apply=False, mcheck=None, refresh=False):
    """Seed one version. Returns (stats, queue)."""
    lock, extra, nab, cb = load_seed_sources()
    adj = load_adjudications()
    lock_v = lock.get(ver, set())
    mcheck = merchant_check_ids(ver) if mcheck is None else mcheck
    stats = {"rows": 0, "already": 0, "seeded": 0, "queued": 0, "by_role": {}, "by_prov": {}}
    queue = []
    for f in register_files(ver):
        doc = json.load(open(f))
        changed = False
        for r in doc.get("rows", []):
            if ver not in (r.get("versions") or [ver]):
                continue
            stats["rows"] += 1
            if r.get("role"):
                stats["already"] += 1
                # --refresh-adjudicated: a reviewed row follows a CHANGED adjudication —
                # a changed ROLE or a changed BATCH (an entry that names its own batch
                # was corrected later, and the row must carry the correcting batch, not
                # the one that got it wrong).
                a = adj.get((ver, r["id"]))
                want_prov = f"review:{a[1]}" if a else None
                if refresh and a and (r["role"] != a[0]                  # adjudication wins outright
                                      or r.get("role_provenance") != want_prov):
                    stats["refreshed"] = stats.get("refreshed", 0) + 1
                    print(f"  refresh {ver} {r['id']}: {r['role']} -> {a[0]} "
                          f"({r.get('role_provenance')} -> {want_prov})")
                    if apply:
                        r["role"], r["role_provenance"] = a[0], want_prov
                        changed = True
                continue
            role, prov, why = assign(r, lock_v, extra, nab, cb, mcheck, adj.get((ver, r["id"])))
            mandatory = r.get("keyword") in MANDATORY
            if why is None and prov == "direction" and role == "platform" and r["id"] not in lock_v:
                # entering the agent denominator on a heuristic alone is a change the
                # reviewed lock never saw -> review; staying merchant-lane is the status quo
                why = "platform-by-direction-unlocked: passive row resolved to platform by message direction, id not in the agent denominator lock"
            if why and mandatory:               # SHOULD/MAY rows: role optional, never queued
                stats["queued"] += 1
                queue.append({"id": r["id"], "version": ver, "file": os.path.basename(f),
                              "heuristic": role, "provenance": prov, "locked": r["id"] in lock_v,
                              "reason_needed": why, "keyword": r.get("keyword"),
                              "requirement": r.get("requirement", "")})
                continue
            if role is None:            # non-mandatory row nobody could resolve: leave unroled
                continue
            stats["seeded"] += 1
            stats["by_role"][role] = stats["by_role"].get(role, 0) + 1
            stats["by_prov"][prov] = stats["by_prov"].get(prov, 0) + 1
            if apply:
                r["role"], r["role_provenance"] = role, prov
                changed = True
        if apply and changed:
            dump_like(f, doc)
    if apply:
        qf = os.path.join(REQ, ver, "role_review_queue.json")
        existing = json.load(open(qf)).get("queue", []) if os.path.exists(qf) else []
        known = {(q["id"], q["version"]) for q in existing}
        merged = existing + [q for q in queue if (q["id"], q["version"]) not in known]
        if merged:
            json.dump({"_about": "role review queue (D2-08 / A4): rows assign_roles.py could not "
                                 "resolve with confidence. The `register` gate is red while this "
                                 "list is non-empty. To resolve an entry: set the row's `role` and "
                                 "`role_provenance: review:<batch>` (batch = a review_signoffs.json "
                                 "entry carrying the >=10% human sample), then delete the entry.",
                       "queue": merged}, open(qf, "w"), indent=1)
            open(qf, "a").write("\n")
        elif os.path.exists(qf):
            os.remove(qf)
    return stats, queue


def selftest():
    fx = [
        ({"id": "F-001", "keyword": "MUST", "quote": "Businesses MUST return the full resource."},
         ("business", "subject", None)),
        ({"id": "F-002", "keyword": "MUST", "quote": "The Platform MUST send the entire object."},
         ("platform", "agent-lock", None)),
        ({"id": "F-003", "keyword": "MUST", "quote": "Both the Platform and the Business MUST use HTTPS."},
         ("both", "subject", None)),
        ({"id": "F-004", "keyword": "MUST", "quote": "Requests MUST carry an Idempotency-Key header."},
         ("platform", "direction", None)),
        ({"id": "F-005", "keyword": "MUST", "quote": "The response MUST include a `status` field."},
         ("business", "direction", None)),
    ]
    bad = 0
    for row, want in fx:
        got = assign(row, lock_ids={"F-002"}, agent_extra=set(), nab=set(), cb=set())
        ok = got[:2] == want[:2] and (got[2] is None) == (want[2] is None)
        print(f"  {'✓' if ok else '✗'} {row['id']}: {got}" + ("" if ok else f"  <-- expected {want}"))
        bad += 0 if ok else 1
    q6 = assign({"id": "F-006", "keyword": "MUST", "quote": "The payment handler MUST tokenize the credential."},
                set(), set(), set(), set())
    q7 = assign({"id": "F-007", "keyword": "MUST", "quote": "Platforms MUST cache the profile."},
                set(), set(), set(), set())
    ok = q6[0] == "handler" and q6[2] and q7[0] == "platform" and q7[2]
    print(f"  {'✓' if ok else '✗'} queued: F-006 {q6} · F-007 {q7}")
    bad += 0 if ok else 1
    # locked AND merchant-CHECK -> both (one-lane rule); adjudication wins outright
    b = assign({"id": "F-008", "keyword": "MUST", "quote": "The Platform MUST derive the prefix."},
               {"F-008"}, set(), set(), set(), merchant_check_ids={"F-008"})
    a = assign({"id": "F-009", "keyword": "MUST", "quote": "Businesses MUST advertise consent options."},
               {"F-009"}, set(), set(), set(), adjudicated=("business", "roles-x"))
    ok = b == ("both", "agent-lock", None) and a == ("business", "review:roles-x", None)
    print(f"  {'✓' if ok else '✗'} locked+merchant-CHECK -> both {b}; adjudicated -> review:<batch> {a}")
    bad += 0 if ok else 1
    print(f"\nassign_roles selftest: {'PASS' if not bad else 'FAIL'}")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--version")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show-queue", action="store_true")
    ap.add_argument("--refresh-adjudicated", action="store_true",
                    help="re-read role_adjudications.json for already-roled rows and follow a changed "
                         "or new decision (an adjudication wins outright; with --apply: write it)")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    versions = [a.version] if a.version else sorted(
        d for d in os.listdir(REQ) if os.path.isdir(os.path.join(REQ, d)) and d[:2] == "20")
    for ver in versions:
        stats, queue = run_version(ver, apply=a.apply, refresh=a.refresh_adjudicated)
        print(f"{ver}: rows {stats['rows']} · already roled {stats['already']} · seeded {stats['seeded']} "
              f"· queued {stats['queued']} · by_role {stats['by_role']} · by_prov {stats['by_prov']}")
        if a.show_queue:
            for q in queue:
                print(f"   Q {q['id']:9} heur={q['heuristic']!s:9} locked={q['locked']!s:5} {q['reason_needed']}")
    if not (a.dry_run or a.apply):
        print("(dry run by default; pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

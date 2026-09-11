#!/usr/bin/env python3
"""
site_gates.py — the site-governance lane: the WEBSITE's copy/claims/security under
the same red/green harness as the engine (spec: docs/superpowers/specs/
2026-07-09-website-ia-redesign-design.md, "Gate mechanics" is normative).

Modes (run_suite gates):
  tdd        SITE-R register traceability — every requirement names >=1 existing
             test, every site test tag cites a SITE-R row, register is ADD-ONLY vs
             git HEAD (removals need a reasoned site_requirements_retired.json row).
             GREEN only at "requirements N, tested N, coverage 100%".
  claims     every factual claim on every page is [LIVE] inside a data-live element
             whose fallback equals the live JSON value, or [REG] registered in
             public/site_claims.json (unexpired review_by) — else RED page:line.
             `--explain` prints every candidate + classification.
  voice      conformance/web/voice_rules.json: banned patterns (with negation
             contexts), third-party names outside data-attribution, required
             per-page disclaimer. (you/your above-fold CTA lives in site_smoke.)
  security   public/_headers (/* rule: nosniff, X-Frame-Options, Referrer-Policy,
             CSP default-src 'self'), un-esc()'d HTML sinks in pages+functions,
             external script/style/font origins, secret-looking strings in public/.
  redirects  public/_redirects has exactly /tool→/check + /guide→/docs 301 rows;
             no page links to /tool or /guide.
  consistency  every page links /site.css and NEVER redefines a shared component
             (.site-nav/.site-footer/.btn-primary/.btn-secondary/.btn-ghost) in its
             own inline <style> — the design system is the single source of the look,
             so the nav/footer/buttons can never silently re-theme per page again.
  freshness  product manifest (coverage JSONs + agent registries) vs the manifest
             block reviewed into public/site_claims.json — product drift with a
             stale review date is RED.
  docclaims  NON-page copy — README.md, conformance/ci/README.md, packaging/README.md,
             docs/*.md (hand-authored) and functions/**/*.js — held to the page bar:
             every advertised count equals the live product value (or is a registered
             `dated` row in conformance/web/doc_claims.json), every registered doc claim
             still holds (must_match / must_not_match / bind), every gate named in
             ci/README's table exists in run_suite.py, and Action snippets are pinned
             to the release tag (D5-21). SPCK_DOCROOT scopes the scan (tests).

Pages audited = every public/*.html PRESENT (tool.html/guide.html drop out of the
audit automatically once retired). Exit 0 pass · 1 fail · 2 honest skip.
Stdlib only.
"""
import datetime, glob, html.parser, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
# SPCK_PUBLIC lets oversight/tests point the gates at a scratch copy of the site
PUB = pathlib.Path(os.environ.get("SPCK_PUBLIC", ROOT / "public"))
WEB = ROOT / "conformance" / "web"
MODES = ("tdd", "claims", "voice", "security", "redirects", "consistency", "freshness", "checkdocs",
         "docclaims")
TODAY = datetime.date.today().isoformat()

# ── shared text extraction ────────────────────────────────────────────────────
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
MUTED = ("style", "script", "template")

class _Text(html.parser.HTMLParser):
    """Visible-text extractor with line numbers. Emits chunks
    (line, text, live, attribution): live = nearest ancestor's data-live value,
    attribution = True iff any ancestor carries data-attribution.
    <style>/<script>/<template> contents are dropped."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.chunks = [], []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        a = dict(attrs)
        self.stack.append((tag, a.get("data-live"), "data-attribution" in a))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):   # tolerate sloppy nesting
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(t in MUTED for t, _, _ in self.stack):
            return
        s = re.sub(r"\s+", " ", data).strip()
        if s:
            live = next((l for _, l, _ in reversed(self.stack) if l), None)
            attrib = any(a for _, _, a in self.stack)
            self.chunks.append((self.getpos()[0], s, live, attrib))

# Generated trees/pages are byte-compared by their own gates (checkdocs; the known-issues
# generator in D5-12) and are NOT hand-authored copy, so the claims/voice/security audits
# skip them. Everything else under public/** — including sub-directories such as
# state-of-ucp/ (the launch page) — is audited (PLAN-v3 §2.18 audit scope, D5-10).
GENERATED_DIRS = ("checks",)
GENERATED_PAGES = ("known-issues.html",)

def pages():
    """Every hand-authored public page currently present, recursively — retired pages
    drop out on deletion; generated pages/dirs are excluded (see GENERATED_*)."""
    out = []
    for f in sorted(PUB.rglob("*.html")):
        rel = f.relative_to(PUB)
        if rel.parts[0] in GENERATED_DIRS or rel.name in GENERATED_PAGES:
            continue
        out.append(str(f))
    return out

def page_key(path):
    """The page's identity in the claims register: its path relative to public/
    (top-level pages keep their bare basename, e.g. index.html)."""
    return str(pathlib.Path(path).resolve().relative_to(PUB.resolve())) \
        if str(path).startswith(str(PUB)) else os.path.basename(path)

def page_chunks(path):
    p = _Text()
    p.feed(open(path, encoding="utf-8").read())
    return p.chunks

def page_lines(path):
    """Chunks grouped per source line → [(line, joined_text, [chunks])]. Joining a
    line reunites split markup like <b>42</b> <span>agent checks</span> so
    number↔noun adjacency is judged on what the READER sees."""
    by = {}
    for c in page_chunks(path):
        by.setdefault(c[0], []).append(c)
    return [(n, " ".join(c[1] for c in cs), cs) for n, cs in sorted(by.items())]

def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]

def _merchant_count():
    """(count, duplicate ids) of the runtime merchant check set — conformance/ci/checkset_count.py
    (D5-16): one helper for every copy/manifest gate, never a source regex."""
    sys.path.insert(0, str(ROOT / "conformance" / "ci"))
    from checkset_count import merchant_check_count
    return merchant_check_count()

# ── tiny jsonpath (dot / ['key'] / [0]) for data-live="file.json:$.a['b'][0]" ──
def resolve_live(spec):
    """Returns (value, error). spec = '<file-under-public>:<path>'."""
    if ":" not in spec:
        return None, f"malformed data-live {spec!r} (want file:jsonpath)"
    fname, path = spec.split(":", 1)
    f = PUB / fname
    if not f.exists():
        return None, f"data-live file {fname} not found under public/"
    try:
        cur = json.load(open(f))
    except Exception as e:
        return None, f"data-live file {fname}: {e}"
    toks = re.findall(r"\.([A-Za-z_][\w-]*)|\['([^']*)'\]|\[\"([^\"]*)\"\]|\[(\d+)\]",
                      path.lstrip("$"))
    if not toks and path.lstrip("$").strip():
        return None, f"unparsable jsonpath {path!r}"
    try:
        for name, q1, q2, idx in toks:
            key = name or q1 or q2
            cur = cur[key] if key else cur[int(idx)]
    except (KeyError, IndexError, TypeError):
        return None, f"jsonpath {path} does not resolve in {fname}"
    return cur, None

# ═══ tdd ═════════════════════════════════════════════════════════════════════
def _git_show(rel):
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=str(ROOT),
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None   # absent at HEAD = None

def tdd():
    reg = json.load(open(WEB / "site_requirements.json"))
    rows = reg["requirements"]
    ids = {r["id"] for r in rows}
    fails = []

    # declared test ids on disk
    smoke = ROOT / "tests" / "web" / "browser" / "site_smoke.mjs"
    tagged = set(re.findall(r"//\s*(SITE-R-\d+)", smoke.read_text())) if smoke.exists() else set()
    have_responsive = (ROOT / "tests" / "web" / "browser" / "responsive_smoke.mjs").exists()
    # web_unit:<tag> rows cite // SITE-R-xxx tags in the functions unit suite (which the
    # web-unit gate runs) — same tag mechanism as site_smoke, different test layer
    unit_tagged = set()
    for f in glob.glob(str(ROOT / "tests" / "web" / "unit" / "*.mjs")):
        unit_tagged |= set(re.findall(r"//\s*(SITE-R-\d+)", open(f, encoding="utf-8").read()))

    tested = 0
    for r in rows:
        ok = False
        for t in r["tests"]:
            if t.startswith("gate:"):
                ok |= t.split(":", 1)[1] in MODES     # internal mode exists
            elif t.startswith("site_smoke:"):
                ok |= r["id"] in tagged               # block tagged // SITE-R-xxx
            elif t.startswith("web_unit:"):
                ok |= r["id"] in unit_tagged          # unit test tagged // SITE-R-xxx
            elif t == "responsive_smoke":
                ok |= have_responsive
            else:
                fails.append(f"{r['id']}: unknown test kind {t!r}")
        if ok:
            tested += 1
        else:
            fails.append(f"{r['id']} UNTESTED — no existing test among {r['tests']}")

    for t in sorted((tagged | unit_tagged) - ids):    # orphan tests: register is SoT
        fails.append(f"orphan tag {t} in a site/unit test cites no register row")

    # ADD-ONLY vs git HEAD (register + retired file). Absent at HEAD = bootstrap ok.
    retired = json.load(open(WEB / "site_requirements_retired.json"))["retired"]
    retired_ok = {e.get("id") for e in retired if e.get("reason")}
    head = _git_show("conformance/web/site_requirements.json")
    if head is not None:
        for old in json.loads(head)["requirements"]:
            cur = next((r for r in rows if r["id"] == old["id"]), None)
            if cur is None and old["id"] not in retired_ok:
                fails.append(f"{old['id']} removed without a reasoned retirement entry")
            elif cur and cur["requirement"] != old["requirement"] and old["id"] not in retired_ok:
                fails.append(f"{old['id']} requirement text changed — weakening needs a "
                             f"retirement entry (add-only register)")
    head_ret = _git_show("conformance/web/site_requirements_retired.json")
    if head_ret is not None:
        old_ids = [e.get("id") for e in json.loads(head_ret)["retired"]]
        now_ids = [e.get("id") for e in retired]
        if any(i not in now_ids for i in old_ids):
            fails.append("site_requirements_retired.json lost entries — it is add-only")

    pct = round(100 * tested / len(rows)) if rows else 0
    print(f"site-tdd: requirements {len(rows)}, tested {tested}, coverage {pct}%")
    for f in fails:
        print(f"  x {f}")
    if not fails and pct == 100:
        print("site-tdd: PASS — full traceability, register intact")
        return 0
    return 1

# ═══ claims ══════════════════════════════════════════════════════════════════
CLAIM_NOUN = re.compile(r"(?i)\b(?:checks?|defects?|coverage|must|versions?|stores?"
                        r"|agents?|failures?)\b|%")
PROOF = re.compile(r"(?i)\b(?:proven|validated|every|all|zero|only|first)\b"
                   r"|\b(?:no|0)\s+false\b|100%")
NUM = re.compile(r"\d+(?:\.\d+)?")
# non-claims: sizes, durations, spec-version dates, HTTP codes, bare years (footers)
EXCLUDE = re.compile(r"(?i)\d+(?:\.\d+)?\s*px\b|\b\d+\s*seconds?\b|\b20\d\d-\d\d(?:-\d\d)?\b"
                     r"|\bHTTP\s*\d{3}\b|(?:©|&copy;)\s*20\d\d\b|\b20\d\d\b(?!\d)")

def _load_claims():
    f = PUB / "site_claims.json"
    if not f.exists():
        return None
    data = json.load(open(f))
    return data.get("claims", data if isinstance(data, list) else [])

def _reg_match(entries, sentence, page):
    """Match a candidate sentence against registered claims.

    Oversight-hardened (2026-07-09): short/numeric registered texts must match as
    WHOLE TOKENS with word boundaries — a bare registered "0" or "42" must never
    legalize an arbitrary sentence that merely contains that digit (the proven
    'Trusted by 50,000 stores' hole). Longer texts still substring-match, but
    one-directionally sensible: the registered text within the sentence, or the
    sentence being a fragment of the registered full sentence.
    """
    for e in entries or []:
        if e.get("class", "REG") != "REG":
            continue
        if e.get("page") not in ("*", page):
            continue
        t = e.get("text", "")
        if not t:
            continue
        short_or_numeric = len(t) < 6 or re.fullmatch(r"[\d.,%\s]+", t)
        if short_or_numeric:
            # whole-token: the registered text with boundaries AND the sentence
            # must contain no other unregistered numeric token
            if not re.search(r"(?<![\w.])" + re.escape(t) + r"(?![\w.])", sentence):
                continue
            others = [n for n in NUM.findall(sentence) if n not in t]
            if others:
                continue
        elif not (t in sentence or sentence in t):
            continue
        if e.get("review_by", "") >= TODAY:
            return e, None
        return None, f"registered claim {e.get('id')} review_by {e.get('review_by')} expired"
    return None, None

# D1-08: an evidence string that cites a run_suite gate must cite one that EXISTS.
# Only strings that mention run_suite are parsed (informal "the claims gate" prose
# is not a table reference); a cited name is a quoted token, `run_suite <name> gate`,
# or a `(a/b/c gates)` list. Filenames (with a dot) are never gate names.
_EV_QUOTED = re.compile(r"'([a-z0-9][a-z0-9-]*)'")
_EV_BARE = re.compile(r"run_suite\s+([a-z0-9][a-z0-9-]*)\s+gate")
_EV_LIST = re.compile(r"\(([a-z0-9-]+(?:/[a-z0-9-]+)+)\s+gates?\)")


def _evidence_gate_names(evidence):
    if "run_suite" not in (evidence or ""):
        return []
    names = set(_EV_QUOTED.findall(evidence)) | set(_EV_BARE.findall(evidence))
    for lst in _EV_LIST.findall(evidence):
        names.update(lst.split("/"))
    return sorted(n for n in names if "." not in n)


def _run_suite_gate_names():
    sys.path.insert(0, str(ROOT / "conformance" / "ci"))
    import run_suite
    return {g[0] for g in run_suite.gates("http://localhost:0")}


def _retired_keys():
    f = PUB / "site_claims.json"
    if not f.exists():
        return set()
    return {(e.get("id"), e.get("page")) for e in (json.load(open(f)).get("retired_claims") or [])}


def _unit_test_pinned_ids():
    """Claim ids named in tests/web/unit/*.mjs — a claim rendered by page script (never
    visible in the HTML) is anchored by the unit test that renders and asserts it."""
    ids = set()
    for f in glob.glob(str(ROOT / "tests" / "web" / "unit" / "*.mjs")):
        ids |= set(re.findall(r"\b((?:CLAIM|SOU|LAU|KI)-[A-Z0-9-]+)\b", open(f, encoding="utf-8").read()))
    return ids


def orphans():
    """`claims --orphans` (D5-05 hygiene): every REG claim must still be FOUND — its page
    present and its text on that page (an audit-style _reg_match against a rendered
    sentence, or the whitespace-normalised text inside the page's visible copy), or the
    claim is rendered by script and pinned by a web unit test naming its id. Anything
    else is an orphan: the copy moved (fix the row) or the claim retired (move it to
    `retired_claims`, keyed by id+page, with a reason). `page: "*"` rows may match any page."""
    entries = _load_claims() or []
    retired = _retired_keys()
    pinned = _unit_test_pinned_ids()
    sents, joined = {}, {}
    # generated pages are not audited for claims (their numbers are byte-compared from their
    # source) but a claim REGISTERED on one (KI-###, D5-12) must still be found on it
    generated = [str(PUB / g) for g in GENERATED_PAGES if (PUB / g).exists()]
    for path in pages() + generated:
        key = page_key(path)
        lines = page_lines(path)
        sents[key] = [s for _, text, _ in lines for s in sentences(text)]
        joined[key] = re.sub(r"\s+", " ", " ".join(t for _, t, _ in lines))
    fails, seen = [], {}
    for e in entries:
        if e.get("class", "REG") != "REG":
            continue
        pg, cid = e.get("page"), e.get("id", "?")
        if (cid, pg) in retired:
            continue
        if cid in seen and seen[cid] != pg:
            fails.append(f"{cid}: duplicate id across pages ({seen[cid]} and {pg}) — one id, one claim")
        seen[cid] = pg
        text = e.get("text")
        if not text:
            fails.append(f"{cid}: malformed row (no text) — retire or fix"); continue
        if cid in pinned:
            continue                                   # script-rendered, unit-test anchored
        targets = list(sents) if pg == "*" else [pg]
        if pg != "*" and pg not in sents:
            fails.append(f"{cid}: page {pg!r} is not a hand-authored page under public/ — retire the row"); continue
        norm = re.sub(r"\s+", " ", text)
        found = any(norm in joined.get(t, "") for t in targets) or any(
            _reg_match([{**e, "review_by": "9999-12-31"}], sent, t)[0] is not None
            for t in targets for sent in sents.get(t, []))
        if not found:
            fails.append(f"{cid}: text not found on {pg} — {text[:70]!r}")
    for f in fails:
        print(f"  x {f}")
    print(f"site-claims --orphans: {len(fails)} orphan(s)")
    return 0 if not fails else 1


def claims(explain=False):
    entries = _load_claims()
    fails, out = [], []

    if entries:
        table = _run_suite_gate_names()
        for e in entries:
            for name in _evidence_gate_names(e.get("evidence", "")):
                if name not in table:
                    fails.append(f"site_claims.json {e.get('id')}: evidence cites run_suite gate "
                                 f"'{name}' which is not in run_suite's gate table")

    for path in pages():
        page = page_key(path)

        # R-007 sweep — every data-live binding must resolve; a numeric fallback
        # must EQUAL the live value (raw scan catches empty/JS-filled elements too)
        raw = open(path, encoding="utf-8").read()
        for i, line in enumerate(raw.splitlines(), 1):
            for m in re.finditer(r'data-live\s*=\s*["\']([^"\']+)["\']', line):
                val, err = resolve_live(m.group(1))
                if err:
                    fails.append(f"{page}:{i}: {err}")
        by_live = {}
        for ln, text, live, _ in page_chunks(path):
            if live:
                by_live.setdefault(live, [ln, ""])
                by_live[live][1] += " " + text
        for spec, (ln, text) in sorted(by_live.items()):
            val, err = resolve_live(spec)
            if err:
                continue                                   # already reported above
            n = NUM.search(text)
            if n and isinstance(val, (int, float)) and float(n.group()) != float(val):
                fails.append(f"{page}:{ln}: data-live fallback '{n.group()}' != live "
                             f"value {val} ({spec})")

        # R-008 — claim candidates in visible text
        seen = set()
        for ln, text, chs in page_lines(path):
            for sent in sentences(text):
                excl = [m.span() for m in EXCLUDE.finditer(sent)]
                covered = lambda m: any(a <= m.start() and m.end() <= b for a, b in excl)
                nums = [m for m in NUM.finditer(sent) if not covered(m)]
                is_num_claim = bool(nums) and bool(CLAIM_NOUN.search(sent))
                is_proof = bool(PROOF.search(sent))
                if not (is_num_claim or is_proof):
                    if explain and nums:
                        out.append(f"    - {page}:{ln} SKIP (no claim noun/proof): {sent[:90]}")
                    continue
                key = (page, ln, sent)
                if key in seen:
                    continue
                seen.add(key)

                # [LIVE] — the chunk carrying the claim sits under data-live
                live_spec = None
                for _, ctext, clive, _ in chs:
                    hit = any(m.group() in ctext for m in nums) if nums else (sent[:40] in ctext or ctext in sent)
                    if hit and clive:
                        live_spec = clive
                        break
                if live_spec:
                    val, err = resolve_live(live_spec)
                    if err:
                        fails.append(f"{page}:{ln}: {err} — for claim: {sent[:90]}")
                    elif nums and isinstance(val, (int, float)) and \
                            not any(float(m.group()) == float(val) for m in nums):
                        fails.append(f"{page}:{ln}: data-live fallback disagrees with live "
                                     f"value {val}: {sent[:90]}")
                    elif explain:
                        out.append(f"    - {page}:{ln} LIVE({live_spec}): {sent[:90]}")
                    continue

                # [REG] — registered with evidence + unexpired review-by
                e, err = _reg_match(entries, sent, page)
                if e:
                    if explain:
                        out.append(f"    - {page}:{ln} REG({e.get('id')}): {sent[:90]}")
                    continue
                fails.append(f"{page}:{ln}: {err or 'unregistered claim'} — {sent[:110]}")
                if explain:
                    out.append(f"    - {page}:{ln} UNREGISTERED: {sent[:90]}")

    if explain:
        print("site-claims --explain:")
        print("\n".join(out) or "    (no candidates)")
    if entries is None:
        print("  ! public/site_claims.json missing — no [REG] register to match against")
    for f in fails:
        print(f"  x {f}")
    print(f"site-claims: {'PASS' if not fails and entries is not None else 'FAIL'} "
          f"({len(fails)} unverified claim(s))")
    return 0 if not fails and entries is not None else 1

# ═══ voice ═══════════════════════════════════════════════════════════════════
def voice():
    rules = json.load(open(WEB / "voice_rules.json"))
    fails = []
    for path in pages():
        page = page_key(path)
        lines = page_lines(path)
        full = " ".join(t for _, t, _ in lines)

        for r in rules["banned"]:
            cre, unless = re.compile(r["pattern"]), r.get("unless_sentence")
            for ln, text, _ in lines:
                for sent in sentences(text):
                    m = cre.search(sent)
                    if m and not (unless and re.search(unless, sent)):
                        fails.append(f"{r['id']} {page}:{ln}: banned {m.group()!r} — {sent[:80]}")

        tp = rules["third_party_names"]
        name_re = re.compile(r"\b(?:" + "|".join(map(re.escape, tp["names"])) + r")\b")
        for ln, text, attrib in ((c[0], c[1], c[3]) for _, _, cs in lines for c in cs):
            m = name_re.search(text)
            if m and not attrib:
                fails.append(f"{tp['id']} {page}:{ln}: third-party name {m.group()!r} outside "
                             f"a data-attribution block — {text[:80]}")

        for r in rules["required_per_page"]:
            if not re.search(r["pattern"], full):
                fails.append(f"{r['id']} {page}: required pattern missing ({r['why'][:60]}…)")

    for f in fails:
        print(f"  x {f}")
    print(f"voice-lint: {'PASS' if not fails else 'FAIL'} ({len(fails)} violation(s))")
    return 0 if not fails else 1

# ═══ security ════════════════════════════════════════════════════════════════
SINK = re.compile(r"\binnerHTML\s*=(?!=)|\bdocument\.write\(|\binsertAdjacentHTML\(")
_STR = r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`[^`$]*`"
# RHS that is ONLY string constants (concatenated) up to the statement end = safe
CONST_RHS = re.compile(r"^\s*(?:%s)(?:\s*\+\s*(?:%s))*\s*(?:[;,)].*)?$" % (_STR, _STR),
                       re.S)
SECRET = re.compile(r"(?i)AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|sk_live_[0-9a-zA-Z]{8,}"
                    r"|(?:api[_-]?key|secret)\s*[:=]\s*['\"][A-Za-z0-9+/_-]{16,}")
SELF_ORIGINS = ("https://spck.dev", "http://spck.dev")
# Reviewed external <script> exception (scoped to scripts only, NOT styles/links/urls):
# the Cloudflare Web Analytics beacon. First-party infra (same host as the site), no
# cookies/PII, and it POSTs to same-origin /cdn-cgi/rum. Its host is also allow-listed in
# the CSP script-src. Any OTHER external script origin still fails the security gate.
TRUSTED_SCRIPT_ORIGINS = SELF_ORIGINS + ("https://static.cloudflareinsights.com",)

def _headers_ok(fails):
    f = PUB / "_headers"
    if not f.exists():
        fails.append("public/_headers missing")
        return
    hdrs, cur = {}, None
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            cur = line.strip()
        elif cur == "/*" and ":" in line:
            k, v = line.strip().split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    if "nosniff" not in hdrs.get("x-content-type-options", ""):
        fails.append("_headers /*: X-Content-Type-Options: nosniff missing")
    if "x-frame-options" not in hdrs:
        fails.append("_headers /*: X-Frame-Options missing")
    if "referrer-policy" not in hdrs:
        fails.append("_headers /*: Referrer-Policy missing")
    if "default-src 'self'" not in hdrs.get("content-security-policy", ""):
        fails.append("_headers /*: Content-Security-Policy with default-src 'self' missing")

def _sink_findings(path, rel):
    """Line-of-sight sink rule: for each innerHTML=/document.write(/
    insertAdjacentHTML( the RHS/argument segment (up to the next sink, capped)
    must contain esc( or be a pure string constant; upstream-sanitized variables
    need a reviewed `/* safe: … */` annotation on the sink's line."""
    out = []
    content = open(path, encoding="utf-8").read()
    lines = content.splitlines()
    sinks = list(SINK.finditer(content))
    for k, m in enumerate(sinks):
        ln = content.count("\n", 0, m.start()) + 1
        if re.search(r"/\*\s*safe:", lines[ln - 1]):    # reviewed annotation
            continue
        # segment = the sink's RHS: to end-of-line when the statement closes there,
        # else onward to the next sink (multi-line concatenations), capped.
        eol = content.find("\n", m.end())
        eol = len(content) if eol < 0 else eol
        if content[m.end():eol].rstrip().endswith(";"):
            seg = content[m.end():eol]
        else:
            end = sinks[k + 1].start() if k + 1 < len(sinks) else len(content)
            seg = content[m.end():min(end, m.end() + 2000)]
        if "esc(" in seg or CONST_RHS.match(seg):
            continue
        out.append(f"{rel}:{ln}: HTML sink without visible esc() — "
                   f"{lines[ln - 1].strip()[:90]}")
    return out

def security():
    fails = []
    _headers_ok(fails)

    files = pages() + sorted(glob.glob(str(ROOT / "functions" / "**" / "*.js"), recursive=True))
    for path in files:
        fails += _sink_findings(path, os.path.relpath(path, ROOT))

    for path in pages():                                # external resource origins
        rel = os.path.relpath(path, ROOT)
        raw = open(path, encoding="utf-8").read()
        for i, line in enumerate(raw.splitlines(), 1):
            for m in re.finditer(r'<script[^>]+src\s*=\s*["\'](https?://[^"\']+)', line):
                if not m.group(1).startswith(TRUSTED_SCRIPT_ORIGINS):
                    fails.append(f"{rel}:{i}: external script origin {m.group(1)[:70]}")
            for m in re.finditer(r'<link\b[^>]*>', line):
                tag = m.group()
                relv = (re.search(r'rel\s*=\s*["\']([^"\']+)', tag) or [None, ""])[1]
                href = (re.search(r'href\s*=\s*["\'](https?://[^"\']+)', tag) or [None, None])[1]
                if href and relv.lower() in ("stylesheet", "preload", "modulepreload",
                                             "prefetch", "icon", "manifest") and \
                        not href.startswith(SELF_ORIGINS):
                    fails.append(f"{rel}:{i}: external {relv} origin {href[:70]}")
            for m in re.finditer(r'url\(\s*["\']?(https?://[^)"\']+)', line):
                if not m.group(1).startswith(SELF_ORIGINS):
                    fails.append(f"{rel}:{i}: external url() origin {m.group(1)[:70]}")
        for m in SECRET.finditer(raw):                  # no secrets in public/
            fails.append(f"{rel}: secret-looking string {m.group()[:24]}…")

    for f in fails:
        print(f"  x {f}")
    print(f"site-security: {'PASS' if not fails else 'FAIL'} ({len(fails)} finding(s))")
    return 0 if not fails else 1

# ═══ redirects ═══════════════════════════════════════════════════════════════
WANT_ROWS = [("/tool", "/check", "301"), ("/guide", "/docs", "301")]
BAD_LINKS = {p + n + s for p in ("", "/", "./") for n in ("tool", "guide")
             for s in ("", ".html")}                   # /tool, tool, ./tool.html, …

def redirects():
    fails = []
    f = PUB / "_redirects"
    if not f.exists():
        fails.append("public/_redirects missing")
    else:
        rows = [tuple(l.split()) for l in f.read_text().splitlines()
                if l.strip() and not l.strip().startswith("#")]
        # Intent, not exact-string: every retired page (/tool, /guide) MUST redirect
        # to its replacement (/check, /docs) with a 301 — a splat (/tool*) that covers
        # both the clean URL and its .html variant satisfies this. Any redirect that
        # mentions tool/guide MUST target the right replacement (no stray redirects).
        for src_pat, dst in (("tool", "/check"), ("guide", "/docs")):
            covering = [r for r in rows if len(r) == 3 and r[2] == "301"
                        and re.fullmatch(rf"/{src_pat}\*?", r[0])]
            if not covering:
                fails.append(f"_redirects: no 301 redirect covering /{src_pat}(*) -> {dst}")
            elif any(r[1] != dst for r in covering):
                fails.append(f"_redirects: /{src_pat} redirect must target {dst}, got "
                             f"{[r[1] for r in covering if r[1] != dst]}")
        for r in rows:
            if len(r) != 3 or r[2] != "301" or not re.fullmatch(r"/(tool|guide)\*?", r[0]):
                fails.append(f"_redirects: unexpected row '{' '.join(r)}' — only the "
                             f"/tool,/guide retirement redirects are allowed")

    for path in pages():
        page = page_key(path)
        for i, line in enumerate(open(path, encoding="utf-8").read().splitlines(), 1):
            for m in re.finditer(r'href\s*=\s*["\']([^"\']+)', line):
                target = m.group(1).split("#")[0].split("?")[0]
                if target in BAD_LINKS:
                    fails.append(f"{page}:{i}: links retired path {m.group(1)!r}")

    for f2 in fails:
        print(f"  x {f2}")
    print(f"site-redirects: {'PASS' if not fails else 'FAIL'} ({len(fails)} finding(s))")
    return 0 if not fails else 1

# ═══ consistency ═════════════════════════════════════════════════════════════
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
# a selector that OPENS a rule for a shared component: the token must appear at a
# selector-boundary (start, after , or after a }) and reach a { — so `.site-nav{…}`
# and `.site-nav .links{…}` are caught, but a page-local `.btn.pri` is not.
SHARED_SEL = re.compile(
    r"(?:^|[,}])\s*(\.site-nav|\.site-footer|\.btn-primary|\.btn-secondary|\.btn-ghost)"
    r"\b[^{}]*\{", re.M)

def _css_line(block_start_line, block, m):
    """Absolute source line of match m inside a style block starting on line block_start_line."""
    return block_start_line + block.count("\n", 0, m.start())

def consistency():
    fails = []
    for path in pages():
        page = page_key(path)
        raw = open(path, encoding="utf-8").read()
        if not re.search(r'<link[^>]+href\s*=\s*["\']/site\.css["\']', raw):
            fails.append(f"{page}: does not link the shared design system "
                         f'(<link rel="stylesheet" href="/site.css">) — every page must')
        for bm in STYLE_BLOCK.finditer(raw):
            block = bm.group(1)
            block_line = raw.count("\n", 0, bm.start(1)) + 1
            for m in SHARED_SEL.finditer(block):
                ln = _css_line(block_line, block, m)
                fails.append(f"{page}:{ln}: inline <style> redefines shared component "
                             f"{m.group(1)} — site.css is the single source; delete the "
                             f"override so the nav/footer/buttons can't re-theme per page")
    for f in fails:
        print(f"  x {f}")
    print(f"site-consistency: {'PASS' if not fails else 'FAIL'} ({len(fails)} finding(s))")
    return 0 if not fails else 1

# ═══ freshness ═══════════════════════════════════════════════════════════════
def _coverage_versions():
    return json.load(open(PUB / "coverage.json"))["versions"]


TESTABLE_TIER = ("testable", "needs-receiver", "needs-oauth")
STATES = ("unregistered", "building", "converting", "live")


def _expected_state(d):
    """Rule R-a (PLAN-v3 §2.18, decision 25) from coverage.json fields ONLY — no
    matrix import, so this mirror and matrix._version_state can be compared but can
    never share a bug:
      unregistered — no register rows (musts == 0)
      building     — register ∧ CHECK+EXEMPT == 0
      converting   — register ∧ CHECK+EXEMPT > 0 ∧ any testable/needs-receiver/needs-oauth GAP
      live         — register ∧ CHECK+EXEMPT > 0 ∧ zero such GAP
    Returns (expected_state, testable_gap)."""
    musts = d.get("musts", 0)
    accounted = (d.get("check", 0) or 0) + (d.get("exempt", 0) or 0)
    g = d.get("gap_by_testability") or {}
    testable_gap = sum(int(g.get(k, 0) or 0) for k in TESTABLE_TIER)
    if not musts:
        return "unregistered", testable_gap
    if not accounted:
        return "building", testable_gap
    return ("converting" if testable_gap else "live"), testable_gap


def _state_failures(cov_export):
    """The state field can never disagree with the artifacts it describes (PLAN-0825
    §E, extended to four states by rule R-a in D5-03). Data-driven, stdlib-only,
    reads only the committed export. Returns the list of failure strings (empty = the
    state field is honest)."""
    fails = []
    for ver, d in sorted(cov_export.items()):
        state = d.get("state")
        if state not in STATES:
            fails.append(f"{ver}: state {state!r} is not one of {'/'.join(STATES)}")
            continue
        want, tg = _expected_state(d)
        if state != want:
            fails.append(f"{ver}: state={state!r} but rule R-a says {want!r} "
                         f"(musts={d.get('musts', 0)}, CHECK={d.get('check', 0)}, "
                         f"EXEMPT={d.get('exempt', 0)}, testable-tier GAP={tg})")
    return fails


def _real_manifest():
    cov_export = _coverage_versions()
    # A version with zero CHECK and zero EXEMPT is register-only (see matrix.py's
    # REGISTER_ONLY_VERSIONS / CURRENT_SITE_VERSION doctrine): its census hasn't
    # closed, so — like 2026-04-08 before its own check/exempt work landed — it
    # "does not back any site copy" and must not appear as a site-claimed
    # "supported version" the moment its register merges. Purely data-driven off
    # the committed export (site_gates.py stays stdlib-only, no cross-module
    # import of matrix.py) — self-corrects the moment the version gets real
    # CHECK/EXEMPT rows, with no code change needed here when that happens.
    # (This is exactly the `backs_site` test _state_failures() uses too — the two
    # can never independently disagree about what counts as supported.)
    # B3 (owner ruling 2026-09-10 adopting W0-review ruling (a)): SUPPORTED = the
    # versions with a REGISTERED CHECK SET — real CHECK/EXEMPT rows, i.e. what the CLI
    # actually grades — independent of the publication-state word. `live` is a
    # publication state (rule R-a: zero testable-tier GAP) and stays on coverage.json;
    # deriving "supported" from it published `versions: []` while the CLI graded four
    # versions with 227 checks — a false public statement. A converting version IS
    # supported (its checks grade); a building/unregistered one (0/0) is not.
    cov = [v for v, d in cov_export.items() if d.get("check") or d.get("exempt")]
    agc = json.load(open(PUB / "agent-coverage.json"))
    # The agent axis can never WIDEN the supported set: a version key that carries
    # agent CHECK/EXEMPT rows but is absent from coverage.json (or not live there) is
    # a manifest failure, raised as drift below via a synthetic marker — an honest-zero
    # agent row for a new version never widens the public claim on its own.
    agv = [v for v, d in agc.items()
           if isinstance(d, dict) and (d.get("check") or d.get("exempt"))
           and v not in cov_export]
    # agent registry counts via subprocess import — same source of truth as the
    # agent_governance copy gate (len(CHECKS); non-None DEFECTS)
    r = subprocess.run([sys.executable, "-c",
                        "import sys,json;sys.path.insert(0,'conformance/agent');"
                        "import agent_checks,reference_agent;"
                        "print(json.dumps({'agent_checks':len(agent_checks.CHECKS),"
                        "'agent_defects':len([k for k in reference_agent.DEFECTS if k])}))"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"agent registry import failed: {r.stderr[-200:]}")
    ag = json.loads(r.stdout)
    # merchant check count = the ENGINE's runtime check set (checkset_count.py, D5-16) — the
    # same helper coverage_gate copy-freshness, docclaims and the site_claims writer use
    merchant, dup_ids = _merchant_count()
    if dup_ids:
        raise RuntimeError(f"duplicate merchant check id(s) {dup_ids} — the product count is undefined until fixed")
    return {
        "merchant_checks": merchant,
        "agent_checks": ag["agent_checks"],
        "agent_defects": ag["agent_defects"],
        # agv is non-empty only when the agent export names a version coverage.json
        # lacks — surfaced here so freshness reports it as manifest drift
        "versions": sorted(set(cov) | {f"{v} (agent-only, absent from coverage.json)" for v in agv}),
    }

ADOPTION_FACTS = "state-of-ucp/adoption-facts.json"


def _adoption_facts_failures(cov_export):
    """B2 (W0-review 2026-09-10): public/state-of-ucp/adoption-facts.json republishes the
    per-version MUST counts (`spec_musts_<YYYY_MM_DD>`) that coverage.json OWNS
    (matrix.py -> `versions[v].musts`, byte-compared by the coverage gate). A snapshot
    value that disagrees with the guarded export is a public number changed outside its
    gate — exactly what happened at 364 vs 366 for 2026-04-08. Every `spec_musts_*` key
    present must equal coverage.json's `musts` for that version; a missing or unreadable
    file, or a file with no such key, is a FAIL (fail-closed: nothing to verify is not
    "verified"). Data-driven off the committed export; stdlib-only."""
    f = PUB / ADOPTION_FACTS
    try:
        facts = json.load(open(f))
    except Exception as e:                                          # noqa: BLE001
        return [f"{ADOPTION_FACTS}: unreadable ({e}) — spec_musts_* snapshot cannot be verified"]
    fails, seen = [], 0
    for ver, d in sorted(cov_export.items()):
        key = "spec_musts_" + ver.replace("-", "_")
        if key not in facts:
            continue
        seen += 1
        if facts[key] != d.get("musts"):
            fails.append(f"{ADOPTION_FACTS}: {key} = {facts[key]!r} but coverage.json "
                         f"$.versions['{ver}'].musts = {d.get('musts')!r}")
    if not seen:
        fails.append(f"{ADOPTION_FACTS}: no spec_musts_<version> key matches any coverage.json "
                     f"version — nothing to verify (fail-closed)")
    return fails


KNOWN_ISSUES = "known-issues.json"
KNOWN_ISSUES_MAX_DAYS = 30


def _known_issues_failures(today=None):
    """D5-12 / SITE-R-033: every published known-issue row must have been re-verified within
    30 days (the tracker and the page bind their counts to public/known-issues.json). A
    missing/unreadable file is a FAIL (fail-closed), like adoption-facts."""
    today = today or datetime.date.today()
    f = PUB / KNOWN_ISSUES
    try:
        doc = json.load(open(f))
    except Exception as e:                                          # noqa: BLE001
        return [f"{KNOWN_ISSUES}: unreadable ({e}) — run conformance/web/gen_known_issues.py --write"]
    fails = []
    for r in doc.get("issues") or []:
        rid = r.get("id", "?")
        try:
            age = (today - datetime.date.fromisoformat(r["re_verified"])).days
        except (KeyError, ValueError):
            fails.append(f"{KNOWN_ISSUES}: {rid} re_verified missing/invalid"); continue
        if age > KNOWN_ISSUES_MAX_DAYS:
            fails.append(f"{KNOWN_ISSUES}: {rid} re_verified {r['re_verified']} is {age} days old "
                         f"(max {KNOWN_ISSUES_MAX_DAYS}) — re-verify the row or retire it")
        if r.get("refuted") is not False:
            fails.append(f"{KNOWN_ISSUES}: {rid} is refuted — a refuted finding never renders")
    return fails


def freshness():
    state_fails = _state_failures(_coverage_versions())
    for sf in state_fails:
        print(f"  x state: {sf}")
    real = _real_manifest()
    f = PUB / "site_claims.json"
    data = json.load(open(f)) if f.exists() else {}
    manifest = data.get("manifest")
    reviewed = data.get("reviewed") or (manifest or {}).get("reviewed", "")
    if state_fails:
        print(f"site-freshness: FAIL — {len(state_fails)} version state "
              f"disagree(s) with its own coverage.json artifacts (see above)")
        return 1
    facts_fails = _adoption_facts_failures(_coverage_versions())
    for ff in facts_fails:
        print(f"  x adoption-facts: {ff}")
    if facts_fails:
        print(f"site-freshness: FAIL — {len(facts_fails)} adoption-facts spec_musts "
              f"snapshot(s) disagree with coverage.json (see above)")
        return 1
    ki_fails = _known_issues_failures()
    for kf in ki_fails:
        print(f"  x known-issues: {kf}")
    if ki_fails:
        print(f"site-freshness: FAIL — {len(ki_fails)} published known-issue row(s) stale or refuted (see above)")
        return 1
    if not manifest:
        print(f"site-freshness: FAIL — claims register missing manifest "
              f"(public/site_claims.json needs a top-level {{\"manifest\": …, "
              f"\"reviewed\": \"YYYY-MM-DD\"}} block). Current product manifest:\n"
              f"  {json.dumps(real)}")
        return 1
    drift = {k: (manifest.get(k), v) for k, v in real.items() if manifest.get(k) != v}
    if drift:
        for k, (old, new) in drift.items():
            print(f"  x manifest drift: {k} reviewed as {old} but the product says {new}")
        # Drift ALWAYS fails — a same-day grace would let the gate fail toward
        # green any day the reviewed stamp is bumped (including by automation),
        # which is exactly the fail-open class this suite exists to forbid. The
        # legitimate same-run flow is to regenerate the manifest block to match
        # the product in the same commit; there is never a reason to ship drift.
        print("site-freshness: FAIL — product changed: regenerate the manifest "
              "block to match the product and set reviewed after review")
        return 1
    print(f"site-freshness: PASS — site claims reviewed {reviewed}, manifest matches "
          f"the product ({json.dumps(real)})")
    return 0


# ═══ selftest (PLAN-0825 §E state-consistency kill-tests) ═══════════════════════
def selftest():
    """Injection kill-tests for _state_failures() / freshness(): plant an
    internally-inconsistent `state` field into a SCRATCH copy of public/ (never the
    committed tree — this repo is untouched before, during, and after) and assert
    the gate reddens; a correctly-labeled copy must stay green. A validator that
    can't be made to fail validates nothing (same doctrine as coverage_gate.py's
    own --selftest).

    The "zero CHECK, zero EXEMPT" condition each variant needs is CONSTRUCTED
    inside the mutation itself, never discovered by scanning the committed export
    for a version that happens to already be register-only. Discovering it would
    make these kill-tests silently stop exercising the moment every pinned spec
    version matures to real CHECK/EXEMPT rows — exactly what happened at the
    2026-08-25 conversion-phase flip (R4/PLAN-0825 §G4): 2026-08-25 was the last
    naturally-zero version, and the flip gave it 27 real CHECK rows, so a
    discovery-based `zero_ver` silently went None and the WHOLE selftest started
    returning a bare SKIP with no red flag anywhere — a maturing product quietly
    disarming its own kill-tests forever is the opposite of what a kill-test gate
    is for. Self-sufficient construction means these variants keep exercising the
    real code path no matter how many real versions eventually go live.

    Re-invokes site_gates.py as a SUBPROCESS with SPCK_PUBLIC pointed at the scratch
    dir, rather than calling freshness() in-process, because PUB is resolved once at
    import time from the environment — a fresh process is the only way to pick up a
    different SPCK_PUBLIC, and it is also the exact same code path a real CI run
    takes."""
    import copy, shutil, tempfile
    # sourced from PUB (not ROOT/public) so a reviewer can point SPCK_PUBLIC at a
    # scratch export — e.g. the D2-06 converting-state export before it merges — and
    # run the whole battery against it; on the committed tree PUB == public/.
    real_cov = json.load(open(PUB / "coverage.json"))
    versions = real_cov["versions"]
    if not versions:
        print("site_gates selftest: SKIP — public/coverage.json has no versions "
              "at all to exercise the state-consistency kill-tests against")
        return 0
    # subject_ver: any real, stable version key — its REAL check/exempt/state don't
    # matter, since variants 1 and 3 below overwrite them in the scratch copy only.
    # Deterministic (not "whichever happens to be zero today") so the test doesn't
    # depend on the register's current maturity.
    subject_ver = sorted(versions)[0]
    # prefer 2026-04-08 for the "building planted on a real version" case (PLAN-0825
    # §E names it explicitly — the mature, fully-accounted version); fall back to
    # whatever live version exists if the register ever moves on.
    live_ver = "2026-04-08" if (versions.get("2026-04-08", {}).get("check")
                                or versions.get("2026-04-08", {}).get("exempt")) \
        else next((v for v, d in versions.items()
                  if d.get("check") or d.get("exempt")), None)
    if live_ver is None:
        print("site_gates selftest: SKIP — need at least one live (real CHECK/"
              "EXEMPT) version in public/coverage.json to exercise the "
              "state-consistency kill-tests")
        return 0
    # a version key GUARANTEED to never be a real spec version (never collides with
    # common.spec_versions.VERSIONS, today or after any future landing) — used ONLY
    # by the agent-widening variant below, planted into agent-coverage.json alone,
    # never into coverage.json's `versions` (so _state_failures never even sees it;
    # only the backs-site UNION in _real_manifest() does).
    synth_ver = "2099-01-01-selftest-synthetic"

    bad = 0

    def run_variant(name, mutate, want_red, mutate_agc=None, mutate_claims=None,
                    mutate_facts=None):
        nonlocal bad
        with tempfile.TemporaryDirectory() as tmp:
            tmpd = pathlib.Path(tmp)
            for fname in ("coverage.json", "agent-coverage.json", "site_claims.json",
                          ADOPTION_FACTS, KNOWN_ISSUES):
                src = PUB / fname
                if src.exists():
                    (tmpd / fname).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, tmpd / fname)
            if mutate_facts is not None:
                facts = json.load(open(tmpd / ADOPTION_FACTS))
                mutate_facts(facts)
                (tmpd / ADOPTION_FACTS).write_text(json.dumps(facts))
            cov = copy.deepcopy(real_cov)
            mutate(cov["versions"])
            (tmpd / "coverage.json").write_text(json.dumps(cov))
            if mutate_agc is not None:
                agc = json.load(open(tmpd / "agent-coverage.json"))
                mutate_agc(agc)
                (tmpd / "agent-coverage.json").write_text(json.dumps(agc))
            if mutate_claims is not None:
                sc = json.load(open(tmpd / "site_claims.json"))
                mutate_claims(sc)
                (tmpd / "site_claims.json").write_text(json.dumps(sc))
            env = dict(os.environ, SPCK_PUBLIC=str(tmpd))
            r = subprocess.run(
                [sys.executable, str(ROOT / "conformance" / "ci" / "site_gates.py"),
                 "freshness"], cwd=str(ROOT), env=env, capture_output=True, text=True)
            got_red = r.returncode != 0
            ok = got_red == want_red
            print(f"  {'✓' if ok else '✗'} {name}: {'RED' if got_red else 'GREEN'}"
                  + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}"
                                   f"\n{r.stdout}{r.stderr}"))
            bad += 0 if ok else 1

    run_variant(
        f"state=live planted on {subject_ver} forced to zero CHECK/EXEMPT",
        lambda vs: vs.__setitem__(subject_ver,
                                  {**vs[subject_ver], "check": 0, "exempt": 0,
                                   "state": "live"}),
        want_red=True)
    run_variant(
        f"state=building planted on {live_ver} (has real CHECK/EXEMPT rows)",
        lambda vs: vs.__setitem__(live_ver, {**vs[live_ver], "state": "building"}),
        want_red=True)
    run_variant(
        f"agent check planted on a synthetic all-zero version ({synth_ver}) absent "
        f"from coverage.json entirely (backs-site filter must widen the real "
        f"manifest via the agent axis ALONE and redden the version-count claim)",
        lambda vs: None,
        want_red=True,
        mutate_agc=lambda a: a.__setitem__(synth_ver, {"check": 1, "exempt": 0}))
    # ── rule R-a (PLAN-v3 §2.18 / decision 25, D5-03): live ⇔ register ∧ CHECK+EXEMPT>0
    #    ∧ zero testable/needs-receiver/needs-oauth GAP; converting ⇔ … ∧ any such GAP;
    #    building ⇔ register ∧ 0/0. Each variant CONSTRUCTS its condition in the scratch
    #    copy (same doctrine as above) so it keeps exercising the code path regardless of
    #    which real version happens to be converting today.
    def with_state(ver, state, **fields):
        return lambda vs: vs.__setitem__(ver, {**vs[ver], **fields, "state": state})
    run_variant(
        "state=live planted on 2026-08-25 forced to testable-tier GAP>0 (rule R-a says converting)",
        with_state("2026-08-25", "live", check=10, exempt=0,
                   gap_by_testability={"testable": 5, "manual": 2}),
        want_red=True)
    run_variant(
        f"state=converting planted on {live_ver} forced to zero testable-tier GAP (rule R-a says live)",
        with_state(live_ver, "converting", check=10, exempt=1, gap_by_testability={"manual": 3}),
        want_red=True)
    run_variant(
        f"state=converting planted on {subject_ver} forced to zero CHECK/EXEMPT (rule R-a says building)",
        with_state(subject_ver, "converting", check=0, exempt=0,
                   gap_by_testability={"testable": 5}),
        want_red=True)
    # B3: a CONVERTING version with real CHECK rows listed as supported is exactly right
    # (its checks grade) -> GREEN; a version forced to zero CHECK/EXEMPT but still listed
    # is a false "supported" -> RED; and a version with CHECK rows MISSING from the list
    # is manifest drift -> RED.
    run_variant(
        "converting version (real CHECK rows) listed in manifest.versions "
        "(B3: supported = registered check set, not live)",
        with_state("2026-08-25", "converting", check=10, exempt=0,
                   gap_by_testability={"testable": 5}),
        want_red=False,
        mutate_claims=lambda sc: sc["manifest"].__setitem__(
            "versions", sorted(set(sc["manifest"]["versions"]) | {"2026-08-25"})))
    run_variant(
        f"{subject_ver} forced to zero CHECK/EXEMPT (state building) but still listed in manifest.versions",
        with_state(subject_ver, "building", check=0, exempt=0, gap_by_testability={}),
        want_red=True,
        mutate_claims=lambda sc: sc["manifest"].__setitem__(
            "versions", sorted(set(sc["manifest"]["versions"]) | {subject_ver})))
    run_variant(
        f"{live_ver} (real CHECK rows) removed from manifest.versions",
        lambda vs: None,
        want_red=True,
        mutate_claims=lambda sc: sc["manifest"].__setitem__(
            "versions", sorted(set(sc["manifest"]["versions"]) - {live_ver})))
    # B3 (owner ruling 2026-09-10 adopting W0-review ruling (a)): manifest.versions = the
    # versions with a REGISTERED CHECK SET (what the CLI grades), independent of the
    # publication-state word. An empty manifest.versions while the export carries
    # versions with CHECK rows is a false public statement ("no supported version" while
    # the CLI grades four) and must be RED.
    run_variant(
        "manifest.versions planted [] while the export has versions with CHECK rows "
        "(B3: supported = registered check set, not live)",
        lambda vs: None,
        want_red=True,
        mutate_claims=lambda sc: sc["manifest"].__setitem__("versions", []))
    run_variant(
        "correct states (unmodified export)",
        lambda vs: None,
        want_red=False)
    # B2 (W0-review): adoption-facts.json republishes coverage.json's per-version MUST
    # counts; a stale snapshot (the real 364-vs-366 shape) must redden freshness, and a
    # snapshot with no spec_musts_* key at all must not read as verified.
    run_variant(
        f"adoption-facts spec_musts for {live_ver} planted 2 below coverage.json musts (B2 shape)",
        lambda vs: None,
        want_red=True,
        mutate_facts=lambda f: f.__setitem__("spec_musts_" + live_ver.replace("-", "_"),
                                             versions[live_ver]["musts"] - 2))
    run_variant(
        "adoption-facts with every spec_musts_* key removed (nothing to verify is not verified)",
        lambda vs: None,
        want_red=True,
        mutate_facts=lambda f: [f.pop(k) for k in list(f) if k.startswith("spec_musts_")])

    # D1-08: a registered claim's `evidence` that names a run_suite gate must name one
    # that EXISTS in run_suite's gate table (the `killrate` -> `proxy-demo` rename left
    # 7 evidence strings citing a gate that no longer runs — evidence pointing at
    # nothing is no evidence). Scratch site_claims.json only; the committed tree is
    # never touched. Runs the `claims` mode (the evidence check is page-independent).
    def run_claims_variant(name, evidence, want_red):
        nonlocal bad
        with tempfile.TemporaryDirectory() as tmp:
            tmpd = pathlib.Path(tmp)
            (tmpd / "site_claims.json").write_text(json.dumps({"claims": [
                {"id": "SELFTEST-001", "class": "REG", "page": "*", "text": "selftest claim",
                 "evidence": evidence, "review_by": "2099-01-01"}]}))
            env = dict(os.environ, SPCK_PUBLIC=str(tmpd))
            r = subprocess.run(
                [sys.executable, str(ROOT / "conformance" / "ci" / "site_gates.py"), "claims"],
                cwd=str(ROOT), env=env, capture_output=True, text=True)
            got_red = r.returncode != 0
            ok = got_red == want_red
            print(f"  {'✓' if ok else '✗'} {name}: {'RED' if got_red else 'GREEN'}"
                  + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}"
                                   f"\n{r.stdout}{r.stderr}"))
            bad += 0 if ok else 1

    run_claims_variant(
        "evidence names run_suite gate 'killrate' (not in the table) → RED",
        "conformance/selfcheck/mutation_proxy_demo.py + run_suite 'killrate' gate", want_red=True)
    run_claims_variant(
        "evidence names run_suite gate 'verdict' (in the table) → GREEN",
        "run_suite 'verdict' gate proves it", want_red=False)
    run_claims_variant(
        "evidence names no gate at all → GREEN",
        "public/coverage.json, regenerated by matrix.py", want_red=False)

    print(f"\nsite_gates selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


# ═══ docclaims ════════════════════════════════════════════════════════════════
DOCROOT = pathlib.Path(os.environ.get("SPCK_DOCROOT", ROOT))
DOC_CLAIMS = WEB / "doc_claims.json"
# hand-authored non-page copy in scope (generated docs/spec-coverage-matrix.md is
# byte-compared by the coverage gate and excluded here)
DOC_FILES = ("README.md", "conformance/ci/README.md", "packaging/README.md",
             "docs/archive/ROADMAP.md", "docs/TWO-LANE.md", "docs/TEST-INTEGRITY.md",
             "docs/merchant-conformance.md", "docs/ap2-vectors.md")
DOC_GLOBS = ("functions/**/*.js",)
# (regex, live-value key, label) — every captured count MUST equal the live value
# unless the exact phrase is a registered `dated` row for that file
DOC_COUNT_RES = [
    (re.compile(r"(\d+)\+?\s+kill-rate-validated\s+(?:merchant\s+)?checks?"), "merchant_checks", "'N kill-rate-validated checks' prose"),
    (re.compile(r"(\d+)\+?\s+checks\s+across"), "merchant_checks", "'N checks across' prose"),
    (re.compile(r"(\d+)\+?\s+checks,\s+from the browser"), "merchant_checks", "'N checks, from the browser' prose"),
    (re.compile(r"(\d+)\+?\s+checks?\s+kill-tested against independent servers?"), "live_wire", "'N checks kill-tested against independent servers' prose"),
    (re.compile(r"(\d+)\+?\s+agent[- ]side checks?"), "agent_checks", "'N agent-side checks' prose"),
    (re.compile(r"(\d+)\+?\s+agent checks?\b"), "agent_checks", "'N agent checks' prose"),
    (re.compile(r"(\d+)\+?\s+checks?\s+\(\d+\s+defects modeled\)"), "agent_checks", "'N checks (M defects modeled)' prose"),
    (re.compile(r"\d+\+?\s+checks?\s+\((\d+)\s+defects modeled\)"), "agent_defects", "'N checks (M defects modeled)' prose"),
    (re.compile(r"(\d+)\+?\s+(?:client )?defects? modeled"), "agent_defects", "'N defects modeled' prose"),
    (re.compile(r"(\d+)\+?\s+failure modes"), "agent_defects", "'N failure modes' prose"),
]


def _pyproject_version():
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "packaging" / "pyproject.toml").read_text(), re.M)
    return m.group(1) if m else None


def _base_version(v):
    """PEP 440 base of a version: '0.4.0rc1' -> '0.4.0' (pre-release tags share the
    final release's Action pin)."""
    m = re.match(r"(\d+(?:\.\d+)*)", v or "")
    return m.group(1) if m else None


def doc_live_values():
    """Live product values the doc counts are pinned to — the SAME counting technique
    the coverage gate / agent_governance / freshness use (never a second opinion)."""
    merchant, _dups = _merchant_count()
    r = subprocess.run([sys.executable, "-c",
                        "import sys,json;sys.path.insert(0,'conformance/agent');"
                        "import agent_checks,reference_agent;"
                        "print(json.dumps({'agent_checks':len(agent_checks.CHECKS),"
                        "'agent_defects':len([k for k in reference_agent.DEFECTS if k])}))"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    ag = json.loads(r.stdout) if r.returncode == 0 else {}
    live_wire = None
    sc = PUB / "site_claims.json"
    if sc.exists():
        ev = (json.load(open(sc)).get("evidence") or {}).get("per_version") or {}
        newest = sorted(k for k in ev if ev[k].get("live-wire") is not None)
        live_wire = ev[newest[-1]].get("live-wire") if newest else None
    pv = _pyproject_version()
    return {"merchant_checks": merchant, "agent_checks": ag.get("agent_checks"),
            "agent_defects": ag.get("agent_defects"), "live_wire": live_wire,
            "pyproject_version": pv, "pyproject_base_version": f"v{_base_version(pv)}" if pv else None}


def _doc_files(root):
    out = [root / f for f in DOC_FILES if (root / f).exists()]
    for g in DOC_GLOBS:
        out += [pathlib.Path(f) for f in sorted(glob.glob(str(root / g), recursive=True))]
    return out


def _run_suite_gate_names():
    """Gate names in run_suite.py's table (the ci/README rows must name real gates)."""
    r = subprocess.run([sys.executable, "-c",
                        "import sys;sys.path.insert(0,'conformance/ci');import run_suite;"
                        "print('\\n'.join(g[0] for g in run_suite.gates('http://localhost:0')))"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    return set(r.stdout.split()) if r.returncode == 0 else None


def docclaims():
    root = DOCROOT
    fails = []
    reg = json.load(open(DOC_CLAIMS)) if DOC_CLAIMS.exists() else {"claims": []}
    rows = reg.get("claims", [])
    live = doc_live_values()

    def dated_ok(rel, phrase):
        """A stale count is fine ONLY as a registered, unexpired `dated` row whose text
        contains the exact phrase for this file (a dated 'where we were' statement)."""
        for e in rows:
            if e.get("kind") == "dated" and e.get("file") == rel and phrase in e.get("text", ""):
                return e if e.get("review_by", "") >= TODAY else None
        return None

    # 1. count sweep over every in-scope file
    for f in _doc_files(root):
        rel = str(f.relative_to(root))
        txt = f.read_text(encoding="utf-8", errors="replace")
        for cre, key, what in DOC_COUNT_RES:
            for m in cre.finditer(txt):
                want = live.get(key)
                if want is None:
                    fails.append(f"{rel}: {what} '{m.group(0)}' but the live value for {key} is unavailable")
                elif int(m.group(1)) != want:
                    if dated_ok(rel, m.group(0)):
                        continue
                    fails.append(f"{rel}: {what} claims {m.group(1)} but the product says {want} "
                                 f"({key}) — update the copy or register a dated row")

    # 2. registered rows still hold
    for e in rows:
        cid, rel = e.get("id", "?"), e.get("file", "")
        f = root / rel
        if not f.exists():
            fails.append(f"{cid}: file {rel} missing"); continue
        if e.get("review_by", "") < TODAY:
            fails.append(f"{cid}: review_by {e.get('review_by')} expired")
        txt = f.read_text(encoding="utf-8", errors="replace")
        if e.get("kind") == "dated":
            if e.get("text", "") not in txt:
                fails.append(f"{cid}: dated text no longer present in {rel} — retire the row")
            continue
        mm = re.search(e["must_match"], txt) if e.get("must_match") else None
        if e.get("must_match") and not mm:
            fails.append(f"{cid}: {rel} does not match /{e['must_match']}/")
        if e.get("must_not_match") and re.search(e["must_not_match"], txt):
            fails.append(f"{cid}: {rel} matches forbidden /{e['must_not_match']}/")
        if e.get("bind") and mm:
            want = live.get(e["bind"])
            got = mm.group(1) if mm.groups() else mm.group(0)
            if str(got) != str(want):
                fails.append(f"{cid}: {rel} says {got!r} but the product says {want!r} ({e['bind']})")

    # 3. ci/README's gate table names real run_suite gates (D1-08 rename lands here)
    ci_readme = root / "conformance" / "ci" / "README.md"
    if ci_readme.exists():
        names = _run_suite_gate_names()
        if names is None:
            fails.append("could not import run_suite.gates() to verify ci/README's gate table")
        else:
            for m in re.finditer(r"^\| `([a-z0-9-]+)` \|", ci_readme.read_text(), re.M):
                if m.group(1) not in names:
                    fails.append(f"conformance/ci/README.md: gate row `{m.group(1)}` names no gate in "
                                 f"run_suite.py's table (renamed/removed?)")

    for f2 in fails:
        print(f"  x {f2}")
    print(f"docclaims: {'PASS' if not fails else 'FAIL'} ({len(fails)} unverified claim(s))")
    return 0 if not fails else 1


# ═══ checkdocs ════════════════════════════════════════════════════════════════
def checkdocs():
    """SITE-R-026 — the published check register can never drift from the product.

    Regenerates the /checks pages from public/coverage.json into a temp dir and
    byte-compares against what's committed; asserts every covered requirement id
    has exactly one page (no missing, no orphans) and /rubric.html exists.
    """
    import filecmp, importlib.util, tempfile
    fails = []
    spec = importlib.util.spec_from_file_location(
        "gen_check_docs", ROOT / "conformance" / "web" / "gen_check_docs.py")
    gen = importlib.util.module_from_spec(spec); spec.loader.exec_module(gen)

    committed = PUB / "checks"
    if not committed.is_dir():
        print("  x public/checks/ missing — run conformance/web/gen_check_docs.py")
        print("site-checkdocs: FAIL (1 finding(s))"); return 1

    with tempfile.TemporaryDirectory() as tmp:
        gen.generate(tmp)
        fresh = {f.name for f in pathlib.Path(tmp).iterdir()}
        have = {f.name for f in committed.iterdir() if f.suffix == ".html"}
        for name in sorted(fresh - have):
            fails.append(f"missing page: checks/{name} (regenerate)")
        for name in sorted(have - fresh):
            fails.append(f"orphan page: checks/{name} (no covered register row)")
        for name in sorted(fresh & have):
            if not filecmp.cmp(pathlib.Path(tmp) / name, committed / name, shallow=False):
                fails.append(f"stale page: checks/{name} differs from regeneration")

    by_id, _ = gen.load()
    expected = {f"{rid}.html" for rid in by_id} | {"index.html"}
    have = {f.name for f in committed.iterdir() if f.suffix == ".html"}
    if expected != have:
        fails.append(f"page set != covered ids: {sorted(expected ^ have)[:5]}")
    if not (PUB / "rubric.html").exists():
        fails.append("rubric.html missing — the grading rubric must be published")

    # D5-12 / SITE-R-033: the known-issues page, its JSON and the KI-* claims are projections
    # of conformance/ci/known_issues.json — regenerated in memory and byte-compared.
    ki_fails = []
    gki = ROOT / "conformance" / "web" / "gen_known_issues.py"
    if gki.exists():
        spec2 = importlib.util.spec_from_file_location("gen_known_issues", gki)
        gen2 = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(gen2)
        ki_fails = [f"known-issues: {f}" for f in gen2.check_failures()]
    else:
        ki_fails = ["known-issues: conformance/web/gen_known_issues.py missing — the page cannot be verified"]
    fails += ki_fails

    for f in fails[:20]:
        print(f"  x {f}")
    print(f"site-checkdocs: {'PASS' if not fails else 'FAIL'} "
          f"({len(by_id)} covered requirement page(s); {len(fails)} finding(s))"
          + (" · known-issues page in sync" if not ki_fails else " · known-issues page OUT OF SYNC"))
    return 0 if not fails else 1

# ═══ main ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        print("usage: site_gates.py tdd|claims|voice|security|redirects|consistency|freshness"
              "|checkdocs|docclaims [--explain]|--selftest")
        sys.exit(1)
    if mode == "claims":
        if "--orphans" in sys.argv[2:]:
            sys.exit(orphans())
        sys.exit(claims(explain="--explain" in sys.argv[2:]))
    sys.exit({"tdd": tdd, "voice": voice, "security": security,
              "redirects": redirects, "consistency": consistency,
              "freshness": freshness, "checkdocs": checkdocs, "docclaims": docclaims}[mode]())

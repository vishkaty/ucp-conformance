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
  freshness  product manifest (coverage JSONs + agent registries) vs the manifest
             block reviewed into public/site_claims.json — product drift with a
             stale review date is RED.

Pages audited = every public/*.html PRESENT (tool.html/guide.html drop out of the
audit automatically once retired). Exit 0 pass · 1 fail · 2 honest skip.
Stdlib only.
"""
import datetime, glob, html.parser, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
# SPCK_PUBLIC lets oversight/tests point the gates at a scratch copy of the site
PUB = pathlib.Path(os.environ.get("SPCK_PUBLIC", ROOT / "public"))
WEB = ROOT / "conformance" / "web"
MODES = ("tdd", "claims", "voice", "security", "redirects", "freshness")
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

def pages():
    """All public pages currently present — retired pages drop out on deletion."""
    return sorted(glob.glob(str(PUB / "*.html")))

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

    tested = 0
    for r in rows:
        ok = False
        for t in r["tests"]:
            if t.startswith("gate:"):
                ok |= t.split(":", 1)[1] in MODES     # internal mode exists
            elif t.startswith("site_smoke:"):
                ok |= r["id"] in tagged               # block tagged // SITE-R-xxx
            elif t == "responsive_smoke":
                ok |= have_responsive
            else:
                fails.append(f"{r['id']}: unknown test kind {t!r}")
        if ok:
            tested += 1
        else:
            fails.append(f"{r['id']} UNTESTED — no existing test among {r['tests']}")

    for t in sorted(tagged - ids):                    # orphan tests: register is SoT
        fails.append(f"orphan tag {t} in site_smoke.mjs cites no register row")

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

def claims(explain=False):
    entries = _load_claims()
    fails, out = [], []

    for path in pages():
        page = os.path.basename(path)

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
        page = os.path.basename(path)
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
                if not m.group(1).startswith(SELF_ORIGINS):
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
        page = os.path.basename(path)
        for i, line in enumerate(open(path, encoding="utf-8").read().splitlines(), 1):
            for m in re.finditer(r'href\s*=\s*["\']([^"\']+)', line):
                target = m.group(1).split("#")[0].split("?")[0]
                if target in BAD_LINKS:
                    fails.append(f"{page}:{i}: links retired path {m.group(1)!r}")

    for f2 in fails:
        print(f"  x {f2}")
    print(f"site-redirects: {'PASS' if not fails else 'FAIL'} ({len(fails)} finding(s))")
    return 0 if not fails else 1

# ═══ freshness ═══════════════════════════════════════════════════════════════
def _real_manifest():
    cov = json.load(open(PUB / "coverage.json"))["versions"]
    agc = json.load(open(PUB / "agent-coverage.json"))
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
    # merchant check count = the ENGINE's MCheck registry — same counting technique
    # as coverage_gate copy-freshness (the advertised "N kill-rate-validated checks")
    merchant = 0
    for f2 in glob.glob(str(ROOT / "conformance" / "checks" / "merchant_checks*.py")):
        merchant += len(re.findall(r"^    MCheck\(", open(f2).read(), re.M))
    return {
        "merchant_checks": merchant,
        "agent_checks": ag["agent_checks"],
        "agent_defects": ag["agent_defects"],
        "versions": sorted(set(cov) | set(agc)),
    }

def freshness():
    real = _real_manifest()
    f = PUB / "site_claims.json"
    data = json.load(open(f)) if f.exists() else {}
    manifest = data.get("manifest")
    reviewed = data.get("reviewed") or (manifest or {}).get("reviewed", "")
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
        # a review dated today covers a same-run product bump; anything older is stale
        if reviewed < TODAY:
            print("site-freshness: FAIL — product changed: re-review site claims "
                  "(update manifest+reviewed after review)")
            return 1
        print("site-freshness: PASS (reviewed today) — but regenerate the manifest "
              "block to match the product")
        return 0
    print(f"site-freshness: PASS — site claims reviewed {reviewed}, manifest matches "
          f"the product ({json.dumps(real)})")
    return 0

# ═══ main ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        print("usage: site_gates.py tdd|claims|voice|security|redirects|freshness [--explain]")
        sys.exit(1)
    if mode == "claims":
        sys.exit(claims(explain="--explain" in sys.argv[2:]))
    sys.exit({"tdd": tdd, "voice": voice, "security": security,
              "redirects": redirects, "freshness": freshness}[mode]())

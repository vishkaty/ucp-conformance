#!/usr/bin/env python3
"""
signed_examples.py — read HTTP signature examples out of the vendored spec docs.

speclint's signature-example rule compares, for one shipped example, the headers
the example actually CARRIES against the components its own `Signature-Input`
covers. That is a two-sided, purely mechanical comparison: both sides live in the
same fenced block, so neither can be argued away as a reading of prose.

Parsing rules, each of which exists because the naive version failed toward GREEN
(a real violation it could not see) or toward a false clean:

* **Indented fences count.** MkDocs content tabs (`=== "Request"`) indent their
  fences four spaces, and the spec ships exactly that shape at
  `shopping/order/rest.md`. A column-0-only fence regex is blind to it. Blocks are
  dedented before their headers are read.
* **Header matching is case insensitive**, because HTTP header names are.
* **Components come only from inside the parens.** `Signature-Input` carries
  parameters after the list (`;created=...;keyid="..."`), and a quoted parameter
  value must never be mistaken for a covered component — otherwise
  `keyid="ucp-agent"` reads as coverage.
* **Elision is judged only inside the parens.** A `...` in a parameter (a
  truncated `created`, say) does not make the component list incomplete.
* **Every signature in the line is parsed.** `Signature-Input` may carry several
  labelled signatures; a header is covered when ANY of them covers it, since the
  message then authenticates on that signature.

A block whose component list is ELIDED (a literal `...` inside the parens) is
parsed with `elided=True` and must be excluded by callers: such a block
illustrates syntax and asserts no complete covered set, so judging its
completeness would be a false positive. `signatures.md` uses that convention
deliberately.

Pure stdlib, read-only. Returns plain dataclasses so callers stay trivially
testable against synthetic fixtures.
"""
from __future__ import annotations

import pathlib
import re
import textwrap
from dataclasses import dataclass

# Opening fence at any indentation, through to a closing fence at the same or
# lower indentation. DOTALL on the body; the lazy quantifier stops at the first
# closing fence.
_FENCE_RE = re.compile(r"(?m)^([ \t]*)```[a-zA-Z0-9]*[ \t]*\n(.*?)^[ \t]*```",
                       re.S)
_SIGINPUT_RE = re.compile(r"(?mi)^[ \t]*Signature-Input:[ \t]*(.+)$")
_HEADER_RE = re.compile(r"(?m)^[ \t]*([A-Za-z0-9-]+):[ \t]")
_REQUEST_LINE_RE = re.compile(r"(?m)^[ \t]*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S")
# Each labelled signature's component list: label=( ... )
_SIGLIST_RE = re.compile(r"=\s*\(([^)]*)\)")
_COMPONENT_RE = re.compile(r'"([^"]+)"')


@dataclass(frozen=True, order=True)
class SignedExample:
    """One fenced example block that carries a Signature-Input line."""
    source: str            # doc path, relative to the docs root passed in
    line: int              # 1-based line of the fence's first content line
    headers: frozenset     # lowercase header names the example carries
    covered: frozenset     # lowercase components covered by ANY signature
    is_request: bool       # True when the block opens with an HTTP request line
    elided: bool           # True when a component list contains a literal "..."


def _parse_block(text, source, offset):
    m = _SIGINPUT_RE.search(text)
    if not m:
        return None
    raw = m.group(1)

    # Only the parenthesised component lists are components. Parameters that
    # follow (";created=...", ';keyid="..."') are deliberately excluded.
    lists = _SIGLIST_RE.findall(raw)
    covered = frozenset(c.lower()
                        for lst in lists
                        for c in _COMPONENT_RE.findall(lst))
    # Elision is a property of a component LIST, not of the whole line.
    elided = any("..." in lst for lst in lists)
    # A Signature-Input with no parsable list at all is not judgeable either.
    if not lists:
        elided = True

    headers = frozenset(h.lower() for h in _HEADER_RE.findall(text))
    return SignedExample(
        source=source,
        line=offset,
        headers=headers,
        covered=covered,
        is_request=bool(_REQUEST_LINE_RE.search(text)),
        elided=elided,
    )


def signed_examples_in_doc(path, source=None):
    """Every SignedExample in one markdown file, in document order."""
    path = pathlib.Path(path)
    text = path.read_text(encoding="utf-8")
    source = source if source is not None else path.name
    out = []
    for m in _FENCE_RE.finditer(text):
        block = textwrap.dedent(m.group(2))
        if not _SIGINPUT_RE.search(block):
            continue
        offset = text[:m.start()].count("\n") + 2  # first line inside the fence
        parsed = _parse_block(block, source, offset)
        if parsed is not None:
            out.append(parsed)
    return out


def signed_examples_in_tree(docs_dir):
    """Every SignedExample under docs_dir, sorted by (source, line).

    `source` is the path relative to docs_dir's parent when that parent is a
    specification root, so findings cite the same repo-relative path a reader
    would use. Deterministic ordering keeps golden sets stable.
    """
    docs_dir = pathlib.Path(docs_dir)
    out = []
    for md in sorted(docs_dir.rglob("*.md")):
        rel = md.relative_to(docs_dir)
        # Cite the conventional repo-relative form when we were handed the
        # specification root, so goldens read like the upstream paths.
        source = (f"docs/specification/{rel.as_posix()}"
                  if docs_dir.name == "specification" else rel.as_posix())
        out.extend(signed_examples_in_doc(md, source=source))
    return sorted(out)

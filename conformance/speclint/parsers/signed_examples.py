#!/usr/bin/env python3
"""
signed_examples.py — read HTTP signature examples out of the vendored spec docs.

speclint's signature-example rule compares, for one shipped example, the headers
the example actually CARRIES against the components its own `Signature-Input`
covers. That is a two-sided, purely mechanical comparison: both sides live in the
same fenced block, so neither can be argued away as a reading of prose.

Only fenced code blocks containing a `Signature-Input:` line are considered. A
block whose covered set is ELIDED (contains a literal `...` inside the component
list) is parsed with `elided=True` and must be excluded by callers: such a block
is illustrating syntax, not asserting a complete covered set, so judging its
completeness would be a false positive. `docs/specification/signatures.md` around
the Signature-Agent parsing example is exactly that shape.

Pure stdlib, read-only. Returns plain dataclasses so callers stay trivially
testable against synthetic fixtures.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.S)
_SIGINPUT_RE = re.compile(r"(?m)^Signature-Input:\s*(.+)$")
_HEADER_RE = re.compile(r"(?m)^([A-Za-z0-9-]+):\s")
_COMPONENT_RE = re.compile(r'"([^"]+)"')
_REQUEST_LINE_RE = re.compile(r"(?m)^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S")


@dataclass(frozen=True, order=True)
class SignedExample:
    """One fenced example block that carries a Signature-Input line."""
    source: str            # doc path, relative to the docs root passed in
    line: int              # 1-based line of the fence's first content line
    headers: frozenset     # lowercase header names the example carries
    covered: frozenset     # lowercase components the Signature-Input covers
    is_request: bool       # True when the block opens with an HTTP request line
    elided: bool           # True when the covered set contains a literal "..."


def _parse_block(text, source, offset):
    m = _SIGINPUT_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    covered = frozenset(c.lower() for c in _COMPONENT_RE.findall(raw))
    # The component list is elided when "..." appears inside it, e.g.
    #   Signature-Input: sig1=("@method" "@authority" ...);...
    elided = "..." in raw
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
        block = m.group(1)
        if "Signature-Input:" not in block:
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

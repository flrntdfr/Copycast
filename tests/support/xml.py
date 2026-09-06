"""XML canonicalization for golden-file comparisons."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

_VOLATILE_TAGS = frozenset({"lastBuildDate", "pubDate"})
_WS = re.compile(r">\s+<")

PARSER = etree.XMLParser(
    remove_blank_text=True,
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
)


def parse(document: str | bytes) -> Any:
    if isinstance(document, str):
        document = document.encode("utf-8")
    return etree.fromstring(document, PARSER)


def canonicalize(
    document: str | bytes,
    *,
    strip_volatile: bool = False,
    pretty: bool = True,
) -> str:
    """Return the C14N 2.0 form of ``document`` as text.

    ``strip_volatile`` blanks ``lastBuildDate`` and ``pubDate`` text so a
    golden file survives re-rendering; ``pretty`` indents the output for
    readable diffs (indentation carries no meaning after canonicalization).
    """
    root = parse(document)
    _drop_non_elements(root)
    if strip_volatile:
        for element in root.iter():
            if isinstance(element.tag, str) and etree.QName(element).localname in _VOLATILE_TAGS:
                element.text = ""
    canonical: bytes = etree.tostring(root, method="c14n2", strip_text=True)
    if not pretty:
        return canonical.decode("utf-8")
    reparsed = etree.fromstring(canonical, PARSER)
    pretty_bytes: bytes = etree.tostring(reparsed, pretty_print=True, encoding="utf-8")
    return pretty_bytes.decode("utf-8")


def _drop_non_elements(root: Any) -> None:
    """Remove entity references, comments and processing instructions (tails preserved)."""
    for node in list(root.iter()):
        if isinstance(node.tag, str):
            continue
        parent = node.getparent()
        if parent is None:
            continue
        tail = node.tail or ""
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        parent.remove(node)


def assert_xml_equal(
    actual: str | bytes, expected: str | bytes, *, strip_volatile: bool = True
) -> None:
    left = canonicalize(actual, strip_volatile=strip_volatile)
    right = canonicalize(expected, strip_volatile=strip_volatile)
    assert left == right, f"XML differs:\n--- actual\n{left}\n--- expected\n{right}"


def compact(document: str) -> str:
    """Whitespace between tags removed; handy for inline expectations."""
    return _WS.sub("><", document.strip())

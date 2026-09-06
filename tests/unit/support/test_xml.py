from __future__ import annotations

import pytest

from tests.support.xml import assert_xml_equal, canonicalize, compact

A = """<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>T</title>
    <lastBuildDate>Mon, 01 Jan 2024 00:00:00 GMT</lastBuildDate>
    <item><itunes:duration>10</itunes:duration><title>a &amp; b</title></item>
  </channel>
</rss>
"""

B = (
    '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>'
    "<title>T</title><lastBuildDate>Tue, 02 Jan 2024 00:00:00 GMT</lastBuildDate>"
    "<item><itunes:duration>10</itunes:duration><title>a &#38; b</title></item></channel></rss>"
)


def test_canonicalization_ignores_whitespace_attribute_order_and_entity_spelling() -> None:
    assert canonicalize(A, strip_volatile=True) == canonicalize(B, strip_volatile=True)
    assert canonicalize(A) != canonicalize(B), "lastBuildDate differs without strip_volatile"
    assert_xml_equal(A, B)


def test_differences_are_reported() -> None:
    with pytest.raises(AssertionError, match="XML differs"):
        assert_xml_equal(A, B.replace("<title>T</title>", "<title>U</title>"))


def test_external_entities_are_not_resolved() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<rss><channel><title>&xxe;</title></channel></rss>"
    )
    text = canonicalize(evil)
    assert "root:" not in text


def test_compact() -> None:
    assert compact(" <a>\n  <b/>\n</a> ") == "<a><b/></a>"

from __future__ import annotations

from pathlib import Path

from copycast.adapters.sources.discovery import discover_feed_links, looks_like_html
from copycast.adapters.sources.http import MAX_HTML_BYTES


def test_two_feeds_page_lists_rss_links_in_order_deduped_and_resolved(fixtures_dir: Path) -> None:
    html = (fixtures_dir / "html/two_feeds.html").read_bytes()
    links = discover_feed_links(html, "https://show.example/about/")
    assert links == [
        "https://show.example/rss/itunes_podcast20.xml",
        "https://bonus.example/feed.xml?utm_source=site",
    ]


def test_attribute_forms_and_entities() -> None:
    html = """
    <LINK REL='alternate feed' TYPE="Application/RSS+XML; charset=utf-8" HREF=feed.xml>
    <link rel="alternate" type="application/rss+xml" href="/a?x=1&amp;y=2" title="x">
    <link href="/atom.xml" rel="alternate" type="application/atom+xml">
    <link rel="stylesheet" href="/s.css">
    <link rel="alternate" type="application/rss+xml">
    """
    assert discover_feed_links(html, "https://h.example/dir/page") == [
        "https://h.example/dir/feed.xml",
        "https://h.example/a?x=1&y=2",
    ]


def test_scan_stops_after_256_kib() -> None:
    late = b" " * MAX_HTML_BYTES + b'<link rel="alternate" type="application/rss+xml" href="/late">'
    assert discover_feed_links(late, "https://h.example") == []
    early = (
        b'<link rel="alternate" type="application/rss+xml" href="/early">' + b" " * MAX_HTML_BYTES
    )
    assert discover_feed_links(early, "https://h.example") == ["https://h.example/early"]


def test_looks_like_html() -> None:
    assert looks_like_html(b"", "text/html")
    assert looks_like_html(b"<!DOCTYPE html><html>", None)
    assert looks_like_html(b"  <html lang=en>", "application/octet-stream")
    assert not looks_like_html(b"<rss version='2.0'/>", "application/rss+xml")
    assert not looks_like_html(b"plain text", "text/plain")

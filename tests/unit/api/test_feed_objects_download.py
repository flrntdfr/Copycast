"""``download_feed_object``'s headers: a safe ``Content-Disposition`` and a sensible media type."""

from __future__ import annotations

from copycast.adapters.api.routes.feeds import content_disposition, object_media_type


def test_content_disposition_keeps_ascii_names_plain() -> None:
    assert content_disposition("0123456789abcdef.m4a") == (
        'attachment; filename="0123456789abcdef.m4a"'
    )


def test_content_disposition_strips_quotes_backslashes_and_control_characters() -> None:
    header = content_disposition('a"b\\c\r\nd.txt')
    assert "\r" not in header and "\n" not in header
    assert header.startswith('attachment; filename="abcd.txt"')
    assert "filename*=utf-8''a%22b%5Cc%0D%0Ad.txt" in header


def test_content_disposition_encodes_non_ascii_names() -> None:
    assert content_disposition("épisode.m4a") == (
        "attachment; filename=\"pisode.m4a\"; filename*=utf-8''%C3%A9pisode.m4a"
    )
    assert (
        content_disposition("日本")
        == "attachment; filename=\"download\"; filename*=utf-8''%E6%97%A5%E6%9C%AC"
    )


def test_object_media_type() -> None:
    assert object_media_type("x.m4a") == "audio/mp4"
    assert object_media_type("x.mp3") == "audio/mpeg"
    assert object_media_type("x.vtt") == "text/vtt"
    assert object_media_type("feed.json") == "application/json"
    assert object_media_type("LAYOUT_VERSION") == "application/octet-stream"

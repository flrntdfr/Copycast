from __future__ import annotations

import json
from pathlib import Path

import pytest

from copycast.adapters.assets.mirror import (
    ARTWORK_MAX_BYTES,
    CHAPTERS_MAX_BYTES,
    TRANSCRIPT_MAX_BYTES,
    AssetError,
    RemoteAsset,
    asset_basename,
    feed_artwork_asset,
    language_tag,
    mirror_asset,
    remote_assets_from_item,
    remote_attachments_from_description,
    sniff_image,
    transcript_format,
    write_atomic,
)
from copycast.adapters.sources.rss import item_xml, parse_feed
from copycast.domain.enums import AssetFormat, AssetKind, AssetProvenance
from tests.support.origin import Origin

MiB = 1024 * 1024


def test_limits_follow_the_plan() -> None:
    assert 10 * MiB == ARTWORK_MAX_BYTES
    assert 2 * MiB == CHAPTERS_MAX_BYTES
    assert 20 * MiB == TRANSCRIPT_MAX_BYTES


def test_basenames() -> None:
    assert asset_basename(AssetKind.artwork, ext="jpg") == "feed.artwork.jpg"
    assert asset_basename(AssetKind.artwork, ext=".JPG", item_id="abc") == "abc.artwork.jpg"
    assert asset_basename(AssetKind.chapters, ext="json", item_id="abc") == "abc.chapters.json"
    assert (
        asset_basename(AssetKind.transcript, ext="vtt", item_id="abc", language="en")
        == "abc.transcript.en.mirrored.vtt"
    )
    assert (
        asset_basename(
            AssetKind.transcript,
            ext="srt",
            item_id="abc",
            language=None,
            provenance=AssetProvenance.generated,
        )
        == "abc.transcript.und.generated.srt"
    )
    assert language_tag(" De-DE ") == "de-de" and language_tag("../x") == "x"


@pytest.mark.parametrize(
    ("mime", "url", "expected"),
    [
        ("text/vtt", None, AssetFormat.vtt),
        ("application/x-subrip", None, AssetFormat.srt),
        ("application/srt", None, AssetFormat.srt),
        ("application/json", None, AssetFormat.json),
        ("text/plain; charset=utf-8", None, AssetFormat.text),
        ("text/html", None, AssetFormat.html),
        (None, "https://x/t.SRT", AssetFormat.srt),
        ("application/octet-stream", "https://x/t.vtt?x=1", AssetFormat.vtt),
        ("application/pdf", "https://x/t.pdf", None),
        (None, None, None),
    ],
)
def test_transcript_format(mime: str | None, url: str | None, expected: AssetFormat | None) -> None:
    assert transcript_format(mime, url) == expected


def test_sniff_image() -> None:
    assert sniff_image(b"\xff\xd8\xff\xe0rest") == ("jpg", "image/jpeg")
    assert sniff_image(b"\x89PNG\r\n\x1a\n....") == ("png", "image/png")
    assert sniff_image(b"GIF89a....") == ("gif", "image/gif")
    assert sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("webp", "image/webp")
    assert sniff_image(b"<svg xmlns=") is None
    assert sniff_image(b"") is None


def test_remote_assets_from_the_rich_fixture(fixtures_dir: Path) -> None:
    parsed = parse_feed((fixtures_dir / "rss/itunes_podcast20.xml").read_bytes())
    five = remote_assets_from_item(item_xml(parsed.items["urn:fixture:episode:5"]), item_id="i5")
    kinds = [(a.kind, a.language, a.format) for a in five]
    assert kinds == [
        (AssetKind.artwork, None, None),
        (AssetKind.chapters, None, AssetFormat.json),
        (AssetKind.transcript, "en", AssetFormat.vtt),
        (AssetKind.transcript, "en", AssetFormat.srt),
        (AssetKind.transcript, "de", AssetFormat.vtt),
        (AssetKind.transcript, "de", AssetFormat.srt),
    ]
    assert five[0].url == "https://podcast.example/artwork/5.jpg" and five[0].item_id == "i5"
    assert five[1].url == "https://podcast.example/chapters/5.json"
    assert five[1].mime == "application/json+chapters"
    assert five[2].mime == "text/vtt" and five[2].url == "https://podcast.example/transcripts/5.vtt"
    four = remote_assets_from_item(
        parsed.items["urn:fixture:episode:4"], item_id="i4", include_artwork=False
    )
    assert [(a.kind, a.format) for a in four] == [(AssetKind.transcript, AssetFormat.json)]
    three = remote_assets_from_item(
        item_xml(parsed.items["https://podcast.example/episodes/3"]), item_id="i3"
    )
    assert three == []


def test_remote_assets_resolve_relative_urls_and_skip_unknown_formats() -> None:
    xml = (
        '<item xmlns:podcast="https://podcastindex.org/namespace/1.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        '<itunes:image href="art.png"/>'
        '<podcast:chapters url="/chapters.json"/>'
        '<podcast:transcript url="t.bin" type="application/x-unknown" language="en"/>'
        '<podcast:transcript url="t.vtt" type="text/vtt" language="en"/>'
        '<podcast:transcript url="t2.vtt" type="text/vtt" language="en"/>'
        "</item>"
    )
    assets = remote_assets_from_item(xml, item_id="x", base_url="https://h.example/show/feed.xml")
    assert [a.url for a in assets] == [
        "https://h.example/show/art.png",
        "https://h.example/chapters.json",
        "https://h.example/show/t.vtt",
    ]
    assert feed_artwork_asset("https://h/a.jpg") == RemoteAsset(
        AssetKind.artwork, "https://h/a.jpg"
    )


def test_mirror_artwork_sniffs_and_names_by_type(origin: Origin, tmp_path: Path) -> None:
    asset = RemoteAsset(AssetKind.artwork, origin.url_for("/media/tiny.jpg"), item_id="abc")
    mirrored = mirror_asset(asset, tmp_path / "assets")
    assert mirrored.basename == "abc.artwork.jpg"
    assert mirrored.local_path == "assets/abc.artwork.jpg"
    assert mirrored.path == tmp_path / "assets" / "abc.artwork.jpg"
    assert mirrored.mime == "image/jpeg" and mirrored.format is None
    assert mirrored.size_bytes == mirrored.path.stat().st_size > 0
    assert not (tmp_path / "assets" / "abc.artwork.jpg.part").exists()
    feed_art = mirror_asset(
        feed_artwork_asset(origin.url_for("/media/tiny.jpg")), tmp_path / "assets"
    )
    assert feed_art.basename == "feed.artwork.jpg" and feed_art.item_id is None


def test_mirror_artwork_rejects_non_images(origin: Origin, tmp_path: Path) -> None:
    asset = RemoteAsset(AssetKind.artwork, origin.url_for("/rss/atom.xml"), item_id="abc")
    with pytest.raises(AssetError, match="not an image"):
        mirror_asset(asset, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_mirror_chapters_validates_json(origin: Origin, tmp_path: Path) -> None:
    good = {"version": "1.2.0", "chapters": [{"startTime": 0, "title": "Intro"}]}
    origin.script("/chapters.json", body=json.dumps(good), content_type="application/json+chapters")
    asset = RemoteAsset(AssetKind.chapters, origin.url_for("/chapters.json"), item_id="abc")
    mirrored = mirror_asset(asset, tmp_path)
    assert mirrored.basename == "abc.chapters.json" and mirrored.format is AssetFormat.json
    assert mirrored.mime == "application/json+chapters"
    assert json.loads(mirrored.path.read_bytes()) == good
    origin.script("/bad.json", body="{not json", content_type="application/json")
    with pytest.raises(AssetError, match="not JSON"):
        mirror_asset(
            RemoteAsset(AssetKind.chapters, origin.url_for("/bad.json"), item_id="abc"), tmp_path
        )


def test_mirror_transcripts_by_declared_then_served_format(origin: Origin, tmp_path: Path) -> None:
    origin.script("/t.vtt", body="WEBVTT\n\n00:00.000 --> 00:01.000\nhi", content_type="text/vtt")
    asset = RemoteAsset(
        AssetKind.transcript,
        origin.url_for("/t.vtt"),
        item_id="abc",
        language="en",
        format=AssetFormat.vtt,
    )
    mirrored = mirror_asset(asset, tmp_path)
    assert mirrored.basename == "abc.transcript.en.mirrored.vtt"
    assert mirrored.mime == "text/vtt" and mirrored.language == "en"
    origin.script(
        "/t2", body="1\n00:00:00,000 --> 00:00:01,000\nhi", content_type="application/x-subrip"
    )
    served = mirror_asset(
        RemoteAsset(AssetKind.transcript, origin.url_for("/t2"), item_id="abc", language="de"),
        tmp_path,
    )
    assert served.basename == "abc.transcript.de.mirrored.srt" and served.format is AssetFormat.srt
    origin.script("/t3", body="??", content_type="application/x-unknown")
    with pytest.raises(AssetError, match="unknown transcript format"):
        mirror_asset(
            RemoteAsset(AssetKind.transcript, origin.url_for("/t3"), item_id="abc"), tmp_path
        )


def test_mirror_reports_limits_and_network_failures(origin: Origin, tmp_path: Path) -> None:
    origin.script("/big.json", body=b"[" + b"1," * (CHAPTERS_MAX_BYTES // 2) + b"1]")
    with pytest.raises(AssetError, match="exceeds"):
        mirror_asset(
            RemoteAsset(AssetKind.chapters, origin.url_for("/big.json"), item_id="a"), tmp_path
        )
    origin.script("/missing.jpg", status=404)
    with pytest.raises(AssetError, match="404"):
        mirror_asset(
            RemoteAsset(AssetKind.artwork, origin.url_for("/missing.jpg"), item_id="a"), tmp_path
        )
    origin.script("/empty.jpg", body=b"", content_type="image/jpeg")
    with pytest.raises(AssetError, match="empty"):
        mirror_asset(
            RemoteAsset(AssetKind.artwork, origin.url_for("/empty.jpg"), item_id="a"), tmp_path
        )


def test_write_atomic_leaves_no_part_file(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "file.bin"
    assert write_atomic(target, b"data") == target
    assert target.read_bytes() == b"data"
    assert sorted(p.name for p in target.parent.iterdir()) == ["file.bin"]
    blocked = tmp_path / "file.bin"
    blocked.write_bytes(b"")
    with pytest.raises(AssetError):
        write_atomic(blocked / "child", b"x")


def test_remote_attachments_from_show_notes() -> None:
    notes = (
        '<p>Pictures: <img src="https://cdn.example/a.jpg"> and <img src="/rel/b.png"></p>'
        '<p><a href="https://cdn.example/notes.pdf">PDF</a>, '
        '<a href="https://x.example/page">page</a>, '
        '<a href="https://cdn.example/a.jpg">the same picture</a>, '
        '<img src="data:image/png;base64,AAAA">, <a href="ftp://old.example/f.mp3">ftp</a></p>'
    )
    found = remote_attachments_from_description(
        notes, item_id="abc", base_url="https://podcast.example/feed.xml"
    )
    assert [a.url for a in found] == [
        "https://cdn.example/a.jpg",
        "https://podcast.example/rel/b.png",
        "https://cdn.example/notes.pdf",
    ]
    assert {a.kind for a in found} == {AssetKind.attachment}
    assert all(a.item_id == "abc" and a.slot and len(a.slot) == 12 for a in found)
    assert found[0].slot != found[1].slot
    assert remote_attachments_from_description("plain text, no tags", item_id="abc") == []
    assert remote_attachments_from_description(None, item_id="abc") == []
    many = "".join(f'<img src="https://cdn.example/{n}.png">' for n in range(30))
    assert len(remote_attachments_from_description(many, item_id="abc")) == 20
    assert (
        asset_basename(AssetKind.attachment, ext="PNG", item_id="abc", slot="0123456789ab")
        == "abc.attachment.0123456789ab.png"
    )


def test_mirror_attachments_by_sniff_type_or_extension(origin: Origin, tmp_path: Path) -> None:
    image = RemoteAsset(
        AssetKind.attachment, origin.url_for("/media/tiny.jpg"), item_id="abc", slot="aaaaaaaaaaaa"
    )
    mirrored = mirror_asset(image, tmp_path / "assets")
    assert mirrored.basename == "abc.attachment.aaaaaaaaaaaa.jpg"
    assert mirrored.mime == "image/jpeg" and mirrored.slot == "aaaaaaaaaaaa"
    audio = RemoteAsset(
        AssetKind.attachment, origin.url_for("/media/tiny.mp3"), item_id="abc", slot="bbbbbbbbbbbb"
    )
    assert mirror_asset(audio, tmp_path / "assets").basename == "abc.attachment.bbbbbbbbbbbb.mp3"
    page = RemoteAsset(
        AssetKind.attachment, origin.url_for("/rss/atom.xml"), item_id="abc", slot="cccccccccccc"
    )
    with pytest.raises(AssetError, match="not an image, PDF or audio"):
        mirror_asset(page, tmp_path / "assets")

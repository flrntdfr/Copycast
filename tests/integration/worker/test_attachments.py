"""Show notes: images and linked files are mirrored and the Mirror Feed points at the copies."""

from __future__ import annotations

from xml.sax.saxutils import escape

import pytest

from copycast.app import Container
from copycast.domain.enums import AssetKind, AssetState
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, uow

pytestmark = pytest.mark.integration


def _rss_with_notes(source: Source, name: str) -> str:
    base = source.base_url
    (source.root / "assets" / "notes.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
    notes = (
        f'<p>Look: <img src="{base}/media/tiny.jpg"></p>'
        f'<p><a href="{base}/assets/notes.pdf">Slides</a> and '
        f'<a href="{base}/assets/missing.png">a broken link</a></p>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>Notes</title><link>{base}/</link><description>d</description>"
        "<item><title>Illustrated</title>"
        f'<guid isPermaLink="false">urn:notes:1</guid>'
        f"<description>{escape(notes)}</description>"
        f'<enclosure url="{base}/media/tiny.mp3" length="4407" type="audio/mpeg"/>'
        "</item></channel></rss>"
    )
    target = source.root / "rss" / f"{name}.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return source.url_for(f"/rss/{name}.xml")


async def test_show_notes_attachments_are_mirrored_and_rewritten(
    container: Container, source: Source, runner: Runner
) -> None:
    url = _rss_with_notes(source, "notes")
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    async with uow(container) as unit:
        [item] = await unit.catalog.for_feed(mirror.id)
        attachments = [
            a for a in await unit.assets.for_item(item.id) if a.kind == AssetKind.attachment
        ]
    by_url = {a.remote_url: a for a in attachments}
    assert set(by_url) == {
        f"{source.base_url}/media/tiny.jpg",
        f"{source.base_url}/assets/notes.pdf",
        f"{source.base_url}/assets/missing.png",
    }
    picture = by_url[f"{source.base_url}/media/tiny.jpg"]
    assert picture.state == AssetState.archived and picture.mime == "image/jpeg"
    assert picture.local_path == f"assets/{item.id}.attachment.{picture.slot}.jpg"
    assets_dir = container.layout.assets_dir(mirror.id)
    assert (assets_dir / f"{item.id}.attachment.{picture.slot}.jpg").is_file()
    slides = by_url[f"{source.base_url}/assets/notes.pdf"]
    assert slides.state == AssetState.archived and slides.mime == "application/pdf"
    broken = by_url[f"{source.base_url}/assets/missing.png"]
    assert broken.state == AssetState.failed and broken.last_error

    rendered = await container.renderer.render(mirror.id)
    assert rendered is not None
    body = rendered.body.decode("utf-8")
    local = f"{container.settings.base_url}/feeds/{mirror.id}/assets/{item.id}.attachment."
    assert f"{local}{picture.slot}.jpg" in body and f"{local}{slides.slot}.pdf" in body
    assert f"{source.base_url}/media/tiny.jpg" not in body
    assert f"{source.base_url}/assets/missing.png" in body  # the broken link is left as it was

    # The API lists them, and the rebuild keeps them.
    read = await container.services.get_item(mirror.id, item.id)
    kinds = sorted(a.kind for a in read.assets)
    assert kinds.count(AssetKind.attachment) == 3

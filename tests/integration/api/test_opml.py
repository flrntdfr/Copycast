"""``GET /api/feeds.opml``: every feed, credentials included, for a podcast app."""

from __future__ import annotations

import httpx
import pytest
from lxml import etree

from tests.integration.api.conftest import Source, create_mirror
from tests.support.paths import api

pytestmark = pytest.mark.integration


async def test_opml_lists_every_feed(client: httpx.AsyncClient, source: Source) -> None:
    url = source.write_rss("opml", items=[1], artwork=False, title="Exported Show")
    mirror = await create_mirror(client, url)
    response = await client.get(api("/feeds.opml"))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/x-opml+xml")
    assert response.headers["content-disposition"] == 'attachment; filename="copycast.opml"'
    root = etree.fromstring(response.content)
    assert root.get("version") == "2.0" and root.findtext("head/title") == "Copycast"
    outlines = {o.get("text"): o for o in root.findall("body/outline")}
    # The Mirror and the default Inbox.
    assert "Exported Show" in outlines and "Copycast" in outlines
    show = outlines["Exported Show"]
    assert show.get("type") == "rss" and show.get("xmlUrl") == mirror["feed_url"]
    assert show.get("htmlUrl") == url

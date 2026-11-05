"""Probe, create and retarget refuse internal destinations with 422 destination-not-allowed.

The settings here exempt ``localhost`` by name only, so the suite's Origin is reachable as
``http://localhost:<port>`` and refused as ``http://127.0.0.1:<port>``: the same server, the
guard deciding by the configured ``[security] allow_private_hosts``.
"""

from __future__ import annotations

import httpx
import pytest

from copycast.settings import SecuritySettings, Settings
from tests.integration.api.conftest import Source, create_mirror
from tests.support.paths import api

pytestmark = pytest.mark.integration

PROBLEM = "application/problem+json"
REFUSED = "urn:copycast:problem:destination-not-allowed"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The suite's settings, with only the name ``localhost`` exempt from the fetch guard."""
    return settings.model_copy(
        update={"security": SecuritySettings(allow_private_hosts=["localhost"])}
    )


def by_name(url: str) -> str:
    return url.replace("://127.0.0.1:", "://localhost:", 1)


def refused(response: httpx.Response) -> dict[str, object]:
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM)
    body: dict[str, object] = response.json()
    assert body["type"] == REFUSED and body["title"] == "Destination not allowed"
    return body


async def test_probing_a_loopback_address_answers_422(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = source.write_rss("probe", items=[1], artwork=False)
    body = refused(await client.post(api("/probe"), json={"url": url}))
    detail = str(body["detail"])
    assert f"127.0.0.1:{source.origin.port} is not an allowed destination" in detail
    assert "non-public" in detail and "security.allow_private_hosts" in detail
    assert source.origin.requests == [], "the guard refused before connecting"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8333/copycast/",
        "http://[::ffff:127.0.0.1]:6379/",
        "http://0.0.0.0:5432/",
        "http://10.0.0.5/feed.xml",
        "http://100.64.0.1/feed.xml",
        "192.168.1.1/feed.xml",
        "ftp://example.com/feed.xml",
    ],
)
async def test_probing_internal_or_non_http_destinations_answers_422(
    client: httpx.AsyncClient, url: str
) -> None:
    refused(await client.post(api("/probe"), json={"url": url}))


async def test_an_exempt_name_probes_and_mirrors(client: httpx.AsyncClient, source: Source) -> None:
    url = by_name(source.write_rss("exempt", items=[1], artwork=False))
    probed = await client.post(api("/probe"), json={"url": url})
    assert probed.status_code == 200, probed.text
    mirror = await create_mirror(client, url, selection="1")
    assert mirror["source_url"] == url


async def test_a_redirect_to_a_refused_address_answers_422(
    client: httpx.AsyncClient, source: Source
) -> None:
    target = source.write_rss("hop", items=[1], artwork=False)
    source.origin.script("/moved", status=301, headers={"Location": target})
    url = by_name(source.url_for("/moved"))
    refused(await client.post(api("/probe"), json={"url": url}))
    assert source.origin.hits("/rss/hop.xml") == 0


async def test_creating_a_mirror_of_a_refused_source_answers_422(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = source.write_rss("create", items=[1], artwork=False)
    refused(
        await client.post(
            api("/mirrors"), json={"source_url": url, "backfill": {"mode": "selection"}}
        )
    )
    feeds = await client.get(api("/feeds"))
    assert all(f.get("source_url") != url for f in feeds.json()["feeds"])


async def test_retargeting_to_a_refused_source_answers_422_and_keeps_the_mirror(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = by_name(source.write_rss("before", items=[1], artwork=False))
    mirror = await create_mirror(client, url, selection="1")
    elsewhere = source.write_rss("after", items=[1], artwork=False)
    refused(await client.patch(api(f"/mirrors/{mirror['id']}"), json={"source_url": elsewhere}))
    kept = await client.patch(api(f"/mirrors/{mirror['id']}"), json={})
    assert kept.status_code == 200 and kept.json()["source_url"] == url

"""Mirrors through the API: candidates, duplicates, policies, selections, guards, tombstones."""

from __future__ import annotations

import httpx
import pytest

from copycast.domain.enums import JobKind
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror, items_of
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin
from tests.support.paths import api

pytestmark = pytest.mark.integration

PROBLEM = "application/problem+json"


def problem_type(response: httpx.Response) -> str:
    assert response.headers["content-type"].startswith(PROBLEM), response.text
    slug: str = response.json()["type"]
    return slug


async def test_create_mirror_populates_catalog_synchronously(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = source.write_rss("create", items=[3, 2, 1], artwork=False)
    response = await client.post(api("/mirrors"), json={"source_url": url})
    assert response.status_code == 201, response.text
    mirror = response.json()
    assert response.headers["location"].endswith(api(f"/feeds/{mirror['id']}"))
    assert mirror["kind"] == "mirror" and mirror["source_kind"] == "rss"
    assert mirror["feed_url"] == f"http://testserver/feeds/{mirror['id']}.xml"
    # No policy given: the operator's default, Automatic with a week of retention.
    assert mirror["follow"] is True and mirror["backfill"] == {
        "mode": "automatic",
        "latest_n": None,
        "retention_days": 7,
    }
    assert mirror["counts"]["wanted"] + mirror["counts"]["available"] == 3
    assert mirror["health"]["status"] == "never"
    items = await items_of(client, mirror["id"], sort="ordinal", order="asc")
    assert [i["source_number"] for i in items] == [1, 2, 3]
    assert [i["ordinal"] for i in items] == [1, 2, 3]
    listed = (await client.get(api("/mirrors"))).json()["feeds"]
    assert [f["id"] for f in listed] == [mirror["id"]]
    by_kind = (await client.get(api("/feeds"), params={"kind": "mirror"})).json()["feeds"]
    assert [f["id"] for f in by_kind] == [mirror["id"]]
    fetched = await client.get(api(f"/feeds/{mirror['id']}"))
    assert fetched.status_code == 200 and fetched.json()["id"] == mirror["id"]


async def test_duplicate_source_is_409_with_existing_feed_id(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = source.write_rss("dup", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    again = await client.post(
        api("/mirrors"), json={"source_url": f"{url}?utm_source=newsletter&fbclid=1"}
    )
    assert again.status_code == 409
    assert problem_type(again) == "urn:copycast:problem:feed-exists"
    assert again.json()["existing_feed_id"] == mirror["id"]


async def test_page_with_two_feeds_needs_a_candidate_token(
    client: httpx.AsyncClient, source: Source
) -> None:
    a = source.write_rss("page-a", items=[1], artwork=False, title="Feed A")
    b = source.write_rss("page-b", items=[1], artwork=False, title="Feed B")
    page = source.root / "page.html"
    page.write_text(
        "<html><head>"
        f'<link rel="alternate" type="application/rss+xml" title="A" href="{a}">'
        f'<link rel="alternate" type="application/rss+xml" title="B" href="{b}">'
        "</head><body>two feeds</body></html>",
        encoding="utf-8",
    )
    page_url = source.url_for("/page.html")
    probed = await client.post(api("/probe"), json={"url": page_url})
    assert probed.status_code == 200, probed.text
    candidates = probed.json()["candidates"]
    assert [c["title"] for c in candidates] == ["Feed A", "Feed B"]
    assert all(c["source_kind"] == "rss" and c["candidate_token"] for c in candidates)

    ambiguous = await client.post(api("/mirrors"), json={"source_url": page_url})
    assert ambiguous.status_code == 422
    assert problem_type(ambiguous) == "urn:copycast:problem:candidates-ambiguous"
    assert [c["title"] for c in ambiguous.json()["candidates"]] == ["Feed A", "Feed B"]

    chosen = await client.post(
        api("/mirrors"),
        json={"source_url": page_url, "candidate_token": candidates[1]["candidate_token"]},
    )
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["title"] == "Feed B" and chosen.json()["source_url"] == b


async def test_latest_n_archives_only_the_newest(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("latest", items=[5, 4, 3, 2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="latest", latest_n=2)
    assert mirror["backfill"] == {"mode": "latest", "latest_n": 2, "retention_days": None}
    archived = await archived_items(client, runner, mirror["id"])
    assert sorted(i["source_number"] for i in archived) == [4, 5]
    available = await items_of(client, mirror["id"], state="available")
    assert sorted(i["source_number"] for i in available) == [1, 2, 3]


async def test_selection_at_create_archives_exactly_the_selection(
    client: httpx.AsyncClient, origin: Origin, runner: Runner
) -> None:
    url = origin.url_for("/rss/large_2000.xml")
    mirror = await create_mirror(client, url, selection="1-42, 180")
    assert mirror["follow"] is False and mirror["backfill"]["mode"] == "selection"
    assert mirror["selection"] and mirror["selection"]["count"] == 43
    assert mirror["counts"]["wanted"] == 43
    wanted = await items_of(client, mirror["id"], state="wanted")
    assert sorted(i["source_number"] for i in wanted) == [*range(1, 43), 180]

    archived = await archived_items(client, runner, mirror["id"])
    assert len(archived) == 43
    # The next Refresh archives nothing new.
    refreshed = await client.post(api(f"/mirrors/{mirror['id']}/refresh"))
    assert refreshed.status_code == 202
    assert len(await archived_items(client, runner, mirror["id"])) == 43

    # Dry run answers 200 without queuing; the real thing 202 with jobs.
    dry = await client.post(
        api(f"/mirrors/{mirror['id']}/selections"), json={"selection": "43-44, 1", "dry_run": True}
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True and dry.json()["already_archived_count"] == 1
    assert len(dry.json()["resolved"]) == 3 and dry.json()["jobs"] == []
    assert len(await items_of(client, mirror["id"], state="wanted")) == 0
    real = await client.post(api(f"/mirrors/{mirror['id']}/selections"), json={"selection": "181"})
    assert real.status_code == 202, real.text
    assert len(real.json()["jobs"]) == 1 and real.json()["jobs"][0]["kind"] == "archive_item"
    assert len(await archived_items(client, runner, mirror["id"])) == 44

    unresolved = await client.post(
        api(f"/mirrors/{mirror['id']}/selections"), json={"selection": "5000", "dry_run": True}
    )
    assert unresolved.status_code == 200
    assert unresolved.json()["unresolved"] == ["5000"] and unresolved.json()["resolved"] == []
    invalid = await client.post(
        api(f"/mirrors/{mirror['id']}/selections"), json={"selection": "abc", "dry_run": True}
    )
    assert invalid.status_code == 422
    assert problem_type(invalid) == "urn:copycast:problem:invalid-selection"
    assert invalid.json()["unresolved"]


async def test_retarget_guards(
    client: httpx.AsyncClient, source: Source, engine: FakeEngine
) -> None:
    a_url = source.write_rss("ra", items=[1], artwork=False, title="A")
    b_url = source.write_rss("rb", items=[1], artwork=False, title="B")
    a = await create_mirror(client, a_url, selection="1")
    b = await create_mirror(client, b_url, selection="1")

    exists = await client.patch(api(f"/mirrors/{a['id']}"), json={"source_url": b_url})
    assert exists.status_code == 409
    assert exists.json()["existing_feed_id"] == b["id"]

    yt_url = "https://www.youtube.com/@retarget/videos"
    engine.script_listing(yt_url, listing(2, service="YouTube", title="Tube"))
    kind_change = await client.patch(api(f"/mirrors/{a['id']}"), json={"source_url": yt_url})
    assert kind_change.status_code == 422
    assert problem_type(kind_change) == "urn:copycast:problem:source-kind-change"

    c_url = source.write_rss("rc", items=[2, 1], artwork=False, title="C")
    moved = await client.patch(api(f"/mirrors/{a['id']}"), json={"source_url": c_url})
    assert moved.status_code == 200 and moved.json()["source_url"] == c_url


async def test_engine_options_are_validated_per_feed(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = source.write_rss("opts", items=[1], artwork=False)
    rejected = await client.post(
        api("/mirrors"), json={"source_url": url, "engine_options": {"outtmpl": "x", "proxy": "y"}}
    )
    assert rejected.status_code == 422
    assert problem_type(rejected) == "urn:copycast:problem:engine-option-rejected"
    assert sorted(rejected.json()["keys"]) == ["outtmpl", "proxy"]
    assert rejected.json()["scope"] == "feed"

    mirror = await create_mirror(client, url, engine_options={"subtitleslangs": ["de"]})
    assert mirror["engine_options"] == {"subtitleslangs": ["de"]}
    patched = await client.patch(
        api(f"/mirrors/{mirror['id']}"), json={"engine_options": {"format": "worst"}}
    )
    assert patched.status_code == 422
    assert patched.json()["keys"] == ["format"]


async def test_validation_problems(client: httpx.AsyncClient) -> None:
    empty = await client.post(api("/mirrors"), json={})
    assert empty.status_code == 422
    assert problem_type(empty) == "urn:copycast:problem:validation"
    assert any(e["loc"][-1] == "source_url" for e in empty.json()["errors"])
    latest = await client.post(
        api("/mirrors"),
        json={"source_url": "http://x.example/f.xml", "backfill": {"mode": "latest"}},
    )
    assert latest.status_code == 422
    unknown_field = await client.post(
        api("/mirrors"), json={"source_url": "http://x.example/f.xml", "bogus": 1}
    )
    assert unknown_field.status_code == 422


async def test_archive_item_and_delete_feed(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("archive", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, selection="1")
    available = await items_of(client, mirror["id"], state="available")
    assert len(available) == 1
    queued = await client.post(api(f"/feeds/{mirror['id']}/items/{available[0]['id']}/archive"))
    assert queued.status_code == 202, queued.text
    assert queued.json()["kind"] == JobKind.archive_item.value
    await runner.run_until_idle()
    archived = await items_of(client, mirror["id"], state="archived")
    assert len(archived) == 2
    again = await client.post(api(f"/feeds/{mirror['id']}/items/{archived[0]['id']}/archive"))
    assert again.status_code == 409

    gone = await client.delete(api(f"/feeds/{mirror['id']}"))
    assert gone.status_code == 204
    assert (await client.get(api(f"/feeds/{mirror['id']}"))).status_code == 404
    assert (await client.get(f"/feeds/{mirror['id']}.xml")).status_code == 404
    unknown = await client.delete(api(f"/feeds/{mirror['id']}"))
    assert unknown.status_code == 404 and problem_type(unknown) == "urn:copycast:problem:not-found"


async def test_default_inbox_cannot_be_deleted(client: httpx.AsyncClient) -> None:
    inbox = (await client.get(api("/inboxes"))).json()["feeds"][0]
    refused = await client.delete(api(f"/feeds/{inbox['id']}"))
    assert refused.status_code == 409
    assert problem_type(refused) == "urn:copycast:problem:conflict"

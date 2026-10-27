"""Uploads and Serial Inboxes through the API (ADR 0015)."""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from lxml import etree

from tests.support.audio import tagged_mp3
from tests.support.paths import FEED_URL, api

pytestmark = pytest.mark.integration

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"


async def upload(client: httpx.AsyncClient, name: str, body: bytes) -> dict[str, Any]:
    staged = await client.post(api("/uploads"), json={"filename": name})
    assert staged.status_code == 201, staged.text
    url = staged.json()["upload_url"]
    put = await client.put(urlsplit(url).path, content=body)
    assert put.status_code == 200, put.text
    received: dict[str, Any] = put.json()
    return received


async def test_upload_reads_tags_and_is_received_once(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    body = tagged_mp3(
        tmp_path, "03.mp3", title="Storm", album="Voyage", tracknumber="3/12", discnumber="2"
    )
    staged = (await client.post(api("/uploads"), json={"filename": "dir/03.mp3"})).json()
    assert staged["state"] == "pending" and staged["filename"] == "03.mp3"
    path = urlsplit(staged["upload_url"]).path
    assert (await client.put(path + "x", content=body)).status_code == 404

    received = (await client.put(path, content=body)).json()
    assert received["state"] == "received" and received["upload_url"] is None
    tags = received["tags"]
    assert (tags["title"], tags["album"], tags["track"], tags["track_total"], tags["disc"]) == (
        "Storm",
        "Voyage",
        3,
        12,
        2,
    )
    assert tags["duration_seconds"] == 1 and received["size_bytes"] == len(body)
    assert (await client.put(path, content=body)).status_code == 409
    fetched = await client.get(api(f"/uploads/{staged['id']}"))
    assert fetched.json()["tags"] == tags


async def test_upload_refuses_formats_and_non_audio(client: httpx.AsyncClient) -> None:
    assert (await client.post(api("/uploads"), json={"filename": "a.flac"})).status_code == 422
    staged = (await client.post(api("/uploads"), json={"filename": "a.m4b"})).json()
    bad = await client.put(urlsplit(staged["upload_url"]).path, content=b"not audio at all")
    assert bad.status_code == 422
    assert (await client.get(api("/uploads/nope"))).status_code == 404


async def test_serial_inbox_plays_in_the_given_order(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    names = ["c.mp3", "a.mp3", "b.mp3"]
    uploads = [await upload(client, n, tagged_mp3(tmp_path, n, title=n)) for n in names]
    pending = (await client.post(api("/uploads"), json={"filename": "z.mp3"})).json()
    refused = await client.post(
        api("/serial-inboxes"),
        json={"name": "Voyage", "episodes": [{"upload_id": pending["id"], "title": "z"}]},
    )
    assert refused.status_code == 409

    episodes = [
        {"upload_id": uploads[1]["id"], "title": "Prologue", "part": 1, "part_title": "Out"},
        {"upload_id": uploads[2]["id"], "title": "Storm", "part": 1, "chapter": 1},
        {
            "upload_id": uploads[0]["id"],
            "title": "Home",
            "part": 2,
            "chapter": 2,
            "author": "Narrator B",
            "description": "The end.",
        },
    ]
    created = await client.post(
        api("/serial-inboxes"),
        json={"name": "Voyage", "author": "A. Writer", "episodes": episodes},
    )
    assert created.status_code == 201, created.text
    inbox = created.json()
    assert inbox["kind"] == "inbox" and inbox["serial"] is True and inbox["episode_count"] == 3

    feed = await client.get(FEED_URL(inbox["id"]))
    assert feed.status_code == 200
    channel = etree.fromstring(feed.content).find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{ITUNES}}}type") == "serial"
    assert channel.findtext(f"{{{ITUNES}}}author") == "A. Writer"
    items = channel.findall("item")
    titles = [item.findtext("title") for item in items]
    assert titles == ["3 · Home", "2 · Storm", "1 · Prologue"]  # newest first
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in items]
    assert dates == sorted(dates, reverse=True)
    assert [item.findtext(f"{{{ITUNES}}}episode") for item in items] == ["3", "2", "1"]
    assert [item.findtext(f"{{{ITUNES}}}season") for item in items] == ["2", "1", "1"]
    last = items[0]
    assert last.findtext(f"{{{ITUNES}}}author") == "Narrator B"
    notes = last.findtext("description") or ""
    assert notes.startswith("Part 2 · Chapter 2 · Episode 3 of 3 · starts 0:02 into 0:03")
    assert notes.endswith("The end.")
    enclosure = last.find("enclosure")
    assert enclosure is not None and enclosure.get("type") == "audio/mpeg"
    media = await client.get(urlsplit(enclosure.get("url") or "").path)
    assert media.status_code == 200 and media.content.startswith(b"ID3")

    used = await client.get(api(f"/uploads/{uploads[0]['id']}"))
    assert used.status_code == 404

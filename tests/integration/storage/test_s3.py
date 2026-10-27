"""The Object store against a real S3 server: SeaweedFS (the compose stack's) in a testcontainer.

``COPYCAST_TEST_S3_URL`` (with ``..._ENDPOINT``, ``..._ACCESS_KEY_ID``,
``..._SECRET_ACCESS_KEY``) points the tier at an existing server instead; the
same variables run the whole suite against S3 (``tests/conftest.py``). Each
test gets a prefix of its own in one bucket.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from copycast.adapters.api.objects import ObjectResponse
from copycast.adapters.storage.layout import Layout
from copycast.adapters.storage.objects import MULTIPART_CHUNK_BYTES, ObjectStore, open_store
from copycast.settings import StorageSettings

pytestmark = pytest.mark.s3

SEAWEEDFS_IMAGE = (
    "chrislusf/seaweedfs:4.48"
    "@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"
)
"""Pinned like the compose stack's (``docker-compose.yml``)."""
BUCKET = "copycast-test"
S3_PORT = 8333


@pytest.fixture(scope="session")
def s3_server() -> Iterator[dict[str, Any]]:
    """``[storage]`` values for a bucket on a running S3 server (``url`` without a prefix)."""
    url = os.environ.get("COPYCAST_TEST_S3_URL")
    if url:
        yield {
            "url": url,
            "endpoint": os.environ.get("COPYCAST_TEST_S3_ENDPOINT", ""),
            "access_key_id": os.environ.get("COPYCAST_TEST_S3_ACCESS_KEY_ID"),
            "secret_access_key": os.environ.get("COPYCAST_TEST_S3_SECRET_ACCESS_KEY"),
        }
        return
    try:
        from testcontainers.core.container import DockerContainer
    except ImportError:  # pragma: no cover - dev group always has it
        pytest.skip("testcontainers not installed and COPYCAST_TEST_S3_URL unset")
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_command("server -s3 -dir=/data -master.volumeSizeLimitMB=64 -volume.max=16")
        .with_exposed_ports(S3_PORT)
    )
    try:
        container.start()
    except Exception as exc:  # docker missing or daemon down
        pytest.skip(f"no S3 server available: {exc}")
    try:
        endpoint = (
            f"http://{container.get_container_host_ip()}:{container.get_exposed_port(S3_PORT)}"
        )
        deadline = time.monotonic() + 60
        while True:  # SeaweedFS answers once its master, volume and filer are up
            try:
                if httpx.put(f"{endpoint}/{BUCKET}", timeout=2).status_code in (200, 409):
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("SeaweedFS never came up")
            time.sleep(0.5)
        yield {
            "url": f"s3://{BUCKET}",
            "endpoint": endpoint,
            "access_key_id": "copycast",
            "secret_access_key": "copycast",
        }
    finally:
        container.stop()


@pytest.fixture
def s3_store(s3_server: dict[str, Any]) -> ObjectStore:
    prefix = f"t{uuid.uuid4().hex[:12]}"
    return open_store(StorageSettings(**{**s3_server, "url": f"{s3_server['url']}/{prefix}"}))


async def test_objects_round_trip_on_s3(s3_store: ObjectStore, tmp_path: Path) -> None:
    store = s3_store
    store.ping()
    layout = Layout(store)
    assert layout.ensure_version() == "2" and layout.read_version() == "2"
    feed, item = "0123456789abcdef", "fedcba9876543210"
    store.put_bytes(layout.descriptor_key(feed), "{}", content_type="application/json")
    head = store.head(layout.descriptor_key(feed))
    assert head is not None and head.key == layout.descriptor_key(feed), "prefix not leaked"
    assert layout.feed_ids() == [feed]

    # A file bigger than one part goes up as a multipart upload and reads back whole.
    big = tmp_path / "big.m4a"
    payload = bytes(range(256)) * ((MULTIPART_CHUNK_BYTES + 4096) // 256)
    big.write_bytes(payload)
    key = layout.media_key(feed, item, "m4a")
    assert store.put_file(key, big, content_type="audio/mp4") == len(payload)
    found = layout.find_media(feed, item)
    assert found is not None and found.key == key and found.size == len(payload)
    assert found.etag and found.etag.endswith('-2"'), "an S3 multipart ETag"
    middle = b"".join(
        [c async for c in store.stream(key, start=1000, end=5000, if_match=found.etag)]
    )
    assert middle == payload[1000:5000]
    whole = b"".join([c async for c in store.stream(key)])
    assert whole == payload

    # Replaced behind a reader's back: the pinned read reads as gone, never spliced.
    store.put_bytes(key, b"replaced")
    with pytest.raises(FileNotFoundError):
        _ = [c async for c in store.stream(key, if_match=found.etag)]

    assert layout.remove_feed(feed) and layout.feed_ids() == []
    store.delete_prefix("")
    assert store.list("") == []


async def test_the_proxy_streams_ranges_from_s3(s3_store: ObjectStore) -> None:
    store = s3_store
    store.put_bytes("media/x.mp3", b"0123456789", content_type="audio/mpeg")
    obj = store.head("media/x.mp3")
    assert obj is not None
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:  # pragma: no cover - never awaited
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=3-5")]}
    response = ObjectResponse(store, obj, media_type="audio/mpeg")
    await response(scope, receive, send)  # type: ignore[arg-type]
    start = sent[0]
    assert start["status"] == 206
    headers = dict(start["headers"])
    assert headers[b"content-range"] == b"bytes 3-5/10" and headers[b"content-length"] == b"3"
    assert b"".join(m.get("body", b"") for m in sent[1:]) == b"345"
    store.delete_prefix("")

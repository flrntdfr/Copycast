"""The Object store against a real S3 server: SeaweedFS (the compose stack's) in a testcontainer.

The container runs the compose stack's own entrypoint (``deploy/compose/seaweedfs``), so
the keys are checked, the bucket is created by its bootstrap, and the tests below can check
what it leaves on the network. ``COPYCAST_TEST_S3_URL`` (with ``..._ENDPOINT``,
``..._ACCESS_KEY_ID``, ``..._SECRET_ACCESS_KEY``) points the tier at an existing server
instead (the SeaweedFS checks then skip); the same variables run the whole suite against S3
(``tests/conftest.py``). Each test gets a prefix of its own in one bucket.
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
from copycast.adapters.storage.objects import (
    MULTIPART_CHUNK_BYTES,
    ObjectStore,
    StorageUnavailable,
    open_store,
)
from copycast.settings import StorageSettings

pytestmark = pytest.mark.s3

SEAWEEDFS_IMAGE = (
    "chrislusf/seaweedfs:4.48"
    "@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"
)
"""Pinned like the compose stack's (``docker-compose.yml``)."""
ENTRYPOINT = Path(__file__).parents[3] / "deploy" / "compose" / "seaweedfs" / "entrypoint.sh"
BUCKET = "copycast-test"
ACCESS_KEY, SECRET_KEY = "copycast", "copycast-test-secret"
S3_PORT = 8333
INTERNAL_PORTS = {
    9333: "master",
    19333: "master gRPC",
    8080: "volume server",
    18080: "volume server gRPC",
    8888: "filer",
    18888: "filer gRPC",
}
"""What SeaweedFS serves without a credential: loopback only."""
NETWORK_PORTS = {S3_PORT, 18333}
"""The S3 gateway and its gRPC port, which share one bind address (see the entrypoint)."""


@pytest.fixture(scope="session")
def seaweedfs() -> Iterator[Any]:
    """The SeaweedFS testcontainer, started by the compose entrypoint; skips without Docker."""
    if os.environ.get("COPYCAST_TEST_S3_URL"):
        pytest.skip("COPYCAST_TEST_S3_URL names an S3 server of its own")
    try:
        from testcontainers.core.container import DockerContainer
    except ImportError:  # pragma: no cover - dev group always has it
        pytest.skip("testcontainers not installed and COPYCAST_TEST_S3_URL unset")
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_copy_into_container(ENTRYPOINT, "/copycast/entrypoint.sh", 0o755)
        .with_kwargs(entrypoint=["/bin/sh", "/copycast/entrypoint.sh"])
        .with_envs(
            COPYCAST_S3_ACCESS_KEY=ACCESS_KEY,
            COPYCAST_S3_SECRET_KEY=SECRET_KEY,
            COPYCAST_S3_BUCKET=BUCKET,
            COPYCAST_S3_VOLUME_MAX="16",
            COPYCAST_S3_VOLUME_SIZE_MB="64",
        )
        .with_exposed_ports(S3_PORT)
    )
    try:
        container.start()
    except Exception as exc:  # docker missing or daemon down
        pytest.skip(f"no S3 server available: {exc}")
    try:
        deadline = time.monotonic() + 90
        # Ready once the bootstrap created the bucket, the compose healthcheck's own test.
        while container.exec(["test", "-f", "/tmp/ready"]).exit_code != 0:
            if time.monotonic() > deadline:
                logs = b"".join(container.get_logs()).decode(errors="replace")[-4000:]
                pytest.fail(f"SeaweedFS never came up:\n{logs}")
            time.sleep(0.5)
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def s3_server(request: pytest.FixtureRequest) -> Iterator[dict[str, Any]]:
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
    container = request.getfixturevalue("seaweedfs")
    yield {
        "url": f"s3://{BUCKET}",
        "endpoint": _endpoint(container),
        "access_key_id": ACCESS_KEY,
        "secret_access_key": SECRET_KEY,
    }


def _endpoint(container: Any) -> str:
    return f"http://{container.get_container_host_ip()}:{container.get_exposed_port(S3_PORT)}"


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


# What the entrypoint leaves on the network (SEC-2): the master, volume server and filer
# answer anyone without a credential (the filer lists and serves every object), so only the
# S3 gateway may listen beyond loopback.


def _listeners(container: Any) -> set[tuple[str, int]]:
    """``(address, port)`` of every TCP socket listening in the container."""
    result = container.exec(["netstat", "-tln"])
    assert result.exit_code == 0, result.output
    found: set[tuple[str, int]] = set()
    for line in result.output.decode().splitlines():
        fields = line.split()
        if fields and fields[0] in ("tcp", "tcp6") and len(fields) >= 4:
            address, _, port = fields[3].rpartition(":")
            found.add((address, int(port)))
    return found


def test_only_the_s3_gateway_listens_beyond_loopback(seaweedfs: Any) -> None:
    listeners = _listeners(seaweedfs)
    for port, name in INTERNAL_PORTS.items():
        assert ("127.0.0.1", port) in listeners, f"the {name} is not up on loopback"
    # 127.0.0.11 is Docker's resolver on a user network.
    beyond = {(a, port) for a, port in listeners if not a.startswith("127.") and a != "::1"}
    assert {port for _, port in beyond} == NETWORK_PORTS, beyond


def test_internal_ports_refuse_the_container_network(seaweedfs: Any) -> None:
    # A TCP connect to the container's own network address meets exactly the sockets another
    # container would; the same connect to loopback shows the probe itself works.
    probe = (
        'ip=$(hostname -i | cut -d" " -f1); for port in "$@"; do '
        'nc -z -w 2 "$ip" "$port" && echo "open $port"; '
        'nc -z -w 2 127.0.0.1 "$port" && echo "loopback $port"; '
        "done; true"
    )
    ports = [str(port) for port in INTERNAL_PORTS]
    result = seaweedfs.exec(["sh", "-c", probe, "probe", *ports])
    lines = set(result.output.decode().splitlines())
    assert not {line for line in lines if line.startswith("open ")}, lines
    assert {f"loopback {port}" for port in ports} <= lines, lines
    gateway = seaweedfs.exec(["sh", "-c", f'nc -z -w 2 "$(hostname -i | cut -d" " -f1)" {S3_PORT}'])
    assert gateway.exit_code == 0, "the S3 gateway is not on the container network"


def test_the_gateway_refuses_anonymous_requests(seaweedfs: Any) -> None:
    endpoint = _endpoint(seaweedfs)
    assert httpx.get(f"{endpoint}/", timeout=5).status_code == 403
    assert httpx.get(f"{endpoint}/{BUCKET}", timeout=5).status_code == 403
    assert httpx.put(f"{endpoint}/{BUCKET}/x", content=b"x", timeout=5).status_code == 403
    assert httpx.put(f"{endpoint}/another-bucket", timeout=5).status_code == 403


def test_the_gateway_refuses_a_wrong_secret(seaweedfs: Any) -> None:
    store = open_store(
        StorageSettings(
            url=f"s3://{BUCKET}/denied",
            endpoint=_endpoint(seaweedfs),
            access_key_id=ACCESS_KEY,
            secret_access_key="not-the-secret",
        )
    )
    with pytest.raises(StorageUnavailable) as refused:
        store.ping()
    assert "403" in str(refused.value) or "SignatureDoesNotMatch" in str(refused.value)


def test_the_bootstrap_created_the_bucket(s3_store: ObjectStore, seaweedfs: Any) -> None:
    result = seaweedfs.exec(["sh", "-c", "echo s3.bucket.list | weed shell -master=127.0.0.1:9333"])
    assert result.exit_code == 0 and BUCKET in result.output.decode()
    s3_store.ping()

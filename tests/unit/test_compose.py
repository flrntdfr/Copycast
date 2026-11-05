"""The compose files keep SeaweedFS off every network but the one the key holders share (SEC-2).

The entrypoint is what keeps SeaweedFS's unauthenticated ports (master, volume, filer, the S3
gateway's gRPC) on loopback, behind a relay that carries S3 over HTTP only; the network is
defence in depth on top. ``tests/integration/storage/test_s3.py`` checks the sockets themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[2]


def _compose(path: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((ROOT / path).read_text())
    return loaded


def _networks(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks", ["default"])
    return set(networks if isinstance(networks, list) else networks.keys())


def test_seaweedfs_sits_alone_with_the_key_holders() -> None:
    compose = _compose("docker-compose.yml")
    services = compose["services"]
    seaweedfs = services["seaweedfs"]
    assert "ports" not in seaweedfs and "network_mode" not in seaweedfs
    assert _networks(seaweedfs) == {"objects"}
    assert compose["networks"]["objects"] == {"internal": True}
    # The api runs in the tailscale sidecar's namespace under the tailscale profile.
    assert services["api"]["network_mode"] == "service:tailscale"
    for name in ("tailscale", "api-direct", "worker"):
        assert _networks(services[name]) == {"default", "objects"}, name
    for name in ("postgres", "valkey"):
        assert "objects" not in _networks(services[name]), name


def test_the_ci_overlay_adds_nothing_to_the_objects_network() -> None:
    services = _compose("docker-compose.ci.yml")["services"]
    assert "seaweedfs" not in services
    for name, service in services.items():
        assert "objects" not in _networks(service) or name in ("api-direct", "worker"), name


def test_the_development_store_publishes_the_gateway_on_loopback_only() -> None:
    seaweedfs = _compose("deploy/compose/docker-compose.dev.yml")["services"]["seaweedfs"]
    assert seaweedfs["ports"] and all(
        port.startswith("127.0.0.1:") and port.endswith(":8333") for port in seaweedfs["ports"]
    )


def test_the_entrypoint_binds_nothing_but_the_relay_beyond_loopback() -> None:
    entrypoint = (ROOT / "deploy/compose/seaweedfs/entrypoint.sh").read_text()
    assert "0.0.0.0" not in entrypoint
    for flag in ("-ip=127.0.0.1", "-ip.bind=127.0.0.1", "-s3.ip.bind=127.0.0.1"):
        assert flag in entrypoint, flag
    assert "nc -lk -p 8333 -e nc 127.0.0.1 8334" in entrypoint

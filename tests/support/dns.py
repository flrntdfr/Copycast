"""A deterministic stand-in for DNS, so the fetch guard never asks the real resolver.

Address literals (and the shorthands ``getaddrinfo`` reads as numbers, ``127.1``)
resolve to themselves, ``localhost`` to the loopback addresses, and every other
name to nothing, as the reserved ``.example``, ``.test`` and ``.invalid`` names
the suite uses do in real DNS. A resolver that hijacks unknown names to a private
sinkhole, a slow one or none at all cannot change what a test sees, and no test
connects to a public address by accident. A test that needs a name to resolve
passes its own resolver.
"""

from __future__ import annotations

import socket
from ipaddress import ip_address
from typing import Final

from copycast.domain.destinations import IPAddress

LOOPBACK: Final = (ip_address("127.0.0.1"), ip_address("::1"))


def fake_resolve(host: str, port: int) -> list[IPAddress]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST)
    except socket.gaierror:
        pass
    else:
        return list(dict.fromkeys(ip_address(str(info[4][0])) for info in infos))
    name = host.lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return list(LOOPBACK)
    raise socket.gaierror(socket.EAI_NONAME, f"{host} does not resolve (fake DNS)")


__all__ = ["LOOPBACK", "fake_resolve"]

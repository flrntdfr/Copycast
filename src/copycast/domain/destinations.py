"""Which network addresses a server-side fetch may reach: the rules of the SSRF guard.

Whoever adds a Source makes the server fetch URLs of their choosing, and inside a
deployment those URLs could name the Object store, the Feed cache, Postgres or a
cloud metadata service. :func:`blocked_reason` refuses every address that is not
plainly on the public internet: loopback, private (RFC 1918), shared (CGNAT
``100.64/10``), link-local, unspecified (``0/8``, ``::``), multicast, reserved and
broadcast, unique-local ``fc00::/7``, site-local ``fec0::/10``, 6to4, and whatever
else ``ipaddress`` does not call globally reachable. An IPv6 address that carries an
IPv4 one (IPv4-mapped ``::ffff:0:0/96``, IPv4-compatible ``::/96``, NAT64
``64:ff9b::/96``) is judged by the address it carries.

A :class:`DestinationPolicy` adds the operator's exemptions
(``[security] allow_private_hosts``): hostnames, matched exactly, and networks.
Resolving names and connecting is the adapters' job; this module only judges.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_network
from typing import Final

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network

_V4_BLOCKED: Final[tuple[tuple[IPv4Network, str], ...]] = (
    (IPv4Network("0.0.0.0/8"), "unspecified"),
    (IPv4Network("10.0.0.0/8"), "private"),
    (IPv4Network("100.64.0.0/10"), "shared (CGNAT)"),
    (IPv4Network("127.0.0.0/8"), "loopback"),
    (IPv4Network("169.254.0.0/16"), "link-local"),
    (IPv4Network("172.16.0.0/12"), "private"),
    (IPv4Network("192.0.0.0/24"), "reserved"),
    (IPv4Network("192.168.0.0/16"), "private"),
    (IPv4Network("198.18.0.0/15"), "reserved"),
    (IPv4Network("224.0.0.0/4"), "multicast"),
    (IPv4Network("255.255.255.255/32"), "broadcast"),
    (IPv4Network("240.0.0.0/4"), "reserved"),
)

_V6_BLOCKED: Final[tuple[tuple[IPv6Network, str], ...]] = (
    (IPv6Network("::/128"), "unspecified"),
    (IPv6Network("::1/128"), "loopback"),
    (IPv6Network("fe80::/10"), "link-local"),
    (IPv6Network("fec0::/10"), "site-local"),
    (IPv6Network("fc00::/7"), "unique-local"),
    (IPv6Network("ff00::/8"), "multicast"),
    (IPv6Network("64:ff9b:1::/48"), "NAT64 local-use"),
    (IPv6Network("100::/64"), "discard-only"),
    (IPv6Network("2002::/16"), "6to4"),
)

NAT64_WELL_KNOWN: Final = IPv6Network("64:ff9b::/96")
IPV4_COMPATIBLE: Final = IPv6Network("::/96")

_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_HOSTNAME_RE: Final = re.compile(rf"(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*")


def embedded_ipv4(address: IPv6Address) -> tuple[IPv4Address, str] | None:
    """The IPv4 address an IPv6 one carries, with the form's name; None when it carries none."""
    mapped = address.ipv4_mapped
    if mapped is not None:
        return mapped, "IPv4-mapped"
    if address in NAT64_WELL_KNOWN:
        return IPv4Address(int(address) & 0xFFFFFFFF), "NAT64"
    if address in IPV4_COMPATIBLE and int(address) > 1:
        return IPv4Address(int(address) & 0xFFFFFFFF), "IPv4-compatible"
    return None


def blocked_reason(address: IPAddress) -> str | None:
    """Why a fetch must not reach ``address`` ("loopback", "private"...); None when it may."""
    if isinstance(address, IPv6Address):
        if address.scope_id:
            address = IPv6Address(address.compressed.split("%", 1)[0])
        carried = embedded_ipv4(address)
        if carried is not None:
            inner, form = carried
            reason = blocked_reason(inner)
            if reason is not None:
                return f"{reason} ({form})"
            # A public IPv4 address reached through NAT64 or the dual-stack mapping is
            # public; the IPv4-compatible form is deprecated and never routed.
            return "reserved (IPv4-compatible)" if form == "IPv4-compatible" else None
        for network, reason in _V6_BLOCKED:
            if address in network:
                return reason
    else:
        for network, reason in _V4_BLOCKED:
            if address in network:
                return reason
    # The tables name loopback, link-local, multicast and unspecified for both families;
    # the IANA special-purpose registries (as ``ipaddress`` knows them) catch the rest.
    if address.is_private:
        return "private"
    if address.is_reserved:
        return "reserved"
    if not address.is_global:  # pragma: no cover - every such address is private on 3.13
        return "non-public"
    return None


def normalize_host(host: str) -> str:
    """``host`` as an allowlist compares it: lowercase, no brackets or trailing dot, IDNA."""
    value = host.strip().lower().removeprefix("[").removesuffix("]").rstrip(".")
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError:
        return value


@dataclass(frozen=True, slots=True)
class DestinationPolicy:
    """The guard's rules plus the operator's exemptions (``[security] allow_private_hosts``).

    A host in ``allowed_hosts`` may resolve to any address; an address inside one of
    ``allowed_networks`` is accepted whatever name led to it. The default policy
    exempts nothing.
    """

    allowed_hosts: frozenset[str] = frozenset()
    allowed_networks: tuple[IPNetwork, ...] = ()

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> DestinationPolicy:
        """Hostnames and CIDRs (a bare address is a /32 or /128); ValueError names a bad entry."""
        hosts: set[str] = set()
        networks: list[IPNetwork] = []
        for raw in entries:
            entry = raw.strip()
            if not entry:
                continue
            try:
                networks.append(ip_network(entry.removeprefix("[").removesuffix("]"), strict=False))
                continue
            except ValueError:
                pass
            host = normalize_host(entry)
            if not _HOSTNAME_RE.fullmatch(host):
                raise ValueError(f"{raw!r} is neither a hostname nor an IP network")
            hosts.add(host)
        return cls(frozenset(hosts), tuple(networks))

    def allows_host(self, host: str) -> bool:
        return bool(self.allowed_hosts) and normalize_host(host) in self.allowed_hosts

    def refusal(self, address: IPAddress) -> str | None:
        """Why ``address`` is refused under this policy; None when a fetch may connect to it."""
        reason = blocked_reason(address)
        if reason is None or self._exempt(address):
            return None
        return reason

    def _exempt(self, address: IPAddress) -> bool:
        candidates: list[IPAddress] = [address]
        if isinstance(address, IPv6Address):
            if address.scope_id:
                address = IPv6Address(address.compressed.split("%", 1)[0])
                candidates.append(address)
            carried = embedded_ipv4(address)
            if carried is not None:
                candidates.append(carried[0])
        return any(
            candidate.version == network.version and candidate in network
            for candidate in candidates
            for network in self.allowed_networks
        )


__all__ = [
    "DestinationPolicy",
    "IPAddress",
    "IPNetwork",
    "blocked_reason",
    "embedded_ipv4",
    "normalize_host",
]

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address

import pytest

from copycast.domain.destinations import (
    DestinationPolicy,
    blocked_reason,
    embedded_ipv4,
    normalize_host,
)


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("0.0.0.0", "unspecified"),
        ("0.1.2.3", "unspecified"),
        ("10.0.0.1", "private"),
        ("10.255.255.255", "private"),
        ("100.64.0.1", "shared (CGNAT)"),
        ("100.127.255.254", "shared (CGNAT)"),
        ("127.0.0.1", "loopback"),
        ("127.255.0.9", "loopback"),
        ("169.254.169.254", "link-local"),
        ("172.16.0.1", "private"),
        ("172.31.255.255", "private"),
        ("192.0.0.8", "reserved"),
        ("192.0.2.10", "private"),
        ("192.168.1.1", "private"),
        ("198.18.0.1", "reserved"),
        ("198.51.100.7", "private"),
        ("203.0.113.9", "private"),
        ("224.0.0.1", "multicast"),
        ("239.255.255.250", "multicast"),
        ("240.0.0.1", "reserved"),
        ("255.255.255.255", "broadcast"),
        ("::", "unspecified"),
        ("::1", "loopback"),
        ("fe80::1", "link-local"),
        ("fe80::1%1", "link-local"),
        ("febf::1", "link-local"),
        ("fec0::1", "site-local"),
        ("fc00::1", "unique-local"),
        ("fd12:3456::1", "unique-local"),
        ("ff02::1", "multicast"),
        ("64:ff9b:1::a", "NAT64 local-use"),
        ("100::1", "discard-only"),
        ("2002:c0a8:101::1", "6to4"),
        ("2001:db8::1", "private"),
        ("2001::1", "private"),
        ("3fff::1", "private"),
        ("4000::1", "reserved"),
    ],
)
def test_internal_addresses_are_refused(address: str, reason: str) -> None:
    assert blocked_reason(ip_address(address)) == reason


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("::ffff:127.0.0.1", "loopback (IPv4-mapped)"),
        ("::ffff:10.1.2.3", "private (IPv4-mapped)"),
        ("::ffff:169.254.169.254", "link-local (IPv4-mapped)"),
        ("::ffff:100.64.0.1", "shared (CGNAT) (IPv4-mapped)"),
        ("::ffff:0.0.0.0", "unspecified (IPv4-mapped)"),
        ("::127.0.0.1", "loopback (IPv4-compatible)"),
        ("::10.0.0.1", "private (IPv4-compatible)"),
        ("::8.8.8.8", "reserved (IPv4-compatible)"),
        ("64:ff9b::7f00:1", "loopback (NAT64)"),
        ("64:ff9b::a9fe:a9fe", "link-local (NAT64)"),
        ("64:ff9b::c0a8:101", "private (NAT64)"),
    ],
)
def test_ipv6_forms_carrying_an_internal_ipv4_address_are_refused(
    address: str, reason: str
) -> None:
    assert blocked_reason(ip_address(address)) == reason


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
        "151.101.1.69",
        "100.63.255.255",
        "100.128.0.1",
        "172.32.0.1",
        "2606:4700:4700::1111",
        "2001:4860:4860::8888",
        "2a00:1450:4007:80e::200e",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
    ],
)
def test_public_addresses_pass(address: str) -> None:
    assert blocked_reason(ip_address(address)) is None


def test_embedded_ipv4_names_the_form() -> None:
    assert embedded_ipv4(IPv6Address("::ffff:1.2.3.4")) == (IPv4Address("1.2.3.4"), "IPv4-mapped")
    assert embedded_ipv4(IPv6Address("64:ff9b::102:304")) == (IPv4Address("1.2.3.4"), "NAT64")
    assert embedded_ipv4(IPv6Address("::1.2.3.4")) == (IPv4Address("1.2.3.4"), "IPv4-compatible")
    assert embedded_ipv4(IPv6Address("::1")) is None
    assert embedded_ipv4(IPv6Address("2606:4700::1")) is None


def test_normalize_host() -> None:
    assert normalize_host(" Fixtures. ") == "fixtures"
    assert normalize_host("[::1]") == "::1"
    assert normalize_host("Bücher.example") == "xn--bcher-kva.example"
    too_long = "a" * 64 + ".example"
    assert normalize_host(too_long) == too_long, "left as is when IDNA refuses it"


def test_the_default_policy_exempts_nothing() -> None:
    policy = DestinationPolicy()
    assert policy.refusal(ip_address("127.0.0.1")) == "loopback"
    assert policy.refusal(ip_address("8.8.8.8")) is None
    assert not policy.allows_host("localhost")


def test_policy_entries_are_hostnames_or_networks() -> None:
    policy = DestinationPolicy.from_entries(
        ["fixtures", " NAS.lan. ", "", "192.168.1.0/24", "10.0.0.5", "[fd00::1]", "my_service"]
    )
    assert policy.allowed_hosts == frozenset({"fixtures", "nas.lan", "my_service"})
    assert [str(n) for n in policy.allowed_networks] == [
        "192.168.1.0/24",
        "10.0.0.5/32",
        "fd00::1/128",
    ]
    assert policy.allows_host("FIXTURES")
    assert policy.allows_host("nas.lan.")
    assert not policy.allows_host("fixtures.evil.example")
    assert not policy.allows_host("evil-fixtures")


@pytest.mark.parametrize("entry", ["not a host", "http://fixtures", "a/b", "-dash.example", "*"])
def test_policy_refuses_malformed_entries(entry: str) -> None:
    with pytest.raises(ValueError, match="neither a hostname nor an IP network"):
        DestinationPolicy.from_entries([entry])


def test_networks_exempt_their_addresses_only() -> None:
    policy = DestinationPolicy.from_entries(["192.168.1.0/24", "fd00::/8"])
    assert policy.refusal(ip_address("192.168.1.20")) is None
    assert policy.refusal(ip_address("192.168.2.20")) == "private"
    assert policy.refusal(ip_address("127.0.0.1")) == "loopback"
    assert policy.refusal(ip_address("fd00::5")) is None
    assert policy.refusal(ip_address("fc00::5")) == "unique-local"


def test_an_exempt_ipv4_network_covers_its_ipv6_forms() -> None:
    policy = DestinationPolicy.from_entries(["10.0.0.0/8"])
    assert policy.refusal(ip_address("::ffff:10.1.2.3")) is None
    assert policy.refusal(ip_address("64:ff9b::a01:203")) is None
    assert policy.refusal(ip_address("::ffff:127.0.0.1")) == "loopback (IPv4-mapped)"


def test_a_scoped_address_is_judged_and_exempted_without_its_scope() -> None:
    policy = DestinationPolicy.from_entries(["fe80::/10"])
    assert policy.refusal(ip_address("fe80::1%3")) is None
    assert DestinationPolicy().refusal(ip_address("fe80::1%3")) == "link-local"

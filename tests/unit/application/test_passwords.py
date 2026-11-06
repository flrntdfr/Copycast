"""argon2id hashing, verification that never raises, the slowest parameters, the dummy."""

from __future__ import annotations

from argon2 import extract_parameters

from copycast.application.passwords import (
    DEFAULT_MEMORY_COST,
    DEFAULT_PARALLELISM,
    DEFAULT_TIME_COST,
    PasswordHasher,
)

FAST = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)


def test_hash_is_argon2id_with_the_parameters_and_verifies() -> None:
    phc = FAST.hash("correct horse")
    assert phc.startswith("$argon2id$v=19$m=1024,t=1,p=1$")
    assert FAST.verify(phc, "correct horse")
    assert not FAST.verify(phc, "wrong horse")


def test_verify_never_raises_on_garbage() -> None:
    assert FAST.verify("plaintext", "plaintext") is False
    assert FAST.verify("$argon2id$v=19$m=1024,t=1,p=1$bad", "x") is False
    assert FAST.verify("", "") is False


def test_slowest_takes_the_largest_of_each_parameter() -> None:
    a = PasswordHasher(time_cost=2, memory_cost=1024, parallelism=1).hash("a")
    b = PasswordHasher(time_cost=1, memory_cost=2048, parallelism=2).hash("b")
    slowest = PasswordHasher.slowest([a, "not a hash", b])
    assert (slowest.time_cost, slowest.memory_cost, slowest.parallelism) == (2, 2048, 2)
    defaults = PasswordHasher.slowest(["garbage"])
    assert (defaults.time_cost, defaults.memory_cost, defaults.parallelism) == (
        DEFAULT_TIME_COST,
        DEFAULT_MEMORY_COST,
        DEFAULT_PARALLELISM,
    )
    assert PasswordHasher.slowest([]).memory_cost == DEFAULT_MEMORY_COST


def test_dummy_verify_uses_these_parameters_once() -> None:
    hasher = PasswordHasher(time_cost=1, memory_cost=2048, parallelism=1)
    hasher.dummy_verify("anything")
    first = hasher._dummy  # pyright: ignore[reportPrivateUsage]
    assert first is not None and extract_parameters(first).memory_cost == 2048
    hasher.dummy_verify("again")
    assert hasher._dummy is first  # pyright: ignore[reportPrivateUsage]

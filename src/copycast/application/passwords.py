"""argon2id password hashes: the configured Users' and the legacy operator password's.

The only argon2 user of the application layer (``copycast.settings`` validates PHC
strings and ``copycast.cli`` hashes new passwords). :meth:`PasswordHasher.dummy_verify`
spends the same work as a real verify, so an unknown username answers no faster than
a wrong password.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Final

from argon2 import PasswordHasher as _Argon2Hasher
from argon2 import Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError

DEFAULT_TIME_COST: Final = 3
DEFAULT_MEMORY_COST: Final = 65536
"""KiB: 64 MiB per verify."""
DEFAULT_PARALLELISM: Final = 4
_DUMMY_PASSWORD: Final = "copycast dummy password"


class PasswordHasher:
    """argon2id with fixed parameters; :meth:`verify` never raises."""

    def __init__(
        self,
        *,
        time_cost: int = DEFAULT_TIME_COST,
        memory_cost: int = DEFAULT_MEMORY_COST,
        parallelism: int = DEFAULT_PARALLELISM,
    ) -> None:
        self.time_cost = time_cost
        self.memory_cost = memory_cost
        self.parallelism = parallelism
        self._hasher = _Argon2Hasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            type=Type.ID,
        )
        self._dummy: str | None = None
        self._dummy_lock = threading.Lock()

    @classmethod
    def slowest(cls, phcs: Iterable[str]) -> PasswordHasher:
        """The largest time cost, memory cost and parallelism among ``phcs`` (for the dummy).

        Hashes argon2 cannot parse are skipped; with none left, the defaults.
        """
        time_cost = memory_cost = parallelism = 0
        for phc in phcs:
            try:
                parameters = extract_parameters(phc)
            except InvalidHashError:
                continue
            time_cost = max(time_cost, parameters.time_cost)
            memory_cost = max(memory_cost, parameters.memory_cost)
            parallelism = max(parallelism, parameters.parallelism)
        if not time_cost:
            return cls()
        return cls(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)

    def hash(self, password: str) -> str:
        """An argon2id PHC string with this hasher's parameters."""
        return self._hasher.hash(password)

    def verify(self, phc: str, password: str) -> bool:
        """Whether ``password`` matches ``phc``; False on a mismatch or an invalid hash."""
        try:
            return self._hasher.verify(phc, password)
        except (VerificationError, InvalidHashError):
            return False

    def dummy_verify(self, password: str) -> None:
        """A verify against a hash with these parameters, its result thrown away."""
        self.verify(self._dummy_hash(), password)

    def _dummy_hash(self) -> str:
        with self._dummy_lock:
            if self._dummy is None:
                self._dummy = self.hash(_DUMMY_PASSWORD)
            return self._dummy


__all__ = [
    "DEFAULT_MEMORY_COST",
    "DEFAULT_PARALLELISM",
    "DEFAULT_TIME_COST",
    "PasswordHasher",
]

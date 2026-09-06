"""Episode selections: ``"1-42, 180"`` parsing and resolution against a Catalog."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from copycast.domain.enums import ArchiveState, Numbering
from copycast.domain.exceptions import InvalidSelection

MAX_SELECTION_SIZE = 100_000
_SEPARATORS = re.compile(r"[,\s;]+")
_RANGE = re.compile(r"^(\d+)(?:-|\u2013|\.\.)(\d+)$")
_RANGE_OP_WS = re.compile(r"\s*(-|\u2013|\.\.)\s*")


@dataclass(frozen=True, slots=True)
class ParsedSelection:
    numbers: list[int] = field(default_factory=list[int])
    errors: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_selection(text: str) -> ParsedSelection:
    """Parse ``"1-42,180"`` into ordered unique numbers plus per-token errors.

    Tokens are integers ``N`` or inclusive ranges ``A-B`` separated by commas,
    whitespace or semicolons. Numbers start at 1; ``A`` must not exceed ``B``.
    Never raises: callers decide whether errors are fatal.
    """
    numbers: list[int] = []
    seen: set[int] = set()
    errors: list[str] = []
    for token in _SEPARATORS.split(_RANGE_OP_WS.sub(r"\1", text.strip())):
        if not token:
            continue
        if token.isdigit():
            lo = hi = int(token)
        else:
            match = _RANGE.match(token)
            if not match:
                errors.append(f"{token!r} is not a number or a range like 1-42")
                continue
            lo, hi = int(match.group(1)), int(match.group(2))
            if lo > hi:
                errors.append(f"{token!r}: range start exceeds its end")
                continue
        if lo < 1:
            errors.append(f"{token!r}: episode numbers start at 1")
            continue
        if hi - lo + 1 > MAX_SELECTION_SIZE or len(numbers) + (hi - lo + 1) > MAX_SELECTION_SIZE:
            errors.append(f"{token!r}: selection exceeds {MAX_SELECTION_SIZE} episodes")
            continue
        for n in range(lo, hi + 1):
            if n not in seen:
                seen.add(n)
                numbers.append(n)
    if not numbers and not errors and text.strip():
        errors.append("selection is empty")
    return ParsedSelection(numbers=numbers, errors=errors)


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    """What the resolver needs to know about one Catalog item."""

    item_id: str
    ordinal: int
    source_number: int | None
    archive_state: ArchiveState
    archivable: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    resolved: list[str]
    unresolved: list[str]
    numbering_used: Numbering
    already_archived_count: int

    @property
    def to_archive(self) -> list[str]:
        return self.resolved


def resolve_selection(
    candidates: Iterable[SelectionCandidate],
    *,
    numbers: Sequence[int] = (),
    item_ids: Sequence[str] = (),
    numbering: Numbering = Numbering.source,
) -> ResolvedSelection:
    """Map numbers and explicit item ids to Catalog item ids.

    With ``numbering=source`` a number resolves to the item whose
    ``source_number`` equals it when that is unique, else to the item with
    that ``ordinal``; ``numbering=ordinal`` uses ordinals only. Unknown
    numbers and ids land in ``unresolved`` (as strings, in request order).
    ``already_archived_count`` counts resolved items whose media is present.
    """
    by_ordinal: dict[int, SelectionCandidate] = {}
    by_source: dict[int, list[SelectionCandidate]] = {}
    by_id: dict[str, SelectionCandidate] = {}
    for cand in candidates:
        by_ordinal[cand.ordinal] = cand
        by_id[cand.item_id] = cand
        if cand.source_number is not None:
            by_source.setdefault(cand.source_number, []).append(cand)

    resolved: list[str] = []
    seen: set[str] = set()
    unresolved: list[str] = []

    def _take(cand: SelectionCandidate) -> None:
        if cand.item_id not in seen:
            seen.add(cand.item_id)
            resolved.append(cand.item_id)

    for number in numbers:
        cand: SelectionCandidate | None = None
        if numbering is Numbering.source:
            matches = by_source.get(number, [])
            if len(matches) == 1:
                cand = matches[0]
        if cand is None:
            cand = by_ordinal.get(number)
        if cand is None:
            unresolved.append(str(number))
        else:
            _take(cand)

    for ident in item_ids:
        cand = by_id.get(ident)
        if cand is None:
            unresolved.append(ident)
        else:
            _take(cand)

    already_archived = sum(1 for i in resolved if by_id[i].archive_state is ArchiveState.archived)
    return ResolvedSelection(
        resolved=resolved,
        unresolved=unresolved,
        numbering_used=numbering,
        already_archived_count=already_archived,
    )


def parse_selection_strict(text: str) -> list[int]:
    """:func:`parse_selection` raising :class:`InvalidSelection` on any error."""
    parsed = parse_selection(text)
    if parsed.errors:
        raise InvalidSelection("; ".join(parsed.errors), unresolved=parsed.errors)
    return parsed.numbers

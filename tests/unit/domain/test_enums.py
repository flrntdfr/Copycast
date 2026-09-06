"""Item states, tombstones and the derived Catalog state table."""

from __future__ import annotations

import pytest

from copycast.domain.enums import (
    ArchiveState,
    CatalogState,
    ErrorKind,
    FeedKind,
    JobKind,
    JobStatus,
    JobTrigger,
    RefreshRunStatus,
    catalog_state,
)


@pytest.mark.parametrize(
    ("archive_state", "listed", "expected"),
    [
        (ArchiveState.archived, True, CatalogState.listed),
        (ArchiveState.archived, False, CatalogState.delisted),
        (ArchiveState.available, True, CatalogState.available),
        (ArchiveState.deleted, True, CatalogState.available),
        (ArchiveState.available, False, CatalogState.hidden),
        (ArchiveState.deleted, False, CatalogState.hidden),
        (ArchiveState.wanted, True, CatalogState.queued),
        (ArchiveState.wanted, False, CatalogState.queued),
        (ArchiveState.archiving, True, CatalogState.archiving),
        (ArchiveState.failed, True, CatalogState.failed),
        (ArchiveState.failed, False, CatalogState.failed),
    ],
)
def test_catalog_state_table(
    archive_state: ArchiveState, listed: bool, expected: CatalogState
) -> None:
    assert catalog_state(archive_state, listed) is expected


def test_tombstone_is_available_again_but_distinguishable() -> None:
    assert catalog_state(ArchiveState.deleted, True) is CatalogState.available
    assert ArchiveState.deleted != ArchiveState.available


def test_enums_are_plain_strings_for_text_columns() -> None:
    assert ArchiveState.archived == "archived"
    assert f"{JobKind.archive_item}" == "archive_item"
    assert FeedKind("inbox") is FeedKind.inbox
    assert set(JobStatus) == {"queued", "running", "succeeded", "failed", "cancelled"}
    assert set(ErrorKind) == {"transient", "permanent", "cancelled", "storage_full"}
    assert set(JobTrigger) == {
        "manual",
        "scheduled",
        "feed_fetch",
        "request",
        "policy",
        "ui",
        "mcp",
    }
    assert set(JobKind) == {"refresh", "archive_item", "expand_request", "prune", "rebuild"}
    assert "export" not in set(JobKind)
    assert set(RefreshRunStatus) == {"running", "succeeded", "unchanged", "failed", "cancelled"}

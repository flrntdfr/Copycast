"""Chapters files archived before 1.4: the Engine's list rewritten as Podcasting 2.0 JSON.

Archive jobs used to write yt-dlp's ``chapters`` list verbatim while the Mirror Feed
advertised the file as ``application/json+chapters``, which no podcast app could read.
:func:`upgrade_chapter_files` rewrites those files in place; the worker runs it at
startup. It is idempotent (a rewritten file is a JSON object and is skipped next time),
leaves Podcasting 2.0 documents alone (Source chapters are always those), and never
raises for one bad file: it is logged and the scan goes on.

The ``assets`` rows keep the ``size_bytes`` they recorded for the old file. That value
only feeds storage totals and "bytes freed" figures, a few bytes off here; serving
stats the file itself (``FileResponse`` sends the real length) and ``copycast rebuild``
re-stats every asset file, so nothing that reads the file depends on the stored size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from copycast.adapters.storage.atomic import write_json_atomic
from copycast.adapters.storage.layout import Layout
from copycast.domain.chapters import upgraded_document
from copycast.domain.enums import AssetKind
from copycast.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChaptersUpgrade:
    """What one scan found: chapters files seen, rewritten, and unreadable."""

    scanned: int = 0
    upgraded: int = 0
    unreadable: int = 0


def upgrade_chapter_files(layout: Layout) -> ChaptersUpgrade:
    """Rewrite every ``assets/{item}.chapters.json`` still in the Engine's shape."""
    scanned = upgraded = unreadable = 0
    for feed_id in layout.feed_ids_on_disk():
        for path in layout.asset_files(feed_id):
            parsed = Layout.parse_asset_name(path.name)
            if parsed is None or parsed.kind is not AssetKind.chapters:
                continue
            scanned += 1
            try:
                raw = path.read_bytes()
                # Only a JSON array can need the rewrite: an object is skipped unparsed.
                if not raw.lstrip().startswith(b"["):
                    continue
                document = upgraded_document(json.loads(raw))
                if document is None:
                    continue
                write_json_atomic(path, document)
            except (OSError, ValueError, RecursionError) as exc:  # unreadable, not JSON, too deep
                unreadable += 1
                log.warning(
                    "chapters.upgrade_skipped", feed_id=feed_id, file=path.name, error=str(exc)
                )
                continue
            upgraded += 1
    return ChaptersUpgrade(scanned=scanned, upgraded=upgraded, unreadable=unreadable)


__all__ = ["ChaptersUpgrade", "upgrade_chapter_files"]

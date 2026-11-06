"""The one-time copy of the pre-3.0 global Defaults and Cookie files to a User (§2.2).

Before 3.0 every Mirror shared ``engine/defaults.json`` and one cookie jar,
``engine/cookies.txt``, merged from ``engine/cookies/{name}.txt``. From 3.0 each User has
their own under ``users/{username}/``; the global ones become the owner of existing
data's (``auth.owner_of_existing``, else the first Admin, else the open/legacy Admin):

========================== ==========================================
legacy key                 copied to
========================== ==========================================
engine/defaults.json       users/{owner}/defaults.json
engine/cookies.txt         users/{owner}/cookies.txt
engine/cookies/{name}.txt  users/{owner}/cookie-files/{name}.txt
========================== ==========================================

3.0 copies and never deletes: the legacy keys stay, so a 2.3.0 image rolled back onto
this Object store still finds its settings (a later release deletes them). A target that
already exists is left alone and reported as a conflict. :data:`MARKER_KEY` is written
last; once it exists the copy is a no-op, so later edits under ``users/{owner}/`` are
never overwritten by the stale legacy keys. Before the marker exists a re-run copies the
same bytes again, so an api and a worker racing at startup are safe.

A legacy key written after the marker can only come from a 2.x image run after a rollback
(3.0 never writes them). It is not copied (that could undo edits made under 3.0), but
every start says so: ``user_settings.legacy_newer_than_copy`` names the keys and how to
copy them again (delete the marker and the owner's copies; the rollback in
``docs/operations.md``), so stale cookies never fail fetches without a word.

:func:`move_user_settings` is the Object store half of ``copycast users reassign OLD NEW``:
``users/OLD/`` moves to ``users/NEW/``, and whatever NEW already has wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, cast

from copycast.adapters.storage.layout import Layout, UnsafePath
from copycast.application.services.context import StoredObject
from copycast.application.services.engine import cookie_store
from copycast.logging import get_logger

log = get_logger(__name__)

MARKER_KEY: Final = "users/.legacy-settings-copied"
"""JSON ``{"owner": …, "at": …, "copied": […], "conflicts": […]}``: the copy is done."""

_CONTENT_TYPES: Final = {".json": "application/json", ".txt": "text/plain"}


@dataclass(frozen=True)
class MoveReport:
    """What one run did: the legacy keys copied, the ones left because the target existed,
    and whether an earlier run had already finished (nothing touched)."""

    copied: list[str] = field(default_factory=list[str])
    conflicts: list[str] = field(default_factory=list[str])
    already_done: bool = False
    legacy_newer: list[str] = field(default_factory=list[str])
    """With ``already_done``: the legacy keys written after the marker (a 2.x run after a
    rollback), which were not copied."""


def legacy_pairs(layout: Layout, owner: str) -> list[tuple[str, str]]:
    """Every legacy key present, with its target under ``users/{owner}/``."""
    pairs = [
        (layout.defaults_key(), layout.user_defaults_key(owner)),
        (layout.cookies_key(), layout.user_cookies_key(owner)),
    ]
    for obj in layout.store.list(layout.cookie_files_prefix()):
        name = obj.key.removeprefix(layout.cookie_files_prefix())
        if not name.endswith(".txt"):
            continue
        try:
            target = layout.user_cookie_file_key(owner, name)
        except UnsafePath:  # a nested or hidden object nobody stored through the API
            log.warning("user_settings.migration_skipped", key=obj.key)
            continue
        pairs.append((obj.key, target))
    return pairs


def migrate_legacy_settings(layout: Layout, owner: str) -> MoveReport:
    """Copy the legacy Defaults and Cookie files to ``owner`` once (see the module docstring).

    Object store errors propagate: the caller treats them as fatal, like preflight.
    """
    store = layout.store
    marker = store.head(MARKER_KEY)
    if marker is not None:
        return MoveReport(already_done=True, legacy_newer=_legacy_newer(layout, marker))
    copied: list[str] = []
    conflicts: list[str] = []
    for source, target in legacy_pairs(layout, owner):
        body = store.get_bytes(source)
        if body is None:
            continue
        if store.exists(target):
            conflicts.append(source)
            log.warning("user_settings.migration_conflict", source=source, target=target)
            continue
        suffix = source[source.rfind(".") :]
        store.put_bytes(target, body, content_type=_CONTENT_TYPES.get(suffix))
        copied.append(source)
    layout.write_json(
        MARKER_KEY,
        {
            "owner": owner,
            "at": datetime.now(UTC).isoformat(),
            "copied": copied,
            "conflicts": conflicts,
        },
    )
    if copied or conflicts:
        log.info(
            "user_settings.migrated",
            owner=owner,
            copied=copied,
            conflicts=conflicts,
        )
    return MoveReport(copied=copied, conflicts=conflicts)


def _legacy_newer(layout: Layout, marker: StoredObject) -> list[str]:
    """The legacy keys written after ``marker`` (warned about, never fatal)."""
    try:
        sources = [
            layout.defaults_key(),
            layout.cookies_key(),
            *(obj.key for obj in layout.store.list(layout.cookie_files_prefix())),
        ]
        newer = [
            source
            for source in sources
            if (found := layout.store.head(source)) is not None
            and found.last_modified > marker.last_modified
        ]
        if newer:
            recorded: Any = layout.read_json(MARKER_KEY)
            fields = cast("dict[str, object]", recorded) if isinstance(recorded, dict) else {}
            named = fields.get("owner")
            owner = named if isinstance(named, str) and named else "{owner}"
            log.warning(
                "user_settings.legacy_newer_than_copy",
                keys=newer,
                owner=owner,
                hint=(
                    f"a 2.x image changed these after 3.0 copied them to users/{owner}/; 3.0 "
                    f"keeps using users/{owner}/. To copy them again, delete {MARKER_KEY} "
                    f"and users/{owner}/defaults.json, cookies.txt and cookie-files/, then "
                    "restart (or set them again under 3.0)"
                ),
            )
        return newer
    except Exception as exc:  # a check, not the copy: never fatal
        log.warning("user_settings.legacy_check_failed", error=str(exc))
        return []


def move_user_settings(layout: Layout, old: str, new: str) -> MoveReport:
    """Move ``users/{old}/`` to ``users/{new}/``; whatever ``new`` already has wins.

    Cookie files move by name: one ``new`` lacks moves (with what the Engine rotated in
    ``old``'s jar carried back into it) and ``new``'s jar is merged again, so their next
    fetch carries it; one ``new`` already has stays ``old``'s, reported as a conflict, and
    ``old``'s jar is merged again from what remains. Every other key (the Defaults) moves
    when absent under ``new`` (copied, then deleted under ``old``) and is otherwise kept
    under ``old`` and reported. ``copied`` lists the ``old`` keys moved; nothing is lost.
    """
    store = layout.store
    moved: list[str] = []
    conflicts: list[str] = []

    old_cookies, new_cookies = cookie_store(layout, old), cookie_store(layout, new)
    old_keys = old_cookies.keys()
    old_files = old_cookies.synced()
    cookie_keys = {old_cookies.jar, *old_keys.values()}
    if old_files:
        new_files = new_cookies.synced()
        kept: dict[str, str] = {}
        for name, text in sorted(old_files.items()):
            if name in new_files:
                kept[name] = text
                conflicts.append(old_keys[name])
            else:
                new_files[name] = text
                moved.append(old_keys[name])
        if len(kept) < len(old_files):
            new_cookies.save(new_files)
            old_cookies.save(kept)

    old_prefix, new_prefix = layout.user_prefix(old), layout.user_prefix(new)
    for obj in store.list(old_prefix):
        if obj.key in cookie_keys:
            continue
        target = new_prefix + obj.key.removeprefix(old_prefix)
        if store.exists(target):
            conflicts.append(obj.key)
            continue
        store.copy(obj.key, target)
        store.delete(obj.key)
        moved.append(obj.key)
    log.info("user_settings.moved", old=old, new=new, moved=moved, conflicts=conflicts)
    return MoveReport(copied=moved, conflicts=conflicts)


__all__ = [
    "MARKER_KEY",
    "MoveReport",
    "legacy_pairs",
    "migrate_legacy_settings",
    "move_user_settings",
]

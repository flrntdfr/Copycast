"""Fixtures for the services and worker suites: a Source served from a temp root, a runner.

Every test gets the shared ``container`` (FakeEngine + a per-test database)
and a :class:`Source`: an ``Origin`` over a temp directory where tests write
RSS documents whose enclosures, artwork, chapters and transcripts all point
back at the origin, so nothing ever reaches the network.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest

from copycast.adapters.db.uow import UnitOfWork, uow_of
from copycast.app import Container
from copycast.application.models import MirrorCreate, MirrorRead
from copycast.domain.enums import BackfillMode, JobKind, JobStatus
from copycast.worker.runner import Runner
from tests.support.origin import FIXTURES_DIR, Origin

pytestmark = pytest.mark.integration

EPOCH = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class Source:
    """An RSS Source under test: write documents, then hand out their URLs."""

    origin: Origin
    root: Path

    @property
    def base_url(self) -> str:
        return self.origin.base_url

    def url_for(self, path: str) -> str:
        return self.origin.url_for(path)

    def write_rss(
        self,
        name: str,
        *,
        items: Sequence[int],
        title: str = "Worker Test Podcast",
        assets: bool = False,
        artwork: bool = True,
        numbered: bool = True,
        language: str = "en",
    ) -> str:
        """Write ``rss/{name}.xml`` with ``items`` (newest first, as served); returns its URL."""
        base = self.base_url
        parts: list[str] = []
        for n in items:
            published = (EPOCH + timedelta(days=n)).strftime("%a, %d %b %Y %H:%M:%S +0000")
            extras = ""
            if numbered:
                extras += f"<itunes:episode>{n}</itunes:episode>"
            if assets:
                extras += (
                    f'<itunes:image href="{base}/media/tiny.jpg"/>'
                    f'<podcast:chapters url="{base}/assets/chapters.json" '
                    'type="application/json+chapters"/>'
                    f'<podcast:transcript url="{base}/assets/transcript.vtt" '
                    'type="text/vtt" language="en"/>'
                )
            parts.append(
                "<item>"
                f"<title>{escape(f'Episode {n}')}</title>"
                f"<link>{base}/episodes/{n}</link>"
                f'<guid isPermaLink="false">urn:worker:episode:{n}</guid>'
                f"<pubDate>{published}</pubDate>"
                f"<description>{escape(f'Description of episode {n}')}</description>"
                f'<enclosure url="{base}/media/tiny.mp3" length="4407" type="audio/mpeg"/>'
                f"<itunes:duration>{60 * n}</itunes:duration>"
                f"{extras}"
                "</item>"
            )
        image = f'<itunes:image href="{base}/media/tiny.jpg"/>' if artwork else ""
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
            'xmlns:podcast="https://podcastindex.org/namespace/1.0" '
            'xmlns:atom="http://www.w3.org/2005/Atom">'
            "<channel>"
            f"<title>{escape(title)}</title>"
            f"<link>{base}/</link>"
            f"<description>{escape(title)} description</description>"
            f"<language>{language}</language>"
            "<itunes:author>Worker Tester</itunes:author>"
            f"{image}"
            f"{''.join(parts)}"
            "</channel></rss>"
        )
        target = self.root / "rss" / f"{name}.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        return self.url_for(f"/rss/{name}.xml")


@pytest.fixture
def source(tmp_path: Path) -> Iterator[Source]:
    root = tmp_path / "origin"
    (root / "media").mkdir(parents=True)
    (root / "assets").mkdir(parents=True)
    shutil.copy(FIXTURES_DIR / "media" / "tiny.mp3", root / "media" / "tiny.mp3")
    shutil.copy(FIXTURES_DIR / "media" / "tiny.jpg", root / "media" / "tiny.jpg")
    (root / "assets" / "chapters.json").write_text(
        '{"version": "1.2.0", "chapters": [{"startTime": 0, "title": "Intro"}]}', encoding="utf-8"
    )
    (root / "assets" / "transcript.vtt").write_text(
        "WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n", encoding="utf-8"
    )
    with Origin(root=root) as origin:
        yield Source(origin=origin, root=root)


@pytest.fixture
def runner(container: Container) -> Iterator[Runner]:
    built = Runner(
        container,
        worker_id="test-worker",
        concurrency=2,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
    )
    yield built


@pytest.fixture
async def default_inbox(container: Container) -> AsyncIterator[str]:
    inbox = await container.services.ensure_default_inbox()
    yield inbox.id


def uow(container: Container) -> UnitOfWork:
    return uow_of(container.uow_factory())


async def create_mirror(
    container: Container,
    url: str,
    *,
    mode: BackfillMode = BackfillMode.all,
    latest_n: int | None = None,
    selection: str | None = None,
    follow: bool | None = None,
    **overrides: Any,
) -> MirrorRead:
    if selection is not None:
        mode = BackfillMode.selection
    body = MirrorCreate(
        source_url=url,
        backfill={"mode": mode, "latest_n": latest_n, "selection": selection},
        follow=follow,
        **overrides,
    )
    return await container.services.create_mirror(body)


async def jobs_of(container: Container, feed_id: str, *, kind: JobKind | None = None) -> list[Any]:
    async with uow(container) as unit:
        rows, _ = await unit.jobs.list_page(feed_id=feed_id, kind=kind, limit=500)
        return list(rows)


async def wait_status(container: Container, job_id: Any, *statuses: JobStatus) -> Any:
    async with uow(container) as unit:
        job = await unit.jobs.require(job_id)
        assert job.status in {s.value for s in statuses}, (job.status, job.error)
        return job


__all__ = [
    "EPOCH",
    "Source",
    "create_mirror",
    "jobs_of",
    "uow",
    "wait_status",
]

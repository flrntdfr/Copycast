"""``ObjectResponse``: an object of the Object store proxied with ``FileResponse``'s HTTP semantics.

Podcast apps see exactly what a local file used to give them: ``Content-Length``,
``Accept-Ranges: bytes``, a strong ``ETag`` (the object's own), ``Last-Modified``,
``206`` for one or several ranges (``If-Range`` honoured), ``416`` past the end
and ``400`` for a malformed ``Range``. The Range parsing is Starlette's own
(``FileResponse._parse_range_header``) so nothing drifts. Every read is pinned
to the ETag ``head`` saw (``If-Match``): an object replaced mid-response ends
the body instead of splicing two versions. Bytes stream through in
``STREAM_CHUNK_BYTES`` chunks; nothing is buffered whole.
"""

from __future__ import annotations

from collections.abc import Mapping
from email.utils import format_datetime
from secrets import token_hex
from typing import Any

from starlette.background import BackgroundTask
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.types import Receive, Scope, Send

from copycast.application.ports import StoredObject
from copycast.logging import get_logger

log = get_logger(__name__)


class _MalformedRange(Exception):
    pass


class _Unsatisfiable(Exception):
    def __init__(self, size: int) -> None:
        self.size = size


def _parse_ranges(header: str, size: int) -> list[tuple[int, int]]:
    """Starlette's parser, with its two exception types folded into ours."""
    try:
        parse: Any = FileResponse._parse_range_header  # pyright: ignore[reportPrivateUsage]
        ranges: list[tuple[int, int]] = parse(header, size)
    except Exception as exc:
        name = type(exc).__name__
        if name == "RangeNotSatisfiable":
            raise _Unsatisfiable(size) from exc
        if name == "MalformedRangeHeader":
            raise _MalformedRange(str(getattr(exc, "content", "")) or "Malformed range") from exc
        raise
    return ranges


class ObjectResponse(Response):
    """Stream ``obj`` from ``store`` (an ``ObjectStore``) honouring ``Range`` and ``If-Range``."""

    def __init__(
        self,
        store: Any,
        obj: StoredObject,
        *,
        media_type: str,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        self.store = store
        self.obj = obj
        self.status_code = 200
        self.media_type = media_type
        self.background = background
        self.init_headers(headers)
        self.headers.setdefault("accept-ranges", "bytes")
        self.headers.setdefault("content-length", str(obj.size))
        self.headers.setdefault("last-modified", format_datetime(obj.last_modified, usegmt=True))
        if obj.etag:
            self.headers.setdefault("etag", obj.etag)

    def _if_range_matches(self, value: str) -> bool:
        return value == self.headers.get("last-modified") or value == self.headers.get("etag")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        head_only = scope["method"].upper() == "HEAD"
        request = Headers(scope=scope)
        http_range = request.get("range")
        if_range = request.get("if-range")
        size = self.obj.size
        if http_range is None or (if_range is not None and not self._if_range_matches(if_range)):
            await self._simple(send, head_only)
        else:
            try:
                ranges = _parse_ranges(http_range, size)
            except _MalformedRange as exc:
                return await PlainTextResponse(str(exc), status_code=400)(scope, receive, send)
            except _Unsatisfiable as exc:
                response = PlainTextResponse(
                    status_code=416, headers={"Content-Range": f"bytes */{exc.size}"}
                )
                return await response(scope, receive, send)
            if not ranges:
                await self._simple(send, head_only)
            elif len(ranges) == 1:
                await self._single(send, *ranges[0], head_only=head_only)
            else:
                await self._multiple(send, ranges, head_only=head_only)
        if self.background is not None:
            await self.background()

    async def _body(self, send: Send, start: int, end: int, *, last: bool) -> None:
        """Send ``[start, end)``; when ``last`` the final message closes the body."""
        sent = start
        if start < end:
            try:
                async for chunk in self.store.stream(
                    self.obj.key, start=start, end=end, if_match=self.obj.etag
                ):
                    sent += len(chunk)
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            except FileNotFoundError:
                # Replaced or deleted after ``head``: the client sees a short body and retries.
                log.warning("media.object_vanished", key=self.obj.key, sent=sent - start)
            except Exception as exc:  # the Object store failed mid-body (timeout, gone away)
                # The headers are out: end the body short (the client retries with a Range)
                # instead of letting the error escape the ASGI app.
                reason = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
                log.warning(
                    "media.stream_failed",
                    key=self.obj.key,
                    sent=sent - start,
                    error=reason or type(exc).__name__,
                )
        if last:
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _simple(self, send: Send, head_only: bool) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        if head_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self._body(send, 0, self.obj.size, last=True)

    async def _single(self, send: Send, start: int, end: int, *, head_only: bool) -> None:
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{self.obj.size}"
        headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if head_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self._body(send, start, end, last=True)

    async def _multiple(
        self, send: Send, ranges: list[tuple[int, int]], *, head_only: bool
    ) -> None:
        boundary = token_hex(13)
        content_type = self.headers["content-type"]
        generate: Any = FileResponse.generate_multipart
        length, part_header = generate(self, ranges, boundary, self.obj.size, content_type)
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        headers["content-length"] = str(length)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if head_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        for start, end in ranges:
            await send(
                {"type": "http.response.body", "body": part_header(start, end), "more_body": True}
            )
            await self._body(send, start, end, last=False)
            await send({"type": "http.response.body", "body": b"\r\n", "more_body": True})
        await send(
            {
                "type": "http.response.body",
                "body": f"--{boundary}--".encode("latin-1"),
                "more_body": False,
            }
        )


__all__ = ["ObjectResponse"]

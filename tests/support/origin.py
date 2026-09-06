"""A threaded HTTP origin serving ``tests/fixtures`` for Source and media tests.

Supports ``Range`` (206/416), strong ``ETag`` + ``If-None-Match`` (304),
``Last-Modified`` + ``If-Modified-Since`` (304), ``HEAD``, and scripted
responses (``origin.script("/rss/a.xml", status=503)``) that fire a given
number of times before the file is served again. Every request is logged.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class _QuietServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that does not print a traceback when a client hangs up.

    Cancelled downloads close the socket mid-body on purpose; the stock
    ``handle_error`` would spam stderr with a ``BrokenPipeError`` for each one.
    """

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, BrokenPipeError | ConnectionResetError):
            return
        super().handle_error(request, client_address)


_EXTRA_TYPES = {
    ".xml": "application/rss+xml",
    ".json": "application/json",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".jpg": "image/jpeg",
    ".vtt": "text/vtt",
    ".srt": "application/x-subrip",
    ".html": "text/html; charset=utf-8",
    ".lock": "text/plain; charset=utf-8",
}


@dataclass(frozen=True, slots=True)
class Scripted:
    """A canned response replacing the file for the next ``times`` requests."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict[str, str])
    body: bytes = b""
    times: int = 1
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class RequestLog:
    method: str
    path: str
    headers: dict[str, str]

    @property
    def range(self) -> str | None:
        return self.headers.get("range")

    @property
    def if_none_match(self) -> str | None:
        return self.headers.get("if-none-match")


def etag_for(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:20] + '"'


def guess_type(path: Path) -> str:
    override = _EXTRA_TYPES.get(path.suffix.lower())
    if override:
        return override
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class Origin:
    """``with Origin() as origin: origin.url_for("/rss/itunes_podcast20.xml")``."""

    def __init__(self, root: Path = FIXTURES_DIR, host: str = "127.0.0.1") -> None:
        self.root = root.resolve()
        self.host = host
        self.requests: list[RequestLog] = []
        self._scripts: dict[str, deque[Scripted]] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.default_headers: dict[str, str] = {}

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> Origin:
        origin = self

        class Handler(_OriginHandler):
            pass

        Handler.origin = origin
        self._server = _QuietServer((self.host, 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> Origin:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------ helpers

    @property
    def port(self) -> int:
        assert self._server is not None, "origin not started"
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def url_for(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def script(
        self,
        path: str,
        *,
        status: int = 200,
        body: bytes | str = b"",
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
        times: int = 1,
    ) -> None:
        """Answer the next ``times`` requests for ``path`` with a canned response."""
        raw = body.encode("utf-8") if isinstance(body, str) else body
        with self._lock:
            self._scripts.setdefault(_norm(path), deque()).append(
                Scripted(
                    status=status,
                    headers=dict(headers or {}),
                    body=raw,
                    times=times,
                    content_type=content_type,
                )
            )

    def clear(self) -> None:
        with self._lock:
            self._scripts.clear()
            self.requests.clear()

    def requests_for(self, path: str) -> list[RequestLog]:
        key = _norm(path)
        return [r for r in self.requests if _norm(r.path) == key]

    def hits(self, path: str) -> int:
        return len(self.requests_for(path))

    def file_path(self, path: str) -> Path | None:
        candidate = (self.root / unquote(_norm(path)).lstrip("/")).resolve()
        if candidate.is_file() and candidate.is_relative_to(self.root):
            return candidate
        return None

    def _take_script(self, path: str) -> Scripted | None:
        with self._lock:
            queue = self._scripts.get(_norm(path))
            if not queue:
                return None
            scripted = queue[0]
            if scripted.times <= 1:
                queue.popleft()
            else:
                queue[0] = Scripted(
                    status=scripted.status,
                    headers=scripted.headers,
                    body=scripted.body,
                    times=scripted.times - 1,
                    content_type=scripted.content_type,
                )
            return scripted

    def _log(self, method: str, path: str, headers: dict[str, str]) -> None:
        with self._lock:
            self.requests.append(RequestLog(method=method, path=path, headers=headers))


def _norm(path: str) -> str:
    return "/" + urlsplit(path).path.lstrip("/")


class _OriginHandler(BaseHTTPRequestHandler):
    origin: Origin
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def do_GET(self) -> None:
        self._serve(head=False)

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def _send(self, status: int, headers: dict[str, str], body: bytes, *, head: bool) -> None:
        self.send_response(status)
        for name, value in {**self.origin.default_headers, **headers}.items():
            self.send_header(name, value)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head and body:
            self.wfile.write(body)

    def _serve(self, *, head: bool) -> None:
        headers = {k.lower(): v for k, v in self.headers.items()}
        self.origin._log("HEAD" if head else "GET", self.path, headers)

        scripted = self.origin._take_script(self.path)
        if scripted is not None:
            extra = dict(scripted.headers)
            if scripted.content_type:
                extra["Content-Type"] = scripted.content_type
            elif scripted.body:
                extra.setdefault("Content-Type", "application/octet-stream")
            self._send(scripted.status, extra, scripted.body, head=head)
            return

        file_path = self.origin.file_path(self.path)
        if file_path is None:
            self._send(
                HTTPStatus.NOT_FOUND, {"Content-Type": "text/plain"}, b"not found", head=head
            )
            return

        data = file_path.read_bytes()
        etag = etag_for(data)
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC).replace(microsecond=0)
        base_headers = {
            "Content-Type": guess_type(file_path),
            "ETag": etag,
            "Last-Modified": format_datetime(mtime, usegmt=True),
            "Accept-Ranges": "bytes",
        }

        inm = headers.get("if-none-match")
        if inm and etag in [t.strip() for t in inm.split(",")]:
            self._send(HTTPStatus.NOT_MODIFIED, base_headers, b"", head=head)
            return
        ims = headers.get("if-modified-since")
        if ims and not inm:
            try:
                since = parsedate_to_datetime(ims)
            except (TypeError, ValueError):
                since = None
            if since is not None and mtime <= since:
                self._send(HTTPStatus.NOT_MODIFIED, base_headers, b"", head=head)
                return

        range_header = headers.get("range")
        if range_header:
            match = _RANGE.match(range_header.strip())
            total = len(data)
            if not match:
                self._send(
                    HTTPStatus.RANGE_NOT_SATISFIABLE,
                    {**base_headers, "Content-Range": f"bytes */{total}"},
                    b"",
                    head=head,
                )
                return
            start_s, end_s = match.groups()
            if start_s:
                start = int(start_s)
                end = int(end_s) if end_s else total - 1
            else:
                suffix = int(end_s or 0)
                start = max(total - suffix, 0)
                end = total - 1
            end = min(end, total - 1)
            if start >= total or start > end:
                self._send(
                    HTTPStatus.RANGE_NOT_SATISFIABLE,
                    {**base_headers, "Content-Range": f"bytes */{total}"},
                    b"",
                    head=head,
                )
                return
            chunk = data[start : end + 1]
            self._send(
                HTTPStatus.PARTIAL_CONTENT,
                {**base_headers, "Content-Range": f"bytes {start}-{end}/{total}"},
                chunk,
                head=head,
            )
            return

        self._send(HTTPStatus.OK, base_headers, data, head=head)

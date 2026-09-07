"""The post-processor classes Copycast subclasses (thumbnail conversion and embedding)."""

from collections.abc import Callable
from typing import Any

from .. import YoutubeDL

ProgressHook = Callable[[dict[str, Any]], object]

class PostProcessor:
    def __init__(self, downloader: YoutubeDL | None = None) -> None: ...
    def set_downloader(self, downloader: YoutubeDL) -> None: ...
    def add_progress_hook(self, ph: ProgressHook) -> None: ...
    def to_screen(self, text: str, prefix: bool = True, *args: Any, **kwargs: Any) -> None: ...
    def report_warning(self, text: str, *args: Any, **kwargs: Any) -> None: ...
    def report_error(self, text: str, *args: Any, **kwargs: Any) -> None: ...
    def write_debug(self, text: str, *args: Any, **kwargs: Any) -> None: ...
    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]: ...

class FFmpegPostProcessor(PostProcessor):
    def get_audio_codec(self, path: str) -> str | None: ...

class FFmpegExtractAudioPP(FFmpegPostProcessor):
    COMMON_AUDIO_EXTS: tuple[str, ...]
    mapping: str
    def __init__(
        self,
        downloader: YoutubeDL | None = None,
        preferredcodec: str | None = None,
        preferredquality: str | None = None,
        nopostoverwrites: bool = False,
    ) -> None: ...

class FFmpegMetadataPP(FFmpegPostProcessor):
    def __init__(
        self,
        downloader: YoutubeDL | None,
        add_metadata: bool = True,
        add_chapters: bool = True,
        add_infojson: bool | str = "if_exists",
    ) -> None: ...

class FFmpegThumbnailsConvertorPP(FFmpegPostProcessor):
    def __init__(self, downloader: YoutubeDL | None = None, format: str | None = None) -> None: ...
    def fixup_webp(self, info: dict[str, Any], idx: int = -1) -> None: ...
    def convert_thumbnail(self, thumbnail_filename: str, target_ext: str) -> str: ...

class EmbedThumbnailPP(FFmpegPostProcessor):
    def __init__(
        self, downloader: YoutubeDL | None = None, already_have_thumbnail: bool = False
    ) -> None: ...

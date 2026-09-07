"""Minimal typed surface of yt-dlp used by copycast.adapters.engine.

yt-dlp ships no type information; this stub declares only what Copycast
calls so that pyright strict mode can check the engine adapter.
"""

from collections.abc import Callable, Mapping
from typing import Any

from . import postprocessor as postprocessor
from . import utils as utils
from . import version as version

ProgressHook = Callable[[dict[str, Any]], object]
InfoDict = dict[str, Any]

class YoutubeDL:
    params: dict[str, Any]
    def __init__(self, params: Mapping[str, Any] | None = None, auto_init: bool = True) -> None: ...
    def __enter__(self) -> YoutubeDL: ...
    def __exit__(self, *exc_info: object) -> None: ...
    def extract_info(
        self,
        url: str,
        download: bool = True,
        ie_key: str | None = None,
        extra_info: Mapping[str, Any] | None = None,
        process: bool = True,
        force_generic_extractor: bool = False,
    ) -> InfoDict | None: ...
    def process_ie_result(
        self,
        ie_result: InfoDict,
        download: bool = True,
        extra_info: Mapping[str, Any] | None = None,
    ) -> InfoDict: ...
    def sanitize_info(
        self, info_dict: InfoDict | None, remove_private_keys: bool = False
    ) -> InfoDict | None: ...
    def add_progress_hook(self, ph: ProgressHook) -> None: ...
    def add_postprocessor_hook(self, ph: ProgressHook) -> None: ...
    def add_post_hook(self, ph: Callable[[str], object]) -> None: ...
    def add_post_processor(
        self, pp: postprocessor.PostProcessor, when: str = "post_process"
    ) -> None: ...
    def prepare_filename(
        self,
        info_dict: InfoDict,
        dir_type: str = "",
        *,
        outtmpl: str | None = None,
        warn: bool = False,
    ) -> str: ...
    def close(self) -> None: ...

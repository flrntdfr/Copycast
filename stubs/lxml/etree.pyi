"""Minimal typed surface of lxml.etree used by Copycast (lxml ships no stubs)."""

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Literal

LXML_VERSION: tuple[int, ...]

class _Attrib(Mapping[str, str]):
    def __getitem__(self, key: str) -> str: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...

class _Element:
    tag: str | Callable[..., Any]
    text: str | None
    tail: str | None
    attrib: _Attrib
    nsmap: dict[str | None, str]
    prefix: str | None
    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def keys(self) -> list[str]: ...
    def find(self, path: str, namespaces: Mapping[str, str] | None = None) -> _Element | None: ...
    def findall(self, path: str, namespaces: Mapping[str, str] | None = None) -> list[_Element]: ...
    def findtext(
        self,
        path: str,
        default: str | None = None,
        namespaces: Mapping[str, str] | None = None,
    ) -> str | None: ...
    def iterfind(
        self, path: str, namespaces: Mapping[str, str] | None = None
    ) -> Iterator[_Element]: ...
    def iter(self, *tags: str) -> Iterator[_Element]: ...
    def iterchildren(self, *tags: str, reversed: bool = False) -> Iterator[_Element]: ...
    def append(self, element: _Element) -> None: ...
    def insert(self, index: int, element: _Element) -> None: ...
    def remove(self, element: _Element) -> None: ...
    def replace(self, old_element: _Element, new_element: _Element) -> None: ...
    def index(self, child: _Element, start: int | None = None, stop: int | None = None) -> int: ...
    def getparent(self) -> _Element | None: ...
    def getprevious(self) -> _Element | None: ...
    def getnext(self) -> _Element | None: ...
    def getroottree(self) -> _ElementTree: ...
    def __iter__(self) -> Iterator[_Element]: ...
    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> _Element: ...

XmlElement = _Element

class _ElementTree:
    def getroot(self) -> _Element: ...

class QName:
    localname: str
    namespace: str | None
    text: str
    def __init__(self, text_or_uri_or_element: str | _Element, tag: str | None = None) -> None: ...

class XMLParser:
    def __init__(
        self,
        *,
        encoding: str | None = None,
        attribute_defaults: bool = False,
        dtd_validation: bool = False,
        load_dtd: bool = False,
        no_network: bool = True,
        ns_clean: bool = False,
        recover: bool = False,
        huge_tree: bool = False,
        remove_blank_text: bool = False,
        resolve_entities: bool | Literal["internal"] = True,
        remove_comments: bool = False,
        remove_pis: bool = False,
        strip_cdata: bool = True,
        collect_ids: bool = True,
        compact: bool = True,
    ) -> None: ...

class HTMLParser(XMLParser):
    def __init__(
        self,
        *,
        encoding: str | None = None,
        remove_blank_text: bool = False,
        remove_comments: bool = False,
        remove_pis: bool = False,
        no_network: bool = True,
        recover: bool = True,
        compact: bool = True,
        huge_tree: bool = False,
    ) -> None: ...

class LxmlError(Exception): ...
class XMLSyntaxError(LxmlError, SyntaxError): ...

def fromstring(
    text: str | bytes, parser: XMLParser | None = None, *, base_url: str | None = None
) -> _Element: ...
def tostring(
    element_or_tree: _Element | _ElementTree,
    *,
    encoding: str | type[str] | None = None,
    method: str = "xml",
    xml_declaration: bool | None = None,
    pretty_print: bool = False,
    with_tail: bool = True,
    standalone: bool | None = None,
    doctype: str | None = None,
    with_comments: bool = True,
    strip_text: bool = False,
) -> Any: ...
def Element(
    _tag: str,
    attrib: Mapping[str, str] | None = None,
    nsmap: Mapping[str | None, str] | None = None,
    **extra: str,
) -> _Element: ...
def SubElement(
    _parent: _Element,
    _tag: str,
    attrib: Mapping[str, str] | None = None,
    nsmap: Mapping[str | None, str] | None = None,
    **extra: str,
) -> _Element: ...
def cleanup_namespaces(
    tree_or_element: _Element | _ElementTree,
    top_nsmap: Mapping[str | None, str] | None = None,
    keep_ns_prefixes: list[str] | None = None,
) -> None: ...
def Comment(text: str | None = None) -> _Element: ...

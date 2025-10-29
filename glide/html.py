"""Lightweight HTML parsing helpers used by the crawlers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional


_SELECTOR_RE = re.compile(
    r"^(?P<tag>[a-zA-Z0-9_-]+)"
    r"(?:\[(?P<attr>[a-zA-Z0-9_-]+)"
    r"(?:=(?P<quote>['\"]?)(?P<value>.*?)(?P=quote))?\])?$"
)


@dataclass
class HTMLElement:
    tag: Optional[str]
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List["HTMLElement"] = field(default_factory=list)
    _text_chunks: List[str] = field(default_factory=list)
    parent: Optional["HTMLElement"] = field(default=None, repr=False)

    # Basic DOM helpers -------------------------------------------------

    def append_child(self, node: "HTMLElement") -> None:
        self.children.append(node)
        node.parent = self

    def append_text(self, text: str) -> None:
        if text:
            self._text_chunks.append(text)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.attrs.get(key, default)

    # Query helpers -----------------------------------------------------

    def select(self, selector: str) -> List["HTMLElement"]:
        match = _SELECTOR_RE.match(selector.strip())
        if not match:
            return []
        tag = match.group("tag").lower()
        attr = match.group("attr")
        value = match.group("value")

        results: List[HTMLElement] = []

        def _walk(node: "HTMLElement") -> None:
            for child in node.children:
                if _matches(child, tag, attr, value):
                    results.append(child)
                _walk(child)

        _walk(self)
        return results

    def select_one(self, selector: str) -> Optional["HTMLElement"]:
        matches = self.select(selector)
        return matches[0] if matches else None

    def find_all(self, tag: str) -> List["HTMLElement"]:
        tag = tag.lower()
        matches: List[HTMLElement] = []

        def _walk(node: "HTMLElement") -> None:
            for child in node.children:
                if child.tag == tag:
                    matches.append(child)
                _walk(child)

        _walk(self)
        return matches

    # Text helpers ------------------------------------------------------

    def get_text(self, separator: str = "") -> str:
        pieces: List[str] = []

        def _collect(node: "HTMLElement") -> None:
            pieces.extend(node._text_chunks)
            for child in node.children:
                _collect(child)

        _collect(self)
        text = "".join(pieces)
        if separator:
            collapsed = re.sub(r"\s+", " ", text).strip()
            return separator.join(collapsed.split()) if collapsed else ""
        return text

    @property
    def string(self) -> Optional[str]:
        if any(child.tag for child in self.children):
            return None
        text = "".join(self._text_chunks).strip()
        return text or None


class _HTMLBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HTMLElement(tag=None)
        self._stack: List[HTMLElement] = [self.root]

    # HTMLParser callbacks ---------------------------------------------

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        element = HTMLElement(tag=tag.lower(), attrs={k: v or "" for k, v in attrs})
        self._stack[-1].append_child(element)
        self._stack.append(element)

    def handle_startendtag(
        self, tag: str, attrs: List[tuple[str, Optional[str]]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].append_text(data)


def _matches(
    element: HTMLElement, tag: str, attr: Optional[str], value: Optional[str]
) -> bool:
    if element.tag != tag:
        return False
    if not attr:
        return True
    attr_value = element.attrs.get(attr)
    if attr_value is None:
        return False
    if value is None:
        return True
    return attr_value == value


def parse_html(html: str) -> HTMLElement:
    parser = _HTMLBuilder()
    parser.feed(html)
    parser.close()
    return parser.root

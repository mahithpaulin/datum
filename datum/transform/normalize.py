"""Configurable, conservative text normalization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from ..config import NormalizeOptions


@dataclass
class NormalizeStats:
    line_endings_converted: int = 0
    control_chars_removed: int = 0
    unicode_normalized: int = 0
    outer_whitespace_stripped: int = 0
    trailing_whitespace_removed: int = 0
    whitespace_collapsed: int = 0

    def add(self, other: "NormalizeStats") -> None:
        for key, value in asdict(other).items():
            setattr(self, key, getattr(self, key) + value)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def normalize_text(text: str, options: NormalizeOptions) -> tuple[str, NormalizeStats]:
    stats = NormalizeStats()
    result = text
    if options.unicode_form in ("NFC", "NFKC"):
        normalized = unicodedata.normalize(options.unicode_form, result)
        if normalized != result:
            stats.unicode_normalized = 1
            result = normalized
    if options.line_endings == "lf":
        crlf = result.count("\r\n")
        lone = result.count("\r") - crlf
        stats.line_endings_converted = crlf + lone
        result = result.replace("\r\n", "\n").replace("\r", "\n")
    if options.strip_control_chars:
        removable = []
        allowed = "\n\t"
        if options.line_endings == "preserve":
            allowed += "\r"
        for character in result:
            if character in allowed:
                continue
            if unicodedata.category(character) == "Cc":
                removable.append(character)
        if removable:
            stats.control_chars_removed = len(removable)
            for character in set(removable):
                result = result.replace(character, "")
    if options.strip_trailing_whitespace:
        lines = result.split("\n")
        changed = 0
        cleaned: list[str] = []
        for line in lines:
            stripped = line.rstrip(" \t")
            if stripped != line:
                changed += len(line) - len(stripped)
            cleaned.append(stripped)
        if changed:
            stats.trailing_whitespace_removed = changed
            result = "\n".join(cleaned)
    if options.collapse_whitespace:
        collapsed = re.sub(r"[ \t]+", " ", result)
        if collapsed != result:
            stats.whitespace_collapsed = len(result) - len(collapsed)
            result = collapsed
    if options.strip_outer_whitespace:
        stripped = result.strip()
        if stripped != result:
            stats.outer_whitespace_stripped = 1
            result = stripped
    return result, stats

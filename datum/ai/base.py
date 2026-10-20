"""Provider-independent interfaces reserved for future AI-assisted adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..errors import ConfigError


@dataclass(frozen=True)
class AIRequest:
    task: str
    payload: Mapping[str, Any]
    model: str = ""
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AIResponse:
    data: Any
    provider: str
    model: str
    metadata: Mapping[str, Any] | None = None


class AIProvider(Protocol):
    name: str

    def complete(self, request: AIRequest, *, api_key: str | None = None) -> AIResponse:
        ...


def ai_not_implemented() -> None:
    raise ConfigError(
        "AI-assisted processing is not implemented in DATUM 0.1.0; "
        "the core build path is deterministic and offline"
    )

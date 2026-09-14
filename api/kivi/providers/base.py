"""The model-provider contract.

One chokepoint for every model call in the system. Two reasons it looks like
this:

1.  Every call returns its own ``ModelUsage``. Token counts, latency and cost
    are therefore captured structurally at the call site rather than being
    reconstructed later -- the assignment asks for model usage and cost
    wherever they matter, and this is the only place that can honestly know.

2.  Chat and embedding are separate protocols because vendors do not sell them
    as a pair. The Claude API has no embeddings endpoint at all, so an
    Anthropic chat provider must be composed with some other embedder
    (ADR-0003). Making them one interface would have forced a lie.

Implementations land in Phase 3. This module is the contract only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class ModelUsage:
    """What one model call consumed.

    ``cost_estimate_usd`` is an estimate and is labelled as such everywhere it
    surfaces; the offline provider reports 0.0 and is not pretending otherwise.
    """

    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_estimate_usd: float = 0.0
    degraded: bool = False  # served by a fallback rather than the configured provider
    error: str | None = None


@dataclass(slots=True)
class Completion:
    text: str
    usage: ModelUsage


@dataclass(slots=True)
class StructuredResult:
    """A validated structured payload plus how it was obtained.

    ``repaired`` records that the first response failed schema validation and a
    single repair attempt was made. It is surfaced in the evaluation rather
    than swallowed: a pipeline that silently retries is a pipeline whose error
    rate you cannot see.
    """

    value: Any
    usage: ModelUsage
    repaired: bool = False


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]] = field(default_factory=list)
    usage: ModelUsage = field(
        default_factory=lambda: ModelUsage(provider="unknown", model="unknown")
    )


class ChatProvider(Protocol):
    """Text in, text or validated structure out."""

    name: str
    model: str

    def complete(self, *, system: str, prompt: str, max_tokens: int = 4096) -> Completion:
        ...

    def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 4096,
    ) -> StructuredResult:
        """Return a schema-valid instance of ``schema``.

        Contract on failure: validate, attempt exactly one repair, then raise
        ``ProviderError``. Callers record the failure against the candidate as
        ``EXTRACTION_FAILED`` -- they never substitute a plausible-looking
        default.
        """
        ...


class EmbeddingProvider(Protocol):
    """Text in, fixed-width vectors out."""

    name: str
    model: str
    dim: int

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        ...


class ProviderError(RuntimeError):
    """A model call failed in a way the caller must record, not hide."""

    def __init__(self, message: str, usage: ModelUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage

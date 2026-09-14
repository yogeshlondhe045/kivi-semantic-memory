"""Runtime configuration, read from the environment.

Every variable named here appears in .env.example. Nothing in the system reads
an environment variable that is not declared on this class -- that is what
makes the RUN.md environment section exhaustive rather than aspirational.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The single embedding space for a deployment. Chosen at schema time because a
# pgvector column and its HNSW index are fixed-width (ADR-0004). Every provider
# must emit exactly this many dimensions; the offline provider does so
# natively, and hosted providers that do not are projected by the adapter.
EMBEDDING_DIM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KIVI_", env_file=".env", extra="ignore"
    )

    # -- database ---------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://kivi:kivi@localhost:5432/kivi",
        description="SQLAlchemy URL for the Postgres instance.",
    )

    # -- model providers --------------------------------------------------
    # Chat and embedding are configured independently on purpose: not every
    # vendor sells both. Anthropic has no embeddings endpoint, so an Anthropic
    # chat provider pairs with some other embedding provider (ADR-0003).
    chat_provider: str = Field(default="offline")
    embedding_provider: str = Field(default="offline")
    chat_model: str = Field(default="offline-rules-v1")
    embedding_model: str = Field(default="offline-hash-v1")

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    sarvam_api_key: str | None = None

    # -- admission policy thresholds --------------------------------------
    # Surfaced as configuration rather than buried as literals so the Phase 7
    # evaluation can sweep them and show the precision/recall tradeoff.
    admission_confidence_floor: float = 0.35
    active_promotion_confidence: float = 0.60
    corroboration_min_sources: int = 2

    # -- retrieval --------------------------------------------------------
    retrieval_candidate_limit: int = 40
    retrieval_context_limit: int = 8
    retrieval_score_threshold: float = 0.25
    rrf_k: int = 60

    # -- determinism ------------------------------------------------------
    seed: int = 20260914

    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIM


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

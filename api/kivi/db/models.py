"""The Kivi semantic-memory relational model.

Twenty tables. Each one carries a docstring saying why it exists; tables that
were considered and rejected are listed in docs/data-model.md rather than
implemented, because a schema that is larger than its justification is a
liability at review time.

Reading order: input -> memory core -> Hey Kivi -> evaluation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kivi import enums
from kivi.config import EMBEDDING_DIM
from kivi.db.base import Base, created_at, pg_enum, pk

# ==========================================================================
# Input
# ==========================================================================


class AppUser(Base):
    """The person Kivi is learning about.

    Multi-tenancy is out of scope for this submission -- the corpus is one
    user's dictations -- but every downstream table carries user_id anyway so
    that scoping is a WHERE clause later rather than a migration.
    """

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = pk()
    handle: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    timezone: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="Asia/Kolkata"
    )
    created_at: Mapped[datetime] = created_at()


class Transcript(Base):
    """One dictation, exactly as the assignment describes a record.

    Both texts are stored because both are given and they carry different
    information. ``formatted_text`` is what extraction reads; ``raw_asr`` is
    what makes it checkable. Where an entity appears in only one of the two,
    the evidence is weaker -- that is the ``asr_agreement`` signal, and it is
    computed at ingestion rather than guessed at answer time.

    ``metadata_json`` is a deliberate escape hatch: the reviewer's internal
    corpus carries "the ordinary metadata available in our logs", and this
    system cannot know in advance what those fields are named. Unmapped keys
    land here intact instead of being dropped on import.
    """

    __tablename__ = "transcript"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "external_id", name="uq_transcript_external"),
        sa.Index("ix_transcript_user_occurred", "user_id", "occurred_at"),
        sa.Index("ix_transcript_surface", "user_id", "app_surface", "occurred_at"),
        sa.Index("ix_transcript_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Idempotency: re-importing the same corpus must not double-count evidence,
    # because evidence count is what drives confidence.
    external_id: Mapped[str | None] = mapped_column(sa.Text)
    content_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)

    raw_asr: Mapped[str] = mapped_column(sa.Text, nullable=False)
    formatted_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    asr_agreement: Mapped[float | None] = mapped_column(sa.Float)

    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = created_at()

    app_surface: Mapped[str | None] = mapped_column(sa.Text)   # slack, gmail, notion...
    device: Mapped[str | None] = mapped_column(sa.Text)
    language: Mapped[str | None] = mapped_column(sa.Text)
    style_id: Mapped[str | None] = mapped_column(sa.Text)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    word_count: Mapped[int | None] = mapped_column(sa.Integer)

    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        sa.Computed("to_tsvector('english', coalesce(formatted_text, ''))", persisted=True),
    )

    embedding = relationship(
        "TranscriptEmbedding", back_populates="transcript",
        uselist=False, cascade="all, delete-orphan",
    )


class TranscriptEmbedding(Base):
    """Vector for the transcript itself, not for a memory derived from it.

    Needed because some Hey Kivi requests are episodic recall over raw
    history -- "the dictation I did around 5 PM yesterday in Slack" -- where
    no memory was ever created and none should have been.
    """

    __tablename__ = "transcript_embedding"

    id: Mapped[uuid.UUID] = pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("transcript.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dim: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = created_at()

    transcript = relationship("Transcript", back_populates="embedding")


class IngestionJob(Base):
    """One import run.

    Exists so that a partially failed import reports itself as PARTIAL with a
    count, rather than as success with quietly missing records.
    """

    __tablename__ = "ingestion_job"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[enums.JobStatus] = mapped_column(
        pg_enum(enums.JobStatus, "job_status"), nullable=False,
        default=enums.JobStatus.PENDING,
    )
    records_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_ingested: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_skipped: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    chat_provider: Mapped[str | None] = mapped_column(sa.Text)
    embedding_provider: Mapped[str | None] = mapped_column(sa.Text)
    tokens_in: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0.0
    )

    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(sa.Text)


class IngestionItem(Base):
    """Per-record progress within a job.

    The per-stage latency columns are what let the evaluation report ingestion
    latency as a distribution instead of one averaged number.
    """

    __tablename__ = "ingestion_item"
    __table_args__ = (
        sa.Index("ix_ingestion_item_job_status", "job_id", "status"),
    )

    id: Mapped[uuid.UUID] = pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("ingestion_job.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("transcript.id", ondelete="SET NULL")
    )
    source_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[enums.ItemStatus] = mapped_column(
        pg_enum(enums.ItemStatus, "item_status"), nullable=False,
        default=enums.ItemStatus.PENDING,
    )
    normalize_ms: Mapped[int | None] = mapped_column(sa.Integer)
    extract_ms: Mapped[int | None] = mapped_column(sa.Integer)
    admit_ms: Mapped[int | None] = mapped_column(sa.Integer)
    embed_ms: Mapped[int | None] = mapped_column(sa.Integer)
    candidates_extracted: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    candidates_admitted: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at()


# ==========================================================================
# Memory core
# ==========================================================================


class Memory(Base):
    """A durable belief about the user.

    Not "text plus an embedding". The columns here are what let the system do
    the six things the assignment asks of a memory -- change, conflict, expire,
    be removed, be retrieved, and be explained:

    * ``status`` + ``confidence``  -> it can be held without being acted on
    * ``valid_from`` / ``valid_to`` -> it can be true only for a while
    * ``volatility``               -> staleness is a property of the claim's kind
    * ``independent_source_count`` -> repetition is distinguished from emphasis
    * ``user_pinned`` / ``user_hidden`` -> the person overrides the system
    * ``current_version_id``       -> every change kept its history

    ``confidence`` is computed by the admission policy from evidence, never
    taken from a model's self-report.
    """

    __tablename__ = "memory"
    __table_args__ = (
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memory_conf"),
        sa.Index("ix_memory_user_status_kind", "user_id", "status", "kind"),
        sa.Index("ix_memory_subject", "user_id", "subject_key"),
        sa.Index("ix_memory_validity", "user_id", "valid_from", "valid_to"),
        sa.Index("ix_memory_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    kind: Mapped[enums.MemoryKind] = mapped_column(
        pg_enum(enums.MemoryKind, "memory_kind"), nullable=False
    )
    # Canonical one-sentence claim, third-person neutral. This is what is shown
    # to the user and what gets embedded.
    statement: Mapped[str | None] = mapped_column(sa.Text)
    # Normalized entity/topic handle, e.g. "acme" -- gives the lexical arm of
    # retrieval something exact to match, which vectors alone are bad at.
    subject_key: Mapped[str | None] = mapped_column(sa.Text)
    # Typed slots where extraction found them; free-form where it did not.
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    status: Mapped[enums.MemoryStatus] = mapped_column(
        pg_enum(enums.MemoryStatus, "memory_status"), nullable=False,
        default=enums.MemoryStatus.PROPOSED,
    )
    volatility: Mapped[enums.MemoryVolatility] = mapped_column(
        pg_enum(enums.MemoryVolatility, "memory_volatility"), nullable=False,
        default=enums.MemoryVolatility.SLOW,
    )
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    evidence_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    independent_source_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )

    # When the claim is true of the world -- distinct from when it was recorded.
    valid_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    first_observed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_observed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    user_pinned: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    user_hidden: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    sensitive: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )

    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True)  # FK added post-hoc in the migration: mutual reference
    )

    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        sa.Computed(
            "to_tsvector('english', coalesce(statement, '') || ' ' || coalesce(subject_key, ''))",
            persisted=True,
        ),
    )

    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.now(), onupdate=sa.func.now(),
    )

    versions = relationship(
        "MemoryVersion", back_populates="memory",
        cascade="all, delete-orphan", foreign_keys="MemoryVersion.memory_id",
    )
    evidence = relationship(
        "MemoryEvidence", back_populates="memory", cascade="all, delete-orphan"
    )


class MemoryVersion(Base):
    """Append-only history of one belief.

    Nothing mutates a memory without writing one of these, so "what did Kivi
    used to think, and what changed it" is answerable by query rather than by
    inference. ``reason_code`` is an enum and ``note`` is one short sentence of
    engineer-readable justification -- never model chain-of-thought.
    """

    __tablename__ = "memory_version"
    __table_args__ = (
        sa.UniqueConstraint("memory_id", "version_no", name="uq_memory_version_no"),
        sa.Index("ix_memory_version_memory", "memory_id", "version_no"),
    )

    id: Mapped[uuid.UUID] = pk()
    memory_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    change_type: Mapped[enums.MemoryChangeType] = mapped_column(
        pg_enum(enums.MemoryChangeType, "memory_change_type"), nullable=False
    )
    reason_code: Mapped[enums.AdmissionReason | None] = mapped_column(
        pg_enum(enums.AdmissionReason, "admission_reason")
    )
    note: Mapped[str | None] = mapped_column(sa.Text)

    # Snapshot of the fields that define the belief at this version, so history
    # is readable without replaying diffs.
    statement: Mapped[str | None] = mapped_column(sa.Text)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[enums.MemoryStatus] = mapped_column(
        pg_enum(enums.MemoryStatus, "memory_status"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    valid_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    actor: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="system")
    created_at: Mapped[datetime] = created_at()

    memory = relationship(
        "Memory", back_populates="versions", foreign_keys=[memory_id]
    )


class MemoryEvidence(Base):
    """The provenance backbone: belief <- observed span of a real transcript.

    A derived belief must not lose the evidence behind it, so this is a join
    table and not a column. ``relation`` carries contradicting evidence too --
    keeping only supporting evidence would make the system unable to explain
    why it distrusts something.

    The span offsets point into ``transcript.formatted_text``; ``quote`` is
    denormalized so the UI and the evaluation report can show the exact words
    without re-slicing, and so evidence survives if formatting is ever redone.
    """

    __tablename__ = "memory_evidence"
    __table_args__ = (
        sa.UniqueConstraint(
            "memory_id", "transcript_id", "span_start", "span_end",
            name="uq_memory_evidence_span",
        ),
        sa.Index("ix_evidence_transcript", "transcript_id"),
        sa.Index("ix_evidence_memory_relation", "memory_id", "relation"),
    )

    id: Mapped[uuid.UUID] = pk()
    memory_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("transcript.id", ondelete="CASCADE"), nullable=False
    )
    # Which version first recorded this evidence -- lets the UI show what was
    # known at each step rather than only the current pile.
    introduced_in_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memory_version.id", ondelete="SET NULL")
    )

    relation: Mapped[enums.EvidenceRelation] = mapped_column(
        pg_enum(enums.EvidenceRelation, "evidence_relation"), nullable=False,
        default=enums.EvidenceRelation.SUPPORTS,
    )
    span_start: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    span_end: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    quote: Mapped[str] = mapped_column(sa.Text, nullable=False)
    weight: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    observed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = created_at()

    memory = relationship("Memory", back_populates="evidence")
    transcript = relationship("Transcript")


class MemoryLink(Base):
    """Typed edges between beliefs.

    A contradiction is an edge, not a table: a dedicated memory_conflict table
    would duplicate the endpoints and let the two drift apart. Conflict state
    is therefore always (edge, endpoint statuses) and cannot disagree with
    itself.
    """

    __tablename__ = "memory_link"
    __table_args__ = (
        sa.UniqueConstraint(
            "from_memory_id", "to_memory_id", "link_type", name="uq_memory_link"
        ),
        sa.CheckConstraint("from_memory_id <> to_memory_id", name="ck_memory_link_self"),
        sa.Index("ix_memory_link_to", "to_memory_id", "link_type"),
    )

    id: Mapped[uuid.UUID] = pk()
    from_memory_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="CASCADE"), nullable=False
    )
    to_memory_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="CASCADE"), nullable=False
    )
    link_type: Mapped[enums.MemoryLinkType] = mapped_column(
        pg_enum(enums.MemoryLinkType, "memory_link_type"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at()


class MemoryEmbedding(Base):
    """Vector for a belief, kept apart from the belief itself.

    Separate table for two reasons: re-embedding with a different model must
    not touch the memory row (and must not bump ``updated_at``, which the UI
    reads as "when Kivi last learned something"), and a memory whose embedding
    failed is still a valid memory that lexical retrieval can serve.
    """

    __tablename__ = "memory_embedding"
    __table_args__ = (
        sa.UniqueConstraint("memory_id", "model", name="uq_memory_embedding_model"),
    )

    id: Mapped[uuid.UUID] = pk()
    memory_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dim: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # Exactly what was embedded (statement plus aliases), so a retrieval miss
    # can be diagnosed without guessing at the input.
    source_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


class MemoryCandidate(Base):
    """Every proposal the extractor made -- admitted or not.

    This is the table that makes rejection a first-class, inspectable outcome
    instead of an absence. Without it, "what did Kivi deliberately ignore?"
    could only be answered by diffing transcripts against memories and
    guessing.

    ``decision`` and ``reason_code`` are enums, so the evaluation counts codes;
    ``note`` is one short sentence for a human reading the audit trail.
    """

    __tablename__ = "memory_candidate"
    __table_args__ = (
        sa.Index("ix_candidate_decision", "user_id", "decision", "reason_code"),
        sa.Index("ix_candidate_transcript", "transcript_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("transcript.id", ondelete="CASCADE"), nullable=False
    )
    ingestion_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ingestion_item.id", ondelete="SET NULL")
    )

    proposed_kind: Mapped[enums.MemoryKind | None] = mapped_column(
        pg_enum(enums.MemoryKind, "memory_kind")
    )
    proposed_statement: Mapped[str | None] = mapped_column(sa.Text)
    proposed_subject_key: Mapped[str | None] = mapped_column(sa.Text)
    proposed_attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    span_start: Mapped[int | None] = mapped_column(sa.Integer)
    span_end: Mapped[int | None] = mapped_column(sa.Integer)
    quote: Mapped[str | None] = mapped_column(sa.Text)

    decision: Mapped[enums.AdmissionDecision] = mapped_column(
        pg_enum(enums.AdmissionDecision, "admission_decision"), nullable=False
    )
    reason_code: Mapped[enums.AdmissionReason] = mapped_column(
        pg_enum(enums.AdmissionReason, "admission_reason"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(sa.Text)
    # Score the policy computed, kept so a threshold sweep in Phase 7 can be
    # replayed from stored state instead of re-running the model.
    admission_score: Mapped[float | None] = mapped_column(sa.Float)
    resolved_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="SET NULL")
    )

    extractor_provider: Mapped[str | None] = mapped_column(sa.Text)
    extractor_model: Mapped[str | None] = mapped_column(sa.Text)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    repaired: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    created_at: Mapped[datetime] = created_at()


# ==========================================================================
# Hey Kivi
# ==========================================================================


class HeyKiviRequest(Base):
    """One Hey Kivi turn, end to end.

    The latency columns are split by stage because "end-to-end latency" alone
    cannot tell a reviewer whether the cost is retrieval or generation, and the
    assignment asks for retrieval latency separately.
    """

    __tablename__ = "hey_kivi_request"
    __table_args__ = (
        sa.Index("ix_request_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    request_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Structured reading of the request: entities, resolved time window,
    # surface filter, wanted kinds. Stored so a bad answer can be blamed on
    # misunderstanding rather than on retrieval, or vice versa.
    retrieval_plan: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    outcome: Mapped[enums.RequestOutcome] = mapped_column(
        pg_enum(enums.RequestOutcome, "request_outcome"), nullable=False
    )
    abstention_reason: Mapped[enums.AbstentionReason | None] = mapped_column(
        pg_enum(enums.AbstentionReason, "abstention_reason")
    )
    grounding_kind: Mapped[enums.GroundingKind | None] = mapped_column(
        pg_enum(enums.GroundingKind, "grounding_kind")
    )
    answer_text: Mapped[str | None] = mapped_column(sa.Text)
    # Memory ids the answer actually cited, after the citation check.
    cited_memory_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    understand_ms: Mapped[int | None] = mapped_column(sa.Integer)
    retrieval_ms: Mapped[int | None] = mapped_column(sa.Integer)
    tool_ms: Mapped[int | None] = mapped_column(sa.Integer)
    generation_ms: Mapped[int | None] = mapped_column(sa.Integer)
    total_ms: Mapped[int | None] = mapped_column(sa.Integer)

    chat_provider: Mapped[str | None] = mapped_column(sa.Text)
    chat_model: Mapped[str | None] = mapped_column(sa.Text)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0.0
    )
    degraded: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = created_at()


class RetrievalEvent(Base):
    """One stage of one retrieval.

    Per-stage rather than per-request so that the contribution of each arm is
    measurable. If an arm never contributes a hit that survives filtering, the
    evaluation will show it and the arm should be deleted.
    """

    __tablename__ = "retrieval_event"

    id: Mapped[uuid.UUID] = pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hey_kivi_request.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    strategy: Mapped[enums.RetrievalStrategy] = mapped_column(
        pg_enum(enums.RetrievalStrategy, "retrieval_strategy"), nullable=False
    )
    query_text: Mapped[str | None] = mapped_column(sa.Text)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    candidate_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = created_at()


class RetrievalHit(Base):
    """One candidate considered for one request -- kept or dropped.

    ``included_in_context`` plus ``exclusion_reason`` is the answer to the
    assignment's question of how an engineer inspects why memory did *or did
    not* affect a result. Dropped candidates are persisted deliberately: a log
    of only what was used cannot explain a miss.
    """

    __tablename__ = "retrieval_hit"
    __table_args__ = (
        sa.CheckConstraint(
            "(memory_id IS NOT NULL) <> (transcript_id IS NOT NULL)",
            name="ck_hit_exactly_one_target",
        ),
        sa.Index("ix_hit_event_rank", "retrieval_event_id", "rank"),
        sa.Index("ix_hit_request_included", "request_id", "included_in_context"),
    )

    id: Mapped[uuid.UUID] = pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hey_kivi_request.id", ondelete="CASCADE"), nullable=False
    )
    retrieval_event_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("retrieval_event.id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memory.id", ondelete="CASCADE")
    )
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("transcript.id", ondelete="CASCADE")
    )
    strategy: Mapped[enums.RetrievalStrategy] = mapped_column(
        pg_enum(enums.RetrievalStrategy, "retrieval_strategy"), nullable=False
    )
    score: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    rank: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    fused_score: Mapped[float | None] = mapped_column(sa.Float)
    included_in_context: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    exclusion_reason: Mapped[enums.ExclusionReason | None] = mapped_column(
        pg_enum(enums.ExclusionReason, "exclusion_reason")
    )
    created_at: Mapped[datetime] = created_at()


class ToolCall(Base):
    """A Hey Kivi tool invocation against real application state.

    Arguments and result are stored verbatim so a reviewer can confirm the tool
    actually ran rather than being narrated by the model.
    """

    __tablename__ = "tool_call"

    id: Mapped[uuid.UUID] = pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hey_kivi_request.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[enums.ToolStatus] = mapped_column(
        pg_enum(enums.ToolStatus, "tool_status"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = created_at()


class DecisionEvent(Base):
    """The single append-only audit timeline.

    Denormalized on purpose. The typed tables above are the source of truth;
    this is the read-optimized stream that powers both the product's "why did
    you say that?" panel and the evaluation report, neither of which should
    have to union five tables to render a chronology. ``detail_table`` and
    ``detail_id`` point back at the authoritative row.
    """

    __tablename__ = "decision_event"
    __table_args__ = (
        sa.Index("ix_decision_user_created", "user_id", "created_at"),
        sa.Index("ix_decision_subject", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[enums.DecisionEventKind] = mapped_column(
        pg_enum(enums.DecisionEventKind, "decision_event_kind"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("hey_kivi_request.id", ondelete="CASCADE")
    )
    reason_code: Mapped[str | None] = mapped_column(sa.Text)
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    detail_table: Mapped[str | None] = mapped_column(sa.Text)
    detail_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


# ==========================================================================
# Evaluation
# ==========================================================================


class EvalCase(Base):
    """A single assertion about how the system should behave.

    Cases live in the database rather than only on disk so that a result row
    can foreign-key to the exact case revision it was graded against.
    """

    __tablename__ = "eval_case"
    __table_args__ = (
        sa.UniqueConstraint("slug", "revision", name="uq_eval_case_slug_rev"),
    )

    id: Mapped[uuid.UUID] = pk()
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    case_type: Mapped[enums.EvalCaseType] = mapped_column(
        pg_enum(enums.EvalCaseType, "eval_case_type"), nullable=False
    )
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Free-form because expectations differ by case type: an abstention case
    # asserts a reason code, a retrieval case asserts a set of memory slugs.
    given: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    expected: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Cases known to fail today are marked, so the suite reports honestly
    # instead of being trimmed to whatever passes.
    known_failure: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    created_at: Mapped[datetime] = created_at()


class EvalRun(Base):
    """One execution of the suite, with the configuration it ran under."""

    __tablename__ = "eval_run"

    id: Mapped[uuid.UUID] = pk()
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    git_sha: Mapped[str | None] = mapped_column(sa.Text)
    chat_provider: Mapped[str | None] = mapped_column(sa.Text)
    embedding_provider: Mapped[str | None] = mapped_column(sa.Text)
    seed: Mapped[int | None] = mapped_column(sa.Integer)
    settings_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    cases_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cases_passed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cases_failed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cases_errored: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    tokens_in: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0.0
    )
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class EvalResult(Base):
    """How one case fared in one run.

    ``request_id`` is the join that makes a failure diagnosable: from a failed
    row a reviewer reaches the retrieval events, the hits that were dropped and
    why, the tool calls, and the evidence -- without re-running anything.
    """

    __tablename__ = "eval_result"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "case_id", name="uq_eval_result_case"),
        sa.Index("ix_eval_result_outcome", "run_id", "outcome"),
    )

    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("eval_run.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("eval_case.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[enums.EvalOutcome] = mapped_column(
        pg_enum(enums.EvalOutcome, "eval_outcome"), nullable=False
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("hey_kivi_request.id", ondelete="SET NULL")
    )
    observed: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    failure_detail: Mapped[str | None] = mapped_column(sa.Text)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = created_at()


class DbSizeSample(Base):
    """Per-table size, sampled at named points in a run.

    The assignment asks for database growth; measuring it needs a before and an
    after, so it is recorded rather than computed at the end.
    """

    __tablename__ = "db_size_sample"
    __table_args__ = (
        sa.Index("ix_db_size_run_stage", "run_id", "stage"),
    )

    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("eval_run.id", ondelete="CASCADE")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ingestion_job.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    table_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    row_count: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at()

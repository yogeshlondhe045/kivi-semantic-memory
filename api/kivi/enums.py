"""Controlled vocabularies for the Kivi semantic-memory system.

These enums are the system's testable contract. Every decision the pipeline
makes is recorded as one of these codes rather than as free text, so the
evaluation in Phase 7 can measure behaviour by counting codes instead of
string-matching model prose.

Anything the model writes in prose lands in a short ``note`` column beside the
code. Notes are concise, engineer-readable justifications ("supported by 3
independent interactions"), never chain-of-thought.
"""

from __future__ import annotations

import enum


class StrEnum(str, enum.Enum):
    """String-valued enum; the value is what is stored in Postgres."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# --------------------------------------------------------------------------
# Memory shape
# --------------------------------------------------------------------------


class MemoryKind(StrEnum):
    """The three forms of understanding named by the assignment.

    PRODUCT-GATED (decision D1): the Part One documents may narrow this set.
    Narrowing is an enum change plus an extraction-prompt change, not a
    re-architecture -- which is why the set lives here and nowhere else.
    """

    FACTUAL = "factual"
    PREFERENCE = "preference"
    EPISODIC = "episodic"


class MemoryStatus(StrEnum):
    """Lifecycle position of a belief.

    PROPOSED is the load-bearing one: it is the honest resting place for a
    single weak mention. Only PROPOSED and ACTIVE memories exist; only ACTIVE
    memories are retrievable.
    """

    PROPOSED = "proposed"          # seen once, not yet enough evidence to act on
    ACTIVE = "active"              # admitted; retrievable
    SUPERSEDED = "superseded"      # a later memory replaced it
    RETRACTED = "retracted"        # the user removed it
    EXPIRED = "expired"            # its valid_to passed
    PURGED = "purged"              # user demanded erasure; statement nulled


class MemoryVolatility(StrEnum):
    """How quickly a belief of this shape goes stale.

    Drives recency decay in the confidence function and the expiry sweep.
    """

    DURABLE = "durable"            # "writes in British English"
    SLOW = "slow"                  # "works with the Acme account"
    TIME_BOUNDED = "time_bounded"  # "is on leave until the 14th"


class EvidenceRelation(StrEnum):
    """How one observed transcript span bears on a memory."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"


class MemoryLinkType(StrEnum):
    """Typed relationships between memories.

    ``CONTRADICTS`` is why there is no separate memory_conflict table: a
    conflict is an edge plus the two endpoints' statuses.
    """

    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    DUPLICATES = "duplicates"
    REFINES = "refines"
    DERIVED_FROM = "derived_from"  # a belief consolidated from other memories


class MemoryChangeType(StrEnum):
    """What a memory_version row records."""

    CREATED = "created"
    REINFORCED = "reinforced"      # more evidence, same claim
    REFINED = "refined"            # same claim, sharper wording or slots
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    EXPIRED = "expired"
    USER_EDITED = "user_edited"
    USER_DELETED = "user_deleted"
    USER_PINNED = "user_pinned"
    USER_RESTORED = "user_restored"


# --------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------


class AdmissionDecision(StrEnum):
    """What the admission policy did with an extracted candidate."""

    CREATE = "create"
    REINFORCE = "reinforce"
    REFINE = "refine"
    SUPERSEDE = "supersede"
    REJECT = "reject"
    DEFER = "defer"                # held as PROPOSED pending corroboration


class AdmissionReason(StrEnum):
    """Why the admission policy decided what it did.

    The rejection codes encode what the system *deliberately ignores*. The
    first is the one that matters most for a dictation corpus: most dictation
    is the user composing text for somebody else, so the content of a dictated
    message is not automatically a belief the user holds.
    """

    # -- rejections -------------------------------------------------------
    DICTATION_CONTENT_NOT_ASSERTION = "dictation_content_not_assertion"
    NOT_ABOUT_USER = "not_about_user"
    TRANSIENT_CONTEXT = "transient_context"
    LOW_SPECIFICITY = "low_specificity"
    SENSITIVE_EXCLUDED = "sensitive_excluded"
    NOT_SALIENT = "not_salient"                # skipped by the pre-LLM gate
    ASR_UNRELIABLE = "asr_unreliable"          # raw/formatted disagree badly
    EXTRACTION_FAILED = "extraction_failed"    # model output never validated

    # -- admissions -------------------------------------------------------
    EXPLICIT_DURABLE_ASSERTION = "explicit_durable_assertion"
    CORROBORATED_BY_REPETITION = "corroborated_by_repetition"
    DUPLICATE_REINFORCES = "duplicate_reinforces"
    SHARPER_RESTATEMENT = "sharper_restatement"
    EXPLICIT_CORRECTION = "explicit_correction"
    CONTRADICTS_EXISTING = "contradicts_existing"
    STALE_SUPERSEDED = "stale_superseded"
    CONSOLIDATED_FROM_EPISODES = "consolidated_from_episodes"

    # -- deferral ---------------------------------------------------------
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# --------------------------------------------------------------------------
# Retrieval and answering
# --------------------------------------------------------------------------


class RetrievalStrategy(StrEnum):
    """Which retrieval arm produced a candidate.

    Recorded per hit so the evaluation can attribute recall to an arm and
    justify (or drop) each one.
    """

    MEMORY_VECTOR = "memory_vector"
    MEMORY_LEXICAL = "memory_lexical"
    MEMORY_STRUCTURED = "memory_structured"
    TRANSCRIPT_VECTOR = "transcript_vector"
    TRANSCRIPT_LEXICAL = "transcript_lexical"
    TRANSCRIPT_STRUCTURED = "transcript_structured"
    FUSION = "fusion"


class ExclusionReason(StrEnum):
    """Why a retrieved candidate did not reach the answer.

    This is the record that answers "why did memory *not* affect this result".
    Most systems log only what was used.
    """

    BELOW_SCORE_THRESHOLD = "below_score_threshold"
    BELOW_CONFIDENCE_FLOOR = "below_confidence_floor"
    NOT_ACTIVE = "not_active"
    USER_HIDDEN = "user_hidden"
    OUTSIDE_VALIDITY_WINDOW = "outside_validity_window"
    OUTSIDE_REQUESTED_TIME_RANGE = "outside_requested_time_range"
    WRONG_SURFACE = "wrong_surface"
    WRONG_KIND = "wrong_kind"
    SUPERSEDED_BY_LATER = "superseded_by_later"
    LOST_CONTRADICTION = "lost_contradiction"
    RANK_CUTOFF = "rank_cutoff"
    REDUNDANT_WITH_HIGHER_RANKED = "redundant_with_higher_ranked"


class RequestOutcome(StrEnum):
    """How a Hey Kivi turn ended."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    CLARIFY = "clarify"
    ACTION_TAKEN = "action_taken"
    FAILED = "failed"


class AbstentionReason(StrEnum):
    """Why Hey Kivi declined to answer.

    Set by the deterministic sufficiency gate *before* generation, or by the
    post-generation citation check.
    """

    NO_EVIDENCE = "no_evidence"
    WEAK_EVIDENCE = "weak_evidence"
    STALE_ONLY = "stale_only"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNCITED_GENERATION = "uncited_generation"


class GroundingKind(StrEnum):
    """Whether an answer rests on something observed or something inferred."""

    DIRECTLY_OBSERVED = "directly_observed"   # a transcript says it
    DERIVED = "derived"                       # consolidated across memories
    TOOL_RESULT = "tool_result"               # produced by a tool this turn


# --------------------------------------------------------------------------
# Pipeline plumbing
# --------------------------------------------------------------------------


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"      # some records failed; never silently "succeeded"


class ItemStatus(StrEnum):
    PENDING = "pending"
    NORMALIZED = "normalized"
    SKIPPED_NOT_SALIENT = "skipped_not_salient"
    EXTRACTED = "extracted"
    ADMITTED = "admitted"
    FAILED = "failed"
    DUPLICATE = "duplicate"   # same external_id/content_hash already ingested


class ToolStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"     # arguments failed validation; never executed


class DecisionEventKind(StrEnum):
    """Channel of the unified audit timeline."""

    ADMISSION = "admission"
    CONFLICT = "conflict"
    RETRIEVAL = "retrieval"
    ABSTENTION = "abstention"
    TOOL = "tool"
    USER_ACTION = "user_action"
    LIFECYCLE = "lifecycle"   # expiry sweeps, consolidation


class EvalCaseType(StrEnum):
    """What a case probes. Every type must be represented in the suite."""

    MEMORY_ADMISSION = "memory_admission"
    MEMORY_REJECTION = "memory_rejection"
    MEMORY_UPDATE = "memory_update"
    MEMORY_REMOVAL = "memory_removal"
    MULTI_SOURCE = "multi_source"
    PROVENANCE = "provenance"
    ABSTENTION = "abstention"
    RETRIEVAL = "retrieval"
    TOOL_USE = "tool_use"


class EvalOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"

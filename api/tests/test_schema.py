"""Schema-level verification for the Phase 2 data model.

These are not unit tests of business logic -- there is no business logic yet.
They assert that the schema can physically represent the things the product
claims, by building a real chain in a real Postgres and traversing it:

    answer -> cited memory -> version history -> evidence -> transcript span
           -> the raw ASR the user actually spoke

and by asserting that the constraints which protect those claims actually
reject bad data. A schema whose guarantees are only described in a document is
a document, not a guarantee.

Run against a live database:

    KIVI_DATABASE_URL=postgresql+psycopg://kivi:kivi@localhost:5432/kivi \
        pytest tests/test_schema.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from kivi import enums
from kivi.config import EMBEDDING_DIM, get_settings
from kivi.db import models as m

pytestmark = pytest.mark.skipif(
    not os.environ.get("KIVI_DATABASE_URL"),
    reason="requires a live Postgres; set KIVI_DATABASE_URL",
)

T0 = datetime(2026, 3, 2, 11, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine():
    return sa.create_engine(get_settings().database_url, future=True)


@pytest.fixture
def db(engine):
    """A session rolled back at the end, so tests leave no residue."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, future=True)()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _user(db: Session) -> m.AppUser:
    user = m.AppUser(
        handle=f"test-{uuid.uuid4().hex[:8]}", display_name="Test User"
    )
    db.add(user)
    db.flush()
    return user


def _transcript(db: Session, user, *, raw: str, formatted: str, at: datetime,
                surface: str = "slack") -> m.Transcript:
    t = m.Transcript(
        user_id=user.id,
        external_id=f"ext-{uuid.uuid4().hex[:10]}",
        content_hash=uuid.uuid4().hex,
        raw_asr=raw,
        formatted_text=formatted,
        occurred_at=at,
        app_surface=surface,
        language="en-IN",
        asr_agreement=0.94,
    )
    db.add(t)
    db.flush()
    return t


def _memory(db: Session, user, *, statement: str, kind=enums.MemoryKind.PREFERENCE,
            status=enums.MemoryStatus.ACTIVE, confidence: float = 0.8) -> m.Memory:
    mem = m.Memory(
        user_id=user.id, kind=kind, statement=statement,
        subject_key="standup-notes", status=status, confidence=confidence,
        volatility=enums.MemoryVolatility.DURABLE, first_observed_at=T0,
        last_observed_at=T0,
    )
    db.add(mem)
    db.flush()
    return mem


def _version(db: Session, mem, *, no: int, change, reason, note: str,
             status=enums.MemoryStatus.ACTIVE) -> m.MemoryVersion:
    v = m.MemoryVersion(
        memory_id=mem.id, version_no=no, change_type=change, reason_code=reason,
        note=note, statement=mem.statement, status=status,
        confidence=mem.confidence,
    )
    db.add(v)
    db.flush()
    return v


# ---------------------------------------------------------------------------
# The claim: a belief is traceable to the words that produced it.
# ---------------------------------------------------------------------------


def test_provenance_chain_resolves_to_raw_speech(db):
    """Two dictations corroborate one preference; the chain walks back to ASR."""
    user = _user(db)
    t1 = _transcript(
        db, user,
        raw="keep the standup notes short just bullets no preamble",
        formatted="Keep the standup notes short - just bullets, no preamble.",
        at=T0,
    )
    t2 = _transcript(
        db, user,
        raw="again bullets only for standup please no intro paragraph",
        formatted="Again, bullets only for standup please, no intro paragraph.",
        at=T0 + timedelta(days=6),
        surface="notion",
    )

    mem = _memory(db, user, statement="Prefers standup notes as bullets with no preamble.")
    v1 = _version(
        db, mem, no=1, change=enums.MemoryChangeType.CREATED,
        reason=enums.AdmissionReason.EXPLICIT_DURABLE_ASSERTION,
        note="Explicit durable preference stated in first person.",
    )
    v2 = _version(
        db, mem, no=2, change=enums.MemoryChangeType.REINFORCED,
        reason=enums.AdmissionReason.CORROBORATED_BY_REPETITION,
        note="Restated 6 days later on a different surface.",
    )
    mem.current_version_id = v2.id
    mem.evidence_count = 2
    mem.independent_source_count = 2

    for transcript, version in ((t1, v1), (t2, v2)):
        db.add(m.MemoryEvidence(
            memory_id=mem.id, transcript_id=transcript.id,
            introduced_in_version_id=version.id,
            relation=enums.EvidenceRelation.SUPPORTS,
            span_start=0, span_end=len(transcript.formatted_text),
            quote=transcript.formatted_text, observed_at=transcript.occurred_at,
        ))
    db.flush()

    # The traversal a reviewer would perform from an answer citation.
    rows = db.execute(
        sa.select(m.Transcript.raw_asr, m.Transcript.app_surface,
                  m.MemoryEvidence.quote, m.MemoryVersion.note)
        .join(m.MemoryEvidence, m.MemoryEvidence.transcript_id == m.Transcript.id)
        .join(m.MemoryVersion,
              m.MemoryVersion.id == m.MemoryEvidence.introduced_in_version_id)
        .where(m.MemoryEvidence.memory_id == mem.id)
        .order_by(m.Transcript.occurred_at)
    ).all()

    assert len(rows) == 2
    assert "bullets" in rows[0].raw_asr
    assert rows[0].app_surface != rows[1].app_surface, (
        "independent sources should be distinguishable by surface"
    )
    # Notes are short justifications, never chain-of-thought.
    assert all(len(r.note) < 120 for r in rows)


def test_correction_supersedes_without_losing_the_original(db):
    """A later correction retires the old belief; both stay inspectable."""
    user = _user(db)
    old = _memory(db, user, statement="Prefers bullets for standup notes.")
    _version(db, old, no=1, change=enums.MemoryChangeType.CREATED,
             reason=enums.AdmissionReason.EXPLICIT_DURABLE_ASSERTION,
             note="First observation.")
    new = _memory(db, user, statement="Prefers short prose for standup notes.")
    _version(db, new, no=1, change=enums.MemoryChangeType.CREATED,
             reason=enums.AdmissionReason.EXPLICIT_CORRECTION,
             note="User explicitly corrected the earlier preference.")

    old.status = enums.MemoryStatus.SUPERSEDED
    retire = _version(
        db, old, no=2, change=enums.MemoryChangeType.SUPERSEDED,
        reason=enums.AdmissionReason.EXPLICIT_CORRECTION,
        note="Superseded by a later explicit correction.",
        status=enums.MemoryStatus.SUPERSEDED,
    )
    old.current_version_id = retire.id
    db.add(m.MemoryLink(from_memory_id=new.id, to_memory_id=old.id,
                        link_type=enums.MemoryLinkType.SUPERSEDES))
    db.flush()

    # The retired belief is still there, still explains itself, and is no
    # longer active. "What did Kivi used to think?" is a query, not a guess.
    assert old.status is enums.MemoryStatus.SUPERSEDED
    assert db.get(m.MemoryVersion, retire.id).note.startswith("Superseded by")
    assert db.scalar(
        sa.select(sa.func.count()).select_from(m.MemoryVersion)
        .where(m.MemoryVersion.memory_id == old.id)
    ) == 2


def test_dropped_retrieval_candidate_records_why(db):
    """The record that explains why memory did NOT affect a result."""
    user = _user(db)
    kept = _memory(db, user, statement="Ships the Acme report on Fridays.")
    dropped = _memory(db, user, statement="Was on leave until 14 March.",
                      status=enums.MemoryStatus.EXPIRED, confidence=0.7)

    req = m.HeyKiviRequest(
        user_id=user.id, request_text="When do I send the Acme report?",
        outcome=enums.RequestOutcome.ANSWERED,
        grounding_kind=enums.GroundingKind.DIRECTLY_OBSERVED,
        answer_text="Fridays.", cited_memory_ids=[str(kept.id)],
        retrieval_ms=31, total_ms=402,
    )
    db.add(req)
    db.flush()
    event = m.RetrievalEvent(
        request_id=req.id, strategy=enums.RetrievalStrategy.MEMORY_VECTOR,
        query_text="acme report schedule", candidate_count=2, latency_ms=12,
    )
    db.add(event)
    db.flush()
    db.add_all([
        m.RetrievalHit(
            request_id=req.id, retrieval_event_id=event.id, memory_id=kept.id,
            strategy=enums.RetrievalStrategy.MEMORY_VECTOR, score=0.81, rank=1,
            included_in_context=True,
        ),
        m.RetrievalHit(
            request_id=req.id, retrieval_event_id=event.id, memory_id=dropped.id,
            strategy=enums.RetrievalStrategy.MEMORY_VECTOR, score=0.44, rank=2,
            included_in_context=False,
            exclusion_reason=enums.ExclusionReason.OUTSIDE_VALIDITY_WINDOW,
        ),
    ])
    db.flush()

    excluded = db.execute(
        sa.select(m.RetrievalHit.exclusion_reason, m.Memory.statement)
        .join(m.Memory, m.Memory.id == m.RetrievalHit.memory_id)
        .where(m.RetrievalHit.request_id == req.id,
               m.RetrievalHit.included_in_context.is_(False))
    ).all()

    assert len(excluded) == 1
    assert excluded[0].exclusion_reason is enums.ExclusionReason.OUTSIDE_VALIDITY_WINDOW
    assert "leave" in excluded[0].statement


def test_rejected_candidate_is_persisted_with_a_reason(db):
    """Deliberate ignoring is a stored row, not an absence."""
    user = _user(db)
    t = _transcript(
        db, user,
        raw="hi sarah the deadline is tuesday let me know if that works",
        formatted="Hi Sarah, the deadline is Tuesday. Let me know if that works.",
        at=T0,
    )
    quote = "the deadline is Tuesday"
    start = t.formatted_text.index(quote)
    db.add(m.MemoryCandidate(
        user_id=user.id, transcript_id=t.id,
        proposed_kind=enums.MemoryKind.FACTUAL,
        proposed_statement="The deadline is Tuesday.",
        decision=enums.AdmissionDecision.REJECT,
        reason_code=enums.AdmissionReason.DICTATION_CONTENT_NOT_ASSERTION,
        note="Message composed for a recipient; not a claim the user holds.",
        quote=quote, span_start=start, span_end=start + len(quote),
        admission_score=0.12,
    ))
    db.flush()

    row = db.execute(
        sa.select(m.MemoryCandidate).where(m.MemoryCandidate.transcript_id == t.id)
    ).scalar_one()
    assert row.decision is enums.AdmissionDecision.REJECT
    assert row.resolved_memory_id is None
    # The rejection points back at the exact words it declined to believe.
    assert t.formatted_text[row.span_start:row.span_end] == quote


# ---------------------------------------------------------------------------
# The constraints that keep the above honest.
# ---------------------------------------------------------------------------


def test_reimporting_the_same_record_cannot_inflate_evidence(db):
    """Confidence is driven by evidence count, so double-import must fail."""
    user = _user(db)
    t = _transcript(db, user, raw="a", formatted="A", at=T0)
    db.add(m.Transcript(
        user_id=user.id, external_id=t.external_id, content_hash="different",
        raw_asr="a", formatted_text="A", occurred_at=T0,
    ))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()


def test_a_retrieval_hit_targets_exactly_one_thing(db):
    user = _user(db)
    mem = _memory(db, user, statement="x")
    t = _transcript(db, user, raw="a", formatted="A", at=T0)
    req = m.HeyKiviRequest(user_id=user.id, request_text="q",
                           outcome=enums.RequestOutcome.ABSTAINED,
                           abstention_reason=enums.AbstentionReason.NO_EVIDENCE)
    db.add(req)
    db.flush()
    ev = m.RetrievalEvent(request_id=req.id,
                          strategy=enums.RetrievalStrategy.FUSION)
    db.add(ev)
    db.flush()
    db.add(m.RetrievalHit(
        request_id=req.id, retrieval_event_id=ev.id,
        memory_id=mem.id, transcript_id=t.id,  # both -> rejected
        strategy=enums.RetrievalStrategy.FUSION, score=0.5, rank=1,
    ))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()


def test_confidence_cannot_leave_the_unit_interval(db):
    user = _user(db)
    db.add(m.Memory(user_id=user.id, kind=enums.MemoryKind.FACTUAL,
                    statement="x", confidence=1.4))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()


def test_a_memory_cannot_contradict_itself(db):
    user = _user(db)
    mem = _memory(db, user, statement="x")
    db.add(m.MemoryLink(from_memory_id=mem.id, to_memory_id=mem.id,
                        link_type=enums.MemoryLinkType.CONTRADICTS))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()


def test_full_text_column_is_generated_by_the_database(db):
    """Lexical retrieval must not depend on the application remembering to
    populate a column."""
    user = _user(db)
    t = _transcript(db, user, raw="acme quarterly numbers",
                    formatted="The Acme quarterly numbers are ready.", at=T0)
    db.flush()
    hit = db.scalar(
        sa.select(sa.func.count()).select_from(m.Transcript).where(
            m.Transcript.id == t.id,
            m.Transcript.tsv.op("@@")(sa.func.plainto_tsquery("english", "acme")),
        )
    )
    assert hit == 1


def test_vector_columns_accept_the_configured_dimension(db):
    user = _user(db)
    mem = _memory(db, user, statement="x")
    db.add(m.MemoryEmbedding(
        memory_id=mem.id, provider="offline", model="offline-hash-v1",
        dim=EMBEDDING_DIM, vector=[0.0] * EMBEDDING_DIM, source_text="x",
    ))
    db.flush()
    stored = db.execute(
        sa.select(m.MemoryEmbedding).where(m.MemoryEmbedding.memory_id == mem.id)
    ).scalar_one()
    assert len(stored.vector) == EMBEDDING_DIM

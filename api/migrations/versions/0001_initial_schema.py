"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-09-14 04:58:08.938444
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import pgvector.sqlalchemy

revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector must exist before any vector column is created. IF NOT EXISTS
    # keeps the migration idempotent against a database where an operator
    # (or a managed provider) already enabled it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table('app_user',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('handle', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('timezone', sa.Text(), server_default='Asia/Kolkata', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('handle')
    )
    op.create_table('eval_case',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('case_type', sa.Enum('memory_admission', 'memory_rejection', 'memory_update', 'memory_removal', 'multi_source', 'provenance', 'abstention', 'retrieval', 'tool_use', name='eval_case_type'), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('given', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('expected', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('known_failure', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug', 'revision', name='uq_eval_case_slug_rev')
    )
    op.create_table('eval_run',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('git_sha', sa.Text(), nullable=True),
    sa.Column('chat_provider', sa.Text(), nullable=True),
    sa.Column('embedding_provider', sa.Text(), nullable=True),
    sa.Column('seed', sa.Integer(), nullable=True),
    sa.Column('settings_snapshot', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('cases_total', sa.Integer(), nullable=False),
    sa.Column('cases_passed', sa.Integer(), nullable=False),
    sa.Column('cases_failed', sa.Integer(), nullable=False),
    sa.Column('cases_errored', sa.Integer(), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('tokens_in', sa.BigInteger(), nullable=False),
    sa.Column('tokens_out', sa.BigInteger(), nullable=False),
    sa.Column('cost_estimate_usd', sa.Float(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('hey_kivi_request',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('request_text', sa.Text(), nullable=False),
    sa.Column('retrieval_plan', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('outcome', sa.Enum('answered', 'abstained', 'clarify', 'action_taken', 'failed', name='request_outcome'), nullable=False),
    sa.Column('abstention_reason', sa.Enum('no_evidence', 'weak_evidence', 'stale_only', 'conflicting_evidence', 'out_of_scope', 'needs_clarification', 'uncited_generation', name='abstention_reason'), nullable=True),
    sa.Column('grounding_kind', sa.Enum('directly_observed', 'derived', 'tool_result', name='grounding_kind'), nullable=True),
    sa.Column('answer_text', sa.Text(), nullable=True),
    sa.Column('cited_memory_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('understand_ms', sa.Integer(), nullable=True),
    sa.Column('retrieval_ms', sa.Integer(), nullable=True),
    sa.Column('tool_ms', sa.Integer(), nullable=True),
    sa.Column('generation_ms', sa.Integer(), nullable=True),
    sa.Column('total_ms', sa.Integer(), nullable=True),
    sa.Column('chat_provider', sa.Text(), nullable=True),
    sa.Column('chat_model', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_estimate_usd', sa.Float(), nullable=False),
    sa.Column('degraded', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_request_user_created', 'hey_kivi_request', ['user_id', 'created_at'], unique=False)
    op.create_table('ingestion_job',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('source_label', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'succeeded', 'failed', 'partial', name='job_status'), nullable=False),
    sa.Column('records_total', sa.Integer(), nullable=False),
    sa.Column('records_ingested', sa.Integer(), nullable=False),
    sa.Column('records_skipped', sa.Integer(), nullable=False),
    sa.Column('records_failed', sa.Integer(), nullable=False),
    sa.Column('chat_provider', sa.Text(), nullable=True),
    sa.Column('embedding_provider', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.BigInteger(), nullable=False),
    sa.Column('tokens_out', sa.BigInteger(), nullable=False),
    sa.Column('cost_estimate_usd', sa.Float(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ingestion_job_user_id'), 'ingestion_job', ['user_id'], unique=False)
    op.create_table('memory',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('factual', 'preference', 'episodic', name='memory_kind'), nullable=False),
    sa.Column('statement', sa.Text(), nullable=True),
    sa.Column('subject_key', sa.Text(), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.Enum('proposed', 'active', 'superseded', 'retracted', 'expired', 'purged', name='memory_status'), nullable=False),
    sa.Column('volatility', sa.Enum('durable', 'slow', 'time_bounded', name='memory_volatility'), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('evidence_count', sa.Integer(), nullable=False),
    sa.Column('independent_source_count', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_pinned', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('user_hidden', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('sensitive', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('current_version_id', sa.UUID(), nullable=True),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', coalesce(statement, '') || ' ' || coalesce(subject_key, ''))", persisted=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='ck_memory_conf'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_subject', 'memory', ['user_id', 'subject_key'], unique=False)
    op.create_index('ix_memory_tsv', 'memory', ['tsv'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_memory_user_id'), 'memory', ['user_id'], unique=False)
    op.create_index('ix_memory_user_status_kind', 'memory', ['user_id', 'status', 'kind'], unique=False)
    op.create_index('ix_memory_validity', 'memory', ['user_id', 'valid_from', 'valid_to'], unique=False)
    op.create_table('transcript',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('external_id', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('raw_asr', sa.Text(), nullable=False),
    sa.Column('formatted_text', sa.Text(), nullable=False),
    sa.Column('asr_agreement', sa.Float(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('app_surface', sa.Text(), nullable=True),
    sa.Column('device', sa.Text(), nullable=True),
    sa.Column('language', sa.Text(), nullable=True),
    sa.Column('style_id', sa.Text(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', coalesce(formatted_text, ''))", persisted=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'external_id', name='uq_transcript_external')
    )
    op.create_index(op.f('ix_transcript_content_hash'), 'transcript', ['content_hash'], unique=False)
    op.create_index('ix_transcript_surface', 'transcript', ['user_id', 'app_surface', 'occurred_at'], unique=False)
    op.create_index('ix_transcript_tsv', 'transcript', ['tsv'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_transcript_user_id'), 'transcript', ['user_id'], unique=False)
    op.create_index('ix_transcript_user_occurred', 'transcript', ['user_id', 'occurred_at'], unique=False)
    op.create_table('db_size_sample',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('stage', sa.Text(), nullable=False),
    sa.Column('table_name', sa.Text(), nullable=False),
    sa.Column('row_count', sa.BigInteger(), nullable=False),
    sa.Column('total_bytes', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['ingestion_job.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['eval_run.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_db_size_run_stage', 'db_size_sample', ['run_id', 'stage'], unique=False)
    op.create_table('decision_event',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('admission', 'conflict', 'retrieval', 'abstention', 'tool', 'user_action', 'lifecycle', name='decision_event_kind'), nullable=False),
    sa.Column('subject_type', sa.Text(), nullable=False),
    sa.Column('subject_id', sa.UUID(), nullable=True),
    sa.Column('request_id', sa.UUID(), nullable=True),
    sa.Column('reason_code', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('detail_table', sa.Text(), nullable=True),
    sa.Column('detail_id', sa.UUID(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['request_id'], ['hey_kivi_request.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_decision_subject', 'decision_event', ['subject_type', 'subject_id'], unique=False)
    op.create_index('ix_decision_user_created', 'decision_event', ['user_id', 'created_at'], unique=False)
    op.create_table('eval_result',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('outcome', sa.Enum('passed', 'failed', 'errored', 'skipped', name='eval_outcome'), nullable=False),
    sa.Column('request_id', sa.UUID(), nullable=True),
    sa.Column('observed', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('failure_detail', sa.Text(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['eval_case.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['request_id'], ['hey_kivi_request.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['eval_run.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'case_id', name='uq_eval_result_case')
    )
    op.create_index('ix_eval_result_outcome', 'eval_result', ['run_id', 'outcome'], unique=False)
    op.create_table('ingestion_item',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('transcript_id', sa.UUID(), nullable=True),
    sa.Column('source_index', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'normalized', 'skipped_not_salient', 'extracted', 'admitted', 'failed', 'duplicate', name='item_status'), nullable=False),
    sa.Column('normalize_ms', sa.Integer(), nullable=True),
    sa.Column('extract_ms', sa.Integer(), nullable=True),
    sa.Column('admit_ms', sa.Integer(), nullable=True),
    sa.Column('embed_ms', sa.Integer(), nullable=True),
    sa.Column('candidates_extracted', sa.Integer(), nullable=False),
    sa.Column('candidates_admitted', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['ingestion_job.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcript.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ingestion_item_job_status', 'ingestion_item', ['job_id', 'status'], unique=False)
    op.create_table('memory_embedding',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('memory_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('dim', sa.Integer(), nullable=False),
    sa.Column('vector', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['memory_id'], ['memory.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('memory_id', 'model', name='uq_memory_embedding_model')
    )
    op.create_table('memory_link',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('from_memory_id', sa.UUID(), nullable=False),
    sa.Column('to_memory_id', sa.UUID(), nullable=False),
    sa.Column('link_type', sa.Enum('supersedes', 'contradicts', 'duplicates', 'refines', 'derived_from', name='memory_link_type'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('from_memory_id <> to_memory_id', name='ck_memory_link_self'),
    sa.ForeignKeyConstraint(['from_memory_id'], ['memory.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_memory_id'], ['memory.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('from_memory_id', 'to_memory_id', 'link_type', name='uq_memory_link')
    )
    op.create_index('ix_memory_link_to', 'memory_link', ['to_memory_id', 'link_type'], unique=False)
    op.create_table('memory_version',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('memory_id', sa.UUID(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('change_type', sa.Enum('created', 'reinforced', 'refined', 'superseded', 'retracted', 'expired', 'user_edited', 'user_deleted', 'user_pinned', 'user_restored', name='memory_change_type'), nullable=False),
    sa.Column('reason_code', sa.Enum('dictation_content_not_assertion', 'not_about_user', 'transient_context', 'low_specificity', 'sensitive_excluded', 'not_salient', 'asr_unreliable', 'extraction_failed', 'explicit_durable_assertion', 'corroborated_by_repetition', 'duplicate_reinforces', 'sharper_restatement', 'explicit_correction', 'contradicts_existing', 'stale_superseded', 'consolidated_from_episodes', 'insufficient_evidence', name='admission_reason'), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('statement', sa.Text(), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.Enum('proposed', 'active', 'superseded', 'retracted', 'expired', 'purged', name='memory_status'), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('actor', sa.Text(), server_default='system', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['memory_id'], ['memory.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('memory_id', 'version_no', name='uq_memory_version_no')
    )
    op.create_index('ix_memory_version_memory', 'memory_version', ['memory_id', 'version_no'], unique=False)
    op.create_table('retrieval_event',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('request_id', sa.UUID(), nullable=False),
    sa.Column('strategy', sa.Enum('memory_vector', 'memory_lexical', 'memory_structured', 'transcript_vector', 'transcript_lexical', 'transcript_structured', 'fusion', name='retrieval_strategy'), nullable=False),
    sa.Column('query_text', sa.Text(), nullable=True),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('candidate_count', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['request_id'], ['hey_kivi_request.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_retrieval_event_request_id'), 'retrieval_event', ['request_id'], unique=False)
    op.create_table('tool_call',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('request_id', sa.UUID(), nullable=False),
    sa.Column('step', sa.Integer(), nullable=False),
    sa.Column('tool_name', sa.Text(), nullable=False),
    sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.Enum('succeeded', 'failed', 'rejected', name='tool_status'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['request_id'], ['hey_kivi_request.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_call_request_id'), 'tool_call', ['request_id'], unique=False)
    op.create_table('transcript_embedding',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('transcript_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('dim', sa.Integer(), nullable=False),
    sa.Column('vector', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcript.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('transcript_id')
    )
    op.create_table('memory_candidate',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('transcript_id', sa.UUID(), nullable=False),
    sa.Column('ingestion_item_id', sa.UUID(), nullable=True),
    sa.Column('proposed_kind', sa.Enum('factual', 'preference', 'episodic', name='memory_kind'), nullable=True),
    sa.Column('proposed_statement', sa.Text(), nullable=True),
    sa.Column('proposed_subject_key', sa.Text(), nullable=True),
    sa.Column('proposed_attributes', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('span_start', sa.Integer(), nullable=True),
    sa.Column('span_end', sa.Integer(), nullable=True),
    sa.Column('quote', sa.Text(), nullable=True),
    sa.Column('decision', sa.Enum('create', 'reinforce', 'refine', 'supersede', 'reject', 'defer', name='admission_decision'), nullable=False),
    sa.Column('reason_code', sa.Enum('dictation_content_not_assertion', 'not_about_user', 'transient_context', 'low_specificity', 'sensitive_excluded', 'not_salient', 'asr_unreliable', 'extraction_failed', 'explicit_durable_assertion', 'corroborated_by_repetition', 'duplicate_reinforces', 'sharper_restatement', 'explicit_correction', 'contradicts_existing', 'stale_superseded', 'consolidated_from_episodes', 'insufficient_evidence', name='admission_reason'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('admission_score', sa.Float(), nullable=True),
    sa.Column('resolved_memory_id', sa.UUID(), nullable=True),
    sa.Column('extractor_provider', sa.Text(), nullable=True),
    sa.Column('extractor_model', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('repaired', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ingestion_item_id'], ['ingestion_item.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resolved_memory_id'], ['memory.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcript.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_candidate_decision', 'memory_candidate', ['user_id', 'decision', 'reason_code'], unique=False)
    op.create_index('ix_candidate_transcript', 'memory_candidate', ['transcript_id'], unique=False)
    op.create_table('memory_evidence',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('memory_id', sa.UUID(), nullable=False),
    sa.Column('transcript_id', sa.UUID(), nullable=False),
    sa.Column('introduced_in_version_id', sa.UUID(), nullable=True),
    sa.Column('relation', sa.Enum('supports', 'contradicts', 'qualifies', name='evidence_relation'), nullable=False),
    sa.Column('span_start', sa.Integer(), nullable=False),
    sa.Column('span_end', sa.Integer(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['introduced_in_version_id'], ['memory_version.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['memory_id'], ['memory.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcript.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('memory_id', 'transcript_id', 'span_start', 'span_end', name='uq_memory_evidence_span')
    )
    op.create_index('ix_evidence_memory_relation', 'memory_evidence', ['memory_id', 'relation'], unique=False)
    op.create_index('ix_evidence_transcript', 'memory_evidence', ['transcript_id'], unique=False)
    op.create_table('retrieval_hit',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('request_id', sa.UUID(), nullable=False),
    sa.Column('retrieval_event_id', sa.UUID(), nullable=False),
    sa.Column('memory_id', sa.UUID(), nullable=True),
    sa.Column('transcript_id', sa.UUID(), nullable=True),
    sa.Column('strategy', sa.Enum('memory_vector', 'memory_lexical', 'memory_structured', 'transcript_vector', 'transcript_lexical', 'transcript_structured', 'fusion', name='retrieval_strategy'), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('fused_score', sa.Float(), nullable=True),
    sa.Column('included_in_context', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('exclusion_reason', sa.Enum('below_score_threshold', 'below_confidence_floor', 'not_active', 'user_hidden', 'outside_validity_window', 'outside_requested_time_range', 'wrong_surface', 'wrong_kind', 'superseded_by_later', 'lost_contradiction', 'rank_cutoff', 'redundant_with_higher_ranked', name='exclusion_reason'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(memory_id IS NOT NULL) <> (transcript_id IS NOT NULL)', name='ck_hit_exactly_one_target'),
    sa.ForeignKeyConstraint(['memory_id'], ['memory.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['request_id'], ['hey_kivi_request.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['retrieval_event_id'], ['retrieval_event.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcript.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_hit_event_rank', 'retrieval_hit', ['retrieval_event_id', 'rank'], unique=False)
    op.create_index('ix_hit_request_included', 'retrieval_hit', ['request_id', 'included_in_context'], unique=False)

    # ------------------------------------------------------------------
    # Things autogenerate cannot infer.
    # ------------------------------------------------------------------

    # memory <-> memory_version is a mutual reference: a memory points at its
    # current version, and every version points back at its memory. The
    # constraint is added after both tables exist rather than being dropped,
    # because "which version is live" must be enforced by the database and not
    # by application convention.
    op.create_foreign_key(
        "fk_memory_current_version",
        "memory", "memory_version",
        ["current_version_id"], ["id"],
        ondelete="SET NULL", use_alter=True,
    )

    # Approximate-nearest-neighbour indexes. HNSW rather than IVFFlat: it needs
    # no training pass over an already-populated table, so a reviewer's first
    # ingestion is indexed correctly instead of degrading until someone
    # remembers to REINDEX. Cosine distance matches the L2-normalised vectors
    # every provider in this system emits.
    op.execute(
        "CREATE INDEX ix_memory_embedding_hnsw ON memory_embedding "
        "USING hnsw (vector vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_transcript_embedding_hnsw ON transcript_embedding "
        "USING hnsw (vector vector_cosine_ops)"
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_transcript_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_memory_embedding_hnsw")
    op.drop_constraint("fk_memory_current_version", "memory", type_="foreignkey")
    op.drop_index('ix_hit_request_included', table_name='retrieval_hit')
    op.drop_index('ix_hit_event_rank', table_name='retrieval_hit')
    op.drop_table('retrieval_hit')
    op.drop_index('ix_evidence_transcript', table_name='memory_evidence')
    op.drop_index('ix_evidence_memory_relation', table_name='memory_evidence')
    op.drop_table('memory_evidence')
    op.drop_index('ix_candidate_transcript', table_name='memory_candidate')
    op.drop_index('ix_candidate_decision', table_name='memory_candidate')
    op.drop_table('memory_candidate')
    op.drop_table('transcript_embedding')
    op.drop_index(op.f('ix_tool_call_request_id'), table_name='tool_call')
    op.drop_table('tool_call')
    op.drop_index(op.f('ix_retrieval_event_request_id'), table_name='retrieval_event')
    op.drop_table('retrieval_event')
    op.drop_index('ix_memory_version_memory', table_name='memory_version')
    op.drop_table('memory_version')
    op.drop_index('ix_memory_link_to', table_name='memory_link')
    op.drop_table('memory_link')
    op.drop_table('memory_embedding')
    op.drop_index('ix_ingestion_item_job_status', table_name='ingestion_item')
    op.drop_table('ingestion_item')
    op.drop_index('ix_eval_result_outcome', table_name='eval_result')
    op.drop_table('eval_result')
    op.drop_index('ix_decision_user_created', table_name='decision_event')
    op.drop_index('ix_decision_subject', table_name='decision_event')
    op.drop_table('decision_event')
    op.drop_index('ix_db_size_run_stage', table_name='db_size_sample')
    op.drop_table('db_size_sample')
    op.drop_index('ix_transcript_user_occurred', table_name='transcript')
    op.drop_index(op.f('ix_transcript_user_id'), table_name='transcript')
    op.drop_index('ix_transcript_tsv', table_name='transcript', postgresql_using='gin')
    op.drop_index('ix_transcript_surface', table_name='transcript')
    op.drop_index(op.f('ix_transcript_content_hash'), table_name='transcript')
    op.drop_table('transcript')
    op.drop_index('ix_memory_validity', table_name='memory')
    op.drop_index('ix_memory_user_status_kind', table_name='memory')
    op.drop_index(op.f('ix_memory_user_id'), table_name='memory')
    op.drop_index('ix_memory_tsv', table_name='memory', postgresql_using='gin')
    op.drop_index('ix_memory_subject', table_name='memory')
    op.drop_table('memory')
    op.drop_index(op.f('ix_ingestion_job_user_id'), table_name='ingestion_job')
    op.drop_table('ingestion_job')
    op.drop_index('ix_request_user_created', table_name='hey_kivi_request')
    op.drop_table('hey_kivi_request')
    op.drop_table('eval_run')
    op.drop_table('eval_case')
    op.drop_table('app_user')

    # Native ENUM types outlive their tables; drop them so that
    # `downgrade base` followed by `upgrade head` is a clean cycle.
    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP TYPE IF EXISTS item_status")
    op.execute("DROP TYPE IF EXISTS memory_kind")
    op.execute("DROP TYPE IF EXISTS memory_status")
    op.execute("DROP TYPE IF EXISTS memory_volatility")
    op.execute("DROP TYPE IF EXISTS memory_change_type")
    op.execute("DROP TYPE IF EXISTS admission_reason")
    op.execute("DROP TYPE IF EXISTS admission_decision")
    op.execute("DROP TYPE IF EXISTS evidence_relation")
    op.execute("DROP TYPE IF EXISTS memory_link_type")
    op.execute("DROP TYPE IF EXISTS request_outcome")
    op.execute("DROP TYPE IF EXISTS abstention_reason")
    op.execute("DROP TYPE IF EXISTS grounding_kind")
    op.execute("DROP TYPE IF EXISTS retrieval_strategy")
    op.execute("DROP TYPE IF EXISTS exclusion_reason")
    op.execute("DROP TYPE IF EXISTS tool_status")
    op.execute("DROP TYPE IF EXISTS decision_event_kind")
    op.execute("DROP TYPE IF EXISTS eval_case_type")
    op.execute("DROP TYPE IF EXISTS eval_outcome")
    # ### end Alembic commands ###

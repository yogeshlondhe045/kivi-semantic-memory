-- 004_conversations_traces
-- Hey Kivi conversations, and the decision trace behind every answer.
--
-- The trace is not logging. It is the artefact that makes the claim "memory
-- affected this result" (or "it did not, and here is why") checkable by an
-- engineer without re-running the pipeline.

CREATE TABLE conversations (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title      TEXT NOT NULL DEFAULT 'New conversation',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE turns (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content         TEXT NOT NULL,
  outcome         TEXT,            -- answered | abstained | clarified | acted | refused
  citations       TEXT NOT NULL DEFAULT '[]',  -- JSON array
  trace_id        TEXT,
  created_at      INTEGER NOT NULL
);

CREATE INDEX idx_turns_conversation ON turns (conversation_id, created_at);

CREATE TABLE traces (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('ingest', 'query')),
  subject_id    TEXT,              -- dictation id or turn id
  provider      TEXT NOT NULL,
  model         TEXT,
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER NOT NULL,
  duration_ms   INTEGER NOT NULL,
  stages        TEXT NOT NULL DEFAULT '[]',  -- JSON: [{name, ms, detail}]
  payload       TEXT NOT NULL DEFAULT '{}',  -- JSON: plan, candidates, included, excluded, tools
  tokens_in     INTEGER NOT NULL DEFAULT 0,
  tokens_out    INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL DEFAULT 0,
  llm_calls     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_traces_kind    ON traces (user_id, kind, started_at DESC);
CREATE INDEX idx_traces_subject ON traces (subject_id);

-- Coarse-grained metrics so "database growth" is measured rather than asserted.
CREATE TABLE db_growth_samples (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL,
  taken_at       INTEGER NOT NULL,
  dictation_count INTEGER NOT NULL,
  memory_count   INTEGER NOT NULL,
  episode_count  INTEGER NOT NULL,
  rejection_count INTEGER NOT NULL,
  bytes          INTEGER NOT NULL
);

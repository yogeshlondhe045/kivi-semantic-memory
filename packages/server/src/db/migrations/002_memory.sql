-- 002_memory
-- What Kivi is allowed to know, why it believes it, and what it refused to keep.

-- A memory is always a claim WITH evidence. There is no row here that cannot be
-- traced back to dictations through memory_evidence.
CREATE TABLE memories (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type          TEXT NOT NULL CHECK (type IN ('entity', 'fact', 'preference', 'commitment')),
  dedupe_key    TEXT NOT NULL,   -- normalised identity used to reinforce instead of duplicate
  subject       TEXT NOT NULL,   -- what the memory is about ("Aarti", "Project Meridian")
  statement     TEXT NOT NULL,   -- the memory as a person would read it back
  detail        TEXT NOT NULL DEFAULT '{}',  -- JSON payload, type specific
  confidence    REAL NOT NULL DEFAULT 0.5,
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'pending_confirmation', 'superseded', 'retired')),
  origin        TEXT NOT NULL DEFAULT 'observed'    -- observed | stated | user_edit
                CHECK (origin IN ('observed', 'stated', 'user_edit')),
  superseded_by TEXT REFERENCES memories(id) ON DELETE SET NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  first_seen_at INTEGER NOT NULL,
  last_seen_at  INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  expires_at    INTEGER,         -- commitments stop being relevant
  embedding     BLOB
);

CREATE UNIQUE INDEX idx_memories_dedupe ON memories (user_id, type, dedupe_key)
  WHERE status IN ('active', 'pending_confirmation');
CREATE INDEX idx_memories_user_status ON memories (user_id, status, type);
CREATE INDEX idx_memories_recency     ON memories (user_id, last_seen_at DESC);

-- Provenance. Every create / reinforce / contradiction is one row, quoting the
-- span of the dictation that justified it.
CREATE TABLE memory_evidence (
  id           TEXT PRIMARY KEY,
  memory_id    TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  dictation_id TEXT NOT NULL REFERENCES dictations(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('created', 'reinforced', 'contradicted', 'superseded', 'user_edit')),
  quote        TEXT NOT NULL,
  char_start   INTEGER,
  char_end     INTEGER,
  confidence   REAL NOT NULL DEFAULT 0.5,
  created_at   INTEGER NOT NULL
);

CREATE INDEX idx_evidence_memory    ON memory_evidence (memory_id, created_at DESC);
CREATE INDEX idx_evidence_dictation ON memory_evidence (dictation_id);

-- One episode per dictation: a cheap, always-written summary. This is the floor
-- of retrieval — even when nothing was worth promoting to a memory, "what
-- happened" is still answerable.
CREATE TABLE episodes (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dictation_id TEXT NOT NULL UNIQUE REFERENCES dictations(id) ON DELETE CASCADE,
  occurred_at  INTEGER NOT NULL,
  app          TEXT NOT NULL,
  summary      TEXT NOT NULL,
  topics       TEXT NOT NULL DEFAULT '[]',   -- JSON array
  entities     TEXT NOT NULL DEFAULT '[]',   -- JSON array
  salience     REAL NOT NULL DEFAULT 0.5,
  embedding    BLOB
);

CREATE INDEX idx_episodes_time ON episodes (user_id, occurred_at DESC);

-- What we deliberately did not keep, and why. Persisted because "it ignored the
-- right things" is a claim that has to be inspectable.
CREATE TABLE rejections (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dictation_id   TEXT REFERENCES dictations(id) ON DELETE CASCADE,
  candidate_type TEXT NOT NULL,
  candidate      TEXT NOT NULL,
  reason_code    TEXT NOT NULL,   -- sensitive_category | low_confidence | transient | trivial | third_party | duplicate | tombstoned
  reason         TEXT NOT NULL,
  created_at     INTEGER NOT NULL
);

CREATE INDEX idx_rejections_dictation ON rejections (dictation_id);
CREATE INDEX idx_rejections_reason    ON rejections (user_id, reason_code);

-- The only channel from semantic memory into ordinary dictation: canonical
-- spellings of terms the user has already used.
CREATE TABLE lexicon (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  canonical  TEXT NOT NULL,
  variants   TEXT NOT NULL DEFAULT '[]',  -- JSON array of observed ASR spellings
  memory_id  TEXT REFERENCES memories(id) ON DELETE CASCADE,
  uses       INTEGER NOT NULL DEFAULT 0,
  enabled    INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL,
  UNIQUE (user_id, canonical)
);

-- Kivi asks rather than assumes. One open card at a time is enforced in the API.
CREATE TABLE review_items (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  memory_id   TEXT REFERENCES memories(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,   -- confirm_new | resolve_conflict | confirm_spelling
  question    TEXT NOT NULL,
  options     TEXT NOT NULL DEFAULT '[]',  -- JSON array of {id,label,effect}
  context     TEXT NOT NULL DEFAULT '{}',  -- JSON
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
  created_at  INTEGER NOT NULL,
  resolved_at INTEGER,
  resolution  TEXT
);

CREATE INDEX idx_review_open ON review_items (user_id, status, created_at);

-- Forgetting has to survive re-ingestion, otherwise "delete" is theatre.
CREATE TABLE tombstones (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type       TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  statement  TEXT NOT NULL,
  reason     TEXT NOT NULL DEFAULT 'user_forget',
  created_at INTEGER NOT NULL,
  UNIQUE (user_id, type, dedupe_key)
);

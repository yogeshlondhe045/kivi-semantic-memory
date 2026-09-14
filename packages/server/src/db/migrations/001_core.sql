-- 001_core
-- The substrate: accounts, the dictations Kivi has seen, and the batches they
-- arrived in. Everything else in the system is derived from these tables and
-- can be rebuilt from them.

CREATE TABLE users (
  id           TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  timezone     TEXT NOT NULL DEFAULT 'Asia/Kolkata',
  created_at   INTEGER NOT NULL
);

CREATE TABLE ingest_batches (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label         TEXT NOT NULL,
  source_path   TEXT,
  record_count  INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  bytes_before  INTEGER,
  bytes_after   INTEGER,
  provider      TEXT
);

-- A dictation is one recorded utterance. `raw_asr` is what the recogniser heard,
-- `formatted` is what Kivi wrote. We keep both: the gap between them is where
-- most phonetic and terminology evidence lives.
CREATE TABLE dictations (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  batch_id       TEXT REFERENCES ingest_batches(id) ON DELETE SET NULL,
  external_id    TEXT,
  captured_at    INTEGER NOT NULL,
  app            TEXT NOT NULL,
  surface        TEXT NOT NULL DEFAULT 'dictation',  -- dictation | hey_kivi
  device         TEXT,
  duration_ms    INTEGER,
  language       TEXT NOT NULL DEFAULT 'en',
  asr_confidence REAL,
  raw_asr        TEXT NOT NULL,
  formatted      TEXT NOT NULL,
  style          TEXT,
  word_count     INTEGER NOT NULL DEFAULT 0,
  content_hash   TEXT NOT NULL,
  ingested_at    INTEGER NOT NULL,
  processed_at   INTEGER,
  UNIQUE (user_id, content_hash)
);

CREATE INDEX idx_dictations_user_time ON dictations (user_id, captured_at DESC);
CREATE INDEX idx_dictations_app       ON dictations (user_id, app, captured_at DESC);
CREATE INDEX idx_dictations_unproc    ON dictations (user_id, processed_at);

CREATE TABLE settings (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key     TEXT NOT NULL,
  value   TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);

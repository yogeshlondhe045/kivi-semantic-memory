-- 003_search
-- Lexical retrieval. FTS5 ships with Node's bundled SQLite, so BM25 costs us no
-- dependency and no network. Vector search is layered on top in the application.

CREATE VIRTUAL TABLE dictations_fts USING fts5(
  formatted,
  raw_asr,
  app UNINDEXED,
  content = 'dictations',
  content_rowid = 'rowid',
  tokenize = "porter unicode61 remove_diacritics 2"
);

CREATE TRIGGER dictations_ai AFTER INSERT ON dictations BEGIN
  INSERT INTO dictations_fts(rowid, formatted, raw_asr, app)
  VALUES (new.rowid, new.formatted, new.raw_asr, new.app);
END;

CREATE TRIGGER dictations_ad AFTER DELETE ON dictations BEGIN
  INSERT INTO dictations_fts(dictations_fts, rowid, formatted, raw_asr, app)
  VALUES ('delete', old.rowid, old.formatted, old.raw_asr, old.app);
END;

CREATE TRIGGER dictations_au AFTER UPDATE ON dictations BEGIN
  INSERT INTO dictations_fts(dictations_fts, rowid, formatted, raw_asr, app)
  VALUES ('delete', old.rowid, old.formatted, old.raw_asr, old.app);
  INSERT INTO dictations_fts(rowid, formatted, raw_asr, app)
  VALUES (new.rowid, new.formatted, new.raw_asr, new.app);
END;

CREATE VIRTUAL TABLE memories_fts USING fts5(
  statement,
  subject,
  content = 'memories',
  content_rowid = 'rowid',
  tokenize = "porter unicode61 remove_diacritics 2"
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, statement, subject) VALUES (new.rowid, new.statement, new.subject);
END;

CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, statement, subject)
  VALUES ('delete', old.rowid, old.statement, old.subject);
END;

CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, statement, subject)
  VALUES ('delete', old.rowid, old.statement, old.subject);
  INSERT INTO memories_fts(rowid, statement, subject) VALUES (new.rowid, new.statement, new.subject);
END;

CREATE VIRTUAL TABLE episodes_fts USING fts5(
  summary,
  topics,
  content = 'episodes',
  content_rowid = 'rowid',
  tokenize = "porter unicode61 remove_diacritics 2"
);

CREATE TRIGGER episodes_ai AFTER INSERT ON episodes BEGIN
  INSERT INTO episodes_fts(rowid, summary, topics) VALUES (new.rowid, new.summary, new.topics);
END;

CREATE TRIGGER episodes_ad AFTER DELETE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, summary, topics)
  VALUES ('delete', old.rowid, old.summary, old.topics);
END;

CREATE TRIGGER episodes_au AFTER UPDATE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, summary, topics)
  VALUES ('delete', old.rowid, old.summary, old.topics);
  INSERT INTO episodes_fts(rowid, summary, topics) VALUES (new.rowid, new.summary, new.topics);
END;

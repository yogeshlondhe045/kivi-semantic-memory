-- 005_forget_suppression
--
-- Forgetting has to mean something. Deleting the memory row stops Kivi
-- *believing* a thing, but the dictations that produced it are still in the
-- history, and recall reads episodes as well as memories — so without this the
-- forgotten claim comes straight back in the next answer, sourced from the
-- original recording.
--
-- A tombstone therefore also records which dictations supported the memory.
-- Those recordings stay in the app and stay findable by name and date; they
-- simply stop being usable as grounds for an answer.

ALTER TABLE tombstones ADD COLUMN suppressed_dictations TEXT NOT NULL DEFAULT '[]';

CREATE TABLE suppressed_evidence (
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dictation_id TEXT NOT NULL REFERENCES dictations(id) ON DELETE CASCADE,
  tombstone_id TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  PRIMARY KEY (user_id, dictation_id)
);

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';

/**
 * Focused tests for the decisions that would be expensive to get wrong and
 * cheap to break: the policy gate, time parsing, supersession, forgetting, and
 * the field mapping the reviewers' corpus import depends on.
 *
 * The broad behavioural coverage lives in `npm run eval`, which exercises the
 * real pipeline over 500 records. These are the unit-level guardrails under it.
 */

const TEMP = fs.mkdtempSync(path.join(os.tmpdir(), 'kivi-test-'));
process.env.DATABASE_URL = path.join(TEMP, 'test.db');
process.env.LLM_PROVIDER = 'local';

describe('the ignore policy', async () => {
  const { evaluateCandidate, screenText } = await import('../packages/server/src/memory/policy.ts');
  const never = { tombstoned: () => false };

  const candidate = (over: Record<string, unknown> = {}) => ({
    type: 'fact' as const,
    subject: 'thing',
    statement: 'Some durable statement about work',
    confidence: 0.8,
    quote: 'Some durable statement about work.',
    ...over,
  });

  it('refuses health information however it is phrased', () => {
    for (const text of [
      'Devansh has been unwell and is taking two weeks off',
      'I have a therapy appointment on Thursday',
      'She was diagnosed last month',
      'He is on sick leave until April',
    ]) {
      assert.ok(screenText(text), `should have been screened: ${text}`);
    }
  });

  it('refuses money, credentials and other people\'s circumstances', () => {
    assert.ok(screenText('My salary review came back lower than I expected'));
    assert.ok(screenText('the api key is in the shared vault'));
    assert.ok(screenText('Priya is going through a divorce'));
  });

  it('does not refuse ordinary work talk', () => {
    assert.equal(screenText('The Meridian pricing update ships on Monday'), null);
    assert.equal(screenText('Rahul is the PM on Meridian'), null);
    assert.equal(screenText('I prefer Slack messages under four lines'), null);
  });

  it('drops passing states rather than storing them', () => {
    const decision = evaluateCandidate(
      candidate({ statement: "I'm tired today", quote: "I'm tired today." }),
      never,
    );
    assert.equal(decision.allowed, false);
    assert.equal(decision.rejection?.reason_code, 'transient');
  });

  it('drops candidates below the per-type confidence floor', () => {
    const decision = evaluateCandidate(candidate({ confidence: 0.2 }), never);
    assert.equal(decision.allowed, false);
    assert.equal(decision.rejection?.reason_code, 'low_confidence');
  });

  it('holds a merely-plausible candidate instead of acting on it', () => {
    const decision = evaluateCandidate(candidate({ confidence: 0.6 }), never);
    assert.equal(decision.allowed, true);
    assert.equal(decision.needsConfirmation, true);
  });

  it('refuses to relearn something the person deleted', () => {
    const decision = evaluateCandidate(candidate(), { tombstoned: () => true });
    assert.equal(decision.allowed, false);
    assert.equal(decision.rejection?.reason_code, 'tombstoned');
  });

  it('gives every refusal a reason a person could read', () => {
    const decision = evaluateCandidate(
      candidate({ statement: 'Their blood pressure is high', quote: 'blood pressure is high' }),
      never,
    );
    assert.equal(decision.allowed, false);
    assert.match(decision.rejection!.reason, /does not retain/i);
  });
});

describe('time expressions', async () => {
  const { parseTimeExpression, parseDueDate } = await import('../packages/server/src/util/time.ts');
  // Monday 14 September 2026, 13:00 IST.
  const now = Date.parse('2026-09-14T07:30:00.000Z');

  it('reads "around 5PM yesterday" as a window around 17:00 local', () => {
    const window = parseTimeExpression('the dictation I did around 5PM yesterday', { now });
    assert.equal(window.precision, 'hour');
    assert.ok(window.from !== null && window.to !== null);
    const centreIst = new Date(window.centre! + 330 * 60_000);
    assert.equal(centreIst.getUTCHours(), 17);
    assert.equal(centreIst.getUTCDate(), 13);
  });

  it('reads a bare part of the day', () => {
    const window = parseTimeExpression('what did I say yesterday morning', { now });
    assert.equal(window.precision, 'part_of_day');
    assert.match(window.label!, /morning/);
  });

  it('returns nothing when no time was mentioned', () => {
    assert.equal(parseTimeExpression('who is the PM on Meridian', { now }).from, null);
  });

  it('resolves a spoken deadline to a date', () => {
    const due = parseDueDate("I'll send it by Friday", now);
    assert.ok(due !== null);
    assert.ok(due! > now);
  });
});

describe('memory lifecycle', async () => {
  const { migrate } = await import('../packages/server/src/db/migrate.ts');
  const { run, one, closeDb } = await import('../packages/server/src/db/index.ts');
  const { writeCandidate, forgetMemory, isTombstoned } = await import(
    '../packages/server/src/memory/store.ts'
  );

  before(() => {
    migrate();
    run('INSERT OR IGNORE INTO users (id, display_name, timezone, created_at) VALUES (?,?,?,?)', [
      'u1',
      'Test',
      'Asia/Kolkata',
      Date.now(),
    ]);
    for (const id of ['d1', 'd2', 'd3']) {
      run(
        `INSERT OR IGNORE INTO dictations
           (id, user_id, captured_at, app, surface, language, raw_asr, formatted, word_count, content_hash, ingested_at)
         VALUES (?, 'u1', ?, 'slack', 'dictation', 'en', ?, ?, 5, ?, ?)`,
        [id, Date.now(), `raw ${id}`, `formatted ${id}`, id, Date.now()],
      );
    }
  });

  after(() => closeDb());

  const fact = (statement: string, subject = 'pm on meridian') => ({
    type: 'fact' as const,
    subject,
    statement,
    confidence: 0.8,
    quote: statement,
    detail: { attribute: subject },
  });

  it('creates, then reinforces rather than duplicating', () => {
    const first = writeCandidate(fact('Priya is the PM on Meridian'), {
      userId: 'u1',
      dictationId: 'd1',
      capturedAt: 1000,
      needsConfirmation: false,
    });
    assert.equal(first.action, 'created');

    const second = writeCandidate(fact('Priya is the PM on Meridian'), {
      userId: 'u1',
      dictationId: 'd2',
      capturedAt: 2000,
      needsConfirmation: false,
    });
    assert.equal(second.action, 'reinforced');
    assert.equal(second.memory.id, first.memory.id);
    assert.ok(second.memory.confidence > first.memory.confidence);
    assert.equal(second.memory.evidence_count, 2);
  });

  it('supersedes on contradiction and keeps the old belief linked', () => {
    const third = writeCandidate(fact('Rahul is the PM on Meridian'), {
      userId: 'u1',
      dictationId: 'd3',
      capturedAt: 3000,
      needsConfirmation: false,
    });
    assert.equal(third.action, 'superseded');
    assert.ok(third.previous);

    const old = one<{ status: string; superseded_by: string }>(
      'SELECT status, superseded_by FROM memories WHERE id = ?',
      [third.previous!.id],
    );
    assert.equal(old?.status, 'superseded');
    assert.equal(old?.superseded_by, third.memory.id);
  });

  it('never lets an older dictation overwrite a newer belief', () => {
    const stale = writeCandidate(fact('Nandini is the PM on Meridian'), {
      userId: 'u1',
      dictationId: 'd1',
      capturedAt: 500,
      needsConfirmation: false,
    });
    assert.equal(stale.action, 'unchanged');
  });

  it('forgets permanently, and refuses to relearn', () => {
    const current = one<{ id: string }>(
      `SELECT id FROM memories WHERE user_id='u1' AND status='active' AND type='fact' LIMIT 1`,
    );
    assert.ok(current);

    assert.equal(forgetMemory('u1', current!.id), true);
    assert.equal(one('SELECT id FROM memories WHERE id = ?', [current!.id]), undefined);
    assert.equal(isTombstoned('u1', fact('Rahul is the PM on Meridian')), true);
  });
});

describe('corpus import mapping', async () => {
  const { mapRecords, parseTimestamp } = await import('../scripts/lib.ts');

  it('finds our fields under other people\'s names', () => {
    const { records, resolved } = mapRecords([
      {
        asr_output: 'raw words here',
        llm_formatted: 'Formatted words here.',
        timestamp: '2026-05-04T09:30:00Z',
        source_app: 'SLACK',
      },
    ]);
    assert.equal(resolved.raw_asr, 'asr_output');
    assert.equal(resolved.formatted, 'llm_formatted');
    assert.equal(records[0]!.app, 'slack');
    assert.equal(records[0]!.captured_at, Date.parse('2026-05-04T09:30:00Z'));
  });

  it('accepts an explicit override', () => {
    const { resolved } = mapRecords([{ weird_column: 'text', ts: 1_700_000_000 }], {
      formatted: 'weird_column',
    });
    assert.equal(resolved.formatted, 'weird_column');
  });

  it('reads seconds and milliseconds alike', () => {
    assert.equal(parseTimestamp('1700000000'), 1_700_000_000_000);
    assert.equal(parseTimestamp('1700000000000'), 1_700_000_000_000);
    assert.equal(parseTimestamp('not a date'), null);
  });

  it('reports skipped rows instead of dropping them silently', () => {
    const { records, skipped } = mapRecords([
      { text: 'fine', timestamp: '2026-05-04T09:30:00Z' },
      { text: '', timestamp: '2026-05-04T09:30:00Z' },
      { text: 'no timestamp' },
    ]);
    assert.equal(records.length, 1);
    assert.equal(skipped.length, 2);
    assert.match(skipped[1]!.reason, /timestamp/);
  });
});

describe('rewriting is attributable', async () => {
  const { rewriteWithMemory } = await import('../packages/server/src/agent/rewrite.ts');

  it('reports which memories applied and which did not', () => {
    const result = rewriteWithMemory('u1', 'This is a sentence. And another one. And a third.', 'make it shorter');
    assert.ok(Array.isArray(result.rules));
    assert.ok(result.draft.length > 0);
    for (const rule of result.rules) {
      assert.equal(typeof rule.effect, 'string');
      assert.ok(rule.effect.length > 0, 'every rule must explain what it did or did not do');
    }
  });
});

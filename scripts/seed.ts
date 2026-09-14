import path from 'node:path';
import { ANCHOR } from '../corpus/generate.ts';
import { config } from '../packages/server/src/config.ts';
import { databaseBytes } from '../packages/server/src/db/index.ts';
import { ingestBatch } from '../packages/server/src/memory/ingest.ts';
import { memoryStats } from '../packages/server/src/memory/store.ts';
import { providerLabel } from '../packages/server/src/llm/index.ts';
import { ensureSchema, ensureUser, formatBytes, progressBar, readRecords } from './lib.ts';

/**
 * Loads the committed corpus.
 *
 * Timestamps are re-based so the newest dictation lands yesterday evening
 * relative to whenever you run this. That is not cosmetic: "find the dictation
 * I did around 5PM yesterday" has to mean something on the day the reviewer
 * opens the app, not on the day the corpus was generated. The shift is a whole
 * number of days, so every time-of-day and weekday relationship is preserved.
 */

const CORPUS = path.join(config.root, 'corpus', 'kivi-corpus-v1.jsonl');
const DAY = 86_400_000;

async function main() {
  const fileArg = process.argv.find((a) => a.startsWith('--file='));
  const file = fileArg ? path.resolve(fileArg.split('=')[1]!) : CORPUS;
  const noShift = process.argv.includes('--no-shift');

  ensureSchema();
  ensureUser();

  const raw = readRecords(file);
  if (raw.length === 0) {
    console.error(`!     ${file} is empty. Run \`npm run corpus:generate\` first.`);
    process.exit(1);
  }

  // Re-base: align the corpus anchor with the start of today.
  const todayStart = Math.floor(Date.now() / DAY) * DAY;
  const shift = noShift ? 0 : todayStart - ANCHOR;

  const records = raw.map((row) => ({
    external_id: String(row.external_id ?? ''),
    captured_at: Number(row.captured_at) + shift,
    app: String(row.app ?? 'unknown'),
    device: (row.device as string) ?? null,
    duration_ms: (row.duration_ms as number) ?? null,
    language: String(row.language ?? 'en'),
    asr_confidence: (row.asr_confidence as number) ?? null,
    raw_asr: String(row.raw_asr ?? ''),
    formatted: String(row.formatted ?? ''),
    style: (row.style as string) ?? null,
  }));

  console.log(`seed  ${records.length} records from ${path.relative(config.root, file)}`);
  console.log(`      provider ${providerLabel()}`);
  console.log(
    `      history re-based to end ${new Date(Math.max(...records.map((r) => r.captured_at))).toISOString().slice(0, 16).replace('T', ' ')}Z`,
  );

  const started = Date.now();
  const result = await ingestBatch(config.defaultUserId, records, {
    label: 'seed:kivi-corpus-v1',
    sourcePath: file,
    onProgress: (done, total) => progressBar(done, total, 'ingest'),
  });

  const stats = memoryStats(config.defaultUserId);
  const elapsed = Date.now() - started;

  console.log('');
  console.log(`ok    ingested ${result.processed} dictations in ${(elapsed / 1000).toFixed(1)}s (${Math.round(elapsed / Math.max(1, result.processed))} ms each)`);
  if (result.duplicates) console.log(`      ${result.duplicates} already present, skipped`);
  console.log(`      memories: ${result.created} created · ${result.reinforced} reinforced · ${result.superseded} superseded · ${result.held} awaiting confirmation`);
  console.log(`      ignored:  ${result.rejected} candidates refused by policy`);
  console.log(`      database: ${formatBytes(result.bytes_before)} -> ${formatBytes(databaseBytes())}`);
  console.log('');
  console.log('      what Kivi now knows');
  for (const row of stats.byType) {
    console.log(`        ${row.type.padEnd(11)} ${String(row.n).padStart(4)}  (${row.status})`);
  }
  console.log('      what it deliberately ignored');
  for (const row of stats.rejections) {
    console.log(`        ${row.reason_code.padEnd(19)} ${String(row.n).padStart(4)}`);
  }
}

main().catch((err) => {
  console.error('!     seed failed:', err);
  process.exit(1);
});

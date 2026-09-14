import path from 'node:path';
import fs from 'node:fs';
import { config } from '../packages/server/src/config.ts';
import { databaseBytes } from '../packages/server/src/db/index.ts';
import { providerLabel } from '../packages/server/src/llm/index.ts';
import { ingestBatch } from '../packages/server/src/memory/ingest.ts';
import { memoryStats } from '../packages/server/src/memory/store.ts';
import { ensureSchema, ensureUser, formatBytes, mapRecords, progressBar, readRecords } from './lib.ts';

/**
 * Imports somebody else's corpus.
 *
 * The reviewing agent will arrive with ~500 dictations shaped differently from
 * ours. Rather than asking them to reshape the file, the importer sniffs the
 * common field names and prints exactly which source column it used for each of
 * our fields — so a wrong guess is visible immediately rather than silently
 * producing an empty memory store.
 *
 *   npm run import -- --file=/path/to/corpus.jsonl
 *   npm run import -- --file=corpus.csv --map=formatted=llm_text,captured_at=recorded
 *   npm run import -- --file=corpus.jsonl --user=user_sarvam --dry-run
 */

function arg(name: string): string | undefined {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
}

async function main() {
  const file = arg('file');
  if (!file) {
    console.error('usage: npm run import -- --file=<path> [--map=our_field=their_field,...] [--user=<id>] [--dry-run]');
    process.exit(1);
  }

  const resolvedPath = path.resolve(file);
  if (!fs.existsSync(resolvedPath)) {
    console.error(`!     no such file: ${resolvedPath}`);
    process.exit(1);
  }

  const userId = arg('user') ?? config.defaultUserId;
  const dryRun = process.argv.includes('--dry-run');
  const overrides = Object.fromEntries(
    (arg('map') ?? '')
      .split(',')
      .filter(Boolean)
      .map((pair) => {
        const [ours, theirs] = pair.split('=');
        return [ours!.trim(), theirs!.trim()];
      }),
  );

  ensureSchema();
  ensureUser(userId, arg('name') ?? 'Imported user');

  const raw = readRecords(resolvedPath);
  const { records, resolved, skipped } = mapRecords(raw, overrides);

  console.log(`import ${raw.length} rows from ${path.basename(resolvedPath)}`);
  console.log(`       user ${userId} · provider ${providerLabel()}`);
  console.log('');
  console.log('       field mapping');
  for (const [ours, theirs] of Object.entries(resolved)) {
    const marker = theirs ? ' ' : '!';
    console.log(`       ${marker} ${ours.padEnd(15)} <- ${theirs ?? '(not found — using default)'}`);
  }
  if (!resolved.raw_asr && !resolved.formatted) {
    console.error('\n!      Neither a raw-ASR nor a formatted-text column was found.');
    console.error('       Pass one explicitly, e.g. --map=formatted=your_column_name');
    process.exit(1);
  }
  if (skipped.length) {
    console.log(`\n       ${skipped.length} row(s) skipped:`);
    for (const s of skipped.slice(0, 5)) console.log(`         row ${s.index + 1}: ${s.reason}`);
    if (skipped.length > 5) console.log(`         ... and ${skipped.length - 5} more`);
  }

  if (dryRun) {
    console.log('\nok     dry run — nothing written. Re-run without --dry-run to ingest.');
    console.log('       first mapped record:');
    console.log(JSON.stringify(records[0], null, 2).split('\n').map((l) => `       ${l}`).join('\n'));
    return;
  }

  console.log('');
  const result = await ingestBatch(userId, records, {
    label: `import:${path.basename(resolvedPath)}`,
    sourcePath: resolvedPath,
    onProgress: (done, total) => progressBar(done, total, 'ingest'),
  });

  const stats = memoryStats(userId);
  console.log('');
  console.log(`ok     ingested ${result.processed} dictations in ${(result.duration_ms / 1000).toFixed(1)}s`);
  console.log(`       memories: ${result.created} created · ${result.reinforced} reinforced · ${result.superseded} superseded · ${result.held} held`);
  console.log(`       ignored:  ${result.rejected} candidates refused by policy`);
  console.log(`       database: ${formatBytes(result.bytes_before)} -> ${formatBytes(databaseBytes())}`);
  console.log(`       memories now: ${stats.byType.reduce((n, r) => n + r.n, 0)} across ${stats.byType.length} type/status buckets`);
  console.log('');
  console.log('       inspect: open the app and use Memory, or query the database directly.');
}

main().catch((err) => {
  console.error('!      import failed:', err);
  process.exit(1);
});

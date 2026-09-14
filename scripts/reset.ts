import fs from 'node:fs';
import path from 'node:path';
import { config } from '../packages/server/src/config.ts';
import { closeDb } from '../packages/server/src/db/index.ts';
import { migrate } from '../packages/server/src/db/migrate.ts';

/**
 * Returns the system to the state a fresh clone is in: no database, no memories,
 * no traces. Deliberately destructive and deliberately obvious about it.
 */
const KEEP_SCHEMA = !process.argv.includes('--empty');

closeDb();

let removed = 0;
for (const suffix of ['', '-wal', '-shm']) {
  const file = config.databaseFile + suffix;
  if (fs.existsSync(file)) {
    fs.rmSync(file);
    removed += 1;
  }
}

console.log(`ok    removed ${removed} database file(s) at ${path.relative(config.root, config.databaseFile)}`);

if (KEEP_SCHEMA) {
  const result = migrate();
  console.log(`ok    recreated an empty database (${result.applied.length} migrations)`);
  console.log('      next: npm run seed');
} else {
  console.log('      next: npm run db:migrate && npm run seed');
}

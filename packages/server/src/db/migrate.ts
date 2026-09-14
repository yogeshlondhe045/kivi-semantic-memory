import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config, log } from '../config.ts';
import { openDb, type Db } from './index.ts';

const MIGRATIONS_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'migrations');

export function migrate(handle?: Db): { applied: string[]; alreadyCurrent: number } {
  const database = handle ?? openDb();
  database.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version    TEXT PRIMARY KEY,
      name       TEXT NOT NULL,
      applied_at INTEGER NOT NULL,
      checksum   TEXT NOT NULL
    )
  `);

  const done = new Set(
    (database.prepare('SELECT version FROM schema_migrations').all() as { version: string }[]).map(
      (r) => r.version,
    ),
  );

  const files = fs
    .readdirSync(MIGRATIONS_DIR)
    .filter((f) => f.endsWith('.sql'))
    .sort();

  const applied: string[] = [];
  for (const file of files) {
    const version = file.split('_')[0];
    if (done.has(version)) continue;
    const sql = fs.readFileSync(path.join(MIGRATIONS_DIR, file), 'utf8');
    database.exec('BEGIN');
    try {
      database.exec(sql);
      database
        .prepare(
          'INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, ?)',
        )
        .run(version, file, Date.now(), checksum(sql));
      database.exec('COMMIT');
      applied.push(file);
    } catch (err) {
      database.exec('ROLLBACK');
      throw new Error(`migration ${file} failed: ${(err as Error).message}`);
    }
  }

  if (!handle) database.close();
  return { applied, alreadyCurrent: files.length - applied.length };
}

function checksum(input: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

const isEntrypoint = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isEntrypoint) {
  const result = migrate();
  if (result.applied.length === 0) {
    log('info', `ok    database already current (${result.alreadyCurrent} migrations) — ${config.databaseFile}`);
  } else {
    log('info', `ok    applied ${result.applied.length} migration(s) to ${config.databaseFile}`);
    for (const f of result.applied) log('info', `      + ${f}`);
  }
}

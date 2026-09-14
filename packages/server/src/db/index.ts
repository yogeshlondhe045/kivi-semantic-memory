import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { config } from '../config.ts';

export type Db = DatabaseSync;

let instance: Db | null = null;

export function openDb(file = config.databaseFile): Db {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new DatabaseSync(file);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec('PRAGMA synchronous = NORMAL');
  db.exec('PRAGMA busy_timeout = 5000');
  return db;
}

export function db(): Db {
  if (!instance) instance = openDb();
  return instance;
}

export function closeDb(): void {
  instance?.close();
  instance = null;
}

/**
 * Size of the database on disk. The write-ahead log is folded back in first —
 * otherwise "database growth" reports a transient WAL rather than what the data
 * actually costs to keep.
 */
export function databaseBytes(file = config.databaseFile): number {
  if (instance && file === config.databaseFile) {
    try {
      instance.exec('PRAGMA wal_checkpoint(TRUNCATE)');
    } catch {
      /* another connection holds the lock; fall through to the raw size */
    }
  }
  let total = 0;
  for (const suffix of ['', '-wal', '-shm']) {
    try {
      total += fs.statSync(file + suffix).size;
    } catch {
      /* not present */
    }
  }
  return total;
}

export function transaction<T>(fn: () => T, handle: Db = db()): T {
  handle.exec('BEGIN');
  try {
    const result = fn();
    handle.exec('COMMIT');
    return result;
  } catch (err) {
    handle.exec('ROLLBACK');
    throw err;
  }
}

export function all<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
  handle: Db = db(),
): T[] {
  return handle.prepare(sql).all(...(params as never[])) as T[];
}

export function one<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
  handle: Db = db(),
): T | undefined {
  return handle.prepare(sql).get(...(params as never[])) as T | undefined;
}

export function run(sql: string, params: unknown[] = [], handle: Db = db()) {
  return handle.prepare(sql).run(...(params as never[]));
}

export function count(sql: string, params: unknown[] = [], handle: Db = db()): number {
  const row = one<{ n: number }>(sql, params, handle);
  return Number(row?.n ?? 0);
}

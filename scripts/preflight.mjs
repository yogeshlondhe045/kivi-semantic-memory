#!/usr/bin/env node
/**
 * Fails loudly, with the exact remedy, before anything else runs.
 * The reviewing agent will not debug a cryptic stack trace on our behalf.
 */
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const problems = [];
const notes = [];

const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 5)) {
  problems.push(
    `Node ${process.versions.node} is too old. This project uses the built-in ` +
      `node:sqlite module (no native compilation, no prebuilt binary download).\n` +
      `   Remedy: install Node 22.5 or newer — e.g. \`nvm install 22 && nvm use 22\`.`,
  );
} else {
  try {
    const { DatabaseSync } = await import('node:sqlite');
    const db = new DatabaseSync(':memory:');
    db.exec('CREATE VIRTUAL TABLE probe USING fts5(body)');
    db.close();
  } catch (err) {
    problems.push(
      `node:sqlite is present but FTS5 is unavailable (${err.message}).\n` +
        `   Remedy: use an official Node 22+ build from nodejs.org.`,
    );
  }
}

if (!existsSync(path.join(root, 'node_modules'))) {
  problems.push('Dependencies are not installed.\n   Remedy: run `npm install`.');
}

if (!existsSync(path.join(root, '.env'))) {
  notes.push('No .env found — falling back to .env.example defaults. `cp .env.example .env` to customise.');
}

for (const note of notes) console.log(`note  ${note}`);

if (problems.length) {
  console.error('\nPreflight failed:\n');
  for (const p of problems) console.error(` !  ${p}\n`);
  process.exit(1);
}

console.log(`ok    node ${process.versions.node} · node:sqlite with FTS5 · dependencies installed`);

#!/usr/bin/env node
/**
 * Runs the API and the Vite dev server together, so `npm run dev` is one
 * command rather than two terminals.
 */
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const children = [];

function start(name, command, args, cwd = root) {
  const child = spawn(command, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' });
  child.on('exit', (code) => {
    if (code !== 0 && code !== null) {
      console.error(`\n!     ${name} exited with code ${code}`);
      shutdown(code);
    }
  });
  children.push(child);
  return child;
}

function shutdown(code = 0) {
  for (const child of children) {
    if (!child.killed) child.kill('SIGTERM');
  }
  process.exit(code);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

start('api', 'npx', ['tsx', 'watch', 'packages/server/src/index.ts']);
start('web', 'npx', ['vite'], path.join(root, 'packages/web'));

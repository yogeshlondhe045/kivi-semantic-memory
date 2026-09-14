import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

/**
 * Minimal .env loader. We deliberately avoid a dependency here: the reviewing
 * agent copies `.env.example` to `.env` and nothing else should be required.
 */
function loadEnvFile(file: string): void {
  if (!fs.existsSync(file)) return;
  for (const rawLine of fs.readFileSync(file, 'utf8').split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined || process.env[key] === '') process.env[key] = value;
  }
}

loadEnvFile(path.join(ROOT, '.env'));
loadEnvFile(path.join(ROOT, '.env.example'));

function resolveFromRoot(p: string): string {
  return path.isAbsolute(p) ? p : path.join(ROOT, p);
}

export type ProviderName = 'local' | 'anthropic';

function resolveProvider(): ProviderName {
  const requested = (process.env.LLM_PROVIDER ?? 'local').toLowerCase();
  if (requested === 'anthropic') {
    if (!process.env.ANTHROPIC_API_KEY) {
      console.warn(
        'warn  LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty — falling back to the offline provider.',
      );
      return 'local';
    }
    return 'anthropic';
  }
  return 'local';
}

export const config = {
  root: ROOT,
  port: Number(process.env.PORT ?? 8787),
  webPort: Number(process.env.WEB_PORT ?? 5173),
  databaseFile: resolveFromRoot(process.env.DATABASE_URL ?? './data/kivi.db'),
  provider: resolveProvider(),
  anthropic: {
    apiKey: process.env.ANTHROPIC_API_KEY ?? '',
    model: process.env.ANTHROPIC_MODEL ?? 'claude-opus-5',
    /** Ingestion reads every dictation once; a smaller model is the right tool. */
    extractionModel:
      process.env.ANTHROPIC_EXTRACTION_MODEL ?? process.env.ANTHROPIC_MODEL ?? 'claude-haiku-4-5',
    /** Empty means "use the SDK default". Set only for gateways or proxies. */
    baseUrl: process.env.ANTHROPIC_BASE_URL ?? '',
  },
  seed: Number(process.env.KIVI_SEED ?? 20260214),
  logLevel: (process.env.LOG_LEVEL ?? 'info') as 'silent' | 'error' | 'info' | 'debug',
  /** The single demo account. The product is single-user; the column exists so an
   *  imported corpus can be kept separate from the seeded one. */
  defaultUserId: 'user_demo',
} as const;

export function log(level: 'error' | 'info' | 'debug', ...args: unknown[]): void {
  const order = { silent: 0, error: 1, info: 2, debug: 3 } as const;
  if (order[config.logLevel] >= order[level]) {
    // eslint-disable-next-line no-console
    console[level === 'error' ? 'error' : 'log'](...args);
  }
}

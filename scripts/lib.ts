import fs from 'node:fs';
import { config } from '../packages/server/src/config.ts';
import { one, run } from '../packages/server/src/db/index.ts';
import { migrate } from '../packages/server/src/db/migrate.ts';
import type { IncomingDictation } from '../packages/server/src/memory/ingest.ts';
import { PERSONA } from '../corpus/persona.ts';

export function ensureSchema(): void {
  migrate();
}

export function ensureUser(userId: string = config.defaultUserId, name: string = PERSONA.name): void {
  const existing = one('SELECT id FROM users WHERE id = ?', [userId]);
  if (existing) return;
  run('INSERT INTO users (id, display_name, timezone, created_at) VALUES (?, ?, ?, ?)', [
    userId,
    name,
    PERSONA.timezone,
    Date.now(),
  ]);
}

/**
 * Reads JSONL, JSON array, or CSV. The reviewing agent will arrive with a file
 * shaped differently from ours; the mapping layer below is what lets them use
 * it without editing code.
 */
export function readRecords(file: string): Record<string, unknown>[] {
  const text = fs.readFileSync(file, 'utf8');
  const trimmed = text.trim();
  if (!trimmed) return [];

  if (file.endsWith('.csv')) return parseCsv(trimmed);
  if (trimmed.startsWith('[')) return JSON.parse(trimmed) as Record<string, unknown>[];

  return trimmed
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        return JSON.parse(line) as Record<string, unknown>;
      } catch (err) {
        throw new Error(`line ${index + 1} of ${file} is not valid JSON: ${(err as Error).message}`);
      }
    });
}

function parseCsv(text: string): Record<string, unknown>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (ch !== '\r') {
      field += ch;
    }
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }

  const header = rows.shift();
  if (!header) return [];
  return rows
    .filter((r) => r.some((cell) => cell.trim()))
    .map((r) => Object.fromEntries(header.map((key, index) => [key.trim(), r[index] ?? ''])));
}

/** Field mapping: our field name -> a list of accepted source field names. */
export const DEFAULT_MAPPING: Record<string, string[]> = {
  raw_asr: ['raw_asr', 'asr', 'raw', 'transcript', 'raw_transcript', 'asr_output', 'raw_text'],
  formatted: ['formatted', 'llm_formatted', 'formatted_output', 'llm_output', 'final_text', 'text', 'output'],
  captured_at: ['captured_at', 'timestamp', 'created_at', 'recorded_at', 'time', 'ts', 'date'],
  app: ['app', 'application', 'source_app', 'target_app', 'surface_app', 'context_app'],
  external_id: ['external_id', 'id', 'record_id', 'dictation_id', 'uuid'],
  device: ['device', 'platform', 'client'],
  duration_ms: ['duration_ms', 'duration', 'length_ms', 'audio_ms'],
  language: ['language', 'lang', 'locale'],
  asr_confidence: ['asr_confidence', 'confidence', 'asr_score'],
  style: ['style', 'style_name', 'style_id', 'profile'],
};

export interface MappingResult {
  records: IncomingDictation[];
  /** Which source field each of our fields came from, for the run log. */
  resolved: Record<string, string | null>;
  skipped: { index: number; reason: string }[];
}

export function mapRecords(
  raw: Record<string, unknown>[],
  overrides: Record<string, string> = {},
): MappingResult {
  const sample = raw[0] ?? {};
  const resolved: Record<string, string | null> = {};

  for (const [target, candidates] of Object.entries(DEFAULT_MAPPING)) {
    if (overrides[target]) {
      resolved[target] = overrides[target]!;
      continue;
    }
    resolved[target] = candidates.find((c) => c in sample) ?? null;
  }

  const records: IncomingDictation[] = [];
  const skipped: { index: number; reason: string }[] = [];

  raw.forEach((row, index) => {
    const rawAsr = pickField(row, resolved.raw_asr);
    const formatted = pickField(row, resolved.formatted);
    const capturedRaw = pickField(row, resolved.captured_at);

    if (!rawAsr && !formatted) {
      skipped.push({ index, reason: 'no transcript text in either raw or formatted field' });
      return;
    }

    const capturedAt = parseTimestamp(capturedRaw);
    if (capturedAt === null) {
      skipped.push({ index, reason: `unparseable timestamp: ${JSON.stringify(capturedRaw)}` });
      return;
    }

    records.push({
      external_id: pickField(row, resolved.external_id) || null,
      captured_at: capturedAt,
      app: (pickField(row, resolved.app) || 'unknown').toLowerCase(),
      device: pickField(row, resolved.device) || null,
      duration_ms: toNumber(pickField(row, resolved.duration_ms)),
      language: pickField(row, resolved.language) || 'en',
      asr_confidence: toNumber(pickField(row, resolved.asr_confidence)),
      raw_asr: rawAsr || formatted,
      formatted: formatted || rawAsr,
      style: pickField(row, resolved.style) || null,
    });
  });

  return { records, resolved, skipped };
}

function pickField(row: Record<string, unknown>, field: string | null | undefined): string {
  if (!field) return '';
  const value = row[field];
  if (value === null || value === undefined) return '';
  return String(value);
}

function toNumber(value: string): number | null {
  if (!value) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function parseTimestamp(value: string): number | null {
  if (!value) return null;
  const asNumber = Number(value);
  if (Number.isFinite(asNumber) && asNumber > 0) {
    // Heuristic: ten digits is seconds, thirteen is milliseconds.
    return asNumber < 1e11 ? Math.round(asNumber * 1000) : Math.round(asNumber);
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function progressBar(done: number, total: number, label: string): void {
  if (!process.stdout.isTTY) {
    if (done === total || done % 100 === 0) console.log(`      ${label} ${done}/${total}`);
    return;
  }
  const width = 28;
  const filled = Math.round((done / total) * width);
  process.stdout.write(
    `\r      ${label} [${'#'.repeat(filled)}${'.'.repeat(width - filled)}] ${done}/${total}`,
  );
  if (done === total) process.stdout.write('\n');
}

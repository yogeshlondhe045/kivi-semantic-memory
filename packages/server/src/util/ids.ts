import { createHash, randomUUID } from 'node:crypto';

const SEP = String.fromCharCode(31); // unit separator — cannot occur in transcript text

export function id(prefix: string): string {
  return `${prefix}_${randomUUID().replace(/-/g, '').slice(0, 20)}`;
}

export function sha256(input: string): string {
  return createHash('sha256').update(input).digest('hex');
}

export function contentHash(parts: (string | number | null | undefined)[]): string {
  return sha256(parts.map((p) => String(p ?? '')).join(SEP)).slice(0, 32);
}

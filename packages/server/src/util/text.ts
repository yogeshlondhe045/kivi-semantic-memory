const STOPWORDS = new Set([
  'a','an','the','and','or','but','if','then','so','of','to','in','on','for','with','at','by','from',
  'up','about','into','over','after','is','are','was','were','be','been','being','am','do','does',
  'did','have','has','had','i','me','my','we','our','you','your','it','its','this','that','these',
  'those','as','not','no','yes','can','could','should','would','will','just','really','very','okay',
  'ok','um','uh','like','know','get','got','going','gonna','want','need','thing','things','stuff',
  // Interrogatives and framing verbs: present in most questions, absent from
  // most answers. Scoring them as content makes every question look unanswerable.
  'who','what','when','where','why','how','whom','whose','which','tell','show','find','give',
  'say','said','says','make','made','use','used','please','kivi','hey','about','there','here',
  'forget','remember','delete','remove','stop','spell','spelt','spelled','spelling','pronounce',
  'call','called','mean','means','would','should','could','will','shall','may','might','again',
]);

/** Lowercase, strip punctuation, collapse whitespace. Used for keys and matching. */
export function normalise(input: string): string {
  return input
    .toLowerCase()
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[^a-z0-9'\s-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function tokens(input: string): string[] {
  return normalise(input)
    .split(' ')
    // "Aarti's" and "Aarti" are the same term; leaving the possessive attached
    // produces a token that can never match anything.
    .map((t) => t.replace(/'s$/, '').replace(/^'+|'+$/g, ''))
    .filter(Boolean);
}

export function contentTokens(input: string): string[] {
  return tokens(input).filter((t) => t.length > 2 && !STOPWORDS.has(t));
}

export function isStopword(word: string): boolean {
  return STOPWORDS.has(word.toLowerCase());
}

/** A stable key for deduplicating memories that mean the same thing. */
export function dedupeKey(parts: string[]): string {
  return parts.map((p) => normalise(p)).filter(Boolean).join('::').slice(0, 180);
}

export function titleCase(input: string): string {
  return input.replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

export function truncate(input: string, max: number): string {
  if (input.length <= max) return input;
  return `${input.slice(0, max - 1).trimEnd()}…`;
}

/** Escapes a user string for an FTS5 MATCH expression. */
export function ftsEscape(input: string): string {
  const terms = contentTokens(input).slice(0, 24);
  if (terms.length === 0) return '';
  return terms.map((t) => `"${t.replace(/"/g, '')}"`).join(' OR ');
}

export function ftsAllTerms(input: string): string {
  const terms = contentTokens(input).slice(0, 12);
  if (terms.length === 0) return '';
  return terms.map((t) => `"${t.replace(/"/g, '')}"`).join(' AND ');
}

/** Levenshtein distance, capped for speed. Used for phonetic-variant matching. */
export function editDistance(a: string, b: string, cap = 4): number {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > cap) return cap + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const curr = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const v = Math.min(prev[j]! + 1, curr[j - 1]! + 1, prev[j - 1]! + cost);
      curr[j] = v;
      if (v < best) best = v;
    }
    if (best > cap) return cap + 1;
    prev = curr;
  }
  return prev[b.length]!;
}

/**
 * A deliberately small phonetic key. Enough to tie "Meridian" to "Meridien" and
 * "Aarti" to "Arthy" without pulling in a full metaphone implementation.
 */
export function phoneticKey(input: string): string {
  let s = normalise(input).replace(/[^a-z]/g, '');
  if (!s) return '';
  s = s
    .replace(/ph/g, 'f')
    .replace(/ck/g, 'k')
    .replace(/qu/g, 'kw')
    .replace(/[cq]/g, 'k')
    .replace(/x/g, 'ks')
    .replace(/z/g, 's')
    .replace(/wh/g, 'w')
    .replace(/th/g, 't')
    .replace(/[aeiouy]+/g, 'a');
  s = s.replace(/(.)\1+/g, '$1');
  return s.slice(0, 10);
}

export function wordCount(input: string): number {
  return input.trim() ? input.trim().split(/\s+/).length : 0;
}

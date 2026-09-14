import { count } from '../db/index.ts';
import type { QueryPlan, RetrievedItem } from '../types.ts';
import { contentTokens, normalise } from '../util/text.ts';

/**
 * Term coverage — the thing that makes abstention work.
 *
 * Rank fusion always returns a top result. Its score says "this was the best of
 * what I had", never "this answers the question", so a grounding test built on
 * the fused score alone will confidently hand back five unrelated memories for
 * a question about an office lease in Zurich. That is exactly the failure the
 * product position forbids.
 *
 * So grounding is decided on evidence instead: do the retrieved items actually
 * contain the rare words of the question? Terms are weighted by inverse
 * document frequency over the user's own corpus, which means a word Kivi has
 * never heard ("Zurich") carries more weight than one it hears daily
 * ("review") — and a question containing an unheard word cannot be answered by
 * anything, however well it ranks.
 */

const dfCache = new Map<string, number>();
let corpusSize = 0;
let corpusSizeAt = 0;

function corpusCount(userId: string): number {
  const now = Date.now();
  if (now - corpusSizeAt > 5_000) {
    corpusSize = count('SELECT COUNT(*) AS n FROM dictations WHERE user_id = ?', [userId]);
    corpusSizeAt = now;
  }
  return Math.max(1, corpusSize);
}

/** How many dictations contain this term. Cached; the corpus is append-only. */
export function documentFrequency(userId: string, term: string): number {
  const key = `${userId}:${term}`;
  const cached = dfCache.get(key);
  if (cached !== undefined) return cached;

  let df = 0;
  try {
    df = count(
      `SELECT COUNT(*) AS n
         FROM dictations_fts
         JOIN dictations d ON d.rowid = dictations_fts.rowid
        WHERE dictations_fts MATCH ? AND d.user_id = ?`,
      [`"${term.replace(/"/g, '')}"`, userId],
    );
  } catch {
    df = 0;
  }
  dfCache.set(key, df);
  return df;
}

export function clearCoverageCache(): void {
  dfCache.clear();
  corpusSizeAt = 0;
}

export function idf(userId: string, term: string): number {
  const n = corpusCount(userId);
  const df = documentFrequency(userId, term);
  // Standard smoothed IDF. An unseen term gets the maximum weight available.
  return Math.log((n + 1) / (df + 0.5));
}

export interface Coverage {
  score: number;
  covered: string[];
  missing: string[];
  /** Query terms the corpus has never contained at all. */
  unknown: string[];
}

export function queryCoverage(userId: string, plan: QueryPlan, items: RetrievedItem[]): Coverage {
  const entityTerms = new Set(plan.entities.flatMap((e) => contentTokens(e)));
  const terms = [...new Set([...plan.keywords, ...entityTerms])];
  if (terms.length === 0) {
    return { score: 1, covered: [], missing: [], unknown: [] };
  }

  const weights = new Map(terms.map((term) => [term, idf(userId, term)]));
  const total = [...weights.values()].reduce((sum, w) => sum + w, 0);

  const unknown = terms.filter(
    // Only a name the person has never said is decisive. An ordinary word the
    // corpus happens not to contain ("written") should lower confidence, not
    // veto the answer — otherwise Kivi refuses every rephrased question.
    (term) => entityTerms.has(term) && documentFrequency(userId, term) === 0,
  );

  // Coverage is judged per candidate, then maximised. An answer needs *one*
  // source that contains the question — not a pile of sources that between them
  // mention each word once. Summing across the result set was how five
  // unrelated memories added up to false confidence.
  let best = { score: 0, covered: [] as string[], missing: terms };

  for (const item of items.slice(0, 8)) {
    const haystack = normalise(item.text);
    const covered: string[] = [];
    const missing: string[] = [];
    let hit = 0;

    for (const term of terms) {
      // Prefix match so "pricing" covers "priced" without a stemmer round trip.
      if (haystack.includes(term) || haystack.includes(term.slice(0, Math.max(4, term.length - 2)))) {
        hit += weights.get(term)!;
        covered.push(term);
      } else {
        missing.push(term);
      }
    }

    const score = total === 0 ? 1 : hit / total;
    if (score > best.score) best = { score, covered, missing };
  }

  return { ...best, unknown };
}

/** Coverage below this means no retrieved item contains the question. */
export const COVERAGE_FLOOR = 0.5;

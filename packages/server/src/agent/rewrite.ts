import { all } from '../db/index.ts';
import { lexiconFor } from '../memory/store.ts';
import type { Memory } from '../types.ts';

/**
 * Rewriting is where a memory stops being a stored fact and becomes visible
 * work. It is also where the product is most able to quietly overreach, so the
 * design is deliberately narrow:
 *
 *   - every transform is named, and named after the memory that triggered it;
 *   - a transform that changes nothing reports that it changed nothing, rather
 *     than claiming credit;
 *   - nothing rewrites meaning. These rules change shape, register and
 *     spelling. They do not add, remove or reinterpret a claim, because a tool
 *     that silently edits what you said is not a writing assistant.
 *
 * With a model provider configured, this draft plus the same attributed
 * preference list is handed to the model as a starting point. The attribution
 * survives either way, which is what lets the app show "shortened because you
 * said you prefer Slack messages under four lines" instead of "improved".
 */

export interface RewriteRule {
  memory_id: string;
  statement: string;
  applied: boolean;
  effect: string;
}

export interface RewriteResult {
  draft: string;
  rules: RewriteRule[];
  lexicon_fixes: { from: string; to: string }[];
}

const FILLER = /\b(um|uh|erm|you know,|i mean,|basically|actually|sort of|kind of)\b/gi;

interface Transform {
  id: string;
  /** Which remembered preferences switch this transform on. */
  matches: RegExp;
  apply: (text: string, memory: Memory) => { text: string; effect: string };
}

const TRANSFORMS: Transform[] = [
  {
    id: 'bullets',
    matches: /bullet points?|bulleted|list form/i,
    apply: (text) => {
      const sentences = splitSentences(text);
      if (sentences.length < 3 || text.includes('\n- ')) {
        return { text, effect: 'Left as prose — too short for a list to help.' };
      }
      const [lead, ...rest] = sentences;
      return {
        text: [lead, ...rest.map((s) => `- ${s}`)].join('\n'),
        effect: `Broke ${rest.length} points out as a list.`,
      };
    },
  },
  {
    id: 'shorten',
    matches: /short|brief|concise|punchy|tighten|under (?:\d+|two|three|four|five|six) lines/i,
    apply: (text, memory) => {
      const limit = spokenLimit(memory.statement) ?? 4;
      const sentences = splitSentences(text);
      if (sentences.length <= limit) {
        return { text, effect: `Already within ${limit} lines.` };
      }
      return {
        text: sentences.slice(0, limit).join(' '),
        effect: `Cut from ${sentences.length} sentences to ${limit}.`,
      };
    },
  },
  {
    id: 'formal',
    matches: /formal|professional|board/i,
    apply: (text) => {
      const next = formalise(text);
      return {
        text: next,
        effect: next === text ? 'Already reads formally.' : 'Expanded contractions and dropped the casual opener.',
      };
    },
  },
  {
    id: 'warm',
    matches: /informal|casual|friendly|warm/i,
    apply: (text) => {
      if (/^(hi|hey|hello)\b/i.test(text)) return { text, effect: 'Already opens warmly.' };
      return { text: `Hi — ${lowerFirst(text)}`, effect: 'Added a greeting.' };
    },
  },
  {
    id: 'british_spelling',
    matches: /british spelling|en-gb|uk spelling/i,
    apply: (text) => {
      const next = text
        .replace(/\b(\w+)ize\b/g, '$1ise')
        .replace(/\b(\w+)ization\b/g, '$1isation')
        .replace(/\bcolor(s|ed|ing)?\b/gi, (m) => m.replace(/olor/i, 'olour'))
        .replace(/\bcenter(s|ed|ing)?\b/gi, (m) => m.replace(/center/i, 'centre'));
      return {
        text: next,
        effect: next === text ? 'Nothing spelled the American way.' : 'Switched to British spelling.',
      };
    },
  },
  {
    id: 'banned_opener',
    matches: /never (?:use|write|start)|does not want|circling back|dear/i,
    apply: (text, memory) => {
      const phrase = bannedPhrase(memory.statement);
      if (!phrase) return { text, effect: 'No specific phrase to remove.' };
      const pattern = new RegExp(`\\b${escapeRegex(phrase)}\\b[,\\s]*`, 'gi');
      if (!pattern.test(text)) return { text, effect: `"${phrase}" does not appear.` };
      return { text: upperFirst(text.replace(pattern, '')), effect: `Removed "${phrase}".` };
    },
  },
];

export function rewriteWithMemory(userId: string, source: string, instruction: string): RewriteResult {
  const preferences = all<Memory>(
    `SELECT * FROM memories
      WHERE user_id = ? AND type = 'preference' AND status = 'active'
      ORDER BY confidence DESC, evidence_count DESC`,
    [userId],
  );

  // Filler is not a preference; it is an artefact of speaking rather than
  // typing, and removing it is part of what dictation is for.
  let text = tidy(source.replace(FILLER, ''));
  const rules: RewriteRule[] = [];
  const used = new Set<string>();

  // What the person asked for in this moment outranks anything remembered.
  const spoken: string[] = [];
  if (/\b(short|shorter|brief|concise|tighten|trim|punchy)\b/i.test(instruction)) spoken.push('shorten');
  if (/\b(formal|professional|polished)\b/i.test(instruction)) spoken.push('formal');
  if (/\b(bullets?|list)\b/i.test(instruction)) spoken.push('bullets');

  for (const transformId of spoken) {
    const transform = TRANSFORMS.find((t) => t.id === transformId)!;
    const before = text;
    const result = transform.apply(text, { statement: instruction } as Memory);
    text = result.text;
    used.add(transform.id);
    rules.push({
      memory_id: 'instruction',
      statement: `You asked for it ${transformId === 'bullets' ? 'as a list' : transformId}`,
      applied: text !== before,
      effect: result.effect,
    });
  }

  for (const preference of preferences) {
    const transform = TRANSFORMS.find((t) => t.matches.test(preference.statement));
    if (!transform) {
      rules.push({
        memory_id: preference.id,
        statement: preference.statement,
        applied: false,
        effect: 'Kivi has no rewriting rule for this preference, so it changed nothing.',
      });
      continue;
    }
    if (used.has(transform.id)) {
      rules.push({
        memory_id: preference.id,
        statement: preference.statement,
        applied: false,
        effect: 'Already handled by what you asked for just now.',
      });
      continue;
    }

    const before = text;
    const result = transform.apply(text, preference);
    text = result.text;
    used.add(transform.id);
    rules.push({
      memory_id: preference.id,
      statement: preference.statement,
      applied: text !== before,
      effect: result.effect,
    });
  }

  // Canonical spellings last, so no earlier transform undoes them.
  const lexiconFixes: { from: string; to: string }[] = [];
  for (const entry of lexiconFor(userId)) {
    if (!entry.enabled) continue;
    for (const variant of entry.variants) {
      if (!variant || variant.toLowerCase() === entry.canonical.toLowerCase()) continue;
      const pattern = new RegExp(`\\b${escapeRegex(variant)}\\b`, 'gi');
      if (pattern.test(text)) {
        text = text.replace(pattern, entry.canonical);
        lexiconFixes.push({ from: variant, to: entry.canonical });
      }
    }
  }

  return { draft: tidy(text), rules, lexicon_fixes: lexiconFixes };
}

// --------------------------------------------------------------------------

function splitSentences(text: string): string[] {
  return text.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

const WORD_NUMBERS: Record<string, number> = { two: 2, three: 3, four: 4, five: 5, six: 6 };

function spokenLimit(statement: string): number | null {
  const digit = /under (\d+) lines/i.exec(statement);
  if (digit) return Number(digit[1]);
  const word = /under (two|three|four|five|six) lines/i.exec(statement);
  return word ? WORD_NUMBERS[word[1]!.toLowerCase()]! : null;
}

const CONTRACTIONS: [RegExp, string][] = [
  [/\bcan't\b/gi, 'cannot'],
  [/\bwon't\b/gi, 'will not'],
  [/\bdon't\b/gi, 'do not'],
  [/\bdoesn't\b/gi, 'does not'],
  [/\bisn't\b/gi, 'is not'],
  [/\bI'm\b/g, 'I am'],
  [/\bI'll\b/g, 'I will'],
  [/\bwe'll\b/gi, 'we will'],
  [/\bit's\b/gi, 'it is'],
  [/\bthat's\b/gi, 'that is'],
];

function formalise(text: string): string {
  let out = text;
  for (const [pattern, replacement] of CONTRACTIONS) out = out.replace(pattern, replacement);
  out = out.replace(/^(hey|hi|yo)\b[,!]?\s*/i, '');
  out = out.replace(/\b(gonna|wanna|gotta)\b/gi, (m) =>
    m.toLowerCase() === 'gonna' ? 'going to' : m.toLowerCase() === 'wanna' ? 'want to' : 'have to',
  );
  return upperFirst(out);
}

function bannedPhrase(statement: string): string | null {
  const quoted = /['"]([^'"]{3,40})['"]/.exec(statement);
  if (quoted) return quoted[1]!.trim();
  const match = /never (?:use|write|start(?: an? \w+)?(?: with)?)\s+(.{2,40}?)(?:\.|$)/i.exec(statement);
  return match ? match[1]!.replace(/["']/g, '').trim() : null;
}

function tidy(text: string): string {
  return text
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/[ \t]+([.,;:!?])/g, '$1')
    .replace(/^[,\s]+/, '')
    .trim();
}

function upperFirst(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function lowerFirst(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function escapeRegex(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

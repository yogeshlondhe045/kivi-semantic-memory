import type { ExtractionResult, MemoryCandidate, Usage } from '../types.ts';
import { contentTokens, editDistance, normalise, phoneticKey, truncate } from '../util/text.ts';
import { parseDueDate } from '../util/time.ts';
import type {
  CompletionRequest,
  CompletionResponse,
  ContentBlock,
  ExtractionInput,
  LlmProvider,
} from './types.ts';

/**
 * The offline provider.
 *
 * This exists so the declared review path needs no credentials and no network:
 * `cp .env.example .env && npm run setup && npm start` gives a working product.
 * It is a rule-based implementation of the same interface the Anthropic
 * provider implements — the surrounding system (policy gate, storage,
 * retrieval, provenance, traces, abstention) is identical either way, which is
 * the point. Swap LLM_PROVIDER=anthropic and the same pipeline runs on a model.
 *
 * It is honest about what it is: every trace, every eval result and the app
 * header record which provider produced a result. Nothing here is a canned
 * answer for the demo — the rules run over whatever text they are given.
 */

const ZERO: Usage = { tokens_in: 0, tokens_out: 0, cost_usd: 0, calls: 0 };

// --------------------------------------------------------------------------
// Extraction
// --------------------------------------------------------------------------

interface Rule {
  id: string;
  type: MemoryCandidate['type'];
  pattern: RegExp;
  confidence: number;
  origin: 'stated' | 'observed';
  build: (m: RegExpMatchArray, sentence: string) => { subject: string; statement: string; detail?: Record<string, unknown> } | null;
}

const PREFERENCE_RULES: Rule[] = [
  {
    id: 'pref.explicit',
    type: 'preference',
    pattern: /\bi (?:really )?(?:prefer|like)\s+(.{6,120}?)(?:\.|,|$)/i,
    confidence: 0.78,
    origin: 'stated',
    build: (m) => ({ subject: headOf(m[1]!), statement: `Prefers ${clean(m[1]!)}` }),
  },
  {
    id: 'pref.dislike',
    type: 'preference',
    pattern: /\bi (?:don'?t like|hate|can'?t stand|dislike)\s+(.{6,120}?)(?:\.|,|$)/i,
    confidence: 0.76,
    origin: 'stated',
    build: (m) => ({ subject: headOf(m[1]!), statement: `Does not want ${clean(m[1]!)}` }),
  },
  {
    id: 'pref.always',
    type: 'preference',
    pattern: /\b(?:always|make sure to|remember to|please)\s+(use|write|keep|send|format|address|call|spell)\s+(.{4,110}?)(?:\.|,|$)/i,
    confidence: 0.74,
    origin: 'stated',
    build: (m) => ({ subject: headOf(m[2]!), statement: `Always ${m[1]!.toLowerCase()} ${clean(m[2]!)}` }),
  },
  {
    id: 'pref.never',
    type: 'preference',
    pattern: /\b(?:never|don'?t ever|do not)\s+(use|write|send|start|open|call)\s+(.{4,110}?)(?:\.|,|$)/i,
    confidence: 0.74,
    origin: 'stated',
    build: (m) => ({ subject: headOf(m[2]!), statement: `Never ${m[1]!.toLowerCase()} ${clean(m[2]!)}` }),
  },
  {
    id: 'pref.style',
    type: 'preference',
    pattern: /\b(?:keep|make)\s+(?:it|this|the \w+)\s+(short|brief|concise|formal|informal|casual|friendly|direct|plain|punchy|warm)\b/i,
    confidence: 0.7,
    origin: 'stated',
    build: (m) => ({ subject: 'writing tone', statement: `Wants writing kept ${m[1]!.toLowerCase()}` }),
  },
  {
    id: 'pref.tooling',
    type: 'preference',
    pattern: /\bwe (?:should |always )?(?:use|standardis?e on)\s+([A-Z][\w.+-]*(?:\s[A-Z][\w.+-]*)?)\s+(?:for|as)\s+(.{4,80}?)(?:\.|,|$)/,
    confidence: 0.68,
    origin: 'stated',
    build: (m) => ({ subject: m[1]!.trim(), statement: `Uses ${m[1]!.trim()} for ${clean(m[2]!)}` }),
  },
];

const FACT_RULES: Rule[] = [
  {
    id: 'fact.role',
    type: 'fact',
    pattern: /\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+is\s+(?:the|our|my)\s+([A-Za-z][\w\s-]{1,48}?)(?:\s+(?:on|for|at)\s+([A-Z][\w\s-]{2,40}?))?(?:\.|,|$)/,
    confidence: 0.72,
    origin: 'stated',
    // Keyed on the role, not the person: "who is the PM on Meridian" has one
    // answer at a time, so a later name for the same slot must supersede the
    // earlier one rather than sit beside it as a second truth.
    build: (m) => {
      const role = clean(m[2]!);
      // "on Meridian now" and "on Meridian" are the same slot. Without this the
      // handover reads as a second, parallel truth instead of a replacement.
      const scope = m[3] ? clean(m[3]).replace(/\s+(now|currently|these days|at the moment|going forward)$/i, '') : null;
      const slot = scope ? `${role} on ${scope}` : role;
      return {
        subject: slot,
        statement: `${m[1]!.trim()} is the ${slot}`,
        detail: { person: m[1]!.trim(), role, scope, attribute: slot },
      };
    },
  },
  {
    id: 'fact.possessive',
    type: 'fact',
    pattern: /\bmy\s+([a-z][\w\s-]{2,30}?)\s+is\s+(.{3,80}?)(?:\.|,|$)/i,
    confidence: 0.72,
    origin: 'stated',
    build: (m) => ({
      subject: clean(m[1]!),
      statement: `Their ${clean(m[1]!)} is ${clean(m[2]!)}`,
      detail: { attribute: clean(m[1]!), value: clean(m[2]!) },
    }),
  },
  {
    id: 'fact.project',
    type: 'fact',
    pattern: /\b(?:the\s+)?(?:project|repo|repository|service|dashboard|doc|document)\s+(?:is\s+)?(?:called\s+)?([A-Z][\w-]*(?:\s[A-Z][\w-]*)?)\b/,
    confidence: 0.6,
    origin: 'observed',
    build: (m) => ({ subject: m[1]!.trim(), statement: `Works on ${m[1]!.trim()}` }),
  },
  {
    id: 'fact.deadline',
    type: 'fact',
    pattern: /\b(?:the\s+)?([\w\s-]{3,40}?)\s+(?:deadline|launch|review|demo)\s+is\s+(?:on\s+)?([\w\s,]{3,30}?)(?:\.|,|$)/i,
    confidence: 0.62,
    origin: 'stated',
    build: (m) => ({ subject: clean(m[1]!), statement: `${clean(m[1]!)} is due ${clean(m[2]!)}` }),
  },
  {
    id: 'fact.hosted',
    type: 'fact',
    pattern: /\b(?:the\s+)?([A-Z][\w-]*(?:\s[\w-]+){0,3}?)\s+(?:lives|sits|runs|is hosted|is deployed)\s+(?:in|on|at)\s+(?:the\s+)?(.{3,40}?)(?:\.|,|$)/,
    confidence: 0.68,
    origin: 'stated',
    build: (m) => ({
      subject: clean(m[1]!),
      statement: `${clean(m[1]!)} lives in ${clean(m[2]!)}`,
      detail: { attribute: `${clean(m[1]!)} location`, value: clean(m[2]!) },
    }),
  },
  {
    id: 'fact.location',
    type: 'fact',
    pattern: /\bi(?:'m| am)\s+(?:based|working|sitting)\s+(?:in|out of|from)\s+([A-Z][\w\s]{2,30}?)(?:\.|,|$)/,
    confidence: 0.74,
    origin: 'stated',
    build: (m) => ({ subject: 'location', statement: `Works from ${m[1]!.trim()}` }),
  },
];

const COMMITMENT_RULES: Rule[] = [
  {
    id: 'commit.will',
    type: 'commitment',
    pattern: /\bi(?:'ll| will| am going to| gonna)\s+((?:send|share|write|finish|review|ship|draft|update|fix|call|email|post|prepare|circulate)\s+.{4,110}?)(?:\.|$)/i,
    confidence: 0.7,
    origin: 'stated',
    build: (m, sentence) => ({
      subject: headOf(m[1]!),
      statement: `Said they would ${clean(m[1]!)}`,
      detail: { due_at: parseDueDate(sentence), verb: m[1]!.split(/\s/)[0]!.toLowerCase() },
    }),
  },
  {
    id: 'commit.promised',
    type: 'commitment',
    pattern: /\bi\s+promised\s+(.{4,110}?)(?:\.|$)/i,
    confidence: 0.76,
    origin: 'stated',
    build: (m, sentence) => ({
      subject: headOf(m[1]!),
      statement: `Promised ${clean(m[1]!)}`,
      detail: { due_at: parseDueDate(sentence) },
    }),
  },
  {
    id: 'commit.need',
    type: 'commitment',
    pattern: /\bi need to\s+((?:send|finish|review|ship|reply to|call|submit|prepare)\s+.{4,110}?)\s+(by|before)\s+([\w\s]{3,24}?)(?:\.|$)/i,
    confidence: 0.68,
    origin: 'stated',
    build: (m, sentence) => ({
      subject: headOf(m[1]!),
      statement: `Needs to ${clean(m[1]!)} by ${clean(m[3]!)}`,
      detail: { due_at: parseDueDate(sentence) },
    }),
  },
];

const ALL_RULES = [...PREFERENCE_RULES, ...FACT_RULES, ...COMMITMENT_RULES];

const PROPER_NOUN = /\b([A-Z][a-zA-Z0-9]+(?:\s[A-Z][a-zA-Z0-9]+){0,2})\b/g;

const NOT_ENTITIES = new Set([
  'i', 'the', 'a', 'an', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
  'sunday', 'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
  'september', 'october', 'november', 'december', 'hey kivi', 'kivi', 'ok', 'okay', 'yes', 'no',
  'so', 'and', 'but', 'also', 'then', 'now', 'today', 'tomorrow', 'yesterday', 'this', 'that',
  'we', 'they', 'he', 'she', 'it', 'let', 'lets', 'please', 'thanks', 'hi', 'hello',
]);

export class LocalProvider implements LlmProvider {
  readonly name = 'local' as const;
  readonly model = 'kivi-rules-v1';

  async extract(input: ExtractionInput): Promise<{ result: ExtractionResult; usage: Usage }> {
    const { formatted, raw_asr, app } = input.dictation;
    const sentences = splitSentences(formatted);
    const candidates: MemoryCandidate[] = [];
    const notes: string[] = [];
    const asrFloor = 1;

    for (const sentence of sentences) {
      for (const rule of ALL_RULES) {
        const match = sentence.match(rule.pattern);
        if (!match) continue;
        const built = rule.build(match, sentence);
        if (!built) continue;
        if (built.statement.length < 8) continue;
        candidates.push({
          type: rule.type,
          subject: built.subject,
          statement: built.statement,
          detail: { ...built.detail, rule: rule.id },
          confidence: rule.confidence,
          quote: sentence.trim(),
          origin: rule.origin,
        });
      }
    }

    // Entities: proper nouns that the recogniser struggled with, or that recur.
    const entityCandidates = extractEntities(formatted, raw_asr, input.knownSubjects);
    candidates.push(...entityCandidates);

    if (candidates.length === 0 && contentTokens(formatted).length > asrFloor) {
      notes.push('No durable claim found; recorded as an episode only.');
    }

    const result: ExtractionResult = {
      episode: buildEpisode(formatted, app),
      candidates: dedupeCandidates(candidates),
      notes,
    };
    return { result, usage: ZERO };
  }

  async complete(request: CompletionRequest): Promise<CompletionResponse> {
    const blocks = decideLocally(request);
    return {
      stopReason: blocks.some((b) => b.type === 'tool_use') ? 'tool_use' : 'end_turn',
      blocks,
      usage: ZERO,
    };
  }
}

// --------------------------------------------------------------------------
// Extraction helpers
// --------------------------------------------------------------------------

function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+|\n+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function clean(input: string): string {
  return input
    .replace(/\s+/g, ' ')
    .replace(/[.,;:]+$/, '')
    .trim();
}

function headOf(input: string): string {
  const words = clean(input).split(' ').slice(0, 4).join(' ');
  return words || clean(input);
}

function extractEntities(formatted: string, rawAsr: string, known: string[]): MemoryCandidate[] {
  const out: MemoryCandidate[] = [];
  const rawTokens = rawAsr.split(/\s+/).map((t) => t.replace(/[^A-Za-z0-9'-]/g, ''));
  const seen = new Set<string>();
  const knownSet = new Set(known.map(normalise));

  for (const match of formatted.matchAll(PROPER_NOUN)) {
    const term = match[1]!.trim();
    const key = normalise(term);
    if (!key || seen.has(key) || NOT_ENTITIES.has(key)) continue;
    if (term.length < 3) continue;
    // A capitalised word that opens a sentence is usually not a name.
    const at = match.index ?? 0;
    const isSentenceStart = at === 0 || /[.!?\n]\s+$/.test(formatted.slice(Math.max(0, at - 3), at));
    seen.add(key);

    const variant = findAsrVariant(term, rawTokens);
    const recognised = knownSet.has(key);

    if (!variant && isSentenceStart && !recognised) continue;

    out.push({
      type: 'entity',
      subject: term,
      // The claim is "this name exists and is spelled this way". Which variants
      // the recogniser produced is evidence about the claim, not part of it —
      // keeping them out of the statement is what lets repeat sightings
      // reinforce the entity instead of endlessly contradicting it.
      statement: `"${term}" is a name they use`,
      detail: { canonical: term, variants: variant ? [variant] : [], lexicon: true },
      confidence: variant ? 0.72 : recognised ? 0.66 : 0.5,
      quote: sentenceAround(formatted, at),
      origin: 'observed',
    });
  }
  return out;
}

/** Finds the token in the raw ASR that the formatter corrected into `term`. */
function findAsrVariant(term: string, rawTokens: string[]): string | null {
  const target = normalise(term);
  const targetPhon = phoneticKey(term);
  if (target.includes(' ')) return null;
  let best: { token: string; distance: number } | null = null;
  for (const token of rawTokens) {
    const norm = normalise(token);
    if (!norm || norm === target) return null; // recogniser already got it right
    if (Math.abs(norm.length - target.length) > 3) continue;
    const distance = editDistance(norm, target, 3);
    const samePhonetic = phoneticKey(token) === targetPhon && targetPhon.length > 2;
    if (distance <= 2 || (samePhonetic && distance <= 3)) {
      if (!best || distance < best.distance) best = { token, distance };
    }
  }
  return best ? best.token : null;
}

function sentenceAround(text: string, index: number): string {
  const before = text.lastIndexOf('.', index);
  const after = text.indexOf('.', index);
  return text.slice(before + 1, after === -1 ? text.length : after + 1).trim();
}

function buildEpisode(formatted: string, app: string) {
  const sentences = splitSentences(formatted);
  const summary = truncate(sentences.slice(0, 2).join(' ') || formatted, 220);
  const counts = new Map<string, number>();
  for (const t of contentTokens(formatted)) counts.set(t, (counts.get(t) ?? 0) + 1);
  const topics = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 6)
    .map(([t]) => t);
  const entities = [...new Set([...formatted.matchAll(PROPER_NOUN)].map((m) => m[1]!.trim()))]
    .filter((e) => !NOT_ENTITIES.has(normalise(e)))
    .slice(0, 8);
  const words = contentTokens(formatted).length;
  const salience = Math.max(0.15, Math.min(1, words / 60 + entities.length * 0.05 + (app === 'slack' ? 0.05 : 0)));
  return { summary, topics, entities, salience: Number(salience.toFixed(3)) };
}

function dedupeCandidates(candidates: MemoryCandidate[]): MemoryCandidate[] {
  const byKey = new Map<string, MemoryCandidate>();
  for (const c of candidates) {
    const key = `${c.type}:${normalise(c.statement)}`;
    const existing = byKey.get(key);
    if (!existing || c.confidence > existing.confidence) byKey.set(key, c);
  }
  return [...byKey.values()];
}

// --------------------------------------------------------------------------
// Agent policy (the offline stand-in for a model choosing tools)
// --------------------------------------------------------------------------

/**
 * The agent loop hands us the same messages and tool specs it would hand a
 * model. We answer with the same block shapes. The decision itself is a small
 * deterministic policy: classify the request, call one tool, then write the
 * answer from the tool result — abstaining when the result is empty.
 */
const COMMITMENT_QUESTION =
  /\b(promis\w*|commit(?:ment|ments|ted)?|owed?|owes|supposed to|follow[- ]?ups?|to-?dos?|action items?|deadlines?|due)\b|\b(?:say|said|told\s+\w+)\s+i\s+(?:would|will|'ll)\b|\bon my plate\b/;

function decideLocally(request: CompletionRequest): ContentBlock[] {
  const lastUser = [...request.messages].reverse().find((m) => m.role === 'user');
  const toolResults = collectToolResults(request.messages);
  const question = textOf(lastUser?.content ?? []) || lastUserQuestion(request.messages);

  if (toolResults.length > 0) {
    return [{ type: 'text', text: composeAnswer(question, toolResults) }];
  }

  const q = question.toLowerCase();
  const call = (name: string, input: Record<string, unknown>): ContentBlock[] => [
    { type: 'tool_use', id: `local_${Math.abs(hashString(name + question))}`, name, input },
  ];

  if (/\b(forget|delete|remove|stop remembering|that'?s wrong|no longer)\b/.test(q)) {
    return call('update_memory', { instruction: question });
  }
  if (/\b(polish|rewrite|clean(?: it)? up|tidy|make it|turn (?:it|that) into|redraft|shorten)\b/.test(q)) {
    return call('rewrite_dictation', { instruction: question });
  }
  if (COMMITMENT_QUESTION.test(q)) {
    return call('list_commitments', { window: /\boverdue|late|missed\b/.test(q) ? 'overdue' : 'open', about: question });
  }
  if (/\b(find|pull up|show me|which dictation|the one where|what did i (?:dictate|say) in)\b/.test(q)) {
    return call('search_dictations', { query: question });
  }
  return call('recall', { question });
}

function collectToolResults(messages: LlmMessageLike[]): { name: string; content: string }[] {
  const names = new Map<string, string>();
  const out: { name: string; content: string }[] = [];
  for (const message of messages) {
    for (const block of message.content) {
      if (block.type === 'tool_use') names.set(block.id, block.name);
      if (block.type === 'tool_result') {
        out.push({ name: names.get(block.tool_use_id) ?? 'tool', content: block.content });
      }
    }
  }
  return out;
}

type LlmMessageLike = { role: string; content: ContentBlock[] };

function lastUserQuestion(messages: LlmMessageLike[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (m.role !== 'user') continue;
    const text = textOf(m.content);
    if (text) return text;
  }
  return '';
}

function textOf(blocks: ContentBlock[]): string {
  return blocks
    .filter((b): b is Extract<ContentBlock, { type: 'text' }> => b.type === 'text')
    .map((b) => b.text)
    .join('\n')
    .trim();
}

/**
 * Answers are assembled from the tool payload only. If the payload carries no
 * grounded items, we say so rather than filling the gap.
 */
function composeAnswer(question: string, results: { name: string; content: string }[]): string {
  const last = results[results.length - 1]!;
  let payload: ToolPayload;
  try {
    payload = JSON.parse(last.content) as ToolPayload;
  } catch {
    return 'Something went wrong reading that result, so I would rather not guess.';
  }

  if (payload.draft) {
    return [payload.lead, '', payload.draft].filter(Boolean).join('\n');
  }

  const items = payload.items ?? [];
  if (payload.grounded === false || (items.length === 0 && payload.empty_message)) {
    return (
      payload.empty_message ??
      "I can't find anything in your history that answers that, so I'd rather not guess."
    );
  }

  if (items.length === 0) return payload.lead ?? 'Done.';

  const lead = payload.lead ?? '';
  const lines = items.slice(0, 5).map((item) => `- ${item.text}${item.when ? ` (${item.when})` : ''}`);
  return [lead, ...lines].filter(Boolean).join('\n');
}

interface ToolPayload {
  grounded?: boolean;
  lead?: string;
  draft?: string;
  empty_message?: string;
  items?: { text: string; when?: string }[];
}

function hashString(input: string): number {
  let h = 0;
  for (let i = 0; i < input.length; i++) h = (Math.imul(h, 31) + input.charCodeAt(i)) | 0;
  return h;
}

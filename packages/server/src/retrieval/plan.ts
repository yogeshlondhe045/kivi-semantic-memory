import type { MemoryType, QueryPlan } from '../types.ts';
import { contentTokens, normalise } from '../util/text.ts';
import { parseTimeExpression } from '../util/time.ts';

/**
 * Query planning.
 *
 * Speech carries filters that text search throws away: "yesterday", "in Slack",
 * "the one about pricing". Turning those into explicit constraints is what lets
 * retrieval be narrow, and lets the app show the person the window it searched
 * so they can argue with it.
 *
 * This runs before retrieval regardless of provider. With the model provider it
 * is a pre-filter the tools honour; the model still chooses which tool to call.
 */

export const KNOWN_APPS = [
  'slack',
  'gmail',
  'notion',
  'whatsapp',
  'linear',
  'vscode',
  'docs',
  'browser',
  'notes',
  'jira',
  'figma',
] as const;

const APP_ALIASES: Record<string, string> = {
  email: 'gmail',
  mail: 'gmail',
  inbox: 'gmail',
  'google docs': 'docs',
  doc: 'docs',
  document: 'docs',
  code: 'vscode',
  editor: 'vscode',
  ticket: 'linear',
  issue: 'linear',
  chat: 'slack',
  wa: 'whatsapp',
};

const TYPE_HINTS: { type: MemoryType; pattern: RegExp }[] = [
  { type: 'preference', pattern: /\b(prefer|like|style|tone|how do i (?:usually|normally)|my way|always|never)\b/i },
  { type: 'commitment', pattern: /\b(promis|commit|owe|supposed to|follow[- ]?up|due|deadline|action item|to-?do)\b/i },
  { type: 'entity', pattern: /\b(spell|spelt|spelled|name of|what is .* called|how do you write .* name)\b/i },
  { type: 'fact', pattern: /\b(who|what|which|where|when|is the|are the)\b/i },
];

const INTENT_RULES: { intent: QueryPlan['intent']; pattern: RegExp }[] = [
  { intent: 'memory_admin', pattern: /\b(forget|delete|remove|stop remembering|that'?s wrong|no longer|don'?t remember)\b/i },
  { intent: 'rewrite', pattern: /\b(polish|rewrite|clean (?:it|this) up|tidy|redraft|shorten|make it (?:shorter|formal|friendlier)|turn (?:it|that) into)\b/i },
  { intent: 'commitments', pattern: /\b(what did i promise|what do i owe|commitments|follow[- ]?ups|action items|my to-?dos|say i would|said i would|on my plate)\b/i },
  { intent: 'find_dictation', pattern: /\b(find|pull up|show me|which dictation|the one (?:where|about)|what did i (?:dictate|record))\b/i },
  { intent: 'smalltalk', pattern: /^(hi|hello|hey kivi|thanks|thank you|good morning)\b/i },
];

export function planQuery(raw: string, now = Date.now()): QueryPlan {
  const text = raw.trim();
  const lower = text.toLowerCase();

  let intent: QueryPlan['intent'] = 'recall';
  for (const rule of INTENT_RULES) {
    if (rule.pattern.test(lower)) {
      intent = rule.intent;
      break;
    }
  }

  const apps: string[] = [];
  const matchedAppWords: string[] = [];

  for (const app of KNOWN_APPS) {
    if (new RegExp(`\\b${app}\\b`, 'i').test(lower)) {
      apps.push(app);
      matchedAppWords.push(app);
    }
  }
  // An alias only narrows the search when it is being used as a place —
  // "in email", not "how should I open an email". Otherwise "email" is the
  // subject of the question and removing it from the search terms guarantees
  // the wrong answer.
  for (const [alias, app] of Object.entries(APP_ALIASES)) {
    if (new RegExp(`\\b(?:in|on|from|via|inside|my)\\s+${alias}\\b`, 'i').test(lower) && !apps.includes(app)) {
      apps.push(app);
      matchedAppWords.push(alias);
    }
  }

  const time = parseTimeExpression(text, { now });

  const memoryTypes: MemoryType[] = [];
  for (const hint of TYPE_HINTS) {
    if (hint.pattern.test(lower)) memoryTypes.push(hint.type);
  }

  // Words the planner has already turned into structure are not search terms
  // any more. Leaving "yesterday" and "slack" in the keyword list makes every
  // coverage check look worse than it is and pulls unrelated results forward.
  // Only the time expression is consumed. An app name stays a search term even
  // when it also becomes a filter — "Slack messages" is a topic as much as a
  // place, and dropping the word loses the preference that mentions it.
  const consumed = new Set([...(time.label ? contentTokens(time.label) : []), ...TIME_WORDS]);

  return {
    intent,
    keywords: contentTokens(text)
      .filter((t) => !consumed.has(t) && !/^\d{1,2}(am|pm)?$/.test(t))
      .slice(0, 16),
    entities: properNouns(text).filter((e) => !consumed.has(e.toLowerCase())),
    apps,
    time_range: { from: time.from, to: time.to, label: time.label, centre: time.centre ?? null },
    memory_types: memoryTypes.length ? [...new Set(memoryTypes)] : [],
    wants_sources: /\b(source|where did|how do you know|which dictation|cite)\b/i.test(lower),
    raw: text,
  };
}

const SENTENCE_OPENERS = new Set([
  'what', 'when', 'where', 'who', 'which', 'how', 'why', 'hey', 'can', 'did', 'do', 'i',
  'find', 'show', 'pull', 'tell', 'give', 'polish', 'rewrite', 'make', 'the', 'is', 'are',
  'should', 'would', 'could', 'remind', 'help', 'please', 'ok', 'okay', 'and', 'but',
  'forget', 'remember', 'delete', 'remove', 'stop', 'don', 'dont', 'never', 'always', 'take',
]);

const TIME_WORDS = [
  'yesterday', 'today', 'tomorrow', 'morning', 'afternoon', 'evening', 'tonight', 'around',
  'last', 'next', 'week', 'month', 'recently', 'lately', 'monday', 'tuesday', 'wednesday',
  'thursday', 'friday', 'saturday', 'sunday', 'pm', 'am',
];

function properNouns(text: string): string[] {
  const out: string[] = [];
  for (const match of text.matchAll(/\b([A-Z][a-zA-Z0-9]+(?:\s[A-Z][a-zA-Z0-9]+)?)\b/g)) {
    const term = match[1]!.trim();
    const key = normalise(term);
    if (!key || SENTENCE_OPENERS.has(key.split(' ')[0]!)) continue;
    if (key === 'kivi' || key === 'hey kivi') continue;
    if (KNOWN_APPS.includes(key as (typeof KNOWN_APPS)[number])) continue;
    if (/^\d{1,2}(am|pm)?$/.test(key)) continue;
    out.push(term);
  }
  return [...new Set(out)].slice(0, 6);
}

export function describePlan(plan: QueryPlan): string {
  const parts: string[] = [];
  if (plan.time_range.label) parts.push(`from ${plan.time_range.label}`);
  if (plan.apps.length) parts.push(`in ${plan.apps.join(' or ')}`);
  if (plan.entities.length) parts.push(`about ${plan.entities.join(', ')}`);
  return parts.length ? `Looked ${parts.join(' ')}.` : 'Searched everything Kivi has.';
}

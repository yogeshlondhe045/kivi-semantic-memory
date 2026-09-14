import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mulberry32, pick, shuffle, chance } from '../packages/server/src/util/rand.ts';
import { APPS, DEVICES, ENTITIES, PERSONA, WORLD } from './persona.ts';

/**
 * Corpus generator.
 *
 * Deterministic: the same seed produces the same 500 records, byte for byte, so
 * an evaluation result committed to the repository can be reproduced.
 *
 * The corpus is not trying to be large or clever. It is trying to contain the
 * specific situations this system claims to handle:
 *
 *   - the same proper noun misheard several different ways
 *   - one fact split across three dictations weeks apart (multi-hop recall)
 *   - a stated preference repeated until it is worth trusting
 *   - a preference later reversed (supersession)
 *   - commitments with and without deadlines
 *   - sensitive material that must produce no memory at all
 *   - filler, tests and passing moods that must produce no memory either
 *   - Hinglish code-switching, because the product is built for it
 *   - topics nobody ever dictated about, so abstention has something to fail on
 *
 * Everything else is ordinary working chatter, which is what most dictation is.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, 'kivi-corpus-v1.jsonl');

/** The corpus is generated relative to this instant; the seeder shifts it to today. */
export const ANCHOR = Date.parse('2026-09-14T00:00:00.000Z');
const DAY = 86_400_000;
const HOUR = 3_600_000;
const MINUTE = 60_000;
/**
 * Every hour in this file is a wall-clock hour in Bengaluru. The stored
 * timestamp is absolute, so the offset has to come off explicitly — without it
 * a 5PM dictation lands at 22:30 local and "around 5PM yesterday" finds nothing.
 */
const TZ_OFFSET = 330 * MINUTE;

export interface CorpusRecord {
  external_id: string;
  captured_at: number;
  /** Milliseconds before the anchor. The seeder uses this to re-base the history. */
  offset_ms: number;
  app: string;
  device: string;
  duration_ms: number;
  language: string;
  asr_confidence: number;
  raw_asr: string;
  formatted: string;
  style: string;
  /** Generator-side labels. The pipeline never reads these; the evaluation does. */
  labels: {
    intent: string;
    expect_memory: boolean;
    expect_reason?: string;
    entities?: string[];
  };
}

type Builder = (rng: () => number, ctx: BuildContext) => Omit<CorpusRecord, 'external_id' | 'captured_at' | 'offset_ms'>;

interface BuildContext {
  entity: (kind?: string) => (typeof ENTITIES)[number];
}

// --------------------------------------------------------------------------
// ASR degradation — how speech actually arrives
// --------------------------------------------------------------------------

const FILLERS = ['um', 'uh', 'so', 'like', 'you know', 'i mean', 'okay so', 'right'];

function degrade(rng: () => number, formatted: string, entities: string[]): string {
  let text = formatted;

  for (const name of entities) {
    const entity = ENTITIES.find((e) => e.canonical === name);
    if (!entity) continue;
    if (chance(rng, 0.62)) {
      const variant = pick(rng, entity.variants);
      text = text.replace(new RegExp(`\\b${entity.canonical}\\b`, 'g'), variant);
    }
  }

  text = text.toLowerCase().replace(/[.,;:!?]/g, '');

  const words = text.split(/\s+/);
  const out: string[] = [];
  for (let i = 0; i < words.length; i++) {
    if (i > 0 && chance(rng, 0.07)) out.push(pick(rng, FILLERS));
    // Recognisers repeat words when someone restarts a sentence.
    if (chance(rng, 0.03)) out.push(words[i]!);
    out.push(words[i]!);
  }
  return out.join(' ').replace(/\s{2,}/g, ' ').trim();
}

// --------------------------------------------------------------------------
// Builders
// --------------------------------------------------------------------------

const STANDUP: Builder = (rng, ctx) => {
  const project = ctx.entity('project');
  const person = ctx.entity('person');
  const formatted = pick(rng, [
    `Yesterday I finished the retry logic on ${project.canonical} and pushed it for review. Today I'm picking up the ingestion errors ${person.canonical} flagged. No blockers.`,
    `Standup: ${project.canonical} migration is done on staging. Today I'm pairing with ${person.canonical} on the rate limiter. Blocked on nothing right now.`,
    `I spent most of yesterday on the ${project.canonical} dashboard queries. Today I want to get the caching in. ${person.canonical} is reviewing the earlier PR.`,
  ]);
  return {
    app: 'slack',
    device: pick(rng, [...DEVICES]),
    duration_ms: 14_000 + Math.floor(rng() * 12_000),
    language: 'en',
    asr_confidence: 0.9 + rng() * 0.08,
    raw_asr: degrade(rng, formatted, [project.canonical, person.canonical]),
    formatted,
    style: 'work-chat',
    labels: { intent: 'standup', expect_memory: false, entities: [project.canonical, person.canonical] },
  };
};

const PREFERENCE: Builder = (rng) => {
  const formatted = pick(rng, [
    'Keep this short. I prefer Slack messages under four lines, anything longer and nobody reads it.',
    "I don't like opening messages with 'just circling back'. It reads like an apology.",
    'Always use British spelling in anything customer-facing. We decided that months ago.',
    'I prefer bullet points over paragraphs when there is more than one decision in a message.',
    'Never start an email with Dear. It sounds like a letter from a bank.',
    'Keep it formal for anything that goes to the board.',
  ]);
  return {
    app: pick(rng, ['slack', 'notes', 'gmail']),
    device: pick(rng, [...DEVICES]),
    duration_ms: 8_000 + Math.floor(rng() * 7_000),
    language: 'en',
    asr_confidence: 0.91 + rng() * 0.07,
    raw_asr: degrade(rng, formatted, []),
    formatted,
    style: 'note',
    labels: { intent: 'preference', expect_memory: true },
  };
};

const COMMITMENT: Builder = (rng, ctx) => {
  const person = ctx.entity('person');
  const project = ctx.entity('project');
  const formatted = pick(rng, [
    `I'll send ${person.canonical} the ${project.canonical} pricing draft by Friday.`,
    `I promised ${person.canonical} a written summary of the ${project.canonical} incident before Thursday.`,
    `I need to review the ${project.canonical} migration plan by Tuesday, otherwise it blocks ${person.canonical}.`,
    `I'll write up the ${project.canonical} onboarding doc this week and share it with ${person.canonical}.`,
  ]);
  return {
    app: pick(rng, ['slack', 'notes', 'linear']),
    device: pick(rng, [...DEVICES]),
    duration_ms: 9_000 + Math.floor(rng() * 8_000),
    language: 'en',
    asr_confidence: 0.9 + rng() * 0.08,
    raw_asr: degrade(rng, formatted, [person.canonical, project.canonical]),
    formatted,
    style: 'note',
    labels: { intent: 'commitment', expect_memory: true, entities: [person.canonical, project.canonical] },
  };
};

const FACT: Builder = (rng, ctx) => {
  const project = ctx.entity('project');
  // Facts are drawn from the shared world so that repeated mentions reinforce
  // one another. The only contradictions in this corpus are the scripted ones.
  const lead = WORLD.designLead[project.canonical] ?? 'Aarti';
  const formatted = pick(rng, [
    `${lead} is the design lead on ${project.canonical}.`,
    `We use ${WORLD.deployTool} for deploys now, not the old script.`,
    `The ${project.canonical} staging environment lives in the ${WORLD.region[project.canonical] ?? 'Singapore'} region.`,
    `My manager is ${WORLD.manager}.`,
    `The ${project.canonical} launch is on ${WORLD.launch[project.canonical] ?? 'the fourteenth of next month'}.`,
  ]);
  const person = { canonical: lead } as (typeof ENTITIES)[number];
  return {
    app: pick(rng, ['notion', 'slack', 'docs']),
    device: pick(rng, [...DEVICES]),
    duration_ms: 7_000 + Math.floor(rng() * 6_000),
    language: 'en',
    asr_confidence: 0.89 + rng() * 0.09,
    raw_asr: degrade(rng, formatted, [person.canonical, project.canonical, WORLD.deployTool]),
    formatted,
    style: 'note',
    labels: { intent: 'fact', expect_memory: true, entities: [person.canonical, project.canonical] },
  };
};

const MESSAGE: Builder = (rng, ctx) => {
  const person = ctx.entity('person');
  const project = ctx.entity('project');
  const formatted = pick(rng, [
    `${person.canonical}, the ${project.canonical} numbers are in the sheet. I've left comments on the two rows that look wrong. Have a look before the call.`,
    `Morning — I pushed the fix for the ${project.canonical} timeout. Can you sanity check it before we ship?`,
    `Quick update on ${project.canonical}: the migration ran clean overnight, no errors in the logs. I'll keep watching it today.`,
    `${person.canonical} I'm going to move the ${project.canonical} sync to Thursday, Wednesday is too tight for everyone.`,
  ]);
  return {
    app: pick(rng, ['slack', 'whatsapp', 'gmail']),
    device: pick(rng, [...DEVICES]),
    duration_ms: 16_000 + Math.floor(rng() * 14_000),
    language: 'en',
    asr_confidence: 0.88 + rng() * 0.1,
    raw_asr: degrade(rng, formatted, [person.canonical, project.canonical]),
    formatted,
    style: 'work-chat',
    labels: { intent: 'message', expect_memory: false, entities: [person.canonical, project.canonical] },
  };
};

const HINGLISH: Builder = (rng, ctx) => {
  const person = ctx.entity('person');
  const project = ctx.entity('project');
  const formatted = pick(rng, [
    `${person.canonical}, ${project.canonical} ka deploy ho gaya, but staging pe ek error aa raha hai. Can you check once?`,
    `Yaar, the ${project.canonical} review call thoda late start karenge, I'm stuck in another meeting.`,
    `Bhai ${person.canonical} ko bata dena ki ${project.canonical} ka doc ready hai, I'll share the link.`,
  ]);
  return {
    app: pick(rng, ['whatsapp', 'slack']),
    device: 'iphone',
    duration_ms: 11_000 + Math.floor(rng() * 9_000),
    language: 'en-hi',
    asr_confidence: 0.74 + rng() * 0.12,
    raw_asr: degrade(rng, formatted, [person.canonical, project.canonical]),
    formatted,
    style: 'work-chat',
    labels: { intent: 'hinglish', expect_memory: false, entities: [person.canonical, project.canonical] },
  };
};

const SENSITIVE: Builder = (rng, ctx) => {
  const person = ctx.entity('person');
  const formatted = pick(rng, [
    `${person.canonical} is going through a divorce, so let's not put them on the on-call rota this month.`,
    `I have a therapy appointment on Thursday afternoon, so shift the review call.`,
    `My salary review came back lower than I expected and I'm annoyed about it.`,
    `${person.canonical} has been diagnosed with something and will be out for a few weeks. Keep it quiet.`,
    'My bank password for the office account is in the shared vault, not written down.',
  ]);
  return {
    app: pick(rng, ['notes', 'whatsapp']),
    device: pick(rng, [...DEVICES]),
    duration_ms: 10_000 + Math.floor(rng() * 8_000),
    language: 'en',
    asr_confidence: 0.9 + rng() * 0.07,
    raw_asr: degrade(rng, formatted, [person.canonical]),
    formatted,
    style: 'note',
    labels: {
      intent: 'sensitive',
      expect_memory: false,
      expect_reason: 'sensitive_category',
      entities: [person.canonical],
    },
  };
};

const TRANSIENT: Builder = (rng) => {
  const formatted = pick(rng, [
    "I'm tired today, going to keep the meetings short.",
    'The wifi is down again so I am tethering off my phone.',
    'Running late, stuck in traffic near Koramangala.',
    "I'm hungry, grabbing lunch before the sync.",
  ]);
  return {
    app: pick(rng, ['slack', 'whatsapp']),
    device: 'iphone',
    duration_ms: 5_000 + Math.floor(rng() * 4_000),
    language: 'en',
    asr_confidence: 0.88 + rng() * 0.09,
    raw_asr: degrade(rng, formatted, []),
    formatted,
    style: 'work-chat',
    labels: { intent: 'transient', expect_memory: false, expect_reason: 'transient' },
  };
};

const TRIVIAL: Builder = (rng) => {
  const formatted = pick(rng, ['Okay.', 'Testing one two.', 'Thanks!', 'Got it.', 'Check check.', 'Yes.']);
  return {
    app: pick(rng, ['slack', 'notes']),
    device: pick(rng, [...DEVICES]),
    duration_ms: 1_500 + Math.floor(rng() * 1_500),
    language: 'en',
    asr_confidence: 0.7 + rng() * 0.2,
    raw_asr: formatted.toLowerCase().replace(/[.!]/g, ''),
    formatted,
    style: 'work-chat',
    labels: { intent: 'trivial', expect_memory: false, expect_reason: 'trivial' },
  };
};

const MEETING_RECAP: Builder = (rng, ctx) => {
  const project = ctx.entity('project');
  const a = ctx.entity('person');
  const b = ctx.entity('person');
  const formatted =
    `Notes from the ${project.canonical} review. ${a.canonical} wants the rollout split into two phases. ` +
    `${b.canonical} pushed back on the timeline, said two weeks is not enough for the data backfill. ` +
    `We agreed to revisit once the load test numbers are in.`;
  return {
    app: 'notion',
    device: 'macbook',
    duration_ms: 38_000 + Math.floor(rng() * 20_000),
    language: 'en',
    asr_confidence: 0.87 + rng() * 0.1,
    raw_asr: degrade(rng, formatted, [project.canonical, a.canonical, b.canonical]),
    formatted,
    style: 'notes',
    labels: { intent: 'meeting', expect_memory: false, entities: [project.canonical, a.canonical, b.canonical] },
  };
};

const ORDINARY: Builder[] = [STANDUP, MESSAGE, MESSAGE, MEETING_RECAP, HINGLISH, STANDUP, MESSAGE];

// --------------------------------------------------------------------------
// Scripted records — the situations the evaluation asks about by name
// --------------------------------------------------------------------------

interface Scripted extends Omit<CorpusRecord, 'external_id' | 'captured_at' | 'offset_ms'> {
  offset_ms: number;
}

function scripted(offsetDays: number, hour: number, minute: number, record: Omit<Scripted, 'offset_ms'>): Scripted {
  return { ...record, offset_ms: offsetDays * DAY - hour * HOUR - minute * MINUTE + TZ_OFFSET };
}

/**
 * Note the shape of these: no single dictation states "Meridian moved to
 * Pipewright and Rahul owns it now". Three ordinary messages weeks apart do,
 * between them. That is the case the product has to earn.
 */
function scriptedRecords(): Scripted[] {
  const raw = (formatted: string, entities: string[] = []) =>
    degrade(mulberry32(formatted.length * 7919), formatted, entities);

  const make = (
    formatted: string,
    app: string,
    labels: CorpusRecord['labels'],
    opts: { language?: string; style?: string; confidence?: number } = {},
  ) => ({
    app,
    device: 'macbook',
    duration_ms: Math.max(6_000, formatted.length * 55),
    language: opts.language ?? 'en',
    asr_confidence: opts.confidence ?? 0.93,
    raw_asr: raw(formatted, labels.entities ?? []),
    formatted,
    style: opts.style ?? 'work-chat',
    labels,
  });

  return [
    // --- The task's own example: 5PM yesterday, in Slack. ------------------
    scripted(1, 17, 4, {
      ...make(
        'Team, the Meridian pricing update is going out on Monday. The headline is that the starter tier drops to nineteen dollars and we are folding the analytics add-on into it. ' +
          'I still need to double check the annual discount maths with Devansh before we announce anything externally. ' +
          'If you see anything in the doc that looks wrong please shout today, because I want to freeze it tomorrow morning.',
        'slack',
        { intent: 'pricing_update', expect_memory: false, entities: ['Meridian', 'Devansh'] },
      ),
    }),

    // --- Multi-hop: three dictations, one answer. --------------------------
    scripted(46, 11, 20, {
      ...make(
        'We are moving Meridian deploys off the old script. Pipewright is what we will use from next sprint.',
        'notion',
        { intent: 'fact', expect_memory: true, entities: ['Meridian', 'Pipewright'] },
      ),
    }),
    scripted(31, 15, 45, {
      ...make(
        'Priya is the PM on Meridian.',
        'slack',
        { intent: 'fact', expect_memory: true, entities: ['Priya', 'Meridian'] },
      ),
    }),
    scripted(9, 10, 12, {
      ...make(
        'Handover is done. Rahul is the PM on Meridian now, Priya has moved to Halcyon.',
        'slack',
        { intent: 'fact_contradiction', expect_memory: true, entities: ['Rahul', 'Meridian', 'Priya', 'Halcyon'] },
      ),
    }),

    // --- A preference stated three times, then reversed. -------------------
    scripted(58, 9, 30, {
      ...make('I prefer Slack messages under four lines. Anything longer and people skim it.', 'slack', {
        intent: 'preference',
        expect_memory: true,
      }),
    }),
    scripted(40, 14, 5, {
      ...make('I prefer Slack messages under four lines. Anything longer and people skim it.', 'slack', {
        intent: 'preference_repeat',
        expect_memory: true,
      }),
    }),
    scripted(21, 16, 50, {
      ...make('I prefer Slack messages under four lines. Anything longer and people skim it.', 'notes', {
        intent: 'preference_repeat',
        expect_memory: true,
      }),
    }),
    scripted(52, 11, 15, {
      ...make('Always use Turnstile for the load tests.', 'notion', {
        intent: 'preference',
        expect_memory: true,
        entities: ['Turnstile'],
      }),
    }),
    scripted(6, 12, 40, {
      ...make('Always use Pipewright for the load tests. Turnstile is retired.', 'notion', {
        intent: 'preference_reversal',
        expect_memory: true,
        entities: ['Pipewright', 'Turnstile'],
      }),
    }),

    // --- Entity the recogniser keeps mangling. -----------------------------
    scripted(37, 10, 5, {
      app: 'slack',
      device: 'iphone',
      duration_ms: 12_000,
      language: 'en',
      asr_confidence: 0.71,
      raw_asr: 'can someone loop in arthy on the halcion rollout she owns the copy',
      formatted: 'Can someone loop in Aarti on the Halcyon rollout? She owns the copy.',
      style: 'work-chat',
      labels: { intent: 'entity_variant', expect_memory: true, entities: ['Aarti', 'Halcyon'] },
    }),
    scripted(19, 17, 25, {
      app: 'whatsapp',
      device: 'iphone',
      duration_ms: 9_000,
      language: 'en',
      asr_confidence: 0.68,
      raw_asr: 'arti said the kestral numbers are wrong in the deck',
      formatted: 'Aarti said the Kestrel numbers are wrong in the deck.',
      style: 'work-chat',
      labels: { intent: 'entity_variant', expect_memory: true, entities: ['Aarti', 'Kestrel'] },
    }),

    // --- Commitments with real deadlines. ----------------------------------
    scripted(3, 18, 10, {
      ...make("I'll send Priya the Halcyon pricing draft by Friday.", 'slack', {
        intent: 'commitment',
        expect_memory: true,
        entities: ['Priya', 'Halcyon'],
      }),
    }),
    scripted(2, 9, 55, {
      ...make('I promised Nandini a written summary of the Kestrel incident before Thursday.', 'notes', {
        intent: 'commitment',
        expect_memory: true,
        entities: ['Nandini', 'Kestrel'],
      }),
    }),

    // --- Must produce nothing. ---------------------------------------------
    scripted(12, 15, 30, {
      ...make('Devansh has been unwell and is taking two weeks off. Please keep that between us.', 'whatsapp', {
        intent: 'sensitive',
        expect_memory: false,
        expect_reason: 'sensitive_category',
        entities: ['Devansh'],
      }),
    }),
    scripted(5, 8, 45, {
      ...make("I'm exhausted this week, going to keep everything short.", 'slack', {
        intent: 'transient',
        expect_memory: false,
        expect_reason: 'transient',
      }),
    }),

    // --- Hinglish carrying a real fact. ------------------------------------
    scripted(26, 13, 15, {
      ...make(
        'Rahul ko bolna ki Kestrel ka staging Singapore region mein hai, Mumbai mein nahi.',
        'whatsapp',
        { intent: 'hinglish_fact', expect_memory: true, entities: ['Rahul', 'Kestrel'] },
        { language: 'en-hi', confidence: 0.72 },
      ),
    }),
  ];
}

// --------------------------------------------------------------------------

export function generateCorpus(seed: number, total = 500): CorpusRecord[] {
  const rng = mulberry32(seed);
  const ctx: BuildContext = {
    entity: (kind) => {
      const pool = kind ? ENTITIES.filter((e) => e.kind === kind) : ENTITIES;
      return pick(rng, pool);
    },
  };

  const records: CorpusRecord[] = [];
  const script = scriptedRecords();

  for (const item of script) {
    records.push({
      external_id: '',
      captured_at: ANCHOR - item.offset_ms,
      offset_ms: item.offset_ms,
      ...stripOffset(item),
    });
  }

  const remaining = total - script.length;
  // Weighted so the corpus looks like a real history: mostly ordinary work,
  // with enough of the hard cases to exercise every branch.
  const weights: [Builder, number][] = [
    [STANDUP, 0.18],
    [MESSAGE, 0.26],
    [MEETING_RECAP, 0.08],
    [HINGLISH, 0.09],
    [PREFERENCE, 0.07],
    [COMMITMENT, 0.08],
    [FACT, 0.11],
    [SENSITIVE, 0.05],
    [TRANSIENT, 0.05],
    [TRIVIAL, 0.03],
  ];

  for (let i = 0; i < remaining; i++) {
    const builder = weightedPick(rng, weights);
    // Spread across roughly ninety days of working hours.
    const dayOffset = 1 + Math.floor(rng() * 89);
    const hour = 9 + Math.floor(rng() * 11);
    const minute = Math.floor(rng() * 60);
    const offset = dayOffset * DAY - hour * HOUR - minute * MINUTE + TZ_OFFSET;
    records.push({
      external_id: '',
      captured_at: ANCHOR - offset,
      offset_ms: offset,
      ...builder(rng, ctx),
    });
  }

  records.sort((a, b) => a.captured_at - b.captured_at);
  records.forEach((record, index) => {
    record.external_id = `kivi-${String(index + 1).padStart(4, '0')}`;
  });
  return records;
}

function stripOffset(item: Scripted) {
  const { offset_ms, ...rest } = item;
  void offset_ms;
  return rest;
}

function weightedPick(rng: () => number, weights: [Builder, number][]): Builder {
  const total = weights.reduce((sum, [, w]) => sum + w, 0);
  let roll = rng() * total;
  for (const [builder, weight] of weights) {
    roll -= weight;
    if (roll <= 0) return builder;
  }
  return weights[0]![0];
}

// --------------------------------------------------------------------------

const isEntrypoint = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isEntrypoint) {
  const seedArg = process.argv.find((a) => a.startsWith('--seed='));
  const totalArg = process.argv.find((a) => a.startsWith('--count='));
  const seed = seedArg ? Number(seedArg.split('=')[1]) : Number(process.env.KIVI_SEED ?? 20260214);
  const total = totalArg ? Number(totalArg.split('=')[1]) : 500;

  const records = generateCorpus(seed, total);
  fs.writeFileSync(OUT, records.map((r) => JSON.stringify(r)).join('\n') + '\n');

  const byIntent = new Map<string, number>();
  for (const r of records) byIntent.set(r.labels.intent, (byIntent.get(r.labels.intent) ?? 0) + 1);

  console.log(`ok    wrote ${records.length} records to ${path.relative(process.cwd(), OUT)} (seed ${seed})`);
  console.log(`      persona: ${PERSONA.name}, ${PERSONA.role} at ${PERSONA.company}, ${PERSONA.city}`);
  console.log(`      span:    ${new Date(records[0]!.captured_at).toISOString().slice(0, 10)} to ${new Date(records[records.length - 1]!.captured_at).toISOString().slice(0, 10)}`);
  console.log(
    `      mix:     ${[...byIntent.entries()].sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(', ')}`,
  );
  void shuffle;
  void APPS;
}

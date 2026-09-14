/**
 * One user, one working life.
 *
 * The corpus is a single person's dictation history because that is what the
 * product sees and what the reviewers' own corpus will contain. A world with
 * real names, projects and habits is what makes multi-dictation recovery,
 * contradiction and abstention testable — a bag of unrelated sentences would
 * make the system look better than it is.
 */

export const PERSONA = {
  userId: 'user_demo',
  name: 'Ananya Rao',
  role: 'product engineer',
  company: 'Loop',
  city: 'Bengaluru',
  timezone: 'Asia/Kolkata',
};

export interface Entity {
  canonical: string;
  /** How a recogniser plausibly mangles it. Drives the lexicon story. */
  variants: string[];
  kind: 'person' | 'project' | 'tool' | 'place';
  note?: string;
}

export const ENTITIES: Entity[] = [
  { canonical: 'Meridian', variants: ['meridien', 'meridian', 'merid ian'], kind: 'project' },
  { canonical: 'Halcyon', variants: ['halcion', 'hal cyon', 'alcyon'], kind: 'project' },
  { canonical: 'Kestrel', variants: ['kestral', 'kestrel', 'castrol'], kind: 'project' },
  { canonical: 'Aarti', variants: ['arthy', 'aarti', 'arti'], kind: 'person' },
  { canonical: 'Devansh', variants: ['devanch', 'devansh', 'the wanch'], kind: 'person' },
  { canonical: 'Priya', variants: ['priya', 'prea', 'pria'], kind: 'person' },
  { canonical: 'Rahul', variants: ['rahul', 'raul', 'ra hool'], kind: 'person' },
  { canonical: 'Nandini', variants: ['nandini', 'nan dini', 'nandani'], kind: 'person' },
  { canonical: 'Turnstile', variants: ['turn style', 'turnstile', 'turn stile'], kind: 'tool' },
  { canonical: 'Pipewright', variants: ['pipe right', 'pipewright', 'pipe write'], kind: 'tool' },
  { canonical: 'Koramangala', variants: ['kora mangala', 'koramangala'], kind: 'place' },
];

export const APPS = [
  'slack',
  'gmail',
  'notion',
  'linear',
  'whatsapp',
  'docs',
  'vscode',
  'notes',
] as const;

export const DEVICES = ['macbook', 'iphone', 'ipad'] as const;

/**
 * A consistent world. Random dictations draw from these rather than pairing
 * names at random, so that the only contradictions in the corpus are the ones
 * scripted on purpose — otherwise supersession fires constantly and the metric
 * measures the generator, not the system.
 */
export const WORLD = {
  designLead: { Meridian: 'Aarti', Halcyon: 'Nandini', Kestrel: 'Devansh' } as Record<string, string>,
  region: { Meridian: 'Singapore', Halcyon: 'Frankfurt', Kestrel: 'Singapore' } as Record<string, string>,
  launch: { Meridian: 'the fourteenth of next month', Halcyon: 'the second of December', Kestrel: 'the ninth of January' } as Record<string, string>,
  manager: 'Nandini',
  deployTool: 'Pipewright',
};

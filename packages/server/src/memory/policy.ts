import type { MemoryCandidate, Rejection } from '../types.ts';
import { normalise } from '../util/text.ts';

/**
 * The ignore policy.
 *
 * This gate sits between *any* extractor and the database. It is code, not
 * prompt text, on purpose: a model that is asked nicely not to record someone's
 * health will eventually do it anyway. Enforcing the boundary here means the
 * guarantee survives a prompt change, a provider swap, or a jailbroken input.
 *
 * Every rejection is persisted with its reason, so "Kivi ignored the right
 * things" is a query, not a claim.
 */

interface SensitiveCategory {
  code: string;
  label: string;
  patterns: RegExp[];
}

const SENSITIVE: SensitiveCategory[] = [
  {
    code: 'health',
    label: 'health and medical information',
    patterns: [
      /\b(diagnos(is|ed)|therapy|therapist|depress(ed|ion)|anxiety|adhd|bipolar|cancer|diabet(es|ic)|blood pressure|migraine|medication|antidepressant|prescription|surgery|hospital|mri|chemo|panic attack|burn(t|ed) out)\b/i,
      /\b(mental health|sick leave|medical leave|unwell|off sick|on sick leave|recovering from|in hospital)\b/i,
    ],
  },
  {
    code: 'politics',
    label: 'political opinion or affiliation',
    patterns: [/\b(voted? for|political party|bjp|congress party|left wing|right wing|election campaign|ideolog(y|ical))\b/i],
  },
  {
    code: 'religion',
    label: 'religious belief or practice',
    patterns: [/\b(pray(er|ing)?|temple|church|mosque|gurudwara|fasting for|religio(n|us)|god willing|namaz|puja)\b/i],
  },
  {
    code: 'sexuality',
    label: 'sexual orientation or intimate relationships',
    patterns: [/\b(girlfriend|boyfriend|dating|tinder|hookup|sexual|divorce|affair|marriage counsell?ing)\b/i],
  },
  {
    code: 'personal_finance',
    label: 'personal financial position',
    patterns: [
      /\b(salary|ctc|my (bank|savings|loan|emi|debt|rent)|credit score|in debt|can'?t afford|overdraft|appraisal (number|amount)|stock options|esops? worth)\b/i,
    ],
  },
  {
    code: 'third_party_sensitive',
    label: "another person's private circumstances",
    patterns: [
      /\b(\w+)'s (divorce|illness|salary|performance issue|pip|termination|resignation letter|mental health)\b/i,
      /\b(is|are) (being )?(fired|let go|laid off|put on a pip)\b/i,
      /\b(pregnan(t|cy)|miscarriage|funeral|passed away)\b/i,
    ],
  },
  {
    code: 'credentials',
    label: 'credentials or secrets',
    patterns: [/\b(password|passcode|otp|api key|secret key|token is|pin is|aadhaar|pan number|cvv)\b/i],
  },
];

/** States that are true for an hour and misleading for a month. */
const TRANSIENT = [
  /\b(i'?m|i am|feeling|feel)\s+(tired|sleepy|hungry|annoyed|frustrated|excited|stressed|bored|angry|sad|happy)\b/i,
  /\b(running late|stuck in traffic|on the metro|in an uber|about to board|just woke up|grabbing (lunch|coffee))\b/i,
  /\b(the (wifi|internet|vpn|laptop|battery)) (is|was) (down|slow|dying|acting up)\b/i,
  // A request to keep one thing quiet is about that conversation, not a
  // standing instruction about how this person likes to work.
  /\bkeep (?:this|that|it) (?:between us|quiet|to yourself)\b/i,
  /\bdon'?t (?:tell|mention (?:it|this) to) anyone\b/i,
];

/** True but worthless: recording it costs trust and buys nothing. */
const TRIVIAL = [
  /^(ok(ay)?|yeah|yes|no|thanks|thank you|cool|got it|sure|hmm+|right)[.!]?$/i,
  /^(testing|test one two|check check|hello|hi)[.!]?$/i,
];

const MIN_CONFIDENCE: Record<MemoryCandidate['type'], number> = {
  entity: 0.45,
  fact: 0.5,
  preference: 0.55,
  commitment: 0.55,
};

/** Below this a memory is stored but held for confirmation instead of used. */
export const CONFIRMATION_THRESHOLD = 0.68;

export interface PolicyDecision {
  allowed: boolean;
  rejection?: Rejection;
  /** Set when the candidate is kept but must be confirmed before it is trusted. */
  needsConfirmation?: boolean;
}

export function screenText(text: string): { code: string; label: string } | null {
  for (const category of SENSITIVE) {
    for (const pattern of category.patterns) {
      if (pattern.test(text)) return { code: category.code, label: category.label };
    }
  }
  return null;
}

export function evaluateCandidate(
  candidate: MemoryCandidate,
  context: { tombstoned: (c: MemoryCandidate) => boolean },
): PolicyDecision {
  const subject = candidate.statement.trim();

  if (!subject || subject.length < 4) {
    return reject(candidate, 'malformed', 'Candidate statement was empty or too short to mean anything.');
  }

  const sensitive = screenText(`${candidate.subject} ${candidate.statement} ${candidate.quote}`);
  if (sensitive) {
    return reject(
      candidate,
      'sensitive_category',
      `Kivi does not retain ${sensitive.label}. Detected category: ${sensitive.code}.`,
    );
  }

  for (const pattern of TRIVIAL) {
    if (pattern.test(subject)) {
      return reject(candidate, 'trivial', 'Acknowledgement or filler with no durable content.');
    }
  }

  for (const pattern of TRANSIENT) {
    if (pattern.test(subject) || pattern.test(candidate.quote)) {
      return reject(
        candidate,
        'transient',
        'Describes a passing state. True now, misleading in a week, so it is not kept.',
      );
    }
  }

  if (context.tombstoned(candidate)) {
    return reject(
      candidate,
      'tombstoned',
      'The person deleted this memory before. Re-observing it does not bring it back.',
    );
  }

  // A promise with nobody to keep it to and no date is not a commitment, it is
  // a turn of phrase. "I'll share the link" said eighteen times across eighteen
  // different conversations is one memory that means nothing.
  if (candidate.type === 'commitment') {
    const hasDeadline = Boolean(candidate.detail?.due_at ?? candidate.detail?.due_at_text);
    const namesSomeone = /\b[A-Z][a-z]{2,}\b/.test(candidate.statement.replace(/^\w+\s/, ''));
    if (!hasDeadline && !namesSomeone) {
      return reject(
        candidate,
        'trivial',
        'A commitment with no deadline and nobody named is a turn of phrase, not something to hold you to.',
      );
    }
  }

  const floor = MIN_CONFIDENCE[candidate.type];
  if (candidate.confidence < floor) {
    return reject(
      candidate,
      'low_confidence',
      `Extraction confidence ${candidate.confidence.toFixed(2)} is below the ${candidate.type} floor of ${floor}.`,
    );
  }

  return {
    allowed: true,
    needsConfirmation: candidate.confidence < CONFIRMATION_THRESHOLD,
  };
}

function reject(
  candidate: MemoryCandidate,
  code: Rejection['reason_code'],
  reason: string,
): PolicyDecision {
  return {
    allowed: false,
    rejection: {
      candidate_type: candidate.type,
      candidate: candidate.statement,
      reason_code: code,
      reason,
    },
  };
}

/**
 * Applied to whole dictations before extraction runs. A dictation about a
 * colleague's health should not even be summarised into an episode in detail.
 */
export function screenDictation(text: string): { code: string; label: string } | null {
  return screenText(text);
}

export function normalisedSubject(candidate: MemoryCandidate): string {
  return normalise(candidate.subject);
}

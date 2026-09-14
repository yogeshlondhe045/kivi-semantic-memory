import { all, one } from '../db/index.ts';
import type { ToolSpec } from '../llm/types.ts';
import { forgetMemory } from '../memory/store.ts';
import { planQuery, describePlan } from '../retrieval/plan.ts';
import { queryCoverage } from '../retrieval/coverage.ts';
import { assessGrounding, GROUNDING_FLOOR, searchDictations, searchEpisodes, searchMemories } from '../retrieval/search.ts';
import type { Citation, QueryPlan, RetrievedItem } from '../types.ts';
import { formatTimestamp } from '../util/time.ts';
import { rewriteWithMemory } from './rewrite.ts';

/**
 * Five tools. Not four, not twenty.
 *
 * Each one exists because a capability in the product position needs it, and
 * each one returns its evidence rather than a conclusion — so that the same
 * payload can be turned into prose by a model or by the offline provider, and
 * so that abstention is a property of the data rather than of the wording.
 */

export const TOOL_SPECS: ToolSpec[] = [
  {
    name: 'recall',
    description:
      "Answer a question about the person's own history using what Kivi has learned from their dictations. " +
      'Use this for anything that starts "what", "who", "when", "how do I", "did I". ' +
      'Returns the memories and past dictations that support an answer, or nothing at all when the history does not contain one.',
    input_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        question: { type: 'string', description: "The person's question, in their own words." },
      },
      required: ['question'],
    },
  },
  {
    name: 'search_dictations',
    description:
      'Find specific past dictations by topic, app and time. Use when the person refers to a particular recording ' +
      '("the one I did around 5PM yesterday in Slack"). Returns matching dictations with their text and timestamps.',
    input_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        query: { type: 'string', description: 'What the dictation was about, plus any time or app the person mentioned.' },
        limit: { type: 'number', description: 'How many to return. Default 5.' },
      },
      required: ['query'],
    },
  },
  {
    name: 'rewrite_dictation',
    description:
      'Rewrite a past dictation for a new purpose, applying what Kivi knows about how this person writes. ' +
      'Pass dictation_id when a previous search identified one; otherwise pass a query and the most likely dictation is used.',
    input_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        dictation_id: { type: 'string', description: 'Id from search_dictations, or empty to search.' },
        query: { type: 'string', description: 'Used to locate the dictation when no id is given.' },
        instruction: { type: 'string', description: 'What the rewrite is for, in the person\'s words.' },
      },
      required: ['instruction'],
    },
  },
  {
    name: 'list_commitments',
    description:
      'List things the person said out loud that they would do. Use for "what did I promise", "what do I owe anyone", ' +
      '"what am I supposed to follow up on".',
    input_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        window: {
          type: 'string',
          enum: ['open', 'overdue', 'all'],
          description: 'open = still live, overdue = past its deadline, all = including expired.',
        },
        about: {
          type: 'string',
          description: 'Narrow to commitments involving a person or project, e.g. "Priya". Empty for all.',
        },
      },
      required: ['window', 'about'],
    },
  },
  {
    name: 'update_memory',
    description:
      'Correct or delete something Kivi believes, when the person says it is wrong or asks Kivi to forget it. ' +
      'Deleting is permanent and leaves a tombstone so the same memory is not relearned.',
    input_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        instruction: { type: 'string', description: 'What the person said, verbatim.' },
        memory_id: { type: 'string', description: 'Id to act on when already known, otherwise empty.' },
      },
      required: ['instruction'],
    },
  },
];

export interface ToolContext {
  userId: string;
  now: number;
  /** Filled in by the executor so the agent can cite what it used. */
  citations: Citation[];
  plans: QueryPlan[];
  retrieved: RetrievedItem[];
  notes: string[];
}

export interface ToolPayload {
  tool: string;
  grounded: boolean;
  lead?: string;
  empty_message?: string;
  draft?: string;
  items?: { text: string; when?: string; id?: string }[];
  [key: string]: unknown;
}

export async function executeTool(
  name: string,
  input: Record<string, unknown>,
  ctx: ToolContext,
): Promise<ToolPayload> {
  switch (name) {
    case 'recall':
      return recall(String(input.question ?? ''), ctx);
    case 'search_dictations':
      return findDictations(String(input.query ?? ''), Number(input.limit ?? 5), ctx);
    case 'rewrite_dictation':
      return rewrite(input, ctx);
    case 'list_commitments':
      return commitments(String(input.window ?? 'open'), String(input.about ?? ''), ctx);
    case 'update_memory':
      return updateMemory(String(input.instruction ?? ''), String(input.memory_id ?? ''), ctx);
    default:
      return { tool: name, grounded: false, empty_message: `Kivi has no tool called ${name}.` };
  }
}

// --------------------------------------------------------------------------

function recall(question: string, ctx: ToolContext): ToolPayload {
  const plan = planQuery(question, ctx.now);
  ctx.plans.push(plan);

  const memories = searchMemories(ctx.userId, plan, { limit: 8, now: ctx.now });
  const episodes = searchEpisodes(ctx.userId, plan, { limit: 6, now: ctx.now });

  // Entity memories are spelling knowledge, not answers. They belong in the
  // lexicon and in rewriting; surfacing '"Meridian" is a name you use' as a
  // reply to "who runs Meridian" is noise dressed up as recall. They stay in
  // unless the question is actually about a name.
  const asksAboutNames = plan.memory_types.includes('entity');
  const usable = asksAboutNames ? memories : memories.filter((m) => m.meta.type !== 'entity');

  const combined = [...usable, ...episodes].sort((a, b) => b.score - a.score);
  ctx.retrieved.push(...combined);

  const verdict = assessGrounding(ctx.userId, plan, combined);
  if (!verdict.grounded) {
    ctx.notes.push(`recall abstained — ${verdict.reason}`);
    return {
      tool: 'recall',
      grounded: false,
      plan: summarisePlan(plan),
      grounding: verdict,
      considered: combined.slice(0, 5).map(brief),
      empty_message:
        "I don't have anything in your history that answers that. I'd rather tell you that than guess.",
    };
  }

  // Rank what gets cited by how much of the question each source actually
  // contains, not by how it fused. The first line of an answer should be the
  // line that answers it.
  const kept = combined
    .filter((item) => item.score >= GROUNDING_FLOOR)
    .map((item) => ({ item, coverage: queryCoverage(ctx.userId, plan, [item]).score }))
    .sort((a, b) => b.coverage - a.coverage || b.item.score - a.item.score)
    .slice(0, 5)
    .map((entry) => entry.item);

  const items: { text: string; when?: string; id?: string }[] = [];

  for (const item of kept) {
    if (item.kind === 'memory') {
      const evidence = evidenceFor(item.id);
      ctx.citations.push({
        kind: 'memory',
        id: item.id,
        label: item.text,
        quote: evidence?.quote,
        captured_at: evidence?.captured_at,
        app: evidence?.app,
      });
      items.push({
        text: item.text,
        when: evidence ? `first heard ${formatTimestamp(evidence.captured_at)}` : undefined,
        id: item.id,
      });
    } else {
      const dictationId = String(item.meta.dictation_id);
      ctx.citations.push({
        kind: 'dictation',
        id: dictationId,
        label: item.text,
        captured_at: Number(item.meta.occurred_at),
        app: String(item.meta.app),
      });
      items.push({
        text: item.text,
        when: `${formatTimestamp(Number(item.meta.occurred_at))} in ${item.meta.app}`,
        id: dictationId,
      });
    }
  }

  return {
    tool: 'recall',
    grounded: true,
    lead: describePlan(plan),
    items,
    plan: summarisePlan(plan),
    grounding: verdict,
    considered: combined.slice(0, 8).map(brief),
  };
}

function findDictations(query: string, limit: number, ctx: ToolContext): ToolPayload {
  const plan = planQuery(query, ctx.now);
  ctx.plans.push(plan);

  // Try the time window as a hard filter first; fall back to preferring it, so
  // "around 5PM" does not silently return nothing when the memory was at 4:40.
  let results = searchDictations(ctx.userId, plan, { limit, strictTime: true, now: ctx.now });
  let relaxed = false;
  if (results.length === 0 && plan.time_range.from !== null) {
    results = searchDictations(ctx.userId, plan, { limit, strictTime: false, now: ctx.now });
    relaxed = true;
    ctx.notes.push('No dictation inside the stated window; widened to a preference rather than a filter.');
  }
  ctx.retrieved.push(...results);

  if (results.length === 0) {
    return {
      tool: 'search_dictations',
      grounded: false,
      plan: summarisePlan(plan),
      empty_message: `I can't find a dictation matching that${plan.time_range.label ? ` ${plan.time_range.label}` : ''}.`,
    };
  }

  for (const item of results.slice(0, 3)) {
    ctx.citations.push({
      kind: 'dictation',
      id: item.id,
      label: item.text.slice(0, 120),
      captured_at: Number(item.meta.captured_at),
      app: String(item.meta.app),
    });
  }

  return {
    tool: 'search_dictations',
    grounded: true,
    lead: relaxed
      ? `Nothing exactly ${plan.time_range.label ?? 'then'}, but this is the closest:`
      : describePlan(plan),
    items: results.map((item) => ({
      id: item.id,
      text: item.text,
      when: `${formatTimestamp(Number(item.meta.captured_at))} in ${item.meta.app}`,
    })),
    plan: summarisePlan(plan),
    considered: results.map(brief),
  };
}

function rewrite(input: Record<string, unknown>, ctx: ToolContext): ToolPayload {
  const instruction = String(input.instruction ?? '');
  let dictationId = String(input.dictation_id ?? '');

  if (!dictationId) {
    const plan = planQuery(String(input.query ?? '') || locatorClause(instruction), ctx.now);
    ctx.plans.push(plan);
    // "polish the one from 5PM yesterday" is a request about a specific
    // recording. Honour the window first; only widen if it is genuinely empty.
    let found = searchDictations(ctx.userId, plan, { limit: 1, strictTime: true, now: ctx.now });
    if (found.length === 0) {
      found = searchDictations(ctx.userId, plan, { limit: 1, strictTime: false, now: ctx.now });
      if (found.length > 0) ctx.notes.push('Nothing inside the stated window; used the closest match instead.');
    }
    ctx.retrieved.push(...found);
    if (found.length === 0) {
      return {
        tool: 'rewrite_dictation',
        grounded: false,
        empty_message: "I couldn't find the dictation you mean. Tell me roughly when it was, or which app.",
      };
    }
    dictationId = found[0]!.id;
  }

  const dictation = one<{ id: string; formatted: string; captured_at: number; app: string }>(
    'SELECT id, formatted, captured_at, app FROM dictations WHERE id = ? AND user_id = ?',
    [dictationId, ctx.userId],
  );
  if (!dictation) {
    return { tool: 'rewrite_dictation', grounded: false, empty_message: 'That dictation is no longer in your history.' };
  }

  const result = rewriteWithMemory(ctx.userId, dictation.formatted, instruction);
  const appliedRules = result.rules.filter((r) => r.applied);

  ctx.citations.push({
    kind: 'dictation',
    id: dictation.id,
    label: dictation.formatted.slice(0, 120),
    captured_at: dictation.captured_at,
    app: dictation.app,
  });
  for (const rule of appliedRules) {
    ctx.citations.push({ kind: 'memory', id: rule.memory_id, label: rule.statement });
  }

  return {
    tool: 'rewrite_dictation',
    grounded: true,
    considered: ctx.retrieved.slice(-6).map(brief),
    draft: result.draft,
    source_dictation: {
      id: dictation.id,
      text: dictation.formatted,
      when: `${formatTimestamp(dictation.captured_at)} in ${dictation.app}`,
    },
    applied_preferences: appliedRules.map((r) => ({ memory_id: r.memory_id, statement: r.statement, effect: r.effect })),
    considered_preferences: result.rules.filter((r) => !r.applied).map((r) => r.statement),
    lexicon_fixes: result.lexicon_fixes,
    lead: appliedRules.length
      ? `Rewritten using ${appliedRules.length} thing${appliedRules.length === 1 ? '' : 's'} Kivi knows about how you write.`
      : 'Rewritten. Nothing Kivi remembers about your style applied here.',
  };
}

function commitments(window: string, about: string, ctx: ToolContext): ToolPayload {
  const rows = all<{
    id: string;
    statement: string;
    detail: string;
    confidence: number;
    first_seen_at: number;
    expires_at: number | null;
  }>(
    `SELECT id, statement, detail, confidence, first_seen_at, expires_at
       FROM memories
      WHERE user_id = ? AND type = 'commitment' AND status = 'active'
      ORDER BY first_seen_at DESC`,
    [ctx.userId],
  );

  const enriched = rows.map((row) => {
    const detail = JSON.parse(row.detail || '{}') as { due_at?: number | null };
    return { ...row, due_at: detail.due_at ?? null };
  });

  // "What did I promise Priya" is a commitments question with a filter in it.
  // The names in the question are matched against the commitment text rather
  // than routed through retrieval, because a promise either names someone or
  // it does not.
  const focus = about ? planQuery(about, ctx.now).entities.map((e) => e.toLowerCase()) : [];

  const live = enriched.filter((row) => {
    if (focus.length && !focus.some((name) => row.statement.toLowerCase().includes(name))) return false;
    if (window === 'all') return true;
    const expired = row.expires_at !== null && row.expires_at < ctx.now;
    if (window === 'overdue') return row.due_at !== null && row.due_at < ctx.now && !expired;
    return !expired;
  });

  if (live.length === 0) {
    return {
      tool: 'list_commitments',
      grounded: false,
      empty_message: focus.length
        ? `Nothing you said out loud mentions ${focus.join(' or ')} as something you owe them.`
        : window === 'overdue'
          ? 'Nothing you said you would do is past its deadline.'
          : "You haven't said anything out loud that Kivi is tracking as a commitment.",
    };
  }

  for (const row of live.slice(0, 6)) {
    const evidence = evidenceFor(row.id);
    ctx.citations.push({
      kind: 'memory',
      id: row.id,
      label: row.statement,
      quote: evidence?.quote,
      captured_at: evidence?.captured_at,
      app: evidence?.app,
    });
  }

  return {
    tool: 'list_commitments',
    grounded: true,
    lead: `${live.length} commitment${live.length === 1 ? '' : 's'}${focus.length ? ` involving ${focus.join(' or ')}` : ''} from what you have said:`,
    items: live.map((row) => ({
      id: row.id,
      text: row.statement,
      when: row.due_at ? `due ${formatTimestamp(row.due_at)}` : `said ${formatTimestamp(row.first_seen_at)}`,
    })),
  };
}

function updateMemory(instruction: string, memoryId: string, ctx: ToolContext): ToolPayload {
  let target = memoryId
    ? one<{ id: string; statement: string }>('SELECT id, statement FROM memories WHERE id = ? AND user_id = ?', [
        memoryId,
        ctx.userId,
      ])
    : undefined;

  if (!target) {
    const plan = planQuery(instruction, ctx.now);
    ctx.plans.push(plan);
    const found = searchMemories(ctx.userId, plan, { limit: 3, now: ctx.now });
    ctx.retrieved.push(...found);
    if (found.length === 0 || !assessGrounding(ctx.userId, plan, found).grounded) {
      return {
        tool: 'update_memory',
        grounded: false,
        empty_message: "I'm not sure which memory you mean. Open Memory and point at it, and I'll remove it.",
      };
    }
    target = { id: found[0]!.id, statement: found[0]!.text };
  }

  const removed = forgetMemory(ctx.userId, target.id, 'user_forget_via_hey_kivi');
  if (!removed) {
    return { tool: 'update_memory', grounded: false, empty_message: 'That memory is already gone.' };
  }

  ctx.notes.push(`Forgot memory ${target.id} and wrote a tombstone so it is not relearned.`);
  return {
    tool: 'update_memory',
    grounded: true,
    lead: `Forgotten: "${target.statement}". It won't come back, even if you say something similar again.`,
    items: [],
    forgot: target.id,
  };
}

/**
 * "Find the dictation I did around 5PM yesterday in Slack and polish it for the
 * meeting I'm walking into" is two requests. Only the first half describes the
 * recording; the second half describes what to do with it, and letting its
 * words into the search is how you end up polishing a note about being tired
 * because it happened to contain the word "meeting".
 */
function locatorClause(instruction: string): string {
  const cut = instruction.search(/\s+(and|then|so that|so i can|to)\s+(polish|rewrite|clean|tidy|make|turn|send|use|shorten|redraft)\b/i);
  const head = cut > 0 ? instruction.slice(0, cut) : instruction;
  return head.replace(/^\s*(hey kivi[,\s]*)?/i, '').trim();
}

// --------------------------------------------------------------------------

function evidenceFor(memoryId: string) {
  return one<{ quote: string; captured_at: number; app: string }>(
    `SELECT e.quote, d.captured_at, d.app
       FROM memory_evidence e
       JOIN dictations d ON d.id = e.dictation_id
      WHERE e.memory_id = ?
      ORDER BY d.captured_at ASC
      LIMIT 1`,
    [memoryId],
  );
}

function brief(item: RetrievedItem) {
  return {
    kind: item.kind,
    id: item.id,
    text: item.text.slice(0, 120),
    score: Number(item.score.toFixed(6)),
    signals: item.signals,
  };
}

function summarisePlan(plan: QueryPlan) {
  return {
    intent: plan.intent,
    keywords: plan.keywords,
    entities: plan.entities,
    apps: plan.apps,
    window: plan.time_range.label,
    from: plan.time_range.from,
    to: plan.time_range.to,
    memory_types: plan.memory_types,
  };
}

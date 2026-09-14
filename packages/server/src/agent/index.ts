import { all, one, run } from '../db/index.ts';
import { llm, providerLabel } from '../llm/index.ts';
import type { ContentBlock, LlmMessage } from '../llm/types.ts';
import { Tracer } from '../trace/index.ts';
import type { Citation, QueryPlan, RetrievedItem } from '../types.ts';
import { id } from '../util/ids.ts';
import { formatTimestamp } from '../util/time.ts';
import { screenText } from '../memory/policy.ts';
import { executeTool, TOOL_SPECS, type ToolContext } from './tools.ts';

const MAX_STEPS = 4;

export type TurnOutcome = 'answered' | 'abstained' | 'acted' | 'clarified';

export interface AskResult {
  conversation_id: string;
  turn_id: string;
  text: string;
  outcome: TurnOutcome;
  citations: Citation[];
  trace_id: string;
  duration_ms: number;
  provider: string;
  tools_used: string[];
}

/**
 * Hey Kivi.
 *
 * The loop is deliberately short. Memory reaches the model in two ways and no
 * others: a small standing brief of active preferences (the things that shape
 * *how* Kivi should write for this person), and tool results (the things that
 * answer *what* they asked). Nothing else from the database is ever pasted into
 * the prompt — the person's whole history is not context, it is a searchable
 * store, and the difference is what keeps the answer citable.
 */
export async function ask(
  userId: string,
  question: string,
  options: { conversationId?: string; now?: number } = {},
): Promise<AskResult> {
  const now = options.now ?? Date.now();
  const conversationId = options.conversationId ?? startConversation(userId, question);
  const tracer = new Tracer('query', userId);

  const ctx: ToolContext = { userId, now, citations: [], plans: [], retrieved: [], notes: [] };
  const provider = llm();

  // A question inside a screened category is answered before any retrieval
  // happens. Kivi never formed a memory from that material, so searching would
  // only ever surface the person's own raw words back at them under the guise
  // of an answer — and the honest reply is about the policy, not the data.
  const screened = screenText(question);
  if (screened) {
    tracer.set('question', question);
    tracer.set('screened', screened);
    tracer.set('outcome', 'abstained');
    tracer.set(
      'memory_influence',
      `No retrieval ran. The question is about ${screened.label}, which Kivi never records, so there is nothing to retrieve.`,
    );
    tracer.stage('policy_screen', { category: screened.code });

    const text = `Kivi doesn't keep anything about ${screened.label}, so I have nothing to answer from — even if you've dictated about it. You can still find the recording itself under Dictations.`;
    insertTurn(conversationId, userId, 'user', question, null, [], null);
    const refusedTurnId = insertTurn(conversationId, userId, 'assistant', text, 'abstained', [], tracer.id);
    const refusedTraceId = tracer.save(refusedTurnId);
    run('UPDATE conversations SET updated_at = ? WHERE id = ?', [Date.now(), conversationId]);

    return {
      conversation_id: conversationId,
      turn_id: refusedTurnId,
      text,
      outcome: 'abstained',
      citations: [],
      trace_id: refusedTraceId,
      duration_ms: tracer.elapsedMs,
      provider: providerLabel(),
      tools_used: [],
    };
  }

  const system = buildSystemPrompt(userId, now);
  tracer.set('system_prompt_bytes', system.length);

  const messages: LlmMessage[] = [
    ...historyFor(conversationId),
    { role: 'user', content: [{ type: 'text', text: question }] },
  ];

  const toolsUsed: string[] = [];
  let finalText = '';
  let grounded = false;
  let acted = false;

  for (let step = 0; step < MAX_STEPS; step++) {
    const response = await tracer.timed(
      `model_step_${step + 1}`,
      () => provider.complete({ system, messages, tools: TOOL_SPECS, maxTokens: 2048 }),
      () => ({ provider: provider.name }),
    );
    tracer.addUsage(response.usage);

    const text = response.blocks
      .filter((b): b is Extract<ContentBlock, { type: 'text' }> => b.type === 'text')
      .map((b) => b.text)
      .join('\n')
      .trim();
    if (text) finalText = text;

    const toolCalls = response.blocks.filter(
      (b): b is Extract<ContentBlock, { type: 'tool_use' }> => b.type === 'tool_use',
    );

    if (toolCalls.length === 0) break;

    messages.push({ role: 'assistant', content: response.blocks });
    const results: ContentBlock[] = [];

    for (const call of toolCalls) {
      const startedAt = Date.now();
      const payload = await executeTool(call.name, call.input, ctx);
      tracer.stage(call.name, { grounded: payload.grounded });
      toolsUsed.push(call.name);
      if (payload.grounded) grounded = true;
      if (call.name === 'update_memory' && payload.grounded) acted = true;

      tracer.push('tool_calls', {
        name: call.name,
        input: call.input,
        ms: Date.now() - startedAt,
        grounded: payload.grounded,
        plan: payload.plan ?? null,
        considered: payload.considered ?? null,
        returned: payload.items?.length ?? (payload.draft ? 1 : 0),
      });

      results.push({ type: 'tool_result', tool_use_id: call.id, content: JSON.stringify(payload) });
    }

    messages.push({ role: 'user', content: results });
  }

  if (!finalText) {
    finalText = "I couldn't work that one out. Try asking it a different way.";
  }

  const outcome: TurnOutcome = acted ? 'acted' : grounded ? 'answered' : 'abstained';
  const citations = dedupeCitations(ctx.citations);

  tracer.set('plans', ctx.plans.map(compactPlan));
  tracer.set('retrieved_count', ctx.retrieved.length);
  tracer.set('citations', citations);
  tracer.set('outcome', outcome);
  tracer.set('notes', ctx.notes);
  tracer.set('question', question);
  tracer.set('answer', finalText);
  tracer.set(
    'memory_influence',
    grounded
      ? 'Memory supplied the answer; the cited rows are what it used.'
      : 'Memory was searched and produced nothing above the grounding floor, so Kivi abstained.',
  );

  const userTurnId = insertTurn(conversationId, userId, 'user', question, null, [], null);
  const turnId = insertTurn(conversationId, userId, 'assistant', finalText, outcome, citations, tracer.id);
  const traceId = tracer.save(turnId);
  run('UPDATE conversations SET updated_at = ? WHERE id = ?', [Date.now(), conversationId]);
  void userTurnId;

  return {
    conversation_id: conversationId,
    turn_id: turnId,
    text: finalText,
    outcome,
    citations,
    trace_id: traceId,
    duration_ms: tracer.elapsedMs,
    provider: providerLabel(),
    tools_used: [...new Set(toolsUsed)],
  };
}

// --------------------------------------------------------------------------

function buildSystemPrompt(userId: string, now: number): string {
  const user = one<{ display_name: string; timezone: string }>(
    'SELECT display_name, timezone FROM users WHERE id = ?',
    [userId],
  );

  const preferences = all<{ statement: string; confidence: number }>(
    `SELECT statement, confidence FROM memories
      WHERE user_id = ? AND type = 'preference' AND status = 'active' AND confidence >= 0.68
      ORDER BY confidence DESC LIMIT 8`,
    [userId],
  );

  const brief = preferences.length
    ? preferences.map((p) => `- ${p.statement}`).join('\n')
    : '- Nothing yet. Do not invent a house style.';

  return `You are Hey Kivi, the conversational side of a voice-first writing tool. The person talks; you help.

Right now it is ${formatTimestamp(now)} for ${user?.display_name ?? 'this person'} (${user?.timezone ?? 'Asia/Kolkata'}).

How this person likes things written, learned from what they have actually said:
${brief}

Your memory of them is a ledger of things they said, not a profile of who they are. Work by these rules:

1. Never answer from your own knowledge about this person. If it is not in a tool result, you do not know it.
2. Call exactly one tool first. Answer from what it returns.
3. When a tool returns grounded: false, say plainly that their history does not contain the answer. Do not soften it into a guess, and do not offer a plausible-sounding possibility.
4. Refer to sources the way a person would — "you said this on Tuesday in Slack" — not by id.
5. Be brief. Two or three sentences unless they asked for written output.
6. Never speculate about their mood, health, relationships, finances or beliefs, even if a transcript hints at it.

Tools: recall (questions about their history), search_dictations (find a specific recording), rewrite_dictation (reuse past text), list_commitments (what they said they would do), update_memory (correct or forget something).`;
}

function startConversation(userId: string, firstMessage: string): string {
  const conversationId = id('conv');
  const now = Date.now();
  run('INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)', [
    conversationId,
    userId,
    firstMessage.slice(0, 60),
    now,
    now,
  ]);
  return conversationId;
}

function insertTurn(
  conversationId: string,
  userId: string,
  role: 'user' | 'assistant',
  content: string,
  outcome: string | null,
  citations: Citation[],
  traceId: string | null,
): string {
  const turnId = id('turn');
  run(
    `INSERT INTO turns (id, conversation_id, user_id, role, content, outcome, citations, trace_id, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [turnId, conversationId, userId, role, content, outcome, JSON.stringify(citations), traceId, Date.now()],
  );
  return turnId;
}

function historyFor(conversationId: string): LlmMessage[] {
  const rows = all<{ role: 'user' | 'assistant'; content: string }>(
    'SELECT role, content FROM turns WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 6',
    [conversationId],
  ).reverse();
  return rows.map((row) => ({ role: row.role, content: [{ type: 'text' as const, text: row.content }] }));
}

function dedupeCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  const out: Citation[] = [];
  for (const citation of citations) {
    const key = `${citation.kind}:${citation.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(citation);
  }
  return out.slice(0, 8);
}

function compactPlan(plan: QueryPlan) {
  return {
    intent: plan.intent,
    keywords: plan.keywords.slice(0, 8),
    entities: plan.entities,
    apps: plan.apps,
    window: plan.time_range.label,
  };
}

export type { RetrievedItem };

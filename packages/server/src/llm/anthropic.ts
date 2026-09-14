import Anthropic from '@anthropic-ai/sdk';
import { config, log } from '../config.ts';
import type { ExtractionResult, MemoryCandidate, Usage } from '../types.ts';
import { costUsd } from './pricing.ts';
import type {
  CompletionRequest,
  CompletionResponse,
  ContentBlock,
  ExtractionInput,
  LlmProvider,
  ToolSpec,
} from './types.ts';

/**
 * The model-backed provider.
 *
 * Two jobs, two models: a cheap model reads 500 dictations at ingest time, the
 * main model runs the Hey Kivi conversation. Both are overridable by env.
 *
 * Note what is *not* here: the ignore policy. The extraction prompt asks the
 * model to skip sensitive material, but the guarantee is enforced afterwards in
 * `memory/policy.ts`, over whatever the model returns. A prompt is a request; a
 * gate is a rule.
 */

const EXTRACTION_TOOL: ToolSpec & { strict?: boolean } = {
  name: 'record_understanding',
  description:
    'Record what is worth remembering from one dictation. Call this exactly once. ' +
    'Return an empty candidates array when the dictation contains nothing durable.',
  strict: true,
  input_schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      summary: {
        type: 'string',
        description: 'One or two sentences describing what the person did or said. Plain, factual.',
      },
      topics: {
        type: 'array',
        items: { type: 'string' },
        description: 'Up to six short topic keywords.',
      },
      entities: {
        type: 'array',
        items: { type: 'string' },
        description: 'Proper nouns mentioned: people, projects, products, tools.',
      },
      salience: {
        type: 'number',
        description: '0 to 1. How likely this dictation is to be worth recalling later.',
      },
      candidates: {
        type: 'array',
        description: 'Durable things learned. Empty is a valid and common answer.',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            type: { type: 'string', enum: ['entity', 'fact', 'preference', 'commitment'] },
            subject: { type: 'string', description: 'What the memory is about.' },
            statement: {
              type: 'string',
              description:
                'The memory as the person would read it back, in the third person. ' +
                'For example: "Prefers Slack messages kept under four lines".',
            },
            confidence: { type: 'number', description: '0 to 1.' },
            quote: {
              type: 'string',
              description: 'The exact sentence from the dictation that justifies this. Verbatim.',
            },
            origin: {
              type: 'string',
              enum: ['stated', 'observed'],
              description: '"stated" if the person said it outright, "observed" if inferred from use.',
            },
            due_at_text: {
              type: 'string',
              description: 'For commitments only: the deadline as spoken ("by Friday"). Empty otherwise.',
            },
          },
          required: ['type', 'subject', 'statement', 'confidence', 'quote', 'origin', 'due_at_text'],
        },
      },
    },
    required: ['summary', 'topics', 'entities', 'salience', 'candidates'],
  },
};

const EXTRACTION_SYSTEM = `You read one dictation at a time for Kivi, a voice-first writing tool, and decide what is worth remembering about the person using it.

Kivi's memory is a ledger of what the person actually said. It is not a profile of who they are.

Record:
- entity: a name the person uses — a colleague, project, product, repository, tool. Note it when the raw transcript misheard it.
- fact: a durable, checkable statement about their working world. Roles, ownership, deadlines, where things live.
- preference: how they want writing or work done, when they say so.
- commitment: something they said they would do, especially with a deadline.

Never record:
- health, medical or mental-health information, about them or anyone else
- political opinions, religious practice, sexual orientation or intimate relationships
- personal financial position, salary, debt
- another person's private circumstances
- passwords, keys, identifiers or any other credential
- passing states ("I'm tired", "the wifi is down") — true now, misleading later
- filler, acknowledgements or test recordings

Two rules that matter more than coverage:
1. Every candidate must quote the sentence that justifies it, verbatim from the dictation. If you cannot quote it, do not record it.
2. Most dictations contain nothing durable. An empty candidates array is the correct answer more often than not. Do not manufacture memories to seem useful.

Call record_understanding exactly once.`;

export class AnthropicProvider implements LlmProvider {
  readonly name = 'anthropic' as const;
  readonly model: string;
  private readonly extractionModel: string;
  private readonly client: Anthropic;

  constructor() {
    this.model = config.anthropic.model;
    this.extractionModel = config.anthropic.extractionModel;
    this.client = new Anthropic({
      apiKey: config.anthropic.apiKey,
      ...(config.anthropic.baseUrl ? { baseURL: config.anthropic.baseUrl } : {}),
      maxRetries: 3,
    });
  }

  async extract(input: ExtractionInput): Promise<{ result: ExtractionResult; usage: Usage }> {
    const { dictation, knownSubjects } = input;
    const known = knownSubjects.slice(0, 60).join(', ');

    const prompt = [
      `App: ${dictation.app}`,
      `Language: ${dictation.language}`,
      known ? `Names Kivi already knows: ${known}` : 'Kivi knows no names for this person yet.',
      '',
      'Raw transcript from the recogniser:',
      dictation.raw_asr,
      '',
      'What Kivi wrote:',
      dictation.formatted,
      '',
      'Call record_understanding for this dictation.',
    ].join('\n');

    const response = await this.client.messages.create({
      model: this.extractionModel,
      max_tokens: 4096,
      system: [{ type: 'text', text: EXTRACTION_SYSTEM, cache_control: { type: 'ephemeral' } }],
      tools: [EXTRACTION_TOOL as never],
      messages: [{ role: 'user', content: prompt }],
      output_config: { effort: 'low' },
    } as never);

    const usage = this.usageOf(response, this.extractionModel);
    const toolUse = response.content.find((b) => b.type === 'tool_use');

    if (!toolUse || toolUse.type !== 'tool_use') {
      log('debug', 'anthropic: extraction returned no tool call; treating as nothing-to-learn');
      return {
        result: { episode: emptyEpisode(dictation.formatted), candidates: [], notes: ['Model returned no structured result.'] },
        usage,
      };
    }

    return { result: this.toExtractionResult(toolUse.input as RawExtraction, dictation.formatted), usage };
  }

  async complete(request: CompletionRequest): Promise<CompletionResponse> {
    const response = await this.client.messages.create({
      model: this.model,
      max_tokens: request.maxTokens ?? 4096,
      system: [{ type: 'text', text: request.system, cache_control: { type: 'ephemeral' } }],
      tools: request.tools?.map((t) => ({ ...t, strict: true })) as never,
      messages: request.messages.map((m) => ({ role: m.role, content: m.content as never })),
      output_config: { effort: 'medium' },
    } as never);

    if (response.stop_reason === 'refusal') {
      return {
        stopReason: 'end_turn',
        blocks: [
          {
            type: 'text',
            text: 'I am not able to answer that one. Ask me about something in your dictation history instead.',
          },
        ],
        usage: this.usageOf(response, this.model),
      };
    }

    const blocks: ContentBlock[] = [];
    for (const block of response.content) {
      if (block.type === 'text') blocks.push({ type: 'text', text: block.text });
      if (block.type === 'tool_use') {
        blocks.push({
          type: 'tool_use',
          id: block.id,
          name: block.name,
          input: (block.input ?? {}) as Record<string, unknown>,
        });
      }
    }

    return {
      stopReason:
        response.stop_reason === 'tool_use'
          ? 'tool_use'
          : response.stop_reason === 'max_tokens'
            ? 'max_tokens'
            : 'end_turn',
      blocks,
      usage: this.usageOf(response, this.model),
    };
  }

  private usageOf(response: { usage?: { input_tokens?: number; output_tokens?: number } }, model: string): Usage {
    const tokensIn = response.usage?.input_tokens ?? 0;
    const tokensOut = response.usage?.output_tokens ?? 0;
    return {
      tokens_in: tokensIn,
      tokens_out: tokensOut,
      cost_usd: costUsd(model, tokensIn, tokensOut),
      calls: 1,
    };
  }

  private toExtractionResult(raw: RawExtraction, formatted: string): ExtractionResult {
    const candidates: MemoryCandidate[] = [];
    const notes: string[] = [];

    for (const item of raw.candidates ?? []) {
      // A candidate whose quote is not actually in the dictation is a
      // hallucination with a citation attached. Drop it here, loudly.
      if (!quoteAppears(item.quote, formatted)) {
        notes.push(`Dropped "${truncateForNote(item.statement)}" — its quote is not in the dictation.`);
        continue;
      }
      candidates.push({
        type: item.type,
        subject: item.subject,
        statement: item.statement,
        confidence: clamp01(item.confidence),
        quote: item.quote,
        origin: item.origin === 'stated' ? 'stated' : 'observed',
        detail: item.due_at_text ? { due_at_text: item.due_at_text } : {},
      });
    }

    return {
      episode: {
        summary: raw.summary?.trim() || emptyEpisode(formatted).summary,
        topics: (raw.topics ?? []).slice(0, 6),
        entities: (raw.entities ?? []).slice(0, 8),
        salience: clamp01(raw.salience ?? 0.5),
      },
      candidates,
      notes,
    };
  }
}

interface RawExtraction {
  summary?: string;
  topics?: string[];
  entities?: string[];
  salience?: number;
  candidates?: {
    type: MemoryCandidate['type'];
    subject: string;
    statement: string;
    confidence: number;
    quote: string;
    origin: string;
    due_at_text?: string;
  }[];
}

function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 0.5;
  return Math.max(0, Math.min(1, n));
}

/** Tolerant containment check: whitespace and casing vary, content should not. */
function quoteAppears(quote: string, haystack: string): boolean {
  if (!quote || quote.length < 6) return false;
  const flat = (s: string) => s.toLowerCase().replace(/\s+/g, ' ').replace(/[^a-z0-9 ]/g, '');
  return flat(haystack).includes(flat(quote).slice(0, 120));
}

function truncateForNote(text: string): string {
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}

function emptyEpisode(formatted: string) {
  const first = formatted.split(/(?<=[.!?])\s+/)[0] ?? formatted;
  return { summary: first.slice(0, 200), topics: [], entities: [], salience: 0.4 };
}

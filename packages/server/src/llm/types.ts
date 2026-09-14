import type { Dictation, ExtractionResult, Usage } from '../types.ts';

export type ContentBlock =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; tool_use_id: string; content: string; is_error?: boolean };

export interface LlmMessage {
  role: 'user' | 'assistant';
  content: ContentBlock[];
}

export interface ToolSpec {
  name: string;
  description: string;
  input_schema: {
    type: 'object';
    properties: Record<string, unknown>;
    required?: string[];
    /** Required by strict tool use, which is how we keep arguments schema-valid. */
    additionalProperties?: boolean;
  };
}

export interface CompletionRequest {
  system: string;
  messages: LlmMessage[];
  tools?: ToolSpec[];
  maxTokens?: number;
  temperature?: number;
}

export interface CompletionResponse {
  stopReason: 'end_turn' | 'tool_use' | 'max_tokens';
  blocks: ContentBlock[];
  usage: Usage;
}

export interface ExtractionInput {
  dictation: Pick<Dictation, 'id' | 'raw_asr' | 'formatted' | 'app' | 'captured_at' | 'language'>;
  /** Subjects Kivi already knows, so the extractor reinforces instead of inventing. */
  knownSubjects: string[];
}

export interface LlmProvider {
  readonly name: 'local' | 'anthropic';
  readonly model: string;
  /** Turn one dictation into an episode plus memory candidates. */
  extract(input: ExtractionInput): Promise<{ result: ExtractionResult; usage: Usage }>;
  /** One step of the Hey Kivi agent loop. May return tool_use blocks. */
  complete(request: CompletionRequest): Promise<CompletionResponse>;
}

export const ZERO_USAGE: Usage = { tokens_in: 0, tokens_out: 0, cost_usd: 0, calls: 0 };

export function addUsage(a: Usage, b: Usage): Usage {
  return {
    tokens_in: a.tokens_in + b.tokens_in,
    tokens_out: a.tokens_out + b.tokens_out,
    cost_usd: a.cost_usd + b.cost_usd,
    calls: a.calls + b.calls,
  };
}

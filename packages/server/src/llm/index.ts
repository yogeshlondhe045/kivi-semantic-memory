import { config, log } from '../config.ts';
import { AnthropicProvider } from './anthropic.ts';
import { LocalProvider } from './local.ts';
import type { LlmProvider } from './types.ts';

let provider: LlmProvider | null = null;

export function llm(): LlmProvider {
  if (provider) return provider;
  if (config.provider === 'anthropic') {
    provider = new AnthropicProvider();
    log('info', `llm   anthropic · ${provider.model} (conversation) · ${config.anthropic.extractionModel} (ingestion)`);
  } else {
    provider = new LocalProvider();
    log('info', 'llm   local deterministic provider — no network, no credentials');
  }
  return provider;
}

/** Used by tests and the evaluation harness to pin a provider explicitly. */
export function setProvider(next: LlmProvider | null): void {
  provider = next;
}

export function providerLabel(): string {
  const p = llm();
  return p.name === 'anthropic' ? `anthropic:${p.model}` : 'local:rules-v1';
}

export * from './types.ts';

/**
 * Published list prices, USD per million tokens (Anthropic first-party API).
 * Cost is reported next to latency everywhere in this system, because "what
 * does it cost to learn from 500 dictations" is a product question, not an
 * afterthought. Unknown models fall back to the Opus tier rather than quietly
 * reporting zero and flattering the numbers.
 */
const TABLE: Record<string, { input: number; output: number }> = {
  'claude-fable-5-1': { input: 10, output: 50 },
  'claude-fable-5': { input: 10, output: 50 },
  'claude-opus-5': { input: 5, output: 25 },
  'claude-opus-4-8': { input: 5, output: 25 },
  'claude-opus-4-7': { input: 5, output: 25 },
  'claude-opus-4-6': { input: 5, output: 25 },
  'claude-sonnet-5': { input: 2, output: 10 },
  'claude-sonnet-4-6': { input: 3, output: 15 },
  'claude-haiku-4-5': { input: 1, output: 5 },
  'kivi-rules-v1': { input: 0, output: 0 },
};

const DEFAULT = { input: 5, output: 25 };

export function priceFor(model: string): { input: number; output: number } {
  const key = Object.keys(TABLE)
    .sort((a, b) => b.length - a.length)
    .find((k) => model.startsWith(k));
  return key ? TABLE[key]! : DEFAULT;
}

export function costUsd(model: string, tokensIn: number, tokensOut: number): number {
  const price = priceFor(model);
  return (tokensIn * price.input + tokensOut * price.output) / 1_000_000;
}

export function formatUsd(amount: number): string {
  if (amount === 0) return '$0.00';
  if (amount < 0.01) return `$${amount.toFixed(5)}`;
  return `$${amount.toFixed(4)}`;
}

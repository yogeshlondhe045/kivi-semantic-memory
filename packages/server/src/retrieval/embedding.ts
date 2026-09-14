import { contentTokens } from '../util/text.ts';

/**
 * A local, deterministic, dependency-free text embedding.
 *
 * Why not a hosted embedding model: the declared primary review path must work
 * with no credentials and no network. Why an embedding at all, when we already
 * have BM25: BM25 misses paraphrase ("what do I owe Priya" vs "I promised Priya
 * the draft"), and character n-grams recover the ASR misspellings that are the
 * whole point of this corpus.
 *
 * This is a hashed bag of word-unigrams, word-bigrams and character 4-grams,
 * sub-linearly weighted and L2-normalised. It is weaker than a trained encoder
 * at pure semantics and stronger at noisy-spelling matching. The interface is
 * the seam: `EmbeddingProvider` can be swapped for a hosted model without
 * touching retrieval.
 */

export const EMBEDDING_DIM = 384;

export interface EmbeddingProvider {
  readonly name: string;
  readonly dim: number;
  embed(text: string): Float32Array;
}

function hash(input: string, salt: number): number {
  let h = 0x811c9dc5 ^ salt;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) % EMBEDDING_DIM;
}

export class LocalEmbeddings implements EmbeddingProvider {
  readonly name = 'local-hashed-ngram-v1';
  readonly dim = EMBEDDING_DIM;

  embed(text: string): Float32Array {
    const vec = new Float32Array(EMBEDDING_DIM);
    const words = contentTokens(text);

    for (const w of words) add(vec, hash(w, 1), 1);
    for (let i = 0; i < words.length - 1; i++) add(vec, hash(`${words[i]} ${words[i + 1]}`, 2), 0.7);

    const joined = words.join(' ');
    for (let i = 0; i + 4 <= joined.length; i++) {
      add(vec, hash(joined.slice(i, i + 4), 3), 0.35);
    }

    // Sub-linear damping keeps a long dictation from dominating a short memory.
    for (let i = 0; i < vec.length; i++) {
      if (vec[i] !== 0) vec[i] = Math.sign(vec[i]) * Math.log1p(Math.abs(vec[i]));
    }
    normalise(vec);
    return vec;
  }
}

function add(vec: Float32Array, index: number, weight: number): void {
  vec[index] += weight;
}

function normalise(vec: Float32Array): void {
  let norm = 0;
  for (let i = 0; i < vec.length; i++) norm += vec[i] * vec[i];
  norm = Math.sqrt(norm);
  if (norm === 0) return;
  for (let i = 0; i < vec.length; i++) vec[i] /= norm;
}

export const embeddings: EmbeddingProvider = new LocalEmbeddings();

export function cosine(a: Float32Array, b: Float32Array): number {
  let dot = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) dot += a[i] * b[i];
  return dot; // both vectors are L2-normalised
}

export function toBlob(vec: Float32Array): Uint8Array {
  return new Uint8Array(vec.buffer.slice(0));
}

export function fromBlob(blob: Uint8Array | null | undefined): Float32Array | null {
  if (!blob || blob.byteLength === 0) return null;
  const copy = new Uint8Array(blob.byteLength);
  copy.set(blob);
  return new Float32Array(copy.buffer);
}

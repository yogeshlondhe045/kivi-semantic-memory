import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The evaluation.
 *
 * It runs the whole pipeline, not a mock of it: a fresh database, all 500
 * corpus records ingested through the real ingestion path, then every graded
 * case asked through the real Hey Kivi agent. Nothing is stubbed and no answer
 * is pre-baked.
 *
 * What it measures, in order of how much it matters:
 *
 *   1. Does Kivi refuse to answer when the history does not contain the answer?
 *      This is first because every other number is worthless without it.
 *   2. Are the answers it does give supported by the sources it cites?
 *   3. Does it learn the right things and ignore the right things at ingestion?
 *   4. What does that cost in latency, database growth, tokens and money?
 *
 * Every result carries its trace, so for any row you can see the input, the
 * memories created or retrieved, their provenance, the resulting behaviour, and
 * the reason the system decided as it did. Failures are reported, never hidden:
 * a green report that hides a known gap is a worse artefact than a red one.
 *
 *   npm run eval                    full run, writes eval/results/
 *   npm run eval -- --skip-ingest   reuse the existing eval database
 *   npm run eval -- --only=abstain  run one family
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const RESULTS = path.join(HERE, 'results');

// The evaluation must never touch the database the app is serving.
process.env.DATABASE_URL = process.env.EVAL_DATABASE_URL ?? './data/eval.db';

interface GradedCase {
  id: string;
  family: string;
  question: string;
  why: string;
  dependsOn?: string;
  /** Documented gap: graded and shown, but not counted as a regression. */
  known_limitation?: boolean;
  expect: {
    outcome: 'answered' | 'abstained' | 'acted' | 'clarified';
    must_include?: string[];
    must_not_include?: string[];
    must_cite?: 'memory' | 'dictation';
    tools?: string[];
  };
}

interface CaseResult {
  id: string;
  family: string;
  question: string;
  why: string;
  passed: boolean;
  known_limitation: boolean;
  failures: string[];
  answer: string;
  outcome: string;
  tools_used: string[];
  citations: { kind: string; id: string; label: string; quote?: string }[];
  duration_ms: number;
  trace_id: string;
  /** The retrieval decision, lifted out of the trace so the report is readable. */
  decision: {
    plan?: unknown;
    grounding?: unknown;
    considered?: unknown[];
    notes?: unknown;
  };
}

async function main() {
  const args = process.argv.slice(2);
  const skipIngest = args.includes('--skip-ingest');
  const only = args.find((a) => a.startsWith('--only='))?.split('=')[1];

  const { config } = await import('../packages/server/src/config.ts');
  const { closeDb, databaseBytes, count } = await import('../packages/server/src/db/index.ts');
  const { providerLabel } = await import('../packages/server/src/llm/index.ts');

  const absoluteDb = config.databaseFile;
  if (!skipIngest) {
    closeDb();
    for (const suffix of ['', '-wal', '-shm']) {
      if (fs.existsSync(absoluteDb + suffix)) fs.rmSync(absoluteDb + suffix);
    }
  }

  const { migrate } = await import('../packages/server/src/db/migrate.ts');
  migrate();

  const { ensureUser } = await import('../scripts/lib.ts');
  ensureUser();

  const { ingestBatch } = await import('../packages/server/src/memory/ingest.ts');
  const { memoryStats } = await import('../packages/server/src/memory/store.ts');
  const { clearCoverageCache } = await import('../packages/server/src/retrieval/coverage.ts');
  const { ask } = await import('../packages/server/src/agent/index.ts');
  const { getTrace, latencySummary, usageSummary } = await import('../packages/server/src/trace/index.ts');

  const corpusPath = path.join(ROOT, 'corpus', 'kivi-corpus-v1.jsonl');
  const corpus = fs
    .readFileSync(corpusPath, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as CorpusRecord);

  const { ANCHOR } = await import('../corpus/generate.ts');
  const DAY = 86_400_000;
  const shift = Math.floor(Date.now() / DAY) * DAY - ANCHOR;

  const header = [
    '',
    '  Kivi — semantic memory evaluation',
    `  provider ${providerLabel()}`,
    `  corpus   ${corpus.length} records · ${path.relative(ROOT, corpusPath)}`,
    `  database ${path.relative(ROOT, absoluteDb)}`,
    '',
  ].join('\n');
  console.log(header);

  // ---------------------------------------------------------------- ingest --
  const growth: { at: number; dictations: number; memories: number; bytes: number }[] = [];
  let ingestResult: Awaited<ReturnType<typeof ingestBatch>> | null = null;

  if (skipIngest) {
    console.log('  (reusing existing database)\n');
  } else {
    const started = Date.now();
    ingestResult = await ingestBatch(
      config.defaultUserId,
      corpus.map((record) => ({
        external_id: record.external_id,
        captured_at: record.captured_at + shift,
        app: record.app,
        device: record.device,
        duration_ms: record.duration_ms,
        language: record.language,
        asr_confidence: record.asr_confidence,
        raw_asr: record.raw_asr,
        formatted: record.formatted,
        style: record.style,
      })),
      {
        label: 'eval',
        sourcePath: corpusPath,
        onProgress: (done, total) => {
          if (done % 100 === 0 || done === total) {
            growth.push({
              at: done,
              dictations: done,
              memories: count(
                `SELECT COUNT(*) AS n FROM memories WHERE user_id = ? AND status = 'active'`,
                [config.defaultUserId],
              ),
              bytes: databaseBytes(),
            });
            process.stdout.write(`  ingest ${done}/${total}\r`);
          }
        },
      },
    );
    console.log(
      `  ingest ${ingestResult.processed} records in ${((Date.now() - started) / 1000).toFixed(1)}s` +
        `  ·  ${ingestResult.created} learned, ${ingestResult.reinforced} reinforced, ` +
        `${ingestResult.superseded} superseded, ${ingestResult.rejected} refused\n`,
    );
  }
  clearCoverageCache();

  // ------------------------------------------------ ingestion-level checks --
  const corpusChecks = runCorpusChecks(corpus, count);
  console.log('  Ingestion checks');
  for (const check of corpusChecks) {
    console.log(`   ${check.passed ? '  ok' : ' FAIL'}  ${check.name.padEnd(42)} ${check.detail}`);
  }
  console.log('');

  // -------------------------------------------------------------- q and a --
  const { cases } = JSON.parse(fs.readFileSync(path.join(HERE, 'cases', 'cases.json'), 'utf8')) as {
    cases: GradedCase[];
  };
  const selected = only ? cases.filter((c) => c.family === only || c.id.startsWith(only)) : cases;

  console.log(`  Hey Kivi cases (${selected.length})`);
  const results: CaseResult[] = [];

  for (const graded of selected) {
    const result = await ask(config.defaultUserId, graded.question);
    const trace = getTrace(result.trace_id);
    const payload = (trace?.payload ?? {}) as {
      plans?: unknown[];
      tool_calls?: { grounding?: unknown; plan?: unknown; considered?: unknown[] }[];
      notes?: unknown;
    };
    const toolCall = payload.tool_calls?.[0];

    const failures = grade(graded, result);
    const caseResult: CaseResult = {
      id: graded.id,
      family: graded.family,
      question: graded.question,
      why: graded.why,
      passed: failures.length === 0,
      known_limitation: Boolean(graded.known_limitation),
      failures,
      answer: result.text,
      outcome: result.outcome,
      tools_used: result.tools_used,
      citations: result.citations,
      duration_ms: result.duration_ms,
      trace_id: result.trace_id,
      decision: {
        plan: toolCall?.plan ?? payload.plans?.[0],
        grounding: toolCall?.grounding,
        considered: toolCall?.considered,
        notes: payload.notes,
      },
    };
    results.push(caseResult);
    const mark = caseResult.passed ? '  ok' : caseResult.known_limitation ? ' limit' : ' FAIL';
    console.log(
      `   ${mark}  ${graded.id.padEnd(30)} ${String(result.outcome).padEnd(10)} ${result.duration_ms}ms`,
    );
    if (!caseResult.passed) {
      for (const failure of failures) console.log(`         ${failure}`);
    }
  }

  // -------------------------------------------------------------- summary --
  const graded = results.filter((r) => !r.known_limitation);
  const limits = results.filter((r) => r.known_limitation);

  const byFamily = new Map<string, { passed: number; total: number }>();
  for (const result of results) {
    const entry = byFamily.get(result.family) ?? { passed: 0, total: 0 };
    entry.total += 1;
    if (result.passed) entry.passed += 1;
    byFamily.set(result.family, entry);
  }

  const stats = memoryStats(config.defaultUserId);
  const report = {
    generated_at: new Date().toISOString(),
    provider: providerLabel(),
    corpus: { path: path.relative(ROOT, corpusPath), records: corpus.length },
    ingestion: ingestResult,
    corpus_checks: corpusChecks,
    cases: results,
    summary: {
      cases_passed: graded.filter((r) => r.passed).length,
      cases_total: graded.length,
      known_limits_total: limits.length,
      known_limits_reached: limits.filter((r) => r.passed).length,
      checks_passed: corpusChecks.filter((c) => c.passed).length,
      checks_total: corpusChecks.length,
      by_family: Object.fromEntries(byFamily),
    },
    memory: stats,
    latency: {
      ingest: latencySummary(config.defaultUserId, 'ingest'),
      query: latencySummary(config.defaultUserId, 'query'),
    },
    usage: usageSummary(config.defaultUserId),
    database: {
      bytes: databaseBytes(),
      per_dictation_bytes: Math.round(databaseBytes() / Math.max(1, stats.dictations)),
      growth,
    },
  };

  fs.mkdirSync(RESULTS, { recursive: true });
  fs.writeFileSync(path.join(RESULTS, 'latest.json'), JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(RESULTS, 'report.html'), renderHtml(report));
  fs.writeFileSync(path.join(RESULTS, 'summary.md'), renderMarkdown(report));

  const passed = report.summary.cases_passed + report.summary.checks_passed;
  const total = report.summary.cases_total + report.summary.checks_total;

  console.log('');
  console.log(`  ${passed}/${total} passed`);
  if (limits.length) {
    console.log(
      `  ${limits.length} documented limits exercised (${limits.filter((r) => r.passed).length} unexpectedly handled) — see the report`,
    );
  }
  for (const [family, entry] of byFamily) {
    console.log(`    ${family.padEnd(20)} ${entry.passed}/${entry.total}`);
  }
  console.log('');
  console.log(`  latency   Hey Kivi p50 ${report.latency.query.p50} ms · p95 ${report.latency.query.p95} ms`);
  console.log(`            ingest   p50 ${report.latency.ingest.p50} ms · p95 ${report.latency.ingest.p95} ms`);
  console.log(`  database  ${(report.database.bytes / 1024 / 1024).toFixed(2)} MB · ${report.database.per_dictation_bytes} B per dictation`);
  console.log(`  model     ${report.usage.llm_calls} calls · ${report.usage.tokens_in + report.usage.tokens_out} tokens · $${report.usage.cost_usd.toFixed(4)}`);
  console.log('');
  console.log(`  written   eval/results/latest.json`);
  console.log(`            eval/results/report.html   ← open this one`);
  console.log(`            eval/results/summary.md`);
  console.log('');

  closeDb();
  // A failing evaluation should fail a pipeline, but the artefacts are written
  // either way — the report is the point, not the exit code.
  process.exit(passed === total ? 0 : 1);
}

// ---------------------------------------------------------------- grading --

function grade(graded: GradedCase, result: { text: string; outcome: string; tools_used: string[]; citations: { kind: string }[] }): string[] {
  const failures: string[] = [];
  const answer = result.text.toLowerCase();

  if (result.outcome !== graded.expect.outcome) {
    failures.push(`expected outcome "${graded.expect.outcome}", got "${result.outcome}"`);
  }
  for (const needle of graded.expect.must_include ?? []) {
    if (!answer.includes(needle.toLowerCase())) failures.push(`answer is missing "${needle}"`);
  }
  for (const needle of graded.expect.must_not_include ?? []) {
    if (answer.includes(needle.toLowerCase())) failures.push(`answer should not contain "${needle}"`);
  }
  if (graded.expect.must_cite && !result.citations.some((c) => c.kind === graded.expect.must_cite)) {
    failures.push(`expected at least one ${graded.expect.must_cite} citation, got ${result.citations.length ? result.citations.map((c) => c.kind).join(', ') : 'none'}`);
  }
  for (const tool of graded.expect.tools ?? []) {
    if (!result.tools_used.includes(tool)) {
      failures.push(`expected tool ${tool}, used ${result.tools_used.join(', ') || 'none'}`);
    }
  }
  return failures;
}

interface CorpusRecord {
  external_id: string;
  captured_at: number;
  app: string;
  device: string;
  duration_ms: number;
  language: string;
  asr_confidence: number;
  raw_asr: string;
  formatted: string;
  style: string;
  labels: { intent: string; expect_memory: boolean; expect_reason?: string; entities?: string[] };
}

interface Check {
  name: string;
  passed: boolean;
  detail: string;
  why: string;
}

/**
 * Checks that run against the database after ingestion, rather than against an
 * answer. These are the claims the product position makes about what Kivi keeps
 * and what it throws away — asserted over all 500 records, not sampled.
 */
type Counter = (sql: string, params?: unknown[]) => number;

function runCorpusChecks(corpus: CorpusRecord[], count: Counter): Check[] {
  const checks: Check[] = [];

  const sensitive = corpus.filter((r) => r.labels.intent === 'sensitive');
  const sensitiveMemories = count(
    `SELECT COUNT(*) AS n
       FROM memory_evidence me
       JOIN dictations d ON d.id = me.dictation_id
      WHERE d.formatted IN (${sensitive.map(() => '?').join(',') || "''"})`,
    sensitive.map((r) => r.formatted),
  );
  checks.push({
    name: 'sensitive dictations produce no memory',
    passed: sensitiveMemories === 0,
    detail: `${sensitive.length} sensitive records → ${sensitiveMemories} memories`,
    why: 'Health, money, relationships and other people\'s circumstances are refused in code, not by prompt.',
  });

  const transient = corpus.filter((r) => r.labels.intent === 'transient');
  const transientMemories = count(
    `SELECT COUNT(*) AS n
       FROM memory_evidence me
       JOIN dictations d ON d.id = me.dictation_id
      WHERE d.formatted IN (${transient.map(() => '?').join(',') || "''"})`,
    transient.map((r) => r.formatted),
  );
  checks.push({
    name: 'passing states produce no memory',
    passed: transientMemories === 0,
    detail: `${transient.length} transient records → ${transientMemories} memories`,
    why: 'True now, misleading later. Recording moods would make every other memory less reliable.',
  });

  const trivial = corpus.filter((r) => r.labels.intent === 'trivial');
  const trivialMemories = count(
    `SELECT COUNT(*) AS n
       FROM memory_evidence me
       JOIN dictations d ON d.id = me.dictation_id
      WHERE d.formatted IN (${trivial.map(() => '?').join(',') || "''"})`,
    trivial.map((r) => r.formatted),
  );
  checks.push({
    name: 'acknowledgements produce no memory',
    passed: trivialMemories === 0,
    detail: `${trivial.length} trivial records → ${trivialMemories} memories`,
    why: '"Okay" and "testing one two" are noise; keeping them would dilute retrieval.',
  });

  const everyMemoryHasEvidence = count(
    `SELECT COUNT(*) AS n FROM memories m
      WHERE NOT EXISTS (SELECT 1 FROM memory_evidence me WHERE me.memory_id = m.id)`,
  );
  checks.push({
    name: 'every memory is traceable to a dictation',
    passed: everyMemoryHasEvidence === 0,
    detail: `${everyMemoryHasEvidence} memories without evidence`,
    why: 'A memory with no provenance cannot be checked, corrected, or trusted.',
  });

  const superseded = count(`SELECT COUNT(*) AS n FROM memories WHERE status = 'superseded'`);
  checks.push({
    name: 'contradictions supersede rather than duplicate',
    passed: superseded > 0,
    detail: `${superseded} memories marked superseded and kept`,
    why: 'The old belief is history, not a mistake to delete. The user can see what changed.',
  });

  const lexicon = count('SELECT COUNT(*) AS n FROM lexicon WHERE json_array_length(variants) > 0');
  checks.push({
    name: 'recogniser mis-spellings are learned',
    passed: lexicon > 0,
    detail: `${lexicon} names with at least one observed variant`,
    why: 'This is the only channel from semantic memory into ordinary dictation.',
  });

  const reinforced = count('SELECT COUNT(*) AS n FROM memories WHERE evidence_count >= 3');
  checks.push({
    name: 'repeated statements are reinforced, not duplicated',
    passed: reinforced > 0,
    detail: `${reinforced} memories with three or more mentions`,
    why: 'Repetition is the main evidence that something is worth acting on.',
  });

  const orphanEpisodes = count(
    'SELECT COUNT(*) AS n FROM dictations d WHERE NOT EXISTS (SELECT 1 FROM episodes e WHERE e.dictation_id = d.id)',
  );
  checks.push({
    name: 'every dictation has an episode',
    passed: orphanEpisodes === 0,
    detail: `${orphanEpisodes} dictations without one`,
    why: 'Episodes are the retrieval floor: even when nothing was worth promoting, "what happened" stays answerable.',
  });

  return checks;
}

// ----------------------------------------------------------------- output --

interface Report {
  generated_at: string;
  provider: string;
  corpus: { path: string; records: number };
  ingestion: unknown;
  corpus_checks: Check[];
  cases: CaseResult[];
  summary: {
    cases_passed: number;
    cases_total: number;
    known_limits_total: number;
    known_limits_reached: number;
    checks_passed: number;
    checks_total: number;
    by_family: Record<string, { passed: number; total: number }>;
  };
  memory: { byType: { type: string; status: string; n: number }[]; rejections: { reason_code: string; n: number }[]; dictations: number };
  latency: Record<string, { p50: number; p95: number; max: number; count: number }>;
  usage: { tokens_in: number; tokens_out: number; cost_usd: number; llm_calls: number };
  database: { bytes: number; per_dictation_bytes: number; growth: { at: number; memories: number; bytes: number }[] };
}

function renderMarkdown(report: Report): string {
  const lines: string[] = [];
  lines.push('# Kivi semantic memory — evaluation results', '');
  lines.push(`Generated ${report.generated_at} · provider \`${report.provider}\` · corpus ${report.corpus.records} records`, '');
  lines.push(
    `**${report.summary.cases_passed}/${report.summary.cases_total} Hey Kivi cases** and ` +
      `**${report.summary.checks_passed}/${report.summary.checks_total} ingestion checks** passed.`,
    '',
  );

  lines.push('## Ingestion checks', '');
  lines.push('| check | result | detail |', '| --- | --- | --- |');
  for (const check of report.corpus_checks) {
    lines.push(`| ${check.name} | ${check.passed ? 'pass' : '**FAIL**'} | ${check.detail} |`);
  }

  const limits = report.cases.filter((c) => c.known_limitation);
  const graded = report.cases.filter((c) => !c.known_limitation);

  lines.push('', '## Hey Kivi cases', '');
  lines.push('| case | family | outcome | result |', '| --- | --- | --- | --- |');
  for (const result of graded) {
    lines.push(
      `| ${result.id} | ${result.family} | ${result.outcome} | ${result.passed ? 'pass' : `**FAIL** — ${result.failures.join('; ')}`} |`,
    );
  }

  if (limits.length) {
    lines.push('', '## Known limits', '');
    lines.push(
      'These are questions the product cannot answer today. They are run on every evaluation so the gap stays measured rather than remembered.',
      '',
    );
    for (const result of limits) {
      lines.push(`### ${result.id}`, '', `**Question.** ${result.question}`, '', `**Why it fails.** ${result.why}`, '');
      lines.push(`**What Kivi does today.** ${result.outcome} — ${result.answer.split('\n')[0]}`, '');
    }
  }

  const failed = graded.filter((c) => !c.passed);
  if (failed.length) {
    lines.push('', '## Failures in full', '');
    for (const result of failed) {
      lines.push(`### ${result.id}`, '', `**Question.** ${result.question}`, '', `**Why this case exists.** ${result.why}`, '');
      lines.push(`**What Kivi said.** ${result.answer}`, '');
      lines.push(`**Why it failed.** ${result.failures.join('; ')}`, '');
    }
  }

  lines.push('', '## Cost of running it', '');
  lines.push('| measure | value |', '| --- | --- |');
  lines.push(`| Hey Kivi latency p50 / p95 | ${report.latency.query!.p50} ms / ${report.latency.query!.p95} ms |`);
  lines.push(`| ingest latency per dictation p50 / p95 | ${report.latency.ingest!.p50} ms / ${report.latency.ingest!.p95} ms |`);
  lines.push(`| database | ${(report.database.bytes / 1024 / 1024).toFixed(2)} MB (${report.database.per_dictation_bytes} B per dictation) |`);
  lines.push(`| model calls | ${report.usage.llm_calls} |`);
  lines.push(`| tokens | ${report.usage.tokens_in.toLocaleString()} in / ${report.usage.tokens_out.toLocaleString()} out |`);
  lines.push(`| cost | $${report.usage.cost_usd.toFixed(4)} |`);
  lines.push('');
  return lines.join('\n');
}

function renderHtml(report: Report): string {
  const esc = (s: unknown) =>
    String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!);

  const caseRow = (result: CaseResult) => `
    <details class="case ${result.passed ? 'pass' : 'fail'}">
      <summary>
        <span class="pill ${result.passed ? 'ok' : 'bad'}">${result.passed ? 'pass' : 'FAIL'}</span>
        <code>${esc(result.id)}</code>
        <span class="q">${esc(result.question)}</span>
        <span class="right">${esc(result.outcome)} · ${result.duration_ms} ms</span>
      </summary>
      <div class="body">
        <p class="why">${esc(result.why)}</p>
        ${result.failures.length ? `<p class="failures">${result.failures.map(esc).join('<br>')}</p>` : ''}
        <h4>What Kivi said</h4>
        <pre>${esc(result.answer)}</pre>
        <h4>What it cited</h4>
        ${
          result.citations.length
            ? `<ul>${result.citations
                .map((c) => `<li><span class="pill">${esc(c.kind)}</span> ${esc(c.quote ?? c.label)}</li>`)
                .join('')}</ul>`
            : '<p class="muted">Nothing — the answer was an abstention.</p>'
        }
        <h4>How it decided</h4>
        <pre class="small">${esc(JSON.stringify(result.decision, null, 2))}</pre>
        <p class="muted">trace <code>${esc(result.trace_id)}</code></p>
      </div>
    </details>`;

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kivi memory — evaluation</title>
<style>
:root{--ink:#071722;--panel:#0b212f;--line:#17323f;--cream:#f0ecd4;--muted:#8ba3b0;--sage:#90c46a;--rose:#e08a7a;--amber:#e0b155}
*{box-sizing:border-box}
body{margin:0;background:var(--ink);color:var(--cream);font:15px/1.55 ui-sans-serif,-apple-system,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:48px 24px 90px}
h1{font-size:27px;letter-spacing:-.02em;margin:0 0 6px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:40px 0 12px}
h4{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:16px 0 6px}
.lede{color:var(--muted);margin:0 0 26px;max-width:64ch}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat .v{font-size:23px;font-weight:640;letter-spacing:-.02em}
.stat .l{font-size:12px;color:var(--muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}
th{text-align:left;color:var(--muted);font-weight:550;font-size:12px;padding:7px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600;background:#16394d;color:var(--muted)}
.pill.ok{background:rgba(144,196,106,.16);color:var(--sage)}
.pill.bad{background:rgba(224,138,122,.16);color:var(--rose)}
.case{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin-bottom:8px;overflow:hidden}
.case.fail{border-color:var(--rose)}
summary{cursor:pointer;padding:12px 16px;display:flex;gap:10px;align-items:center;list-style:none}
summary::-webkit-details-marker{display:none}
summary code{color:var(--muted);font-size:12px}
summary .q{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
summary .right{color:var(--muted);font-size:12px;white-space:nowrap}
.body{padding:0 16px 16px;border-top:1px solid var(--line)}
.why{color:var(--muted);font-size:13.5px}
.failures{color:var(--rose);font-size:13.5px}
pre{background:var(--ink);border:1px solid var(--line);border-radius:8px;padding:11px 13px;overflow-x:auto;white-space:pre-wrap;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#cfcbb6}
pre.small{font-size:11px;max-height:280px}
ul{margin:6px 0;padding-left:18px;font-size:13.5px}
.muted{color:var(--muted);font-size:12.5px}
a{color:var(--sage)}
</style></head><body><div class="wrap">
<h1>Kivi semantic memory — evaluation</h1>
<p class="lede">The complete pipeline over ${report.corpus.records} transcript-like records: a fresh database, real ingestion, real retrieval, real Hey Kivi. Every case below opens to show the answer, the sources it cited, and the decision that produced it. Failures are listed, not filtered.</p>

<div class="grid">
  <div class="stat"><div class="v">${report.summary.cases_passed}/${report.summary.cases_total}</div><div class="l">Hey Kivi cases passed</div></div>
  <div class="stat"><div class="v">${report.summary.checks_passed}/${report.summary.checks_total}</div><div class="l">ingestion checks passed</div></div>
  <div class="stat"><div class="v">${report.latency.query!.p50} ms</div><div class="l">answer latency, median</div></div>
  <div class="stat"><div class="v">${(report.database.bytes / 1024 / 1024).toFixed(2)} MB</div><div class="l">database after ${report.memory.dictations} dictations</div></div>
</div>
<p class="muted" style="margin-top:10px">Generated ${esc(report.generated_at)} · provider <code>${esc(report.provider)}</code></p>

<h2>Ingestion checks</h2>
<table><thead><tr><th style="width:90px">result</th><th>check</th><th>detail</th></tr></thead><tbody>
${report.corpus_checks
  .map(
    (c) => `<tr><td><span class="pill ${c.passed ? 'ok' : 'bad'}">${c.passed ? 'pass' : 'FAIL'}</span></td>
      <td>${esc(c.name)}<div class="muted">${esc(c.why)}</div></td><td class="muted">${esc(c.detail)}</td></tr>`,
  )
  .join('')}
</tbody></table>

<h2>Hey Kivi cases</h2>
${report.cases.filter((c) => !c.known_limitation).map(caseRow).join('')}

<h2>Known limits</h2>
<p class="lede">Questions this product cannot answer today. They run on every evaluation so the gap stays measured instead of remembered. A green report that omitted these would be less informative, not more.</p>
${report.cases.filter((c) => c.known_limitation).map(caseRow).join('')}

<h2>By family</h2>
<table><thead><tr><th>family</th><th style="width:100px">passed</th></tr></thead><tbody>
${Object.entries(report.summary.by_family)
  .map(([family, entry]) => `<tr><td>${esc(family)}</td><td>${entry.passed}/${entry.total}</td></tr>`)
  .join('')}
</tbody></table>

<h2>What Kivi kept, and refused</h2>
<table><thead><tr><th>kind</th><th>status</th><th style="width:90px">count</th></tr></thead><tbody>
${report.memory.byType.map((r) => `<tr><td>${esc(r.type)}</td><td class="muted">${esc(r.status)}</td><td>${r.n}</td></tr>`).join('')}
${report.memory.rejections.map((r) => `<tr><td>refused</td><td class="muted">${esc(r.reason_code)}</td><td>${r.n}</td></tr>`).join('')}
</tbody></table>

<h2>Cost of running it</h2>
<table><tbody>
<tr><td>Hey Kivi latency</td><td>p50 ${report.latency.query!.p50} ms · p95 ${report.latency.query!.p95} ms · max ${report.latency.query!.max} ms</td></tr>
<tr><td>Ingest latency per dictation</td><td>p50 ${report.latency.ingest!.p50} ms · p95 ${report.latency.ingest!.p95} ms</td></tr>
<tr><td>Database</td><td>${(report.database.bytes / 1024 / 1024).toFixed(2)} MB · ${report.database.per_dictation_bytes} B per dictation</td></tr>
<tr><td>Model calls</td><td>${report.usage.llm_calls}</td></tr>
<tr><td>Tokens</td><td>${report.usage.tokens_in.toLocaleString()} in · ${report.usage.tokens_out.toLocaleString()} out</td></tr>
<tr><td>Cost</td><td>$${report.usage.cost_usd.toFixed(4)}</td></tr>
</tbody></table>

<h2>Database growth</h2>
<table><thead><tr><th>dictations</th><th>active memories</th><th>bytes</th></tr></thead><tbody>
${report.database.growth.map((g) => `<tr><td>${g.at}</td><td>${g.memories}</td><td>${(g.bytes / 1024).toFixed(0)} KB</td></tr>`).join('')}
</tbody></table>

</div></body></html>`;
}

main().catch((err) => {
  console.error('\n!     evaluation failed:', err);
  process.exit(1);
});

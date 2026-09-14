# RUN

## Primary review method

**A completely local application.** One Node process serves the API and the built
interface; the database is a SQLite file inside the repository. No Docker, no
cloud account, and **no model credentials are required** — the system runs,
seeds, answers and evaluates end to end with `.env.example` copied unchanged.

Supplying `ANTHROPIC_API_KEY` upgrades extraction and answering from the
deterministic offline provider to a real model. It changes the quality of the
output; it does not change whether anything works. See
[Running it on a model](#running-it-on-a-model).

A `docker-compose.yml` is included as a documented secondary path. It is not the
declared review method.

Four commands, from a fresh clone:

```bash
npm install
cp .env.example .env
npm run setup      # preflight → migrate → seed 500 dictations → build the UI
npm start          # http://localhost:8787
```

`npm run setup` takes about 30 seconds, most of it the npm install you have
already done and the Vite build.

---

## 1. Required runtimes and versions

| Requirement | Version | Why |
| --- | --- | --- |
| Node.js | **22.5.0 or newer** (tested on 22.22) | The database layer uses the built-in `node:sqlite` module, so there is no native compilation step and no prebuilt binary to download. Node 22 LTS is fine. |
| npm | 10 or newer (ships with Node 22) | |

Nothing else. No Python, no system SQLite, no compiler toolchain, no Docker.

`npm run preflight` checks the Node version, confirms FTS5 is available in the
bundled SQLite, and fails with the exact remedy if either is wrong. It runs
automatically as the first step of `npm run setup`.

## 2. Every required environment variable

Copy the template and change nothing:

```bash
cp .env.example .env
```

Every variable has a working default. In full:

| Variable | Default | Required? | Meaning |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | `local` | no | `local` = deterministic, offline, no credentials. `anthropic` = real model calls. |
| `ANTHROPIC_API_KEY` | empty | only when `LLM_PROVIDER=anthropic` | If this is empty the system logs a warning and falls back to `local` rather than failing. |
| `ANTHROPIC_MODEL` | `claude-opus-5` | no | Model that runs the Hey Kivi conversation. |
| `ANTHROPIC_EXTRACTION_MODEL` | `claude-haiku-4-5` | no | Model that reads dictations during ingestion. Smaller on purpose: ingestion is one cheap pass over every record. |
| `ANTHROPIC_BASE_URL` | unset | no | Only for a gateway or proxy. |
| `PORT` | `8787` | no | API and interface. |
| `WEB_PORT` | `5173` | no | Vite dev server, `npm run dev` only. |
| `DATABASE_URL` | `./data/kivi.db` | no | SQLite file, relative to the repository root. |
| `KIVI_SEED` | `20260214` | no | Makes the corpus reproducible. |
| `LOG_LEVEL` | `info` | no | `silent` \| `error` \| `info` \| `debug` |

No credentials are committed anywhere in this repository.

## 3. Exact commands to install dependencies

```bash
npm install
```

One install covers the server, the interface and the tooling (npm workspaces).

## 4. Exact commands to create, migrate and seed the database

```bash
npm run db:migrate      # creates ./data/kivi.db and applies all 5 migrations
npm run seed            # ingests the 500-record corpus through the real pipeline
```

Or both, plus the preflight check and the UI build, in one step:

```bash
npm run setup
```

Seeding runs the complete ingestion path — extraction, the policy gate, memory
writes, provenance, traces — so the database you end up with is one the system
built, not a fixture. It prints what it learned, what it refused, and how much
the database grew. Expect roughly:

```
ok    ingested 500 dictations in 1.2s (2 ms each)
      memories: 52 created · 773 reinforced · 5 superseded · 3 awaiting confirmation
      ignored:  55 candidates refused by policy
      database: 316.0 KB -> 2.64 MB
```

The corpus timestamps are re-based on each seed so the most recent dictation
lands yesterday evening **relative to the day you run it**. This is deliberate:
"the dictation I did around 5PM yesterday" has to mean something today.

The corpus itself is committed, and regenerable:

```bash
npm run corpus:generate      # deterministic; same seed → identical 500 records
```

## 5. Exact commands to start every required process

One process:

```bash
npm start
```

If you skipped `npm run setup` and the interface has not been built, build it
first with `npm run build`.

For development with hot reload (two processes, started by one command):

```bash
npm run dev            # API on :8787, Vite on :5173 — open :5173
```

## 6. The URL to open

**<http://localhost:8787>**

(or <http://localhost:5173> when using `npm run dev`)

No login. The demo account is a single user, Ananya Rao, seeded with 500
dictations spanning about ninety days.

## 7. The primary interactions to try

Open **Hey Kivi** and use the suggested prompts, or type these:

1. **`Find the dictation I did around 5PM yesterday in Slack and polish it for the meeting I'm walking into`**
   The task's own example. Locates one recording out of 500 by time and app, then
   rewrites it using a preference learned from earlier dictations. Click **Why
   this answer** to see the window it searched, everything it considered with
   scores, and which memory changed the output.

2. **`Who is the PM on Meridian?`**
   Two dictations three weeks apart name different people. The later one wins.
   Open the cited memory to see the superseded belief kept alongside it.

3. **`What did I decide about the Zurich office lease?`**
   Nothing in the history is about this. Kivi says so. This is the behaviour the
   whole design is built around — try variations, it should keep refusing.

4. **`What is wrong with Devansh's health?`**
   The dictation exists and is findable under **Dictations**. No memory was ever
   formed from it, and the question itself is refused on category grounds.

5. **`What did I promise Priya?`**
   Commitments filtered by who they name, each citing the sentence you said.

6. **`Forget that I prefer bullet points over paragraphs`** — then ask
   **`Do I prefer bullet points over paragraphs?`**
   Deletion is permanent, leaves a tombstone, and suppresses the recordings that
   supported it from being used as grounds again.

Then look at:

- **Memory** — everything Kivi knows, grouped, each traceable to its source.
  Toggle **Show what changed** to see superseded beliefs.
- **Never kept** — every refusal, with its reason, on this user's own recordings.
- **Dictations** — the full history, raw ASR beside formatted output. Use
  **Dictate something new** to replay a transcript through the live pipeline and
  watch memory form (or be refused) immediately.
- **System** — latency, database growth, model usage and cost, from real traces.

## 8. Exact command to run the candidate evaluation

```bash
npm run eval
```

Runs the complete pipeline on a **separate** database (`./data/eval.db`) so your
running app is untouched: fresh schema → all 500 records ingested → 8 ingestion
checks → 25 graded Hey Kivi cases. Takes about 15 seconds on the offline
provider.

Useful flags:

```bash
npm run eval -- --only=abstention      # one family
npm run eval -- --skip-ingest          # reuse the existing eval database
```

Exit code is 0 when every graded case and check passes. Cases marked
`known_limitation` are run and reported but do not fail the build — they
document what the product cannot do yet.

## 9. Exact procedure for importing another corpus

```bash
npm run import -- --file=/absolute/path/to/corpus.jsonl
```

Accepts **JSONL**, a **JSON array**, or **CSV**. Field names are auto-detected
from the common variants (`raw_asr` / `asr` / `transcript`, `formatted` /
`llm_output` / `text`, `timestamp` / `captured_at` / `created_at`, and so on).
The importer prints the mapping it chose before writing anything:

```
       field mapping
         raw_asr         <- asr_output
         formatted       <- llm_formatted
         captured_at     <- timestamp
         app             <- source_app
       ! device          <- (not found — using default)
```

Check that table. If a column was guessed wrong, or your names are unusual,
override any field without touching code:

```bash
npm run import -- --file=corpus.jsonl --map=formatted=llm_text,captured_at=recorded_at
```

Verify the mapping before committing to a full run:

```bash
npm run import -- --file=corpus.jsonl --dry-run
```

To keep an imported corpus separate from the seeded demo user:

```bash
npm run import -- --file=corpus.jsonl --user=user_sarvam --name="Sarvam user"
```

Timestamps may be ISO-8601, epoch seconds, or epoch milliseconds — all three are
detected. Rows with no text or no parseable timestamp are skipped and listed
rather than silently dropped. Re-importing the same file is safe: records are
de-duplicated on a content hash.

Full schema and a worked example: [`docs/corpus-format.md`](docs/corpus-format.md).

## 10. Where evaluation results and memory state can be inspected

**Evaluation results** — written on every run:

| File | What it is |
| --- | --- |
| `eval/results/report.html` | **Open this one.** Every case expands to show the answer, the sources cited, and the retrieval decision behind it. |
| `eval/results/latest.json` | The same data, complete, including full traces. |
| `eval/results/summary.md` | Readable summary with a failures section. |

The results committed to this repository were generated by `npm run eval` on the
offline provider; the run's provider is stamped into all three files.

**Memory state** — three ways, in order of convenience:

1. **In the app.** *Memory* (what it knows, with provenance), *Never kept*
   (what it refused and why), *System* (latency, growth, cost). Every answer in
   Hey Kivi has a **Why this answer** panel with the full trace.
2. **Over HTTP.** `GET /api/memories`, `/api/memories/:id` (evidence and
   superseded versions), `/api/ignored`, `/api/dictations/:id`, `/api/traces/:id`,
   `/api/stats`.
3. **In SQL.** The database is a plain SQLite file:

   ```bash
   sqlite3 data/kivi.db "SELECT type, status, COUNT(*) FROM memories GROUP BY 1,2;"
   sqlite3 data/kivi.db "SELECT reason_code, COUNT(*) FROM rejections GROUP BY 1;"
   sqlite3 data/kivi.db "SELECT m.statement, e.quote FROM memories m
                           JOIN memory_evidence e ON e.memory_id = m.id LIMIT 20;"
   ```

   Schema and the reasoning behind it: [`docs/memory-model.md`](docs/memory-model.md).

## 11. Exact procedure for resetting the system

```bash
npm run db:reset        # deletes the database, recreates an empty schema
npm run seed            # optional: load the 500-record corpus again
```

`npm run db:reset -- --empty` leaves no database at all, exactly as a fresh
clone. The evaluation database is separate and is rebuilt from scratch by every
`npm run eval`.

---

## Running it on a model

```bash
# in .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Then re-seed so memories are extracted by the model rather than the rule engine:

```bash
npm run db:reset && npm run seed && npm start
```

The provider in use is shown in the app sidebar, stamped into every trace, and
recorded in every evaluation result. Nothing silently pretends a rule-based run
was a model run.

Ingestion of 500 records makes 500 extraction calls. On the default
`claude-haiku-4-5` that is roughly $0.30–$0.60 depending on transcript length;
the exact figure is reported by `npm run seed` and on the **System** page.

## Secondary path: Docker

```bash
docker compose up --build      # http://localhost:8787
```

Builds, migrates, seeds and serves in one container with the database on a named
volume. Provided for convenience; the local path above is the declared and
tested review method.

## If something goes wrong

| Symptom | Cause and fix |
| --- | --- |
| `node:sqlite` or FTS5 error on start | Node older than 22.5. Run `npm run preflight` for the exact version check. |
| "The interface has not been built yet" | Run `npm run build`, or use `npm run dev`. |
| The app loads but shows no history | The database was never seeded. Run `npm run seed`. |
| Port 8787 in use | `PORT=9000 npm start` |
| `npm run eval` exits 1 | A graded case genuinely failed. `eval/results/report.html` names which and why. |

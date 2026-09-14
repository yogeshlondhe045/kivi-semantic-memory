# Corpus format and import

How to get another 500 dictations into this system without editing any code.

```bash
npm run import -- --file=/absolute/path/to/corpus.jsonl
```

## Accepted files

- **JSONL** — one JSON object per line (preferred)
- **JSON** — a top-level array of objects
- **CSV** — with a header row; quoted fields and embedded newlines handled

## Fields

Only two things are genuinely required: some text, and a timestamp.

| Our field | Required | Accepted source names | Notes |
| --- | --- | --- | --- |
| `raw_asr` | one of the two | `raw_asr`, `asr`, `raw`, `transcript`, `raw_transcript`, `asr_output`, `raw_text` | What the recogniser produced |
| `formatted` | one of the two | `formatted`, `llm_formatted`, `formatted_output`, `llm_output`, `final_text`, `text`, `output` | What was written |
| `captured_at` | **yes** | `captured_at`, `timestamp`, `created_at`, `recorded_at`, `time`, `ts`, `date` | ISO-8601, epoch seconds or epoch milliseconds — all detected |
| `app` | no | `app`, `application`, `source_app`, `target_app`, `surface_app`, `context_app` | Lowercased; defaults to `unknown` |
| `external_id` | no | `external_id`, `id`, `record_id`, `dictation_id`, `uuid` | Kept for cross-referencing |
| `device` | no | `device`, `platform`, `client` | |
| `duration_ms` | no | `duration_ms`, `duration`, `length_ms`, `audio_ms` | |
| `language` | no | `language`, `lang`, `locale` | Defaults to `en` |
| `asr_confidence` | no | `asr_confidence`, `confidence`, `asr_score` | 0–1 |
| `style` | no | `style`, `style_name`, `style_id`, `profile` | The dictation style in use |

If only one of `raw_asr` / `formatted` is present, it is used for both. The gap
between them is where most of the phonetic and terminology evidence lives, so
supply both when you have them.

Any additional columns in your file are ignored, not rejected.

## Example record

```json
{
  "external_id": "kivi-0231",
  "captured_at": 1787654640000,
  "app": "slack",
  "device": "macbook",
  "duration_ms": 22400,
  "language": "en",
  "asr_confidence": 0.93,
  "raw_asr": "team the meridien pricing update is going out on monday um the headline is that the starter tier drops to nineteen dollars",
  "formatted": "Team, the Meridian pricing update is going out on Monday. The headline is that the starter tier drops to nineteen dollars.",
  "style": "work-chat"
}
```

## The mapping is printed before anything is written

```
import 500 rows from corpus.jsonl
       user user_demo · provider local:rules-v1

       field mapping
         raw_asr         <- asr_output
         formatted       <- llm_formatted
         captured_at     <- timestamp
         app             <- source_app
       ! device          <- (not found — using default)
```

A `!` means the field was not found and a default is being used. If a guess is
wrong, override it — no code change required:

```bash
npm run import -- --file=corpus.jsonl --map=formatted=llm_text,captured_at=recorded_at
```

`--map` takes `our_field=their_field` pairs, comma separated.

## Options

| Flag | Effect |
| --- | --- |
| `--file=<path>` | Required. Absolute or relative. |
| `--map=a=b,c=d` | Override field detection. |
| `--user=<id>` | Import under a separate account, leaving the seeded demo user intact. |
| `--name="..."` | Display name for a new account. |
| `--dry-run` | Print the mapping and the first mapped record; write nothing. |

Recommended first run:

```bash
npm run import -- --file=corpus.jsonl --dry-run
```

## What happens to each record

Exactly what happens to a seeded one — there is no separate import path:

1. stored as a dictation, de-duplicated on a content hash of text + timestamp;
2. screened: a whole dictation in a sensitive category is kept and stays
   searchable, but produces no memories;
3. extracted into an episode plus memory candidates;
4. every candidate through the policy gate — kept with provenance, or refused
   with a recorded reason;
5. reinforced, superseded or held for confirmation as appropriate;
6. a trace written for the whole decision.

Re-running the same file is safe. Records already present are counted as
duplicates and skipped.

## After importing

```bash
npm start                       # then open Memory, Never kept, System
```

or straight to SQL:

```bash
sqlite3 data/kivi.db "SELECT type, COUNT(*) FROM memories WHERE status='active' GROUP BY 1;"
sqlite3 data/kivi.db "SELECT reason_code, COUNT(*) FROM rejections GROUP BY 1 ORDER BY 2 DESC;"
```

To evaluate against an imported corpus rather than the committed one, point the
evaluation at its own database and import there first:

```bash
EVAL_DATABASE_URL=./data/eval.db npm run import -- --file=corpus.jsonl
npm run eval -- --skip-ingest
```

The graded cases in `eval/cases/cases.json` ask about *this* corpus's world, so
against a different corpus they will fail as written. Replace them with cases
about yours — the format is documented at the top of that file, and adding one
is a JSON object with a question, an expected outcome and a note saying why the
case exists.

## Regenerating our corpus

```bash
npm run corpus:generate                 # 500 records, seed from KIVI_SEED
npm run corpus:generate -- --count=200 --seed=7
```

Deterministic: the same seed produces the same records byte for byte, which is
what makes a committed evaluation result reproducible. `corpus/generate.ts`
documents what each part of the corpus is there to exercise.

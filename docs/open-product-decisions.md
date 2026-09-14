# Open product decisions

`PRODUCT_POSITION.md` and `PRODUCT_VISION.md` are not in the repository. The
assignment requires Part One to be the candidate's own thinking and explicitly
forbids using generative AI to arrive at the position or write the documents.
These decisions are therefore **not** made here.

Each one below records a provisional default so implementation is not blocked,
and names the blast radius so adopting the real answer is a small edit rather
than a rewrite. The defaults are the *least committal* option consistent with
the assignment — where a default would smuggle in a product thesis, the entry
says so instead.

| # | Decision | Provisional default | Changes when answered |
|---|---|---|---|
| **D1** | Which memory kinds exist | All three the assignment names: `factual`, `preference`, `episodic` | `MemoryKind` enum + one migration + the extraction prompt. Narrowing is cheap; the schema does not assume three. |
| **D2** | The 2–3 headline use cases | None chosen. Retrieval and the schema are use-case-neutral. | The Hey Kivi tool registry, the UI's primary screen, and which eval cases are headline vs. supporting. |
| **D3** | Which Hey Kivi tools exist | None implemented. Building tools before the use cases are fixed is features in search of a justification. | New modules under `api/kivi/tools/` + registry entries. The `tool_call` schema is already tool-agnostic. |
| **D4** | What must never be remembered | Conservative placeholder only: the `sensitive_excluded` code and `memory.sensitive` flag exist, but the *category list* is a product decision, not an engineering one. | The salience gate's exclusion list + the extraction prompt. |
| **D5** | Does memory touch ordinary dictation at all? | **No.** The assignment observes that semantic memory "may have little reason to affect ordinary dictation while being central to an interactive request" — the default follows that hint rather than inventing a reason to spend it. | Whether a dictation-time retrieval path exists at all. Currently there is none. |
| **D6** | Memory surface: passive vs. explicit review queue | Undecided. The schema supports both: `status='proposed'` is exactly a review queue if the product wants one, and is invisible if it does not. | The UI's memory screen; whether `proposed` memories are shown to the user or only to engineers. |
| **D7** | Consent model: opt-in per memory vs. automatic-with-undo | Undecided — this is a trust position, the heart of Part One. Schema supports both (`user_pinned`, `user_hidden`, retract, purge). | Default `status` on admission; whether promotion to `active` requires a user action. |
| **D8** | Product surface identity, naming and tone | None. No copy has been written. | All user-facing strings; the visual identity. |

## What was deliberately *not* defaulted

D2, D3 and D7 have no default because any choice would constitute a product
position. Picking a use case *is* the Part One decision; implementing tools for
a guessed use case would produce exactly the "collection of unrelated AI
features" the brief warns against. Work continues on the parts that are
genuinely position-independent — ingestion, admission, provenance, retrieval,
abstention, evaluation, import and reset — all of which the assignment demands
regardless of which position is taken.

## How the schema stays neutral

- No table encodes a use case.
- `MemoryKind` is one enum in one file.
- `tool_call` stores `tool_name` + JSONB arguments, so it does not know what tools exist.
- `eval_case.given`/`expected` are JSONB, so case shape follows the use cases rather than constraining them.
- Retrieval filters on generic properties (status, validity, confidence, kind, surface, time), none of which assume a domain.

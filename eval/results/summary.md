# Kivi semantic memory — evaluation results

Generated 2026-09-14T08:11:50.386Z · provider `local:rules-v1` · corpus 500 records

**21/21 Hey Kivi cases** and **8/8 ingestion checks** passed.

## Ingestion checks

| check | result | detail |
| --- | --- | --- |
| sensitive dictations produce no memory | pass | 24 sensitive records → 0 memories |
| passing states produce no memory | pass | 31 transient records → 0 memories |
| acknowledgements produce no memory | pass | 12 trivial records → 0 memories |
| every memory is traceable to a dictation | pass | 0 memories without evidence |
| contradictions supersede rather than duplicate | pass | 5 memories marked superseded and kept |
| recogniser mis-spellings are learned | pass | 8 names with at least one observed variant |
| repeated statements are reinforced, not duplicated | pass | 24 memories with three or more mentions |
| every dictation has an episode | pass | 0 dictations without one |

## Hey Kivi cases

| case | family | outcome | result |
| --- | --- | --- | --- |
| fact.pm-handover | multi_dictation | answered | pass |
| fact.design-lead | factual_recall | answered | pass |
| fact.manager | factual_recall | answered | pass |
| fact.staging-region | multi_dictation | answered | pass |
| fact.deploy-tool | factual_recall | answered | pass |
| pref.slack-length | preference_recall | answered | pass |
| pref.email-opening | preference_recall | answered | pass |
| commit.priya | commitments | answered | pass |
| commit.all | commitments | answered | pass |
| find.pricing-slack | find_dictation | answered | pass |
| rewrite.pricing-for-meeting | rewrite | answered | pass |
| rewrite.shorten | rewrite | answered | pass |
| abstain.zurich-lease | abstention | abstained | pass |
| abstain.salary | abstention | abstained | pass |
| abstain.health | abstention | abstained | pass |
| abstain.never-said | abstention | abstained | pass |
| abstain.future | abstention | abstained | pass |
| abstain.opinion | abstention | abstained | pass |
| entity.spelling | entity | answered | pass |
| forget.preference | control | acted | pass |
| forget.check | control | abstained | pass |

## Known limits

These are questions the product cannot answer today. They are run on every evaluation so the gap stays measured rather than remembered.

### limit.two-hop-join

**Question.** Which deploy tool does the project Rahul runs use?

**Why it fails.** Answering needs two memories joined: Rahul is the PM on Meridian, and Meridian deploys with Pipewright. Retrieval scores each memory against the question independently, so neither scores well and no join is attempted. Kivi should abstain rather than guess — but abstaining here is a miss, not a success.

**What Kivi does today.** abstained — I don't have anything in your history that answers that. I'd rather tell you that than guess.

### limit.aggregation

**Question.** How many times have I talked about Meridian?

**Why it fails.** A counting question. Nothing in the tool surface aggregates over the history, and inventing a number from the retrieved sample would be worse than not answering.

**What Kivi does today.** abstained — I don't have anything in your history that answers that. I'd rather tell you that than guess.

### limit.temporal-ordering

**Question.** Did I talk about the Meridian pricing before or after Rahul took over as PM?

**Why it fails.** Both facts are known and both are timestamped, but nothing compares them. Ordering two retrieved events is a capability the product does not have yet.

**What Kivi does today.** abstained — I don't have anything in your history that answers that. I'd rather tell you that than guess.

### limit.negation

**Question.** Which projects have I never mentioned in Slack?

**Why it fails.** A question about absence. Retrieval finds what is present; proving something is missing needs a different query shape entirely.

**What Kivi does today.** abstained — I don't have anything in your history that answers that. I'd rather tell you that than guess.


## Cost of running it

| measure | value |
| --- | --- |
| Hey Kivi latency p50 / p95 | 26 ms / 54 ms |
| ingest latency per dictation p50 / p95 | 2 ms / 8 ms |
| database | 2.75 MB (5775 B per dictation) |
| model calls | 0 |
| tokens | 0 in / 0 out |
| cost | $0.0000 |

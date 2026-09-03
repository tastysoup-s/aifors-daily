# Phase 7 — AI4S Backend Validation

## 1. Scope

Phase 7 validates the existing Fetch → Analyze → Summarize backend under a strict API-call budget. It does not implement Daily, Weekly, frontend, workflow, crawling, README/PDF retrieval, schema changes, or taxonomy changes.

Validation was performed on `feat/ai4s-redesign` on 2026-09-03, and all recorded Phase 7 validation commands explicitly targeted `data/ai4s_dev.db` or a temporary copy. The stable `main` branch was not modified. A final audit found that a separate default-path Fetch batch had written 45 Items to `data/ai_daily.db` at `2026-09-03T04:52:20.870923+00:00`; its SHA256 changed from the earlier recorded `3CBF173E...E16E83` to `B3400E34...E770AC`. The source process cannot be established from repository history, so the reference database was neither restored nor otherwise changed during the audit.

## 2. Fetch Content Improvement

Commit `2fa1168` improves RSS content selection and GitHub metadata composition without adding dependencies or requests:

- RSS: longest non-empty `entry.content`, then `summary`, then `description`; basic HTML, repeated lead, script, and link-helper noise removal; 20,000-character cap.
- GitHub: description plus up to eight topics, language, and homepage.
- arXiv remains unchanged because it already stores the abstract.

An offline replay of the same 58 real source responses, measured as plain text, showed:

| Metric | Before | After |
| --- | ---: | ---: |
| Median content length | 99 | 285 |
| Empty rate | 1.7% | 1.7% |
| Very-short rate (`1–199`) | 89.7% | 13.8% |

The remaining empty item was an external Hacker News link with no `story_text`. No generic crawler, README batch fetch, PDF parsing, or full-text scraper was introduced.

## 3. Source Content Quality

A real Fetch returned 115 items, deduplicated 52 new URLs, and stored 52. The resulting development database contains 110 items. It mixes old pre-enrichment GitHub/RSS rows with newly fetched rows, so old short content is not evidence against the new Fetcher behavior.

| Source | Items | Empty | Very short | Short | Usable | Rich | Median | Average |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| arxiv-ai-methods | 20 | 0 | 0 | 0 | 20 | 0 | 1550.5 | 1474.2 |
| arxiv-biology-medicine | 10 | 0 | 0 | 0 | 10 | 0 | 1679 | 1636.7 |
| arxiv-chemistry-materials | 10 | 0 | 0 | 0 | 10 | 0 | 1524 | 1432.3 |
| arxiv-physics-earth | 7 | 0 | 0 | 0 | 7 | 0 | 1070 | 1161.1 |
| github-ai-for-science | 16 | 0 | 14 | 2 | 0 | 0 | 147.5 | 133.3 |
| github-drug-discovery | 21 | 0 | 19 | 2 | 0 | 0 | 69 | 94.7 |
| github-materials-informatics | 7 | 0 | 6 | 1 | 0 | 0 | 54 | 85.1 |
| github-protein-design | 5 | 0 | 5 | 0 | 0 | 0 | 102 | 99.8 |
| hackernews-ai-science | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| deepmind-blog | 5 | 4 | 1 | 0 | 0 | 0 | 0 | 10.8 |
| microsoft-research | 1 | 0 | 0 | 0 | 1 | 0 | 618 | 618 |
| nvidia-dev-blog | 7 | 0 | 0 | 6 | 1 | 0 | 431 | 443.6 |

`EMPTY = 0`, `VERY_SHORT = 1–199`, `SHORT = 200–499`, `USABLE = 500–1999`, and `RICH >= 2000`. arXiv is consistently usable and is the strongest current input. Microsoft RSS is usable. NVIDIA RSS is mostly short but semantically useful after replay enrichment. DeepMind RSS and Hacker News remain weak. Existing GitHub rows are short because URL deduplication prevents automatic backfill; future unique repositories use the enriched representation.

## 4. Analyzer Boundary Validation

The 10 existing results were retained. Ten additional real calls were deliberately selected rather than random: five clear AI4S papers, four pure-science boundaries, and one ordinary-AI item. The additional batch completed with 10 analyses, 0 runtime errors, and cost `$0.00260640`.

Across 20 reviewed results: 18 were correct, 1 was reasonable/ambiguous, and 1 was likely wrong. Four pure-science samples were checked: three were correctly rejected and one was a false positive.

| Title | Type guess | is_ai4s | Category | Content type | Score | Assessment |
| --- | --- | --- | --- | --- | ---: | --- |
| HiPoly | AI4S paper | true | materials | paper | 8 | Correct |
| Low-Temperature Li-Ion Transport | AI4S paper | true | chemistry | paper | 7 | Correct |
| Graphene-Perovskite Interactions | AI4S paper | true | materials | paper | 8 | Correct |
| CliffRank | AI4S paper | true | biology | paper | 7 | Correct |
| FLaG | AI4S paper | true | biology | paper | 7 | Correct |
| From Goldene to Noblene | Pure science / DFT | true | materials | paper | 7 | Likely Wrong |
| JUICE-UVS | Pure science instrument | false | — | paper | 1 | Correct |
| Relating Solute Interactions | Pure science | false | — | paper | 2 | Correct |
| Altermagnetic Spin Splitting | Pure science | false | — | paper | 1 | Correct |
| Double-Blind AI Evaluations | Ordinary AI | false | — | research_news | 1 | Correct |
| Terminal-Bench-Science | AI4S benchmark | true | general | benchmark | 8 | Reasonable/Ambiguous: empty body |
| GigaPath-Flash / GigaTIME-Flash | AI4S research news | true | biology | research_news | 7 | Correct |
| BioNeMo NIM + Claude Science | AI4S tool | true | biology | tool | 7 | Correct |
| TensorRT Model Connect | AI infrastructure | false | — | tool | 1 | Correct |
| pypolymlp | AI4S tool | true | materials | tool | 6 | Correct |

The false positive used exhaustive structure enumeration, DFT, and a fitted pair model but no AI/ML method. Three other pure-science papers were correctly rejected, so this was recorded as an edge case rather than treated as a systematic failure. The Analyzer prompt was not changed.

## 5. Summary Quality Validation

Three existing summaries and three new rich-input summaries were reviewed. The new run selected 3 of 6 eligible candidates, produced 3 summaries with 0 errors, and cost `$0.00263560`.

| Title | Source | Content length | Score | Quality | Observation |
| --- | --- | ---: | ---: | --- | --- |
| HiPoly | arXiv materials | 1671 | 8 | A | Concrete representation, prediction/design pipeline, result and innovation |
| Graphene-Perovskite Interactions | arXiv materials | 1510 | 8 | A | Separates DFT and ML simulation; extracts 21.5% → 23.7% efficiency |
| Low-Temperature Li-Ion Transport | arXiv chemistry | 1681 | 7 | A | Extracts GP method, temperature effects, activation energies and RMSE |
| Terminal-Bench-Science | Hacker News | 0 | 8 | C | Conservative lack-of-information output, as expected |
| GigaPath-Flash / GigaTIME-Flash | Microsoft RSS, old row | 618 | 7 | C | Old stored teaser lacks enough plain-text evidence |
| BioNeMo NIM + Claude Science | NVIDIA RSS, old row | 462 | 7 | C | Short input yields appropriately sparse fields |

The three rich arXiv summaries were informative and evidence-bound. No invented metrics, methods, or resources were found. Sparse inputs remained conservative instead of being expanded speculatively, so the Summarizer prompt was not changed.

## 6. Backend Pipeline Validation

- Fetch: PASS — `fetched=115`, `deduped=52`, `stored=52`.
- Analyze: PASS — 10 selected real calls through the production batch and persistence path; 6 AI4S, 4 non-AI4S, 0 errors.
- Summarize: PASS — 6 candidates, 3 selected, 3 persisted, 0 errors.
- Persistence: PASS — 110 items, 20 analyses, and 6 summaries read back successfully.
- Integrity: PASS — 0 orphan analyses, 0 duplicate analysis URLs, and 0 summaries attached to non-AI4S or below-threshold rows.

Final database state:

```text
items = 110
analyzed = 20
AI4S true = 10
AI4S false = 10
score >= threshold = 9
summarized = 6
```

## 7. Idempotency

A temporary copy containing only the six completed Summary records was exercised through the normal CLI:

```text
analyze: found=0, analyzed=0, errors=0, cost=$0.000000
summarize-ai4s: candidates=0, selected=0, summarized=0, errors=0, cost=$0.000000
```

No completed URL was reprocessed. The temporary database was deleted after validation.

## 8. Cost

| Phase 7 operation | Calls | Cost |
| --- | ---: | ---: |
| Analyzer | 10 | $0.00260640 |
| Summarizer | 3 | $0.00263560 |
| Total | 13 | $0.00524200 |

No retries were requested by the application. LiteLLM twice timed out while refreshing its public model-price map and safely used its bundled fallback; this did not fail an analysis or add a model call.

## 9. Known Limitations

- Pure DFT/structure-enumeration work containing phrases such as “fitted model” can be misclassified as AI4S; one such false positive was observed.
- Existing GitHub and RSS rows are not backfilled after Fetcher enrichment because URL deduplication preserves stored Items.
- DeepMind RSS supplied empty content for 4 of 5 new entries; Hacker News can also provide no body for external links.
- Three older sparse summaries remain sparse. They were intentionally not regenerated, and 90 newly fetched items remain unanalyzed to respect the validation budget.
- GitHub README, landing-page text, and paper full text remain intentionally out of scope.
- The reference `data/ai_daily.db` received an independent/default-path Fetch batch during validation. Its data was preserved, but the process responsible should be identified before relying on that file as an immutable baseline.

## 10. Readiness Decision

**READY**

Core AI4S backend is sufficiently stable and informative. Ready to implement Daily / Weekly reports.

Fetch input materially improved, rich-input summaries are informative without systematic hallucination, persistence and idempotency are correct, and the observed Analyzer false positive is a documented non-blocking boundary rather than a repeated failure.

```text
Phase 7 complete.
Phase 8 not started.
```

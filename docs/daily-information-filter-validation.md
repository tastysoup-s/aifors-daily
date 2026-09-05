# Daily information sufficiency validation

Validated locally on 2026-09-05, without network or LLM calls.

## Rule

`src/information_sufficiency.py` counts substantive `scientific_problem`,
`ai_method`, `main_result`, and `innovation` fields. Daily requires at least
two fields, including a method or result. The clause-based placeholder
heuristic preserves factual clauses alongside missing-information caveats;
it does not verify the truth of summary claims. Assessment, significance,
resources, and raw content length do not contribute.

Filtering runs after AI4S/score qualification and before exact-score source
diversity and Top N. No sparse refill occurs. Existing persisted Daily reports
are reused unchanged; `qualified` and `filtered_sparse` describe current
candidates, while `selected` describes the saved report. Rebuilding an old
report must be explicit and was done only in disposable validation copies.

## Real database comparison

Source: `data/ai4s_dev.db`, opened read-only and copied with SQLite backup to
`data/information_filter_validation_20260905.db`. Only corresponding Daily
reports/report_items were deleted and rebuilt in the copy. Validation used
`score_threshold=7, top_n=10` for comparison with the previous ten-item report;
the user's local `top_n=30` preference was not edited.

| UTC report date | Before items | Before sparse | Candidates | Qualified | Filtered sparse | After items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-09-03 | 6 | 2 | 7 | 5 | 2 | 5 |
| 2026-09-04 | 10 | 1 | 82 | 80 | 2 | 10 |
| 2026-09-05 | No report | N/A | 18 | 18 | 0 | 10 |

Actual exclusions (across September 3 and 4, not all from the ten-item report):

| Title | Information score | Reason |
| --- | ---: | --- |
| Introducing WeatherNext 3, our most advanced and accurate global weather AI model | 0 | insufficient factual fields |
| scverse/pertpy | 2 | no method or result |
| Run NVIDIA BioNeMo NIM Microservices for Protein Structure Prediction in Claude Science | 0 | insufficient factual fields |
| GigaPath-Flash and GigaTIME-Flash: Toward population-scale discovery with efficient pathology foundation models | 0 | insufficient factual fields |

Actual retained September 4 items include:

| Title | Information score |
| --- | ---: |
| mir-group/nequip | 3 |
| An Integrative Computational Approach to Predict Viral Epitopes by Targeting the MHC-TCR Complexation (DynamiT) | 4 |
| Ageas enables time-agnostic cell fate inference from single-cell and spatial multi-omics data | 4 |

Separately, the already enriched BioNeMo summary in
`data/reading_validation_20260905.db` scored **4** and entered a September 5
Daily generated in `data/information_filter_enriched_validation_20260905.db`.
That run had 17 candidates, 16 qualified, 1 filtered, and 10 selected.
The sparse legacy BioNeMo summary and this enriched summary are different
stored versions; no summaries were regenerated for this validation.

## Checks

- Source database SHA-256 unchanged:
  `66175843800205e7d97af111a44c23a36dfc1fa51901a6df77a278357854ba64`.
- Items, analyses/summaries (including assessment and surfaced_at), and all
  Weekly report rows/items compared equal before and after Daily generation.
- Repeated generation reused the same report ID without rewriting selection.
- Local September 4 preview: `data/information_filter_preview/index.html`;
  10 Daily items and the existing 6-item Weekly rendered successfully.
- Tests cover empty and undersized filtered Daily rendering, caveat handling,
  factual-field-only scoring, selection order/diversity, and immutability.
- Full suite: **277 passed** (27 added tests). Windows' default pytest temp
  directory denied access; tests used a new isolated `--basetemp` under `data/`.
- No schema, Weekly, prompt, enrichment, or frontend changes. No LLM calls;
  additional cost **$0**. Validation databases and HTML remain Git-ignored.

This change is committed locally only; it has not been pushed or deployed.

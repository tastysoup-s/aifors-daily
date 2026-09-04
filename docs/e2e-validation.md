# AI4S End-to-End Validation

Validation date: 2026-09-03 (Asia/Shanghai; report windows use UTC).

## Scope

The validation used the local development database `data/ai4s_dev.db`. It did not modify or copy `data/ai_daily.db`, did not use the production path `data/ai4s.db`, and did not change `main`.

## Pipeline Result

| Stage | Result | Evidence |
| --- | --- | --- |
| Fetch | PASS | 116 fetched, 2 deduplicated, 2 stored |
| Analyze | PASS | limit 3; 3 analyzed, 2 AI4S, 1 non-AI4S, 0 runtime errors |
| Summarize AI4S | PASS with isolated rejection | limit 2; 1 persisted, 1 schema-invalid response rejected without incomplete persistence |
| Daily Report | PASS | existing report id 1 reused; 6 items |
| Weekly Report | PASS | existing report id 2 reused; 6 representative items; no repeat LLM call |
| Render | PASS | `site/index.html`; Daily 6 and Weekly 6 |
| Local HTTP | PASS | HTTP 200, UTF-8 Chinese text and required AI4S page content loaded |
| GitHub Pages | MANUAL STEP | expected public URL currently returns 404; repository Pages must be set to `data` branch and `/docs` |

The rejected summary was for `https://github.com/ddmms/ml-peg`: its model response omitted the required `scientific_problem` field. The batch continued, and the rejected item was not marked complete. This is the intended error-isolation behavior.

## Rendered Data Checks

- Three Daily items were compared against SQLite. Titles, links, sources, displayed dates, scores, categories, mapped content types, tags, and all six summary fields were present in the generated HTML.
- Weekly overview, category-trend values, watchlist values, and representative-work sections were present.
- The page contains no legacy summary labels and reads only the Report Layer.
- Desktop and 390 × 844 mobile layouts were inspected in a browser.
- Daily / Weekly switching and category filtering were exercised in the rendered page.

## Model Usage

- Analyzer calls: 3
- AI4S Summarizer calls: 2
- Additional cost for this validation: `$0.000921`
- Automatic JSON/API retries observed: 0
- Weekly report reuse cost: `$0.000000`

The validation used only small explicit limits. No additional LLM call is required to reproduce the static rendering or HTTP checks once reports exist.

## Deployment Checkpoint

The workflow persists `data/ai4s.db` and `docs/` to the independent `data` branch. A scheduled GitHub Actions workflow only runs from the repository default branch. After an approved release places the workflow on the default branch and the first successful run creates `data`, enable GitHub Pages with:

```text
Settings → Pages → Deploy from a branch → data → /docs
```

Expected URL: `https://tastysoup-s.github.io/aifors-daily/`.

# Prompt 1 — build the dashboard

The prompt this project started from, kept verbatim. Readers find the prompts
more useful than the finished code, because they show where the design
decisions actually came from.

> Help me build an open-source, self-refreshing dashboard on Google Cloud using
> `bigquery-public-data.thelook_ecommerce`. Metric: weekly item return rate,
> this year vs last year, split into rate and mix effects by traffic source and
> by product category. Build (1) the BigQuery grain query, (2) a single
> self-contained HTML dashboard with the data embedded as a JS array, filters, a
> waterfall and tables, and (3) a notebook that re-runs the query, replaces the
> embedded data, and uploads the file to a GCS bucket with no-cache headers.
> Keep it simple enough to publish as a template repo.

## What came back changed, and why

Worth recording as you go — this is the part of the article nobody else can
copy.

- **The waterfall is drawn in percentage points, not rate levels.** A bridge
  between 9.53% and 9.81% on a zero-based axis shows two bars of identical
  height and nothing else. Drawing it in delta space, with last year's rate at
  the zero line, keeps the axis honest and the bars readable.
- **The lag guard is measured, not guessed.** The notebook queries the
  90th-percentile return lag and drops that many weeks, instead of hardcoding
  "about three weeks".
- **The grain stores counts, not rates**, so that filtering in the browser never
  averages an average.

## Still to add

- Prompt 2: the Gemini themes section over `cfpb_complaints`.

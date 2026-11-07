# InsightPilot AI

A dashboard that explains **why** a metric moved, and rebuilds itself every week
for about the price of a coffee per year.

Most dashboards report that the return rate went from 11.7% to 12.4% and stop
there. This one splits that change into the only two things it can be:

- **Rate effect** — customers inside each segment behaved differently.
- **Mix effect** — customers behaved the same, but the composition shifted.

Those point at different teams and different fixes, and a trend line cannot tell
them apart.

<!-- TODO: GIF of the dashboard — change a filter, watch the bridge redraw. -->

**Live demo:** <!-- TODO: https://storage.googleapis.com/YOUR-BUCKET/Article_Insights_AI.html -->

---

## How it works

```
 BigQuery public dataset           Vertex AI scheduled job              Cloud Storage
 thelook_ecommerce       ──SQL──▶  (weekly schedule, runs as   ──PUT──▶  Article_Insights_AI.html
 orders · users · products         a service account)                    no-cache, one URL
                                   1. measure the return lag                  │
                                   2. run the grain query                     ▼
                                   3. inject rows into the page        a permanent link
```

No server, no BI licence, no build step. The dashboard is a single HTML file
with its data embedded as a JavaScript array, so it opens instantly, works
offline, and runs anywhere a browser does.

| Path | What it is |
|---|---|
| `refresh_dashboard.py` | **The whole job, in one file.** Queries, checks, injects, publishes |
| `sql/weekly_grain.sql` | The weekly grain — week × traffic source × category, as counts |
| `sql/return_lag.sql` | Measures how late returns arrive, which sets the censoring guard |
| `dashboard/index.html` | The dashboard itself, and the template the job fills in |
| `dashboard/data.json` | A generated sample payload, so the page works with no cloud account |
| `scripts/deploy_template.sh` | Copies the page and the queries to the bucket for the scheduled job |
| `scripts/make_sample_data.py` | Generates `data.json` |
| `scripts/inject_data.py` | Puts a payload into the page locally |
| `prompts/` | The prompts used to build this, kept verbatim |

### One implementation, two places to read it from

The repo is the source of truth. A Vertex AI job has no checkout, so
`scripts/deploy_template.sh` copies the page template and the two queries to
`gs://BUCKET/template/`, and the job reads them from there.

`refresh_dashboard.py` resolves each input the same way every time: **a local
file wins if it exists, otherwise the deployed copy in the bucket.** Run it from
a clone and it uses your working tree; run it on Vertex AI and it uses what you
deployed. One authored file, one deployed copy of that file, and no third
version drifting quietly out of sync.

The deployed template lives under `template/` while the published page sits at
the bucket root, so a bad run can overwrite the output but never the input it
was built from.

---

## Quick start

See it before touching Google Cloud:

```bash
python refresh_dashboard.py --data dashboard/data.json --no-upload --out page.html
```

That fills the page from the committed sample payload and writes it next to you.
The sample is clearly marked as sample on screen, so you can judge the layout
without an account. Then:

1. **Create a Google Cloud project** and enable BigQuery, Cloud Storage and
   Vertex AI. Set a $10 budget alert before anything else runs.
2. **Authenticate for the client libraries**, which is not the same thing as
   authenticating the CLI:

   ```bash
   gcloud auth application-default login
   ```

3. **Set your own project and bucket** at the top of `refresh_dashboard.py`, or
   pass `IP_PROJECT` and `IP_BUCKET` as environment variables.
4. **Price it first.** This is free and it is the only chance to catch an
   expensive query before paying for it:

   ```bash
   python refresh_dashboard.py --estimate-only
   ```

5. **Create the bucket and publish**, private by construction — uniform
   bucket-level access on, public access prevention enforced:

   ```bash
   python refresh_dashboard.py --create-bucket
   ```

6. **Deploy the inputs** so a job with no checkout can find them:

   ```bash
   ./scripts/deploy_template.sh
   ```

7. **Schedule it weekly** as a Vertex AI custom job running
   `python refresh_dashboard.py`. It must run as a service account, not as you —
   see the gotchas, because this is the step that fails for everyone the first
   time. The service account needs **BigQuery Job User** on the project and
   **Storage Object User** on the bucket.

### Going public

Two changes, in this order:

```bash
gcloud storage buckets update gs://YOUR-BUCKET --no-public-access-prevention
```

```bash
gcloud storage buckets add-iam-policy-binding gs://YOUR-BUCKET \
  --member=allUsers --role=roles/storage.objectViewer
```

The permanent link is then `storage.googleapis.com/BUCKET/OBJECT`.

**That grant is bucket-wide, and there is no way to narrow it.** The obvious
instinct is to scope it to the one object you actually want served, with an IAM
condition on `resource.name`. Cloud Storage refuses:

```
HTTPError 400: PublicResourceAllowConditionCheck
Error: Conditions are not allowed on public resources.
```

Per-object ACLs are the other way to do it, and uniform bucket-level access —
which you want on, because it is what removes per-object ACLs as a leak path —
disables exactly that. So the honest model is: **a bucket is public or it is
not.** Treat this one as a public web root. Everything in it is world-readable,
including the `template/` prefix, which is why the template here carries sample
data and the queries are the same ones already published in this repo.

If you ever need the job's inputs to be private, they do not belong in the same
bucket as the page. Put them in a second, private bucket, or — since this repo
is public — point `--template` and `--sql` at GitHub raw URLs and keep the
public bucket holding nothing but the published page.

### Creating the bucket by hand

With `gcloud`, two commands:

```bash
gcloud storage buckets create gs://YOUR-BUCKET --location=us-central1 \
  --uniform-bucket-level-access --public-access-prevention
```

```bash
gcloud storage cp page.html gs://YOUR-BUCKET/Article_Insights_AI.html \
  --content-type="text/html; charset=utf-8" --cache-control="no-cache, max-age=0"
```

**Dragging the file into the console works too, but sets neither header.** A
console upload gives the object `text/html` with no charset and no cache policy,
which is exactly the mojibake-and-stale-numbers pair from the gotchas below. If
you upload by hand, edit the object's metadata afterwards and set both.

Bucket names are global across all of Google Cloud, not per project, so the name
you want may be taken; the script says so and suggests a suffix. Lowercase only.

---

## Reading the numbers

The overall rate is a weighted average — each segment's rate, weighted by its
share of items — so a change in the total decomposes exactly:

```
Δ rate = Σ (share_now − share_before) × rate_before     ← mix
       + Σ  share_now  × (rate_now − rate_before)       ← rate
```

There is no residual. The two terms always sum to the observed change. This is
a **Kitagawa decomposition**, published by Evelyn Kitagawa in 1955 as
*"Components of a Difference Between Two Rates"* — worth knowing, both because
the method is older and better understood than it looks, and because her
symmetric variant splits the interaction term evenly between the two components
instead of attributing it all to rate. Both forms close exactly; they disagree
only about who gets the overlap.

### What the data actually says

Measured against the live dataset over the two 52-week windows ending
2026-10-12, with a 2-week lag guard:

| | Last year | This year | Change |
|---|---|---|---|
| Return rate | 11.658% | 12.400% | **+0.742 pt** |

| Split by | Mix | Rate |
|---|---|---|
| Traffic source | −0.003 pt | +0.745 pt |
| Product category | +0.008 pt | +0.734 pt |

**The mix effect is essentially zero on both dimensions.** The rate rose because
customers inside every major segment returned more, not because the mix of
customers changed — Search went 11.6% → 12.4%, Facebook 11.5% → 13.1%, and only
Email improved. The segment shares barely moved at all: the largest shift in a
year is 0.2 pt.

That is a real answer, and worth stating plainly rather than dressing up: on
this dataset the decomposition's verdict is "it isn't mix." Synthetic data tends
to hold its proportions steady, so a flat mix term is what you should expect
here. The method earns its keep on real business data, where mix shifts are
exactly what gets mistaken for behaviour.

**Which is why the dimension matters.** A mix effect can only show up on a
dimension that actually shifted, so "is it rate or mix?" has no answer until you
name the split. Segment by something stable and you will always conclude
"behaviour" — correctly here, but not because you checked anything.

Three decisions are baked into the query, each for a reason:

- **Counts, not rates.** Counts add up across any grouping; rates do not. Every
  rate is computed at display time, so filtering never averages an average.
- **Cancelled items are excluded.** They never shipped, so they could never be
  returned. Leaving them in the denominator quietly drags every rate down.
- **The last few weeks are dropped.** Returns arrive late, so recent weeks always
  look good. `sql/return_lag.sql` measures the 90th-percentile return lag and the
  script drops that many weeks from *both* comparison windows, keeping them
  matched.

---

## Gotchas

Every one of these cost real time, and most of them fail by producing something
that looks fine rather than an error.

**A zero-byte dry run is a broken dry run, not a free query.** BigQuery reports
`0 bytes` for a multi-statement script, so the moment a `.sql` file here holds
two statements instead of one, the cost estimate reads zero and
`maximum_bytes_billed` stops protecting anything — silently, because zero sails
under every ceiling. Measured: one statement estimates at 8.4 MB; the same query
with a second statement appended estimates at 0. The script now refuses to run
on a zero estimate. A single statement ending in a semicolon is fine.

**You cannot make one object public.** An IAM condition scoped to a single
object is rejected outright — *"Conditions are not allowed on public
resources"* — and the per-object ACL route is closed off by the uniform
bucket-level access you want switched on. Public is a property of the bucket.
Plan the bucket's contents around that rather than discovering it at the moment
you want a demo link.

**The authenticated link is not the public link.**
`storage.cloud.google.com` needs every viewer signed in with access, and signed
URLs time out. For public data, make the object public and share the
`storage.googleapis.com` URL — that one is permanent. Never do this with a
bucket holding anything that is not already public.

**A private HTML object does not render in the browser.** While the bucket is
private, `storage.cloud.google.com` serves the file as a *download* rather than
displaying it, so clicking the link saves the file instead of showing the
dashboard. That is the endpoint's behaviour, not a problem with the file — open
the downloaded copy and it works identically, because the page is entirely
self-contained. In-browser rendering from a URL needs the public object.

**There are two different logins.** `gcloud auth login` authenticates the CLI;
`gcloud auth application-default login` writes the credentials that Python
client libraries actually read. The CLI can work perfectly while every script
fails with `DefaultCredentialsError`. If the consent screen comes back having
granted only `openid` and `userinfo.email`, the `cloud-platform` box was not
ticked and the libraries still will not work.

**The schedule runs as you, by default.** A scheduled job using your own
end-user credentials breaks silently when your token, permissions or job
changes. Run it as a service account. If you schedule a Colab Enterprise
notebook rather than this script, you must create a runtime template with
**"Enable end-user credentials" turned off** before the service-account option
appears at all — and then grant that account **Code Viewer** on the notebook, or
the first run fails because it cannot open the notebook it is meant to run.
Running a plain script, as here, avoids that second problem entirely.

**Analytics belong in the template, not the published page.** Any snippet added
to the live object is wiped by the next refresh without a trace. Add it to
`dashboard/index.html` and deploy.

Two smaller ones, both of which produce a page that looks broken rather than an
error message:

- Upload with `Content-Type: text/html; charset=utf-8`. Without the charset,
  every dash and minus sign renders as mojibake.
- Set `Cache-Control: no-cache`. The URL never changes, so otherwise viewers
  keep seeing last week's numbers.

---

## Cost

Prices change and vary by region. These are US on-demand figures — check the
pricing pages before quoting them. The script prints the real query number on
every run, and that is the one to trust:

```bash
python refresh_dashboard.py --estimate-only
```

| Line | Rate | Free each month | A weekly refresh |
|---|---|---|---|
| BigQuery analysis | ~$6.25 / TiB scanned | the first 1 TiB | ~8 MB a week, under 0.1% of the free TiB |
| Vertex AI compute | ~$0.13–0.19 / hour | — | a few minutes a week, under 2¢ a run |
| Cloud Storage | ~$0.020 / GB-month | 5 GB | a 200 KB file, $0.000005 a month |
| Class A ops (uploads) | ~$0.005 / 1,000 | 5,000 | 52 a year, free |
| Class B ops (reads) | ~$0.0004 / 1,000 | 50,000 | one per page view |
| Egress | ~$0.12 / GB | 100 GB (N. America) | 200 KB per page view |

**About $0.50–1.00 a year, and nearly all of it is the compute.** The query and
the storage are rounding errors against their free tiers; the schedule is the
only line you really pay for. Daily instead of weekly roughly septuples that
line and still lands near $5 a year.

**The one line that scales with success is egress** — which is not where anyone
looks. Every page view ships the whole 200 KB file, data and all. Ten thousand
readers is 2 GB, inside the free allowance; a hundred thousand is 20 GB, still
inside it. A front-page spike of a million views is ~200 GB, so ~$12 once.
Embedding the data in the page is exactly what makes the hosting free, and
exactly what makes a viral day cost money. Worth knowing before you post it.

### Keeping it that way

Four rails, because a scheduled job bills you while you are not looking:

- **A $10 budget alert on the project**, set before anything else runs. It does
  not stop spending; it emails you.
- **`--max-scan-mb`**, which the script hands to BigQuery as
  `maximum_bytes_billed`. A query that would scan more is refused by the server,
  so a bad edit to the SQL fails instead of billing.
- **A dry run on every execution.** Free, and the only chance to catch an
  expensive query before paying for it.
- **A floor under the dry run**, because a zero estimate means the ceiling is
  not protecting anything. See the first gotcha.

And one rail on correctness rather than cost: the script checks the finished
payload before publishing — row count, plausible overall rate, no segment
returning more than it sold, nothing stale — and a failure exits non-zero with
the live page untouched. A schedule that silently publishes a broken dashboard
is worse than one that fails, because nobody looks at a job that succeeded.

---

## Licence

MIT. See [LICENSE](LICENSE).

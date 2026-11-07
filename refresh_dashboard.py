#!/usr/bin/env python3
"""Rebuild the InsightPilot dashboard from BigQuery and publish it to Cloud Storage.

One plain script with no arguments required, so a Vertex AI custom job can run
it as-is:

    python refresh_dashboard.py                      # query, inject, publish
    python refresh_dashboard.py --estimate-only      # what would this cost?
    python refresh_dashboard.py --data dashboard/data.json --no-upload --out page.html
    python refresh_dashboard.py --create-bucket      # first run: make the bucket too

What it changes is only the numbers. The page carries its data as a single JSON
blob between two markers, and every chart, table and sentence in it is derived
from that blob in the browser. Swapping the blob redraws the visuals, so there
is no rendering step here and nothing that can fall out of sync.

The queries and the page template live in this repo, in `sql/` and `dashboard/`.
A local checkout always wins; a scheduled job, which has no checkout, reads the
copies that `scripts/deploy_template.sh` put in the bucket. So there is one
authored copy of each file and one deployed copy of that same file, and never a
second version drifting quietly out of sync.

Safety rails, because a schedule bills you and lies to you while you are not
looking: every run prices its queries with a free dry run first and refuses
anything over --max-scan-mb, and the finished payload is checked before it is
published. A payload that fails the checks exits non-zero with the live page
untouched, because a job that silently publishes a broken dashboard is worse
than one that fails -- nobody looks at a job that succeeded.

Needs: google-cloud-bigquery, google-cloud-storage (see requirements.txt)
Permissions for the job's service account:
    BigQuery Job User      on the project   (to run the queries)
    Storage Object User    on the bucket     (to read the template, write the page)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import urlopen

# --- configuration -----------------------------------------------------------
# Flags beat environment variables beat these.
PROJECT = os.environ.get("IP_PROJECT", "insightpilot-ai-512716")
BUCKET = os.environ.get("IP_BUCKET", "insightspilot-ai")
OBJECT = os.environ.get("IP_OBJECT", "Article_Insights_AI.html")

# The three authored inputs, as paths in this repo.
TEMPLATE = os.environ.get("IP_TEMPLATE", "dashboard/index.html")
GRAIN_SQL = os.environ.get("IP_SQL", "sql/weekly_grain.sql")
LAG_SQL = os.environ.get("IP_LAG_SQL", "sql/return_lag.sql")

# ...and where the deploy step puts copies of them, for runs with no checkout.
PREFIX = os.environ.get("IP_TEMPLATE_PREFIX", f"gs://{BUCKET}/template")

MARKERS = re.compile(r"/\*DATA_START\*/.*?/\*DATA_END\*/", re.S)

# --- cost and safety rails ---------------------------------------------------
# BigQuery on-demand analysis is priced per tebibyte scanned, after a free
# allowance each month. Both numbers change and vary by region, so they are
# settings rather than facts baked into the code. Check the pricing page.
TIB = 1024 ** 4
USD_PER_TIB = float(os.environ.get("IP_USD_PER_TIB", "6.25"))
FREE_TIB_MONTHLY = float(os.environ.get("IP_FREE_TIB_MONTHLY", "1"))

# A hard ceiling handed to BigQuery itself, so a query that goes wrong is
# refused by the server instead of being billed and then noticed. The real
# queries scan about 8 MB.
MAX_SCAN_MB = float(os.environ.get("IP_MAX_SCAN_MB", "500"))

# The grain is ~11k rows on this dataset. Far below that means the query or the
# dataset changed under us, and the live page should not be overwritten with it.
MIN_ROWS = int(os.environ.get("IP_MIN_ROWS", "1000"))
STALE_DAYS = int(os.environ.get("IP_STALE_DAYS", "21"))


def fmt_bytes(n: int) -> str:
    return f"{n / 1e6:,.1f} MB" if n < 1e9 else f"{n / 1e9:,.2f} GB"


def fmt_cost(n: int) -> str:
    usd = n / TIB * USD_PER_TIB
    return f"${usd:,.2f}" if usd >= 0.01 else f"${usd:.5f}"


def resolve(ref: str) -> str:
    """Repo first, bucket second.

    The repo is the source of truth, so a local checkout always wins. A Vertex
    AI job has no checkout, so it falls through to the deployed copy. One
    authored file, one deployed copy of it, no third version.
    """
    if ref.startswith(("gs://", "http://", "https://")):
        return ref
    if Path(ref).exists():
        return ref
    return f"{PREFIX.rstrip('/')}/{Path(ref).name}"


def read_text(ref: str, project: str) -> str:
    """Read a local path, an https:// URL, or a gs:// URI."""
    if ref.startswith("gs://"):
        from google.cloud import storage

        bucket, _, name = ref[5:].partition("/")
        data = storage.Client(project=project).bucket(bucket).blob(name).download_as_bytes()
        return data.decode("utf-8")
    if ref.startswith(("http://", "https://")):
        return urlopen(ref).read().decode("utf-8")
    return Path(ref).read_text(encoding="utf-8")


def read_sql(ref: str, project: str) -> str:
    """Read a .sql file as one statement, minus any trailing semicolon.

    BigQuery is perfectly happy with a single statement that ends in a
    semicolon, so this is tidiness rather than a fix. What is not tidiness is
    the check on the estimate in main(): the moment SQL here becomes *two*
    statements it is a script, and a dry run of a script reports zero bytes
    processed. Both the cost estimate and the maximum_bytes_billed ceiling would
    then read as "free" and stop protecting anything, silently. Measured, not
    assumed: one statement estimates at 8.4 MB, the same query plus a second
    statement estimates at 0.
    """
    return read_text(ref, project).strip().rstrip(";")


def estimate_bytes(bq, sql: str) -> int:
    """Ask BigQuery what a query would scan, without running it.

    A dry run is free and instant, which makes it the cheapest possible way to
    find out a query got expensive. Worth doing on every run rather than once at
    the start: the SQL is read from a file that anyone can edit.
    """
    from google.cloud import bigquery

    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return bq.query(sql, job_config=cfg).total_bytes_processed or 0


def measure_lag_weeks(bq, sql: str, cap_bytes: int) -> tuple[int, int]:
    from google.cloud import bigquery

    job = bq.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=cap_bytes))
    p90 = list(job.result())[0]["p90_days"] or 21
    weeks = max(1, math.ceil(p90 / 7))
    print(f"          90% of returns arrive within {p90} days -> guard = {weeks} week(s)")
    return weeks, job.total_bytes_processed or 0


def build_payload(rows, lag_weeks: int) -> dict:
    weeks = sorted({r["week_start"] for r in rows})
    sources = sorted({r["traffic_source"] for r in rows})
    categories = sorted({r["category"] for r in rows})

    wi = {w: i for i, w in enumerate(weeks)}
    si = {s: i for i, s in enumerate(sources)}
    ci = {c: i for i, c in enumerate(categories)}

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source_label": "bigquery-public-data.thelook_ecommerce",
        "is_sample": False,
        "lag_weeks": lag_weeks,
        "sources": sources,
        "categories": categories,
        "weeks": [w.isoformat() for w in weeks],
        # Index-encoded to keep the embedded blob small. The same grain written
        # as objects is roughly four times the bytes, and every one of those
        # bytes ships to every viewer on every page load.
        "rows": [
            [
                wi[r["week_start"]],
                si[r["traffic_source"]],
                ci[r["category"]],
                int(r["returned_items"]),
                int(r["total_items"]),
            ]
            for r in rows
        ],
    }


def sanity(payload: dict) -> tuple[list[str], list[str]]:
    """Decide whether this payload is fit to overwrite the live page.

    Anything fatal stops the upload and exits non-zero, which is what a schedule
    alerts on. Warnings are printed and do not block: they describe a page worth
    looking at, not a page worth withholding.
    """
    fatal: list[str] = []
    warn: list[str] = []

    rows = payload.get("rows") or []
    weeks = payload.get("weeks") or []

    if len(rows) < MIN_ROWS:
        fatal.append(f"only {len(rows):,} grain rows, expected at least {MIN_ROWS:,}")
    if len(weeks) < 2:
        fatal.append(f"{len(weeks)} week(s) of data -- nothing to compare")

    returned = sum(r[3] for r in rows)
    total = sum(r[4] for r in rows)
    if total <= 0:
        fatal.append("zero items in the whole grain")
    elif not 0.001 <= returned / total <= 0.5:
        fatal.append(f"overall return rate of {returned / total:.1%} is not plausible")

    if any(r[3] < 0 or r[4] < 0 for r in rows):
        fatal.append("negative counts in the grain")
    if any(r[3] > r[4] for r in rows):
        fatal.append("some segments return more items than they sold")

    lag = payload.get("lag_weeks", 0)
    if len(weeks) < 104 + lag:
        warn.append(
            f"{len(weeks)} weeks is short of the {104 + lag} that two full years plus the "
            f"{lag}-week guard need, so each window will be under a year"
        )
    if weeks:
        age = (date.today() - date.fromisoformat(max(weeks))).days
        if age > STALE_DAYS:
            warn.append(f"newest week is {age} days old -- has the dataset stopped refreshing?")
    if payload.get("is_sample"):
        warn.append("is_sample is true, so the page will say so on screen")

    return fatal, warn


def inject(html: str, payload: dict) -> str:
    blob = json.dumps(payload, separators=(",", ":"))
    # A literal "</" inside the data would close the script tag early.
    blob = blob.replace("</", "<\\/")
    # The replacement is a lambda, not a string. A string replacement reads
    # backslashes in the data as regex escapes, which corrupts any value holding
    # one and then fails loudly only much later.
    return MARKERS.sub(lambda _m: f"/*DATA_START*/{blob}/*DATA_END*/", html, count=1)


def ensure_bucket(project: str, name: str, location: str) -> bool:
    """Create the bucket if it is missing. Returns True if it was created.

    Private by construction rather than by remembering to be careful later:
    uniform bucket-level access means there are no per-object ACLs to leak
    through, and public access prevention set to enforced means a stray allUsers
    grant is refused by the server instead of quietly working. Opening it up
    later is then a deliberate act with a visible switch.
    """
    from google.api_core.exceptions import Conflict, Forbidden
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = client.bucket(name)

    if bucket.exists():
        bucket.reload()
        pap = bucket.iam_configuration.public_access_prevention
        print(f"bucket    gs://{name} already exists (public access prevention: {pap})")
        return False

    bucket.iam_configuration.uniform_bucket_level_access_enabled = True
    bucket.iam_configuration.public_access_prevention = "enforced"
    try:
        client.create_bucket(bucket, location=location)
    except Conflict:
        sys.exit(
            f"the name '{name}' is taken. Bucket names are global across all of "
            f"Google Cloud, not per project -- try '{name}-{project.rsplit('-', 1)[-1]}'."
        )
    except Forbidden as e:
        sys.exit(f"not allowed to create a bucket in {project}: {e}")

    print(f"bucket    created gs://{name} in {location}, private and enforced")
    return True


def upload(html: str, project: str, bucket: str, name: str, generated_at: str) -> str:
    from google.cloud import storage

    blob = storage.Client(project=project).bucket(bucket).blob(name)

    # Both of these matter, and both fail by rendering a broken-looking page
    # rather than an error. Without the charset the browser falls back to a
    # legacy encoding and every minus sign becomes mojibake. Without no-cache
    # the URL never changes, so viewers keep seeing last week's numbers.
    blob.content_type = "text/html; charset=utf-8"
    blob.cache_control = "no-cache, max-age=0"

    # No ACL is touched here. The object inherits the bucket's IAM, so a private
    # bucket stays private. Making it public is a deliberate, separate act.
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    # Stamp when this page was built, so the object can be answered for without
    # downloading and grepping it. This has to be a patch *after* the upload:
    # setting blob.metadata beforehand looks like it works and silently does
    # nothing, because the upload writes a fresh object resource over it.
    blob.metadata = {"generated_at": generated_at}
    blob.patch()
    return f"gs://{bucket}/{name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--bucket", default=BUCKET)
    ap.add_argument("--object", dest="object_", default=OBJECT)
    ap.add_argument("--template", default=TEMPLATE, help="repo path, https:// URL or gs:// URI")
    ap.add_argument("--sql", default=GRAIN_SQL, help="repo path, https:// URL or gs:// URI")
    ap.add_argument("--lag-sql", default=LAG_SQL, help="repo path, https:// URL or gs:// URI")
    ap.add_argument("--data", help="use this JSON payload instead of querying BigQuery")
    ap.add_argument("--lag-weeks", type=int, help="skip the lag query and use this value")
    ap.add_argument("--out", help="also write the finished page here")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument(
        "--estimate-only",
        action="store_true",
        help="dry-run the queries, print what they would cost, change nothing",
    )
    ap.add_argument(
        "--max-scan-mb",
        type=float,
        default=MAX_SCAN_MB,
        help=f"refuse to run if the queries would scan more (default {MAX_SCAN_MB:,.0f})",
    )
    ap.add_argument(
        "--force", action="store_true", help="publish even if the sanity checks fail"
    )
    ap.add_argument(
        "--create-bucket",
        action="store_true",
        help="create the bucket first if it does not exist (private, enforced)",
    )
    ap.add_argument("--location", default=os.environ.get("IP_LOCATION", "us-central1"))
    args = ap.parse_args(argv)

    # Load the template first. If the markers are missing, fail before spending
    # a single byte of query quota.
    template = resolve(args.template)
    print(f"template  {template}")
    html = read_text(template, args.project)
    if not MARKERS.search(html):
        sys.exit("the template has no /*DATA_START*/ .. /*DATA_END*/ markers")

    # Do this before the query, not before the upload: a permissions problem
    # here should surface while the run is still free.
    if args.create_bucket and not args.no_upload:
        ensure_bucket(args.project, args.bucket, args.location)

    scanned = 0

    if args.data:
        print(f"data      {args.data} (BigQuery not touched)")
        payload = json.loads(Path(args.data).read_text(encoding="utf-8"))
    else:
        from google.cloud import bigquery

        bq = bigquery.Client(project=args.project)
        grain_ref, lag_ref = resolve(args.sql), resolve(args.lag_sql)
        grain_sql = read_sql(grain_ref, args.project)
        lag_sql = read_sql(lag_ref, args.project)
        cap = int(args.max_scan_mb * 1e6)

        # Price the work before doing any of it. A dry run costs nothing, and it
        # is the only chance to refuse a query that got expensive.
        planned = estimate_bytes(bq, grain_sql) + estimate_bytes(bq, lag_sql)
        print(f"estimate  {fmt_bytes(planned)} to scan, {fmt_cost(planned)} at ${USD_PER_TIB:g}/TiB")
        # A zero estimate is not good news, it is a broken estimate -- and it
        # would sail straight through the ceiling below. BigQuery reports zero
        # bytes for a multi-statement script, so one stray semicolon in the
        # middle of a .sql file turns both rails off without a word.
        if planned <= 0:
            sys.exit(
                "the dry run reported 0 bytes, which means the cost ceiling cannot "
                "protect this run. Usually the SQL has become more than one "
                "statement -- these files must each hold a single query."
            )
        if planned > cap:
            sys.exit(
                f"refusing to run: {fmt_bytes(planned)} is over the "
                f"{args.max_scan_mb:,.0f} MB ceiling. Raise --max-scan-mb if that is expected."
            )
        if args.estimate_only:
            return 0

        print(f"lag       {lag_ref}")
        if args.lag_weeks:
            lag = args.lag_weeks
        else:
            lag, lag_bytes = measure_lag_weeks(bq, lag_sql, cap)
            scanned += lag_bytes

        print(f"query     {grain_ref}")
        job = bq.query(grain_sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=cap))
        rows = list(job.result())
        scanned += job.total_bytes_processed or 0
        print(f"          {len(rows):,} grain rows")
        payload = build_payload(rows, lag)

    print(
        f"payload   {len(payload['rows']):,} rows | {len(payload['weeks'])} weeks | "
        f"{len(payload['sources'])} sources | {len(payload['categories'])} categories"
    )

    fatal, warn = sanity(payload)
    for w in warn:
        print(f"warning   {w}")
    if fatal:
        for f in fatal:
            print(f"FAILED    {f}")
        if not args.force:
            sys.exit("the live page was left untouched. Pass --force to publish anyway.")
        print("          --force given, publishing anyway")

    if scanned:
        per_year = scanned / TIB * USD_PER_TIB * 52
        print(f"cost      scanned {fmt_bytes(scanned)}, {fmt_cost(scanned)} for this run")
        print(
            f"          weekly, that is ${per_year:,.3f}/year of query -- and the first "
            f"{FREE_TIB_MONTHLY:g} TiB each month is free, so in practice $0"
        )

    html = inject(html, payload)
    print(f"page      {len(html.encode('utf-8')) / 1024:,.0f} KB")

    if args.out:
        out = Path(args.out)
        if out.parent != Path(""):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"wrote     {out}")

    if args.no_upload:
        print("upload    skipped (--no-upload)")
        return 0

    uri = upload(html, args.project, args.bucket, args.object_, payload["generated_at"])
    print(f"uploaded  {uri}")
    print(f"          https://storage.cloud.google.com/{args.bucket}/{args.object_}")
    print("          private, so only signed-in viewers with access can open it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

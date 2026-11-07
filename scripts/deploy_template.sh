#!/usr/bin/env bash
# Deploy this checkout's inputs to the bucket, where the scheduled job reads them.
#
# The repo is the source of truth. A Vertex AI job has no checkout, so it needs
# a copy of the page template and the two queries somewhere it can reach, and
# this is the step that puts them there. Run it after changing any of the three.
#
# Nothing here touches the published page. The template lives under template/
# and the published dashboard sits at the bucket root, so a bad run can
# overwrite the output but never the input it was built from.
#
#   ./scripts/deploy_template.sh                       # uses the defaults below
#   IP_BUCKET=my-bucket ./scripts/deploy_template.sh
#
# Needs gcloud, authenticated as someone with Storage Object User on the bucket.
# On Windows, run the three gcloud commands by hand or use Git Bash.

set -euo pipefail

BUCKET="${IP_BUCKET:-insightspilot-ai}"
PREFIX="gs://${BUCKET}/template"

cd "$(dirname "$0")/.."

for f in dashboard/index.html sql/weekly_grain.sql sql/return_lag.sql; do
  test -f "$f" || { echo "missing $f -- run this from the repo"; exit 1; }
done

# The deployed template is read by the job, never served to a browser, so its
# headers do not matter here. The headers that do matter are set on the
# published page by refresh_dashboard.py.
gcloud storage cp dashboard/index.html "${PREFIX}/index.html"
gcloud storage cp sql/weekly_grain.sql "${PREFIX}/weekly_grain.sql"
gcloud storage cp sql/return_lag.sql   "${PREFIX}/return_lag.sql"

echo
echo "deployed to ${PREFIX}/"
echo "the next scheduled run will pick these up."

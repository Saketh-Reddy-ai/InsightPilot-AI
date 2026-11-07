"""Generate sample grain data shaped exactly like sql/weekly_grain.sql output.

This exists so the dashboard renders before anyone has run the real query.
The notebook overwrites it with real BigQuery rows on the first scheduled run.
"""

import json
import math
import random
from datetime import date, timedelta

random.seed(20261026)

SOURCES = ["Search", "Organic", "Email", "Facebook", "Display"]

CATEGORIES = [
    "Accessories", "Active", "Blazers & Jackets", "Dresses",
    "Fashion Hoodies & Sweatshirts", "Intimates", "Jeans", "Leggings",
    "Maternity", "Outerwear & Coats", "Pants", "Plus", "Shorts", "Skirts",
    "Sleep & Lounge", "Socks", "Suits", "Sweaters", "Swim", "Tops & Tees",
]

# Baseline return propensity per acquisition channel. Paid/display traffic
# converts worse-qualified buyers, so it returns more.
SOURCE_RATE = {
    "Search": 0.075, "Organic": 0.080, "Email": 0.090,
    "Facebook": 0.105, "Display": 0.120,
}

# Fit-sensitive categories return far more than accessories.
CATEGORY_MULT = {
    "Accessories": 0.55, "Socks": 0.50, "Sleep & Lounge": 0.70, "Active": 0.95,
    "Intimates": 0.80, "Tops & Tees": 0.90, "Sweaters": 1.05, "Shorts": 1.00,
    "Leggings": 1.00, "Pants": 1.20, "Jeans": 1.45, "Dresses": 1.55,
    "Skirts": 1.25, "Swim": 1.60, "Suits": 1.40, "Blazers & Jackets": 1.30,
    "Outerwear & Coats": 1.15, "Maternity": 1.10, "Plus": 1.25,
    "Fashion Hoodies & Sweatshirts": 0.85,
}

# Channel mix shifts across the two years: paid grows, search shrinks.
MIX_PRIOR = {"Search": 0.40, "Organic": 0.22, "Email": 0.15, "Facebook": 0.13, "Display": 0.10}
MIX_CURR = {"Search": 0.29, "Organic": 0.20, "Email": 0.16, "Facebook": 0.18, "Display": 0.17}

# Merchandising fixed some sizing guides, so within-segment rates improved a little.
RATE_TREND_CURR = 0.978

CATEGORY_WEIGHT = {c: random.uniform(0.6, 1.4) for c in CATEGORIES}
# Apparel skews to a few big categories.
for big in ("Tops & Tees", "Jeans", "Dresses", "Accessories"):
    CATEGORY_WEIGHT[big] *= 2.3

# 112 = two 52-week windows plus headroom for the return-lag guard.
N_WEEKS = 112
LAG_WEEKS = 3  # returns still in flight for the most recent weeks


def monday(d):
    return d - timedelta(days=d.weekday())


def build():
    last_week = monday(date.today()) - timedelta(weeks=1)
    weeks = [last_week - timedelta(weeks=i) for i in range(N_WEEKS - 1, -1, -1)]

    rows = []
    for wi, wk in enumerate(weeks):
        recent = wi >= N_WEEKS - LAG_WEEKS - 52
        mix = MIX_CURR if recent else MIX_PRIOR
        rate_scale = RATE_TREND_CURR if recent else 1.0

        # Volume grows ~12% a year with a seasonal hump before the holidays.
        growth = 1.0 + 0.12 * (wi / 52.0)
        season = 1.0 + 0.22 * math.sin((wk.timetuple().tm_yday / 365.0) * 2 * math.pi - 1.1)
        week_items = 2600 * growth * season

        # Returns not yet filed for the newest weeks: this is the censoring trap.
        weeks_from_end = (N_WEEKS - 1) - wi
        observed = 1.0 if weeks_from_end >= LAG_WEEKS else (0.30, 0.62, 0.86)[weeks_from_end]

        for si, src in enumerate(SOURCES):
            src_items = week_items * mix[src]
            for ci, cat in enumerate(CATEGORIES):
                share = CATEGORY_WEIGHT[cat] / sum(CATEGORY_WEIGHT.values())
                total = src_items * share * random.uniform(0.82, 1.18)
                total = int(round(total))
                if total <= 0:
                    continue

                rate = SOURCE_RATE[src] * CATEGORY_MULT[cat] * rate_scale
                rate *= random.uniform(0.88, 1.12)
                returned = int(round(total * min(rate, 0.9) * observed))
                rows.append([wi, si, ci, returned, total])

    return {
        "generated_at": date.today().isoformat(),
        "source_label": "SAMPLE DATA - not from BigQuery",
        "is_sample": True,
        "lag_weeks": LAG_WEEKS,
        "sources": SOURCES,
        "categories": CATEGORIES,
        "weeks": [w.isoformat() for w in weeks],
        "rows": rows,
    }


if __name__ == "__main__":
    payload = build()
    out = json.dumps(payload, separators=(",", ":"))
    with open("dashboard/data.json", "w", encoding="utf-8") as fh:
        fh.write(out)

    # Quick sanity read on the story the sample tells.
    weeks, rows = payload["weeks"], payload["rows"]
    cur = [r for r in rows if r[0] >= 52]
    pri = [r for r in rows if r[0] < 52]
    cr = sum(r[3] for r in cur) / sum(r[4] for r in cur)
    pr = sum(r[3] for r in pri) / sum(r[4] for r in pri)
    print(f"rows={len(rows)}  bytes={len(out):,}")
    print(f"prior={pr:.4%}  current={cr:.4%}  delta={(cr - pr) * 100:+.2f}pt")

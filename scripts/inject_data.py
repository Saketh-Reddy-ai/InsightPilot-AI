"""Swap a JSON payload into the dashboard's embedded data block.

The dashboard is one self-contained HTML file with its data baked in between
two markers. Re-running this is idempotent: it replaces whatever sits between
the markers, so the same file is both the template and the output.

    python scripts/inject_data.py dashboard/data.json

The refresh notebook does exactly this, in memory, before uploading.
"""

import json
import re
import sys
from pathlib import Path

PATTERN = re.compile(r"/\*DATA_START\*/.*?/\*DATA_END\*/", re.S)


def inject(html: str, payload: dict) -> str:
    blob = json.dumps(payload, separators=(",", ":"))
    # Can't let a "</script>" inside the data close the tag early.
    blob = blob.replace("</", "<\\/")
    if not PATTERN.search(html):
        raise SystemExit("markers /*DATA_START*/ .. /*DATA_END*/ not found in the HTML")
    # A lambda replacement keeps backslashes in the data from being read as
    # regex escapes. This is the bug that bites everyone who writes re.sub here.
    return PATTERN.sub(lambda _m: f"/*DATA_START*/{blob}/*DATA_END*/", html, count=1)


def main() -> None:
    data_path = Path(sys.argv[1] if len(sys.argv) > 1 else "dashboard/data.json")
    html_path = Path(sys.argv[2] if len(sys.argv) > 2 else "dashboard/index.html")

    payload = json.loads(data_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")
    html_path.write_text(inject(html, payload), encoding="utf-8")

    rows = len(payload.get("rows", []))
    kb = html_path.stat().st_size / 1024
    print(f"injected {rows:,} rows into {html_path}  ({kb:,.0f} KB)")


if __name__ == "__main__":
    main()

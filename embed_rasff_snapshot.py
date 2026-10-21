#!/usr/bin/env python3
"""Embed a compact RASFF API snapshot in the standalone FoodWatch HTML page."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "rasff_notifications.json"
PAGE = ROOT / "index.html"
SCRIPT_ID = 'id="rasff-snapshot"'
FIELDS = (
    "NOTIF_ID",
    "NOTIF_DATE",
    "PRODUCT_NAME",
    "PRODUCT_CATEGORY_DESC",
    "NOTIF_SUBJECT",
    "NOTIFYNG_COUNTRY_DESC",
    "HAZARD_CATEGORY_NAME",
    "ORIGIN_COUNTRY_DESC",
)
METADATA = (
    "source",
    "sourceUrl",
    "apiUrl",
    "apiVersion",
    "collectedAt",
    "recordsCollected",
    "dateCoverage",
)


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    compact = {key: data[key] for key in METADATA if key in data}
    compact["records"] = [
        {key: row[key] for key in FIELDS if key in row and row[key] is not None}
        for row in data.get("records", [])
    ]
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")

    page = PAGE.read_text(encoding="utf-8")
    pattern = r'(<script id="rasff-snapshot" type="application/json">).*?(</script>)'
    updated, count = re.subn(
        pattern,
        lambda match: match.group(1) + payload + match.group(2),
        page,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise SystemExit("网页中未找到 rasff-snapshot 插入位置；请检查 index.html")
    PAGE.write_text(updated, encoding="utf-8")
    print(f"已将 {len(compact['records']):,} 条 RASFF 记录嵌入 {PAGE}")


if __name__ == "__main__":
    main()

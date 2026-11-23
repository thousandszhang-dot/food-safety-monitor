#!/usr/bin/env python3
"""Fetch public RASFF notifications from the European Commission DG SANTE API."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

API_URL = "https://api.datalake.sante.service.ec.europa.eu/rasff/irasff-general-info-view"
SOURCE_URL = "https://webgate.ec.europa.eu/rasff-window/screen/search"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "rasff_notifications.json"
API_VERSION = "v1.2"


def period_windows(start: date, end: date, days: int = 14):
    cursor = datetime.combine(start, datetime_time.min, tzinfo=timezone.utc)
    final = datetime.combine(end, datetime_time(23, 59, 59), tzinfo=timezone.utc)
    while cursor <= final:
        window_end = min(cursor + timedelta(days=days) - timedelta(seconds=1), final)
        yield cursor, window_end
        cursor = window_end + timedelta(seconds=1)


def get_records(start: datetime, end: datetime, retries: int = 4) -> list[dict]:
    params = {
        "format": "json",
        "NETWORK_DESC": "RASFF",
        "NOTIF_DATE_FROM": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "NOTIF_DATE_TO": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api-version": API_VERSION,
    }
    # DG SANTE's API expects literal RFC3339 colons in date filters.
    url = f"{API_URL}?{urlencode(params, safe=':')}"
    headers = {"Content-Type": "application/json"}
    records: list[dict] = []
    next_url: Optional[str] = url
    visited: set[str] = set()

    while next_url:
        parsed = urlparse(next_url)
        if parsed.scheme != "https" or parsed.netloc != urlparse(API_URL).netloc:
            raise RuntimeError(f"API 返回了非预期分页地址：{next_url}")
        if next_url in visited:
            raise RuntimeError("API 分页地址重复，停止以避免循环")
        visited.add(next_url)

        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                request = Request(next_url, headers=headers)
                with urlopen(request, timeout=120) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                    raise ValueError("API 响应未包含 value 数组")
                records.extend(payload["value"])
                link = payload.get("nextLink")
                next_url = link.strip() if isinstance(link, str) and link.strip() else None
                break
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"查询 {start} 至 {end} 失败：{last_error}") from last_error

    return records


def save_snapshot(output: Path, by_id: dict[str, dict]) -> None:
    records = sorted(by_id.values(), key=lambda row: (row.get("NOTIF_DATE", ""),
                                                       str(row.get("NOTIF_ID", ""))), reverse=True)
    result = {
        "source": "European Commission DG SANTE iRASFF API",
        "sourceUrl": SOURCE_URL,
        "apiUrl": API_URL,
        "apiVersion": API_VERSION,
        "collectedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "recordsCollected": len(records),
        "dateCoverage": {
            "from": min((str(row.get("NOTIF_DATE", ""))[:10] for row in records if row.get("NOTIF_DATE")), default=None),
            "to": max((str(row.get("NOTIF_DATE", ""))[:10] for row in records if row.get("NOTIF_DATE")), default=None),
        },
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="起始日期 YYYY-MM-DD；首次抓取默认 2020-01-01")
    parser.add_argument("--until", help="结束日期 YYYY-MM-DD；默认今天 UTC")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overlap-days", type=int, default=35,
                        help="增量同步回看天数，默认 35 天")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    old = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    by_id = {str(row["NOTIF_ID"]): row for row in old.get("records", [])
             if row.get("NOTIF_ID") is not None}

    today = date.fromisoformat(args.until) if args.until else datetime.now(timezone.utc).date()
    if args.since:
        start = date.fromisoformat(args.since)
    elif by_id:
        dated = [date.fromisoformat(str(row["NOTIF_DATE"])[:10])
                 for row in by_id.values() if row.get("NOTIF_DATE")]
        last_date = max(dated, default=date(2020, 1, 1))
        start = date.fromordinal(max(date(2020, 1, 1).toordinal(),
                                     last_date.toordinal() - args.overlap_days))
    else:
        start = date(2020, 1, 1)

    windows = list(period_windows(start, today))
    # Keep the public endpoint load modest while avoiding a long sequential backfill.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(get_records, first, last): (first, last)
                   for first, last in windows}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            first, last = futures[future]
            rows = future.result()
            for row in rows:
                if row.get("NOTIF_ID") is not None:
                    by_id[str(row["NOTIF_ID"])] = row
            save_snapshot(output, by_id)
            print(f"RASFF {first:%Y-%m-%d}..{last:%Y-%m-%d}: fetched {len(rows)}; "
                  f"completed {index}/{len(windows)}; unique total {len(by_id)}", flush=True)
    print(f"Saved {len(by_id)} RASFF records to {output}")


if __name__ == "__main__":
    main()

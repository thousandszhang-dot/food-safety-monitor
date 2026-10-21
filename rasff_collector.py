#!/usr/bin/env python3
"""Fetch public RASFF notifications from the European Commission DG SANTE API."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

API_URL = "https://api.datalake.sante.service.ec.europa.eu/rasff/irasff-general-info-view"
SOURCE_URL = "https://webgate.ec.europa.eu/rasff-window/screen/search"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "rasff_notifications.json"
API_VERSION = "v1.1"


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
    subscription_key = os.environ.get("SANTE_SUBSCRIPTION_KEY", "").strip()
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("需要系统 curl 命令来读取 DG SANTE API")
    last_error = None
    for attempt in range(retries):
        result = subprocess.run(
            [curl, "--fail", "--silent", "--show-error", "--location",
             "--retry", "3", "--retry-all-errors", "--retry-delay", "1",
             "--max-time", "120", *( ["--header", f"Ocp-Apim-Subscription-Key: {subscription_key}"] if subscription_key else [] ), url],
            capture_output=True,
        )
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
                records = payload.get("value") if isinstance(payload, dict) else None
                if not isinstance(records, list):
                    raise ValueError("API 响应未包含 value 数组")
                # Observed response cap is 100. Split capped windows so results are not silently lost.
                if len(records) >= 100:
                    if (end - start).total_seconds() <= 60:
                        raise RuntimeError("一分钟窗口仍返回 100 条，需先确认 API 分页规则")
                    midpoint = (start + (end - start) / 2).replace(microsecond=0)
                    right_start = midpoint + timedelta(seconds=1)
                    print(f"  API reached 100-row cap; splitting at {midpoint.isoformat()}", flush=True)
                    return get_records(start, midpoint, retries) + get_records(right_start, end, retries)
                return records
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
        else:
            last_error = result.stderr.decode("utf-8", "replace")[:500]
        if attempt + 1 < retries:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"查询 {start} 至 {end} 失败：{last_error}")


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

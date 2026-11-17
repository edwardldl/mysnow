#!/usr/bin/env python3
"""Collect resort-reported snowfall from the SnoCountry JSON API.

For every resort in the MySnow catalogue (data/tracking/registry.json)
with a SnoCountry id (data/tracking/resort-reports/snocountry_ids.json),
fetch its current snow report, store the raw response verbatim under
data/tracking/resort-reports/raw/<collection UTC date>/<slug>.json, and
append the parsed record (see slr_calibration.resort_reports) to
data/tracking/resort-reports/reports-<collection UTC date>.jsonl.
Raw responses are always kept so parsing can be redone offline.

Access notes: SnoCountry's production API keys are purchased; the
publicly documented sample key ("SnoCountry.example") is provided by
SnoCountry for development/evaluation and caps multi-resort responses
at 3 resorts, so this collector uses the documented single-resort
request form (their guidelines place no limit on single ski area
requests). Requests are spaced politely (default 1.1 s). A standing
production monitor should use a purchased key -- see the README in
data/tracking/resort-reports/.

Usage:
    collect_resort_reports.py [--api-key KEY] [--only slug,slug]
                              [--delay SECONDS] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration.resort_reports import parse_snocountry_item  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "data" / "tracking" / "registry.json"
IDS_PATH = REPO_ROOT / "data" / "tracking" / "resort-reports" / "snocountry_ids.json"
OUT_DIR = REPO_ROOT / "data" / "tracking" / "resort-reports"
API_URL = (
    "http://feeds.snocountry.net/getSnowReport.php"
    "?apiKey={key}&ids={resort_id}&output=json"
)
USER_AGENT = "MySnow resort-report collector (development; github.com/edwardldl/mysnow)"


def fetch_one(api_key: str, resort_id: str) -> dict:
    url = API_URL.format(key=api_key, resort_id=resort_id)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default="SnoCountry.example")
    parser.add_argument("--only", default="", help="comma-separated resort slugs")
    parser.add_argument("--delay", type=float, default=1.1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    registry = json.loads(REGISTRY_PATH.read_text())
    resorts = {r["resort_slug"]: r for r in registry["resorts"]}
    id_map = json.loads(IDS_PATH.read_text())

    slugs = sorted(id_map)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        slugs = [s for s in slugs if s in wanted]
    if args.limit:
        slugs = slugs[: args.limit]

    collected_at = datetime.now(timezone.utc)
    date_tag = collected_at.strftime("%Y-%m-%d")
    raw_dir = OUT_DIR / "raw" / date_tag
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_path = OUT_DIR / f"reports-{date_tag}.jsonl"

    fetched = parsed_ok = with_snow = failed = 0
    failures: list[str] = []
    with parsed_path.open("a") as parsed_out:
        for index, slug in enumerate(slugs):
            if index:
                time.sleep(args.delay)
            entry = id_map[slug]
            resort = resorts.get(slug)
            if resort is None:
                failures.append(f"{slug}: not in registry")
                failed += 1
                continue
            try:
                payload = fetch_one(args.api_key, entry["snocountry_id"])
            except Exception as exc:  # per-resort failure: record, continue
                failures.append(f"{slug}: fetch failed: {exc}")
                failed += 1
                continue
            fetched += 1
            raw_path = raw_dir / f"{slug}.json"
            raw_path.write_text(json.dumps(payload, indent=1))
            items = payload.get("items") or []
            if not items:
                failures.append(f"{slug}: empty items in response")
                failed += 1
                continue
            record = parse_snocountry_item(
                items[0],
                resort_slug=slug,
                resort_name=resort["pilot_name"],
                timezone_name=resort["timezone"],
                collected_at_utc=datetime.now(timezone.utc),
                raw_ref=f"raw/{date_tag}/{slug}.json",
            )
            if record is None:
                failures.append(f"{slug}: no usable reportDateTime")
                failed += 1
                continue
            parsed_out.write(json.dumps(record) + "\n")
            parsed_ok += 1
            if record["new_snow_24h_cm"]["mid"] is not None:
                with_snow += 1

    print(
        f"collection {date_tag}: attempted={len(slugs)} fetched={fetched} "
        f"parsed={parsed_ok} with_24h_snow={with_snow} failed={failed}"
    )
    for failure in failures:
        print(f"  ! {failure}")
    print(f"parsed records appended to {parsed_path}")
    return 0 if fetched else 1


if __name__ == "__main__":
    raise SystemExit(main())

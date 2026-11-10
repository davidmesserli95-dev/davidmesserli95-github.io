"""Pull runs from the Garmin Connect Personal API and save them to data/runs.json.

Runs in a GitHub Action (see .github/workflows/sync-garmin.yml). The API key comes
from the GARMIN_PERSONAL_API_KEY environment variable, which the Action fills from
a repository secret. Only the fields the website needs are saved: date, start time,
run type, distance and duration. No GPS, heart rate or health data is written,
because everything in this repository is public.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator
from garmin_personal_api.errors import GarminPersonalApiError

PLAN_START = date(2026, 11, 9)  # Monday of week 1 of the marathon plan
OUT_FILE = Path(__file__).resolve().parent.parent / "data" / "runs.json"
PAGE_SIZE = 100
METERS_PER_MILE = 1609.344


def type_key(activity: dict) -> str:
    """Return Garmin's activity type key, e.g. 'running' or 'treadmill_running'."""
    for field in ("activityType", "activityTypeDTO"):
        value = activity.get(field)
        if isinstance(value, dict) and value.get("typeKey"):
            return str(value["typeKey"])
        if isinstance(value, str):
            return value
    return ""


def is_run(activity: dict) -> bool:
    key = type_key(activity)
    return "run" in key and "walk" not in key


def to_run(activity: dict) -> dict | None:
    """Keep only what the website shows. Returns None if the essentials are missing."""
    start = activity.get("startTimeLocal") or ""
    distance_m = activity.get("distance")
    duration_s = activity.get("duration") or activity.get("movingDuration")
    if not start or not distance_m:
        return None
    start = start.replace("T", " ")
    return {
        "id": activity.get("activityId"),
        "date": start[:10],
        "start": start[11:16],
        "type": type_key(activity),
        "mi": round(float(distance_m) / METERS_PER_MILE, 2),
        "sec": round(float(duration_s)) if duration_s else None,
    }


def fetch_activities(client: GarminPersonalApiClient, start: date, end: date) -> list[dict]:
    activities: list[dict] = []
    offset = 0
    while True:
        page = client.search_activities(
            start=offset, limit=PAGE_SIZE, start_date=start, end_date=end
        )
        if isinstance(page, dict):  # tolerate a wrapped response
            page = page.get("activityList") or page.get("activities") or []
        if not page:
            break
        activities.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return activities


def main() -> int:
    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
    if not api_key:
        print("GARMIN_PERSONAL_API_KEY is not set. Add it as a repository secret.", file=sys.stderr)
        return 1

    today = date.today()
    try:
        with PersonalApiAuthenticator() as auth:
            token = auth.obtain_token(api_key)
        with GarminPersonalApiClient(token.access_token) as client:
            activities = fetch_activities(client, PLAN_START, today)
    except GarminPersonalApiError as exc:
        print(f"Garmin request failed: {exc}", file=sys.stderr)
        print(
            "If this says 401 or 403, the API key may have expired or been deleted. "
            "Create a new key in Garmin Connect and update the repository secret.",
            file=sys.stderr,
        )
        return 1

    if activities:
        # Field names only (no values), to help debug if Garmin changes its format.
        print("Fields in the first activity:", ", ".join(sorted(activities[0].keys())))

    runs = [r for r in (to_run(a) for a in activities if is_run(a)) if r]
    runs.sort(key=lambda r: (r["date"], r["start"]))
    skipped = len(activities) - len(runs)
    print(f"Found {len(activities)} activities since {PLAN_START}; saved {len(runs)} runs "
          f"({skipped} non-runs or incomplete skipped).")

    previous = None
    if OUT_FILE.exists():
        try:
            previous = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except ValueError:
            previous = None
    if previous and previous.get("runs") == runs:
        print("No new runs; leaving data/runs.json unchanged.")
        return 0

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(
            {
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "planStart": PLAN_START.isoformat(),
                "runs": runs,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT_FILE.relative_to(OUT_FILE.parent.parent)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
weekly_leads_framework.py — Weekly QL/DQL leads report generator.

Pulls leads from Monday CRM for two periods, classifies each as QL/DQL,
merges with Rick.AI attribution CSV, and renders a Markdown report.

Usage:
  python3 weekly_leads_framework.py \\
    --week 2026-W16 \\
    --rick-csv ~/Downloads/typhoon.coffee_bq_widget_263366\\ \\(2\\).csv \\
    --output weekly_reports/2026-W16.md

  # Auto-detect latest Rick CSV in ~/Downloads:
  python3 weekly_leads_framework.py --week 2026-W16

  # Custom date range instead of ISO week:
  python3 weekly_leads_framework.py \\
    --from 2026-04-13 --to 2026-04-19
"""

import argparse
import csv
import os
import sys
import glob
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Load .env before importing monday module
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Re-use Monday API helpers from existing script
from monday_leads_report import (
    fetch_items_by_date,
    fetch_updates_for_item,
    build_channel,
    parse_dt,
)
from qualify import classify_lead
from report import render_report

PRAGUE = ZoneInfo("Europe/Prague")
DEFAULT_BOARD_ID = "1231414321"
DEFAULT_BOARD_URL = "https://typhoon-roasters-new.monday.com/boards/1231414321/pulses"

RICK_METRIC_SESSIONS = "сессии last click"
RICK_METRIC_USERS = "пользователи last click"
RICK_METRIC_LEADS_WEB = "лиды оформил заказ через вебсайт last click"

# Maps Monday-derived channel (first line of build_channel()) to Rick channel name.
# Rick uses Russian/mixed names; Monday uses English.
CHANNEL_MAP: dict[str, str] = {
    "google paid search": "google ads",
    "paid search": "google ads",
    "meta paid": "instagram ads",
    "organic (google)": "google organic",
    "organic (yandex)": "yandex organic",
    "organic (bing)": "other organic",
    "email": "email рассылка",
    "referral": "other channels",
    "social (instagram)": "instagram referral",
    "social (facebook)": "facebook referral",
    "social (youtube)": "youtube",
    "direct / unknown": "direct type-in",
    "direct": "direct type-in",
}


def _canonical_channel(full_channel: str) -> str:
    """Map Monday full channel string (with campaign lines) to Rick channel name."""
    first_line = full_channel.split("\n")[0].strip().lower()
    # Try exact match first
    if first_line in CHANNEL_MAP:
        return CHANNEL_MAP[first_line]
    # Try prefix matches
    for key, val in CHANNEL_MAP.items():
        if first_line.startswith(key):
            return val
    # Strip parenthetical and retry
    base = first_line.split("(")[0].strip()
    if base in CHANNEL_MAP:
        return CHANNEL_MAP[base]
    return full_channel.split("\n")[0].strip()  # fallback: first line as-is


# ── Date helpers ──────────────────────────────────────────────────────────────

def iso_week_to_range(week_str: str):
    """'2026-W16' → (mon_dt, sun_dt) in UTC. Uses ISO 8601 week numbering."""
    year, w = week_str.split("-W")
    # %G-W%V-%u = ISO year, ISO week number, ISO weekday (1=Mon)
    mon = datetime.strptime(f"{year}-W{int(w):02d}-1", "%G-W%V-%u")
    mon = mon.replace(tzinfo=timezone.utc)
    sun = mon + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return mon, sun


def prev_week_range(from_dt: datetime, to_dt: datetime):
    """Return the same-length range one week earlier."""
    delta = timedelta(weeks=1)
    return from_dt - delta, to_dt - delta


# ── Rick CSV parser ───────────────────────────────────────────────────────────

def _auto_detect_rick_csv() -> str | None:
    """Find the most recently modified BQ widget CSV in ~/Downloads."""
    pattern = os.path.expanduser("~/Downloads/typhoon.coffee_bq_widget_*.csv")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def parse_rick_csv(path: str) -> tuple[dict, dict, str, str]:
    """Parse Rick.AI BQ widget CSV.

    The CSV has columns: channel, campaign, keyword, metric, period_cur, period_prev
    All rows are filled forward (channel/campaign blank after first row of a group).

    Returns:
        rick_cur   : channel → {sessions, users}
        rick_prev  : channel → {sessions, users}
        period_cur_label : str (e.g. "13 .. 19 апр")
        period_prev_label: str (e.g. "6 .. 12 апр")
    """
    rick_cur: dict = defaultdict(lambda: {"sessions": 0.0, "users": 0.0})
    rick_prev: dict = defaultdict(lambda: {"sessions": 0.0, "users": 0.0})
    period_cur_label = period_prev_label = ""

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)

        if len(header) >= 6:
            period_cur_label = header[4].strip()
            period_prev_label = header[5].strip()

        prev_channel = ""
        for row in reader:
            if len(row) < 6:
                continue
            ch = row[0].strip() if row[0].strip() else prev_channel
            if row[0].strip():
                prev_channel = ch

            # Skip aggregation rows
            if ch == "(not set)":
                continue

            metric = row[3].strip()
            try:
                v_cur = float(row[4]) if row[4].strip() else 0.0
                v_prev = float(row[5]) if row[5].strip() else 0.0
            except ValueError:
                continue

            if metric == RICK_METRIC_SESSIONS:
                rick_cur[ch]["sessions"] += v_cur
                rick_prev[ch]["sessions"] += v_prev
            elif metric == RICK_METRIC_USERS:
                rick_cur[ch]["users"] += v_cur
                rick_prev[ch]["users"] += v_prev

    return dict(rick_cur), dict(rick_prev), period_cur_label, period_prev_label


# ── Monday fetcher ────────────────────────────────────────────────────────────

def fetch_and_classify(from_dt: datetime, to_dt: datetime, api_key: str) -> list:
    """Fetch leads from Monday for a period and classify each as QL/DQL."""
    print(f"  Загружаю лиды: {from_dt.date()} → {to_dt.date()}")
    items = fetch_items_by_date(DEFAULT_BOARD_ID, from_dt, to_dt, api_key)

    if not items:
        print("  Лидов не найдено.")
        return []

    print(f"  Найдено: {len(items)} лидов. Загружаю переписку…")
    result = []
    for i, item in enumerate(items, 1):
        print(f"    [{i}/{len(items)}] {item['name']}", end="\r", flush=True)
        updates = fetch_updates_for_item(item["id"], api_key)
        classification = classify_lead(item, updates)
        channel_full = build_channel(item)
        channel_rick = _canonical_channel(channel_full)
        result.append({
            "id": item["id"],
            "name": item["name"],
            "created_at": item.get("created_at", ""),
            "channel": channel_rick,          # Rick-canonical, used in tables
            "channel_full": channel_full,      # Full detail, used in appendix
            "classification": classification,
            "updates_count": len(updates),
        })

    print()  # newline after \r progress
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Weekly QL/DQL leads report")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--week", metavar="YYYY-WNN", help="ISO week, e.g. 2026-W16")
    grp.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD")
    parser.add_argument("--rick-csv", metavar="PATH", help="Rick.AI BQ widget CSV (both periods)")
    parser.add_argument("--board", default=DEFAULT_BOARD_ID)
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--api-key")
    return parser.parse_args()


def main():
    args = parse_args()

    api_key = args.api_key or os.environ.get("MONDAY_API_KEY")
    if not api_key:
        print("ERROR: MONDAY_API_KEY не найден. Укажите --api-key или добавьте в .env", file=sys.stderr)
        sys.exit(1)

    # Determine date ranges
    if args.week:
        cur_from, cur_to = iso_week_to_range(args.week)
        prev_from, prev_to = prev_week_range(cur_from, cur_to)
        week_label = args.week
    elif args.from_date:
        cur_from = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        cur_to = (
            datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc) + timedelta(hours=23, minutes=59, seconds=59)
            if args.to_date
            else datetime.now(timezone.utc)
        )
        prev_from, prev_to = prev_week_range(cur_from, cur_to)
        week_label = f"{cur_from.date()}_{cur_to.date()}"
    else:
        # Default: current ISO week
        today = datetime.now(timezone.utc)
        iso_week = today.strftime("%Y-W%W")
        cur_from, cur_to = iso_week_to_range(iso_week)
        prev_from, prev_to = prev_week_range(cur_from, cur_to)
        week_label = iso_week

    # Rick CSV
    rick_path = args.rick_csv or _auto_detect_rick_csv()
    if not rick_path:
        print("WARN: Rick.AI CSV не найден. Таблица каналов будет без сессий.", file=sys.stderr)
        rick_cur = rick_prev = {}
        period_cur_label = str(cur_from.date())
        period_prev_label = str(prev_from.date())
    else:
        print(f"Rick.AI CSV: {rick_path}")
        rick_cur, rick_prev, period_cur_label, period_prev_label = parse_rick_csv(rick_path)

    # Fetch and classify both periods
    print(f"\n=== Период ТЕКУЩИЙ ({period_cur_label}) ===")
    cur_details = fetch_and_classify(cur_from, cur_to, api_key)

    print(f"\n=== Период ПРЕДЫДУЩИЙ ({period_prev_label}) ===")
    prev_details = fetch_and_classify(prev_from, prev_to, api_key)

    # Render
    md = render_report(
        period_cur=period_cur_label,
        period_prev=period_prev_label,
        cur_lead_details=cur_details,
        prev_lead_details=prev_details,
        rick_cur=rick_cur,
        rick_prev=rick_prev,
        board_url=DEFAULT_BOARD_URL,
    )

    # Output
    out_path = args.output or f"weekly_reports/{week_label}.md"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(md, encoding="utf-8")
    print(f"\n✓ Отчёт сохранён: {out_path}")
    print(f"  QL: {sum(1 for ld in cur_details if ld['classification']['status'] == 'QL')}"
          f" / {len(cur_details)} лидов")


if __name__ == "__main__":
    main()

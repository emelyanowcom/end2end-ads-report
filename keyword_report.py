#!/usr/bin/env python3
"""
keyword_report.py — Google Ads keyword performance report.

Data sources:
  - Rick.AI CSV  : sessions, leads (all types) per keyword+campaign
  - Monday CRM   : QL/DQL classification per lead (matched via utm_term)
  - Outline API  : 3-week rolling history (for disqualification rule)
  - GA4 API      : campaign-level sessions (keyword level: auto-tagging not working)
  - Google Ads API: impressions, CTR, clicks, cost — NOT YET CONFIGURED
                   (need: developer_token + customer_id + SA access to Ads account)

Usage:
  python3 keyword_report.py --week 2026-W16
  python3 keyword_report.py --week 2026-W16 --rick-csv PATH/TO/typhoon.coffee_bq_widget.csv
  python3 keyword_report.py --from 2026-04-13 --to 2026-04-19 --no-outline
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote_plus

# Load .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from monday_leads_report import fetch_items_by_date, fetch_updates_for_item, get_col
from qualify import classify_lead, KSF_GROUP_IDS
from outline_client import read_keyword_history, append_week
from keyword_decisions import (
    make_decisions,
    build_history_rows,
    DECISION_LABELS,
    _key,
    MIN_CLICKS_FOR_SIGNAL,
)

def _prev_week(week_str: str) -> str:
    """Return ISO week string for the week before week_str."""
    year, w = week_str.split("-W")
    mon = datetime.strptime(f"{year}-W{int(w):02d}-1", "%G-W%V-%u")
    prev_mon = mon - timedelta(weeks=1)
    return prev_mon.strftime("%G-W%V")

DEFAULT_BOARD_ID = "1231414321"

# Rick CSV metric names
RICK_SESSIONS  = "сессии last click"
RICK_LEADS_ALL = "лиды оформил заказ любым способом last click"
RICK_LEADS_WEB = "лиды оформил заказ через вебсайт last click"

# Channels to highlight in the channel summary (others are grouped as "прочие")
TRACKED_CHANNELS = ["google ads", "direct type-in", "google organic", "yandex organic",
                    "other organic", "other channels"]


# ── Date helpers ──────────────────────────────────────────────────────────────

def iso_week_to_range(week_str: str):
    year, w = week_str.split("-W")
    mon = datetime.strptime(f"{year}-W{int(w):02d}-1", "%G-W%V-%u")
    mon = mon.replace(tzinfo=timezone.utc)
    sun = mon + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return mon, sun


# ── Rick CSV: keyword-level sessions + leads ──────────────────────────────────

def parse_rick_keywords(path: str | None) -> list[dict]:
    """Parse Rick CSV for keyword-level data (current period only).

    Returns list of {keyword, campaign, sessions, leads_all, leads_web}.
    Only Google Ads rows with non-placeholder keywords.
    """
    if not path:
        files = glob.glob(os.path.expanduser("~/Downloads/typhoon.coffee_bq_widget_*.csv"))
        if not files:
            return []
        path = max(files, key=os.path.getmtime)
        print(f"  Rick CSV (auto-detect): {path}")

    data: dict[str, dict] = {}
    prev_ch = prev_camp = prev_kw = ""

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 5:
                continue
            ch = row[0].strip() or prev_ch
            camp_full = row[1].strip() or prev_camp
            kw = row[2].strip() or prev_kw
            if row[0].strip():
                prev_ch = ch
            if row[1].strip():
                prev_camp = camp_full
            if row[2].strip():
                prev_kw = kw

            if "google ads" not in ch.lower():
                continue
            if kw in ("", "(not set)", "(not provided)"):
                continue

            camp = camp_full.split(" · ", 1)[1] if " · " in camp_full else camp_full
            metric = row[3].strip()
            try:
                v_cur = float(row[4]) if row[4].strip() else 0.0
            except ValueError:
                continue

            key = f"{kw.lower().strip()}||{camp.lower().strip()}"
            if key not in data:
                data[key] = {
                    "keyword": kw,
                    "campaign": camp,
                    "sessions": 0.0,
                    "leads_all": 0.0,
                    "leads_web": 0.0,
                    "impressions": None,
                    "clicks": None,
                    "cost": None,
                }

            if metric == RICK_SESSIONS:
                data[key]["sessions"] += v_cur
            elif metric == RICK_LEADS_ALL:
                data[key]["leads_all"] += v_cur
            elif metric == RICK_LEADS_WEB:
                data[key]["leads_web"] += v_cur

    return list(data.values())


def parse_rick_channel_summary(path: str | None) -> list[dict]:
    """Parse Rick CSV and return aggregated web leads + sessions per channel.

    Ignores (not set) rows (date subtotals). Uses web-form leads as the lead count.
    Returns list of {channel, sessions, leads_web} sorted by leads_web desc.
    """
    if not path:
        files = glob.glob(os.path.expanduser("~/Downloads/typhoon.coffee_bq_widget_*.csv"))
        if not files:
            return []
        path = max(files, key=os.path.getmtime)

    channels: dict[str, dict] = {}
    prev_ch = ""

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) < 5:
                continue
            ch = row[0].strip() or prev_ch
            if row[0].strip():
                prev_ch = ch
            if ch == "(not set)":
                continue
            metric = row[3].strip()
            try:
                v = float(row[4]) if row[4].strip() else 0.0
            except ValueError:
                continue

            if ch not in channels:
                channels[ch] = {"channel": ch, "sessions": 0.0, "leads_web": 0.0}
            if metric == RICK_SESSIONS:
                channels[ch]["sessions"] += v
            elif metric == RICK_LEADS_WEB:
                channels[ch]["leads_web"] += v

    result = sorted(channels.values(), key=lambda x: (-x["leads_web"], -x["sessions"]))
    return result


# ── Monday: QL + KSF per keyword ─────────────────────────────────────────────

def fetch_monday_keywords(from_dt, to_dt, api_key) -> tuple[dict, dict, list]:
    """Returns (ql_by_key, ksf_by_key, lead_details)."""
    print(f"  Monday: {from_dt.date()} → {to_dt.date()}")
    items = fetch_items_by_date(DEFAULT_BOARD_ID, from_dt, to_dt, api_key)

    if not items:
        print("  Лидов не найдено.")
        return {}, {}, []

    print(f"  {len(items)} лидов. Классифицирую…")

    ql_by_key: dict[str, int] = defaultdict(int)
    leads_by_key: dict[str, int] = defaultdict(int)
    ksf_by_key: dict[str, bool] = {}
    details = []

    for i, item in enumerate(items, 1):
        print(f"    [{i}/{len(items)}] {item['name']}", end="\r", flush=True)
        updates = fetch_updates_for_item(item["id"], api_key)
        to_lost = get_col(item, "to_lost_leads3").strip()
        clf = classify_lead(item, updates, to_lost_leads3=to_lost)

        utm_term = unquote_plus(get_col(item, "utm_term").strip())
        utm_campaign = get_col(item, "utm_campaign").strip()
        group_id = item.get("group", {}).get("id", "")
        is_ksf = group_id in KSF_GROUP_IDS

        # Skip placeholder values
        if utm_term.lower() in ("undefined", "null", "none", ""):
            utm_term = ""

        # Duplicates are excluded from all counts
        is_skip = clf["status"] == "SKIP"
        is_ql = clf["status"] == "QL"

        if utm_term and not is_skip:
            k = _key(utm_term, utm_campaign)
            leads_by_key[k] += 1          # all leads from Monday (QL + DQL)
            if is_ql:
                ql_by_key[k] += 1
            if is_ksf:
                ksf_by_key[k] = True

        details.append({
            "id": item["id"],
            "name": item["name"],
            "utm_term": utm_term,
            "utm_campaign": utm_campaign,
            "group_id": group_id,
            "is_ksf": is_ksf,
            "to_lost_leads3": to_lost,
            "classification": clf,
        })

    print()
    return dict(ql_by_key), dict(leads_by_key), ksf_by_key, details


# ── Disqualification rules ────────────────────────────────────────────────────

# Cost-based criteria (require Google Ads data):
DISQ_COST_ZERO_CONV = 120.0   # €120 with 0 conversions
DISQ_COST_ONE_CONV = 180.0    # €180 with only 1 conversion
# History-based criteria (no cost needed):
DISQ_WEEKS_DQL = 3            # consecutive weeks DQL, never QL
DISQ_MIN_SESSIONS_WEEK = 10   # min sessions/week to count as "enough data" (proxy for traffic)

def _check_disqualify(
    keyword: str,
    campaign: str,
    cur: dict,
    ql: int,
    history: dict[str, list[dict]],
    current_week: str,
) -> tuple[bool, str]:
    """Return (should_disqualify, reason). Uses cost rules if cost available, else sessions."""
    cost = cur.get("cost")  # None = not available

    # Cost-based rules (only when we have actual cost data)
    if cost is not None:
        leads_all = cur.get("leads_all", 0)
        if leads_all == 0 and cost >= DISQ_COST_ZERO_CONV:
            return True, f"€{cost:.0f} потрачено, 0 конверсий (порог €{DISQ_COST_ZERO_CONV:.0f})"
        if leads_all <= 1 and cost >= DISQ_COST_ONE_CONV:
            return True, f"€{cost:.0f} потрачено, ≤1 конверсия (порог €{DISQ_COST_ONE_CONV:.0f})"

    # 3-week history rule: need enough traffic data
    sessions = cur.get("sessions", 0)
    if sessions < DISQ_MIN_SESSIONS_WEEK and ql == 0:
        return False, ""  # not enough data

    # Check rolling 3-week history: QL=0 every week, never had QL
    weeks_sorted = sorted(
        [w for w in history.keys() if w != current_week],
        reverse=True,
    )
    consecutive_dql = 0
    ever_had_ql = ql > 0  # current week

    for week in weeks_sorted:
        for row in history[week]:
            if (row.get("keyword", "").lower().strip() == keyword.lower().strip()
                    and row.get("campaign", "").lower().strip() == campaign.lower().strip()):
                if row.get("ql", 0) > 0:
                    ever_had_ql = True
                elif row.get("sessions", 0) >= DISQ_MIN_SESSIONS_WEEK:
                    consecutive_dql += 1
                break

    if not ever_had_ql and consecutive_dql >= DISQ_WEEKS_DQL - 1 and sessions >= DISQ_MIN_SESSIONS_WEEK:
        total_weeks = consecutive_dql + 1
        return True, f"{total_weeks} нед подряд: QL=0, сессий ≥ {DISQ_MIN_SESSIONS_WEEK}/нед"

    return False, ""


# ── Report renderer ───────────────────────────────────────────────────────────

NA = "н/д"


def _fmt_num(v, decimals=0) -> str:
    if v is None:
        return NA
    if isinstance(v, float):
        return f"{v:.{decimals}f}" if decimals > 0 else str(int(v))
    return str(v)


def _fmt_cost(v) -> str:
    if v is None:
        return NA
    return f"€{v:.0f}" if v else "—"


def _fmt_ctr(clicks, impressions) -> str:
    if clicks is None or impressions is None:
        return NA
    if impressions == 0:
        return "—"
    return f"{clicks / impressions * 100:.1f}%"


def _fmt_cpql(cost, ql) -> str:
    if cost is None:
        return NA
    if ql == 0 or cost == 0:
        return "—"
    return f"€{cost / ql:.0f}"


def render_report(
    week_str: str,
    period_label: str,
    rows: list[dict],
    ql_by_key: dict,
    leads_by_key: dict,
    ksf_by_key: dict,
    history: dict,
    has_ad_data: bool,
    lead_details: list | None = None,
    channel_summary: list | None = None,
) -> tuple[str, dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build enriched rows — leads and QL both from Monday so QL ≤ Leads always
    enriched = []
    for r in rows:
        kw = r["keyword"]
        camp = r["campaign"]
        k = _key(kw, camp)
        ql = ql_by_key.get(k, 0)
        leads = int(r.get("leads_web", 0) or 0)   # Rick CSV is source of truth for lead count
        ksf = ksf_by_key.get(k, False)
        disq, disq_reason = _check_disqualify(kw, camp, r, ql, history, week_str)
        sessions = r.get("sessions", 0) or 0

        if ql > 0 or ksf:
            decision_str = "amplify"
        elif disq:
            decision_str = "disable"
        elif sessions >= MIN_CLICKS_FOR_SIGNAL:
            decision_str = "reduce"
        else:
            decision_str = "watch"

        enriched.append({
            **r,
            "leads": leads,
            "ql": ql,
            "ksf": ksf,
            "disqualify": disq,
            "disq_reason": disq_reason,
            "amplify": ql > 0 or ksf,
            "decision": decision_str,
        })

    # decisions_by_key — for saving to Outline history
    decisions_by_key = {_key(r["keyword"], r["campaign"]): r["decision"] for r in enriched}

    # Sort: QL desc → leads desc → sessions desc
    enriched.sort(key=lambda x: (-x["ql"], -x["leads"], -x.get("sessions", 0)))

    # DQL per keyword = Rick leads_web − Monday QL (Rick is source of truth for lead count)
    dql_by_key: dict[str, int] = {
        _key(r["keyword"], r["campaign"]): max(0, r["leads"] - r["ql"])
        for r in enriched
        if r["leads"] > 0
    }

    # Group by campaign
    by_campaign: dict[str, list] = defaultdict(list)
    for r in enriched:
        by_campaign[r["campaign"]].append(r)

    # Sort campaigns: by total QL desc
    sorted_camps = sorted(by_campaign.keys(), key=lambda c: -sum(r["ql"] for r in by_campaign[c]))

    lines = [
        f"# Google Ads — Keyword Report · {period_label}",
        f"",
        f"_Сформирован: {now}_",
        f"",
    ]

    if not has_ad_data:
        lines += [
            "> ⚠️ **Показы, CTR, Клики, Расход, CPQL — н/д.**",
            "> Причина: GA4 не получает keyword-level данные из Google Ads (auto-tagging не настроен).",
            "> Для полного отчёта нужны: Google Ads developer token + Customer ID.",
            "> Лиды и QL — из Monday CRM (utm_term). Сессии — из Rick.AI CSV (last click).",
            "",
        ]

    # ── Channel summary (from Rick CSV) ──────────────────────────────────────
    if channel_summary:
        tracked = [c for c in channel_summary if c["leads_web"] > 0]
        if tracked:
            lines += ["## Лиды по каналам (Rick CSV, web-форма)", ""]
            lines.append("| Канал | Лиды (web) | Сессии |")
            lines.append("|---|---|---|")
            for c in tracked:
                lines.append(f"| {c['channel']} | {int(c['leads_web'])} | {int(c['sessions'])} |")
            total_web = sum(int(c["leads_web"]) for c in tracked)
            lines.append(f"| **Итого** | **{total_web}** | |")
            lines.append("")

    # ── Section 1: General table by campaign ─────────────────────────────────
    lines += ["## Все ключевые слова (по кампаниям)", ""]

    if has_ad_data:
        col_header = "| Ключ | Показы | CTR | Клики | Лиды | QL | Расход | CPQL | Статус |"
        col_sep    = "|---|---|---|---|---|---|---|---|---|"
    else:
        col_header = "| Ключ | Сессии (Rick) | Лиды (Monday) | QL | KSF | Статус |"
        col_sep    = "|---|---|---|---|---|---|"

    for camp in sorted_camps:
        camp_rows = sorted(by_campaign[camp], key=lambda x: (-x["ql"], -x["leads"], -x.get("sessions", 0)))
        camp_ql = sum(r["ql"] for r in camp_rows)
        camp_leads = sum(r["leads"] for r in camp_rows)
        lines.append(f"### {camp} — QL: {camp_ql}, Лидов: {camp_leads}")
        lines.append("")
        lines.append(col_header)
        lines.append(col_sep)

        for r in camp_rows:
            ksf_str = "✅" if r["ksf"] else ""
            decision_label = {
                "amplify": "🟢 Усилить",
                "disable": "🔴 Отключить",
                "reduce": "🟡 Ослабить",
                "watch": "⚪ Наблюдать",
            }[r["decision"]]

            if has_ad_data:
                lines.append(
                    f"| {r['keyword']} | {_fmt_num(r.get('impressions'))} "
                    f"| {_fmt_ctr(r.get('clicks'), r.get('impressions'))} "
                    f"| {_fmt_num(r.get('clicks'))} "
                    f"| {r['leads']} | {r['ql']} "
                    f"| {_fmt_cost(r.get('cost'))} | {_fmt_cpql(r.get('cost'), r['ql'])} "
                    f"| {decision_label} {ksf_str} |"
                )
            else:
                lines.append(
                    f"| {r['keyword']} | {int(r.get('sessions', 0))} "
                    f"| {r['leads']} | {r['ql']} | {ksf_str} | {decision_label} |"
                )
        lines.append("")

    # ── Section 2: Amplify table ──────────────────────────────────────────────
    amplify = [r for r in enriched if r["amplify"]]
    if amplify:
        lines += ["---", "", "## 🟢 Усилить", ""]
        lines.append(
            "| Кампания | Ключ | Сессии (Rick) | Лиды (Monday) | QL | KSF | Рекомендация |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for r in sorted(amplify, key=lambda x: (-x["ql"], -x["leads"])):
            ksf_str = "✅" if r["ksf"] else "—"
            rec = (
                "Увеличить ставку/бюджет" if r["ql"] >= 2
                else "KSF — проверить оффер" if r["ksf"] and r["ql"] == 0
                else "Добавить вариации ключа"
            )
            lines.append(
                f"| {r['campaign']} | {r['keyword']} | {int(r.get('sessions', 0))} "
                f"| {r['leads']} | {r['ql']} | {ksf_str} | {rec} |"
            )
        lines.append("")

    # ── Section 3: Disqualify table ───────────────────────────────────────────
    disq = [r for r in enriched if r["disqualify"]]
    if disq:
        lines += ["---", "", "## 🔴 Отключить / Снизить бюджет", ""]
        lines.append(
            "| Кампания | Ключ | Сессии (Rick) | Лиды (Monday) | QL | Причина |"
        )
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(disq, key=lambda x: -x.get("sessions", 0)):
            lines.append(
                f"| {r['campaign']} | {r['keyword']} | {int(r.get('sessions', 0))} "
                f"| {r['leads']} | {r['ql']} | {r['disq_reason']} |"
            )
        lines.append("")

    # ── Section 4: DQL keywords ───────────────────────────────────────────────
    dql_rows = [
        r for r in enriched
        if dql_by_key.get(_key(r["keyword"], r["campaign"]), 0) > 0
    ]
    if dql_rows:
        lines += ["---", "", "## ❌ Ключи, привлёкшие DQL-лиды", ""]
        lines.append(
            "_Лиды получены, но не ответили на контакт. "
            "Возможная причина: нерелевантный трафик или слишком широкое соответствие ключа._"
        )
        lines.append("")
        lines.append("| Кампания | Ключ | DQL | Сессии (Rick) | Всего лидов |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(dql_rows, key=lambda x: -dql_by_key.get(_key(x["keyword"], x["campaign"]), 0)):
            k = _key(r["keyword"], r["campaign"])
            lines.append(
                f"| {r['campaign']} | {r['keyword']} "
                f"| {dql_by_key[k]} "
                f"| {int(r.get('sessions', 0))} "
                f"| {r['leads']} |"
            )
        lines.append("")

    # ── Section 5: Recommendations from last week ─────────────────────────────
    prev_w = _prev_week(week_str)
    prev_rows = history.get(prev_w, [])
    prev_with_decision = [r for r in prev_rows if r.get("decision")]
    if prev_with_decision:
        cur_sessions_by_key = {_key(r["keyword"], r["campaign"]): r.get("sessions", 0) or 0 for r in enriched}
        lines += ["---", "", f"## 📋 Сверка рекомендаций ({prev_w} → {week_str})", ""]
        lines.append("| Кампания | Ключ | Рекомендация | QL (было→стало) | Сессии (было→стало) | Результат |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(prev_with_decision, key=lambda x: x.get("decision", "")):
            k = _key(r["keyword"], r["campaign"])
            decision = r.get("decision", "")
            label = DECISION_LABELS.get(decision, decision)
            prev_ql = r.get("ql", 0)
            cur_ql = ql_by_key.get(k, 0)
            prev_sess = r.get("sessions", 0) or 0
            cur_sess = cur_sessions_by_key.get(k)

            ql_str = f"{prev_ql} → {cur_ql}"
            sess_str = f"{prev_sess} → {cur_sess}" if cur_sess is not None else f"{prev_sess} → н/д"

            if decision == "amplify":
                if cur_ql > prev_ql:
                    result = "✅ QL вырос"
                elif cur_ql == prev_ql and cur_ql > 0:
                    result = "➡️ QL стабилен"
                elif cur_ql == 0:
                    result = "⚠️ QL не вырос"
                else:
                    result = "↘️ QL снизился"
            elif decision == "disable":
                if cur_sess is None or cur_sess == 0:
                    result = "✅ Отключён"
                else:
                    result = f"⚠️ Ещё активен ({cur_sess} сессий)"
            elif decision == "reduce":
                if cur_sess is None or cur_sess == 0:
                    result = "✅ Убран"
                elif cur_sess < prev_sess:
                    result = "✅ Сессии снизились"
                else:
                    result = "➡️ Без изменений"
            else:
                result = "👁️ Наблюдаем"

            lines.append(
                f"| {r['campaign']} | {r['keyword']} | {label} | {ql_str} | {sess_str} | {result} |"
            )
        lines.append("")

    # ── Section 6: Summary ────────────────────────────────────────────────────
    n_amplify = len(amplify)
    n_disq = len(disq)
    n_reduce = len([r for r in enriched if r["decision"] == "reduce"])
    n_watch  = len([r for r in enriched if r["decision"] == "watch"])
    total_ql        = sum(r["ql"] for r in enriched)
    total_leads_kw  = sum(r["leads"] for r in enriched)   # Rick web leads for Google Ads keys
    total_dql_kw    = sum(dql_by_key.values())
    total_web_leads = int(sum(c["leads_web"] for c in (channel_summary or [])))

    lines += [
        "---",
        "",
        "## Итого",
        "",
        "| | Значение |",
        "|---|---|",
        f"| Лидов web-форма всего (Rick, все каналы) | {total_web_leads} |",
        f"| Лидов Google Ads (Rick, web-форма) | {total_leads_kw} |",
        f"| QL Google Ads (Monday) | {total_ql} |",
        f"| DQL Google Ads (Rick − Monday QL) | {total_dql_kw} |",
        f"| Всего ключей | {len(enriched)} |",
        f"| 🟢 Усилить | {n_amplify} |",
        f"| 🔴 Отключить | {n_disq} |",
        f"| 🟡 Ослабить | {n_reduce} |",
        f"| ⚪ Наблюдать | {n_watch} |",
        "",
    ]

    return "\n".join(lines), decisions_by_key


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Google Ads keyword performance report")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--week", metavar="YYYY-WNN")
    grp.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD")
    p.add_argument("--output", "-o")
    p.add_argument("--api-key")
    p.add_argument("--rick-csv", metavar="PATH")
    p.add_argument("--no-outline", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    api_key = args.api_key or os.environ.get("MONDAY_API_KEY")
    if not api_key:
        print("ERROR: MONDAY_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    outline_token = os.environ.get("OUTLINE_API_TOKEN", "")
    outline_base_url = os.environ.get("OUTLINE_BASE_URL", "")
    outline_doc_id = os.environ.get("OUTLINE_DOC_ID", "")
    outline_collection_id = os.environ.get("OUTLINE_COLLECTION_ID", "")
    outline_parent_doc_id = os.environ.get("OUTLINE_PARENT_DOC_ID", "")

    # Period
    if args.week:
        cur_from, cur_to = iso_week_to_range(args.week)
        week_label = args.week
        period_label = f"{cur_from.strftime('%d %b')} – {cur_to.strftime('%d %b %Y')}"
    elif args.from_date:
        cur_from = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        cur_to = (
            datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
            + timedelta(hours=23, minutes=59, seconds=59)
            if args.to_date else datetime.now(timezone.utc)
        )
        week_label = f"{cur_from.date()}_{cur_to.date()}"
        period_label = f"{cur_from.strftime('%d %b')} – {cur_to.strftime('%d %b %Y')}"
    else:
        today = datetime.now(timezone.utc)
        week_label = today.strftime("%Y-W%W")
        cur_from, cur_to = iso_week_to_range(week_label)
        period_label = f"{cur_from.strftime('%d %b')} – {cur_to.strftime('%d %b %Y')}"

    print(f"\n=== Keyword Report · {week_label} ({period_label}) ===\n")

    # 1. Rick CSV
    print("Rick.AI CSV: загружаю ключи…")
    rick_rows = parse_rick_keywords(args.rick_csv)
    channel_summary = parse_rick_channel_summary(args.rick_csv)
    print(f"  {len(rick_rows)} ключей Google Ads")
    for c in channel_summary:
        if c["leads_web"] > 0:
            print(f"  {c['channel']}: {int(c['leads_web'])} web-лидов")

    # 2. Monday
    print("\nMonday CRM: загружаю лиды…")
    ql_by_key, leads_by_key, ksf_by_key, lead_details = fetch_monday_keywords(cur_from, cur_to, api_key)
    print(f"  QL лидов с ключами: {sum(ql_by_key.values())}, всего лидов с ключами: {sum(leads_by_key.values())}")

    # 3. Outline history
    if args.no_outline or not outline_token:
        print("\nOutline: пропускаю")
        history = {}
    else:
        print(f"\nOutline: читаю историю…")
        history = read_keyword_history(outline_base_url, outline_token, outline_doc_id)
        print(f"  Недель в истории: {len(history)}")

    # 4. Render
    has_ad_data = any(r.get("impressions") is not None for r in rick_rows)
    md, decisions_by_key = render_report(
        week_label, period_label, rick_rows,
        ql_by_key, leads_by_key, ksf_by_key,
        history, has_ad_data, lead_details, channel_summary,
    )

    out_path = args.output or f"keyword_reports/{week_label}.md"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(md, encoding="utf-8")
    print(f"\n✓ Отчёт сохранён: {out_path}")

    # 5. Save to Outline
    if not args.no_outline and outline_token:
        import requests as _req

        # 5a. Save keyword history
        print("Outline: обновляю историю…")
        history_rows = build_history_rows(rick_rows, ql_by_key, ksf_by_key, decisions_by_key)
        ok = append_week(outline_base_url, outline_token, outline_doc_id, week_label, history_rows)
        print("  ✓ Outline история обновлена" if ok else "  ✗ Outline история — ошибка")

        # 5b. Publish weekly report doc under Agents – Google Ads reports
        if outline_collection_id and outline_parent_doc_id:
            print("Outline: публикую отчёт…")
            _headers = {"Authorization": f"Bearer {outline_token}", "Content-Type": "application/json"}
            # Search for existing doc with this week's title
            title = f"Google Ads — Keyword Report {week_label}"
            sr = _req.post(f"{outline_base_url}/api/documents.search",
                           json={"query": title, "collectionId": outline_collection_id},
                           headers=_headers, timeout=20)
            hits = sr.json().get("data", [])
            existing = next(
                (h["document"] for h in hits if h["document"].get("title") == title),
                None,
            )
            if existing:
                upd = _req.post(f"{outline_base_url}/api/documents.update",
                                json={"id": existing["id"], "text": md, "publish": True},
                                headers=_headers, timeout=20)
                doc_url = existing.get("url", "")
                print(f"  ✓ Отчёт обновлён: {outline_base_url}{doc_url}" if upd.status_code == 200 else f"  ✗ Ошибка: {upd.status_code}")
            else:
                cr = _req.post(f"{outline_base_url}/api/documents.create",
                               json={"title": title, "text": md, "collectionId": outline_collection_id,
                                     "parentDocumentId": outline_parent_doc_id, "publish": True},
                               headers=_headers, timeout=20)
                if cr.status_code == 200:
                    doc_url = cr.json().get("data", {}).get("url", "")
                    print(f"  ✓ Отчёт создан: {outline_base_url}{doc_url}")
                else:
                    print(f"  ✗ Ошибка создания отчёта: {cr.status_code}")

    # Summary
    amplify_n = len([r for r in rick_rows if ql_by_key.get(_key(r["keyword"], r["campaign"]), 0) > 0 or ksf_by_key.get(_key(r["keyword"], r["campaign"]), False)])
    disq_n = sum(1 for r in rick_rows if _check_disqualify(r["keyword"], r["campaign"], r, ql_by_key.get(_key(r["keyword"], r["campaign"]), 0), history, week_label)[0])
    print(f"\n  🟢 Усилить: {amplify_n}")
    print(f"  🔴 Отключить: {disq_n}")


if __name__ == "__main__":
    main()

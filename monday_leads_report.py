#!/usr/bin/env python3
"""
monday_leads_report.py — Monday.com CRM Leads Analyzer

Выгружает лиды с UTM-данными, перепиской и действиями менеджеров в Markdown.

Использование:
  # По конкретным item ID:
  python3 monday_leads_report.py --ids 2749126332 2745731359 2743636892

  # По диапазону дат (сортировка по дате создания, desc):
  python3 monday_leads_report.py --from 2026-03-01 --to 2026-03-19

  # Комбинация с кастомным файлом вывода:
  python3 monday_leads_report.py --from 2026-03-01 --output leads_march.md

  # С фильтром по группе:
  python3 monday_leads_report.py --from 2026-03-01 --group "Taken into work"

Переменные окружения:
  MONDAY_API_KEY  — API-ключ Monday.com (или --api-key)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

# Загружаем .env из директории скрипта
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ─── Конфигурация ────────────────────────────────────────────────────────────

DEFAULT_BOARD_ID = "1231414321"
DEFAULT_BOARD_URL = "https://typhoon-roasters-new.monday.com/boards/1231414321/pulses"
API_URL = "https://api.monday.com/v2"

MONTHS_EN = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# ─── GraphQL запросы ──────────────────────────────────────────────────────────

ITEMS_BY_IDS_QUERY = """
query ($ids: [ID!]!) {
  items(ids: $ids, limit: 100) {
    id name created_at updated_at
    group { id title }
    column_values { id text value }
  }
}
"""

ITEMS_PAGE_QUERY = """
query ($boardId: ID!, $limit: Int!, $cursor: String) {
  boards(ids: [$boardId]) {
    items_page(limit: $limit, cursor: $cursor, query_params: {
      order_by: [{ column_id: "date_of_creation", direction: desc }]
    }) {
      cursor
      items {
        id name created_at updated_at
        group { id title }
        column_values { id text value }
      }
    }
  }
}
"""

UPDATES_QUERY = """
query ($ids: [ID!]!) {
  items(ids: $ids, limit: 1) {
    updates(limit: 100) {
      id text_body body created_at
      creator { id name }
      replies {
        id text_body created_at
        creator { id name }
      }
    }
  }
}
"""

# ─── API клиент ───────────────────────────────────────────────────────────────

def gql(query: str, variables: dict, api_key: str) -> dict:
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
        "API-Version": "2024-01",
    }
    resp = requests.post(API_URL, json={"query": query, "variables": variables},
                         headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {json.dumps(data['errors'], ensure_ascii=False)}")
    return data["data"]

# ─── Получение данных ─────────────────────────────────────────────────────────

def fetch_items_by_ids(ids: list, api_key: str) -> list:
    data = gql(ITEMS_BY_IDS_QUERY, {"ids": ids}, api_key)
    return data.get("items", [])

def fetch_items_by_date(board_id: str, from_dt: datetime, to_dt: datetime,
                        api_key: str, group_filter: str = None) -> list:
    all_items = []
    cursor = None
    page = 0
    print(f"  Выгружаю лиды из борда {board_id}...", flush=True)
    while True:
        page += 1
        data = gql(ITEMS_PAGE_QUERY, {"boardId": board_id, "limit": 200, "cursor": cursor}, api_key)
        page_data = data["boards"][0]["items_page"]
        items = page_data.get("items", [])
        next_cursor = page_data.get("cursor")
        filtered = []
        stop_early = False
        for item in items:
            created = parse_dt(item.get("created_at", ""))
            if created is None:
                continue
            if created < from_dt:
                stop_early = True
                break
            if created > to_dt:
                continue
            if group_filter:
                group_title = item.get("group", {}).get("title", "")
                if group_filter.lower() not in group_title.lower():
                    continue
            filtered.append(item)
        all_items.extend(filtered)
        print(f"  Страница {page}: {len(items)} записей, подходит {len(filtered)}, всего {len(all_items)}", flush=True)
        if stop_early or not next_cursor or not items:
            break
        cursor = next_cursor
    return all_items

def fetch_updates_for_item(item_id: str, api_key: str) -> list:
    data = gql(UPDATES_QUERY, {"ids": [item_id]}, api_key)
    items = data.get("items", [])
    if not items:
        return []
    return items[0].get("updates", [])

# ─── Парсинг утилиты ──────────────────────────────────────────────────────────

def parse_dt(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def fmt_dt_short(s: str) -> str:
    dt = parse_dt(s)
    if not dt:
        return s or "—"
    return f"{MONTHS_EN[dt.month-1]} {dt.day}, {dt.hour:02d}:{dt.minute:02d} UTC"

def fmt_time(s: str) -> str:
    dt = parse_dt(s)
    if not dt:
        return s or "—"
    return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

def get_col(item: dict, col_id: str) -> str:
    for cv in item.get("column_values", []):
        if cv["id"] == col_id:
            text = (cv.get("text") or "").strip()
            if not text and cv.get("value"):
                try:
                    val = json.loads(cv["value"])
                    if isinstance(val, dict) and "linkedPulseIds" in val:
                        ids = [str(p["linkedPulseId"]) for p in val["linkedPulseIds"]]
                        return ", ".join(ids) if ids else ""
                except Exception:
                    pass
            return text
    return ""

# ─── Аналитика ────────────────────────────────────────────────────────────────

def time_to_first_contact(item: dict, updates: list) -> str:
    """Считает время от создания лида до первого обновления (первый контакт)."""
    created = parse_dt(item.get("created_at", ""))
    if not created or not updates:
        return None, None
    sorted_updates = sorted(updates, key=lambda x: x.get("created_at", ""))
    first = sorted_updates[0]
    first_dt = parse_dt(first.get("created_at", ""))
    if not first_dt:
        return None, None
    delta = first_dt - created
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        time_str = f"{seconds} seconds"
    elif seconds < 3600:
        time_str = f"{seconds // 60} min {seconds % 60} sec"
    else:
        time_str = f"{seconds // 3600}h {(seconds % 3600) // 60}min"
    return time_str, first

def build_channel(item: dict) -> str:
    utm_source = get_col(item, "utm_source")
    utm_medium = get_col(item, "utm_medium")
    utm_campaign = get_col(item, "utm_campaign")
    utm_term = get_col(item, "utm_term")
    source = get_col(item, "source")
    where_heard = get_col(item, "dropdown_mkv663sg")

    # Определяем канал
    medium_lower = utm_medium.lower() if utm_medium else ""
    source_lower = utm_source.lower() if utm_source else ""

    if "paid" in medium_lower or "cpc" in medium_lower:
        if "google" in source_lower:
            channel = "Google Paid Search"
        elif "facebook" in source_lower or "fb" in source_lower or "instagram" in source_lower:
            channel = "Meta Paid"
        else:
            channel = f"Paid Search ({utm_source})"
    elif "organic" in medium_lower:
        channel = f"Organic ({utm_source})"
    elif "email" in medium_lower:
        channel = "Email"
    elif "social" in medium_lower:
        channel = f"Social ({utm_source})"
    elif "referral" in medium_lower:
        channel = "Referral"
    elif utm_source and utm_source.lower() not in ("unknown", "—", ""):
        channel = utm_source
    elif where_heard and where_heard not in ("—", ""):
        channel = where_heard
    else:
        channel = f"Direct / Unknown ({source})" if source else "Direct / Unknown"

    lines = [channel]
    if utm_campaign and utm_campaign not in ("—", ""):
        lines.append(f"  * Campaign: `{utm_campaign}`")
    if utm_term and utm_term not in ("—", ""):
        lines.append(f"  * Keyword: `{utm_term}`")

    return "\n".join(lines)

def build_contact_summary(item: dict, updates: list, first_update) -> str:
    """Строит narrative переписки: WA, звонки, email, заметки."""
    lines = []
    seen_ids = set()

    wa_raw = get_col(item, "long_text_mm0xfk14")
    calls_col = get_col(item, "link_to_calls58")
    has_calls = bool(calls_col)
    has_email = False

    sorted_updates = sorted(updates, key=lambda x: x.get("created_at", ""))

    for u in sorted_updates:
        uid = u.get("id")
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        text = (u.get("text_body") or "").strip()
        creator = u.get("creator", {}).get("name", "?").split("@")[0].strip()
        # Имя: убираем домен из email
        if "@" in creator:
            creator = creator.split("@")[0]
        t = fmt_time(u.get("created_at", ""))

        # WhatsApp авто-сообщение от TimelinesAI
        if "timelines" in text.lower() or "wrote at" in text.lower():
            # Извлекаем первую строку сообщения
            msg_match = re.search(r"wrote at \d+:\d+:\s*(.+?)(?:\n|$)", text)
            if msg_match:
                msg = msg_match.group(1).strip()[:120]
                lines.append(f"* **{creator}** → lead ({t} UTC): \"{msg}…\"")
            else:
                lines.append(f"* WhatsApp sent at {t} UTC by {creator}")
        # Email упоминание
        elif "email" in text.lower() and "sent" in text.lower():
            has_email = True
            lines.append(f"* Email sent ({creator}, {t} UTC)")
        # Обычная заметка/комментарий
        elif text:
            short = text[:200].replace("\n", " ")
            lines.append(f"* Note ({creator}, {t} UTC): \"{short}\"")

        # Реплаи
        for r in u.get("replies", []):
            rid = r.get("id")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            rt = (r.get("text_body") or "").strip()
            rc = r.get("creator", {}).get("name", "?")
            if "@" in rc:
                rc = rc.split("@")[0]
            rtime = fmt_time(r.get("created_at", ""))
            if rt:
                lines.append(f"  ↳ **{rc}** replied ({rtime} UTC): \"{rt[:150]}\"")

    # Звонки
    if has_calls:
        lines.append(f"* Outgoing call placed (see Calls board)")

    return "\n".join(lines) if lines else "_No contact activity recorded_"

def build_status_summary(item: dict, updates: list) -> str:
    """Краткий статус: что сделано, есть ли ответ, что дальше."""
    stage = get_col(item, "dup__of_old_stage") or get_col(item, "dropdown_mknfg703")
    wa_raw = get_col(item, "long_text_mm0xfk14")
    calls_col = get_col(item, "link_to_calls58")

    actions = []
    has_response = False

    for u in sorted(updates, key=lambda x: x.get("created_at", "")):
        text = (u.get("text_body") or "").strip()
        creator_name = u.get("creator", {}).get("name", "")

        if "timelines" in text.lower() or "wrote at" in text.lower():
            actions.append("WhatsApp")
        if "email" in text.lower() and "sent" in text.lower():
            actions.append("Email")
        if calls_col:
            actions.append("Call")
        # Если lead сам написал (не менеджер и не системное)
        if "timelines" not in text.lower() and text and not any(
            m in creator_name.lower() for m in ["typhoon", "irina", "daria", "lina", "arina", "vlad"]
        ):
            has_response = True

    # Ищем "next touch" в заметках
    next_touch = ""
    for u in updates:
        text = (u.get("text_body") or "").lower()
        match = re.search(r"next touch\s+([\d.]+)", text)
        if match:
            next_touch = f" Next follow-up planned {match.group(1)}."

    unique_actions = list(dict.fromkeys(actions))  # дедупликация с сохранением порядка
    actions_str = " + ".join(unique_actions) if unique_actions else "No contact"
    response_str = "Lead responded." if has_response else "No response yet."

    if stage and stage not in ("—", ""):
        return f"{actions_str}. {response_str} Stage: {stage}.{next_touch}"
    return f"{actions_str}. {response_str}{next_touch}"

# ─── Рендер лида ─────────────────────────────────────────────────────────────

def render_lead(idx: int, item: dict, updates: list, board_url: str) -> str:
    item_id = item["id"]
    name = item.get("name", "—")
    created_str = item.get("created_at", "")
    created_fmt = fmt_dt_short(created_str)
    url = f"{board_url}/{item_id}/"
    group = item.get("group", {}).get("title", "—")

    channel = build_channel(item)
    ttc, first_update = time_to_first_contact(item, updates)

    # Первый контакт
    if ttc and first_update:
        first_creator = first_update.get("creator", {}).get("name", "?")
        if "@" in first_creator:
            first_creator = first_creator.split("@")[0]
        first_time = fmt_time(first_update.get("created_at", ""))

        # Определяем метод первого контакта
        first_text = (first_update.get("text_body") or "").lower()
        if "timelines" in first_text or "wrote at" in first_text:
            first_method = f"WhatsApp sent at {first_time} UTC by {first_creator}"
        else:
            first_method = f"Update by {first_creator} at {first_time} UTC"

        ttc_line = f"**Time to first contact:** {ttc} — {first_method}"
    else:
        ttc_line = "**Time to first contact:** — (no updates found)"

    contact_summary = build_contact_summary(item, updates, first_update if ttc else None)

    # Тип контакта для заголовка summary
    wa_raw = get_col(item, "long_text_mm0xfk14")
    calls = get_col(item, "link_to_calls58")
    contact_types = []
    if wa_raw:
        contact_types.append("WhatsApp")
    if calls:
        contact_types.append("Call")
    for u in updates:
        t = (u.get("text_body") or "").lower()
        if "email" in t and "sent" in t:
            contact_types.append("Email")
            break
    contact_type_str = " + ".join(dict.fromkeys(contact_types)) if contact_types else "No contact"

    status = build_status_summary(item, updates)

    lead_email = get_col(item, "lead_email")
    lead_phone = get_col(item, "lead_phone")
    country = get_col(item, "country")
    owner = get_col(item, "lead_owner")
    equipment = get_col(item, "equipment")

    contact_info = []
    if lead_email: contact_info.append(f"email: {lead_email}")
    if lead_phone: contact_info.append(f"phone: {lead_phone}")
    if country: contact_info.append(f"country: {country}")
    if owner: contact_info.append(f"manager: {owner}")
    if equipment: contact_info.append(f"equipment: {equipment}")

    lines = [
        f"### {idx}. {name} — {created_fmt}",
        f"**URL:** {url}  ",
        f"**Group:** {group}",
        "",
        f"**Channel:** {channel}",
    ]
    if contact_info:
        lines.append(f"**Contact info:** {' | '.join(contact_info)}")
    lines += [
        "",
        ttc_line,
        "",
        f"**Contact summary ({contact_type_str}):**",
        contact_summary,
        "",
        f"**Status:** {status}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)

# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Monday.com CRM Leads → Markdown report")
    parser.add_argument("--ids", nargs="+", metavar="ID", help="Конкретные item ID лидов")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD", help="Начало диапазона")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD", help="Конец диапазона (default: сегодня)")
    parser.add_argument("--board", default=DEFAULT_BOARD_ID, metavar="BOARD_ID")
    parser.add_argument("--group", metavar="NAME", help="Фильтр по группе (частичное совпадение)")
    parser.add_argument("--output", "-o", default="leads_report.md")
    parser.add_argument("--api-key", metavar="KEY")
    parser.add_argument("--board-url", default=DEFAULT_BOARD_URL)
    return parser.parse_args()

def main():
    args = parse_args()
    api_key = args.api_key or os.environ.get("MONDAY_API_KEY")
    if not api_key:
        print("ERROR: Укажите MONDAY_API_KEY env var или --api-key", file=sys.stderr)
        sys.exit(1)

    if args.ids:
        ids = list(dict.fromkeys(args.ids))
        print(f"Режим: по {len(ids)} item ID")
        items = fetch_items_by_ids(ids, api_key)
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    elif args.from_date:
        from_dt = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        to_dt = (datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
                 + timedelta(days=1, seconds=-1)) if args.to_date else datetime.now(timezone.utc)
        print(f"Режим: {from_dt.date()} → {to_dt.date()}")
        items = fetch_items_by_date(args.board, from_dt, to_dt, api_key, args.group)
    else:
        print("ERROR: Укажите --ids или --from", file=sys.stderr)
        sys.exit(1)

    if not items:
        print("Лиды не найдены.")
        sys.exit(0)

    print(f"\nНайдено лидов: {len(items)}")
    print("Загружаю обновления и формирую отчёт...\n")

    report_lines = [
        "# Monday.com CRM — Leads Report",
        "",
        f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_  ",
        f"_Leads: {len(items)}_",
        "",
        "---",
        "",
    ]

    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] {item['name']} (ID: {item['id']})")
        updates = fetch_updates_for_item(item["id"], api_key)
        report_lines.append(render_lead(i, item, updates, args.board_url))

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"\n✓ Готово: {args.output} ({len(items)} лидов)")

if __name__ == "__main__":
    main()

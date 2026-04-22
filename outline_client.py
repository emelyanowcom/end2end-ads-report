"""
outline_client.py — Outline API client for reading and writing keyword history.

The history document is a Markdown doc in Outline with sections per ISO week.
Each week section is a Markdown table with one row per keyword+campaign.

Format stored in Outline:
  ## 2026-W16
  | Ключ | Кампания | Сессии | Показы | Клики | Расход | QL | KSF |
  |---|---|---|---|---|---|---|---|
  | typhoon roasters | HW_EU_search_brand | 340 | 1240 | 47 | 38.0 | 2 | false |
  ...

  ## 2026-W15
  ...
"""

from __future__ import annotations

import re
import json
import requests
from typing import Any


class OutlineClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}/api/{path}",
            json=payload,
            headers=self._headers,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def get_document(self, doc_id: str) -> dict:
        data = self._post("documents.info", {"id": doc_id})
        return data.get("data", {})

    def update_document(self, doc_id: str, text: str, title: str | None = None) -> dict:
        payload: dict[str, Any] = {"id": doc_id, "text": text, "publish": True}
        if title:
            payload["title"] = title
        data = self._post("documents.update", payload)
        return data.get("data", {})


# ── Markdown history format ────────────────────────────────────────────────────

HISTORY_HEADER = """# Google Ads — Keyword History

Этот документ обновляется автоматически скриптом `keyword_report.py`.
Используется для применения 3-недельного правила (🔴 Отключить).

"""

WEEK_COLS = ["keyword", "campaign", "sessions", "impressions", "clicks", "cost", "ql", "ksf", "decision"]
TABLE_HEADER = (
    "| Ключ | Кампания | Сессии | Показы | Клики | Расход | QL | KSF | Решение |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)


def parse_history(text: str) -> dict[str, list[dict]]:
    """Parse Outline document text → {week_str: [{keyword, campaign, sessions, ...}]}."""
    history: dict[str, list[dict]] = {}
    current_week: str | None = None
    in_table = False

    for line in text.splitlines():
        week_match = re.match(r"^##\s+(20\d\d-W\d{1,2})\s*$", line.strip())
        if week_match:
            current_week = week_match.group(1)
            history[current_week] = []
            in_table = False
            continue

        if current_week is None:
            continue

        if line.startswith("|---|"):
            in_table = True
            continue

        if line.startswith("| Ключ |"):
            in_table = False  # header row, skip
            continue

        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 8:
                try:
                    history[current_week].append(
                        {
                            "keyword": parts[0],
                            "campaign": parts[1],
                            "sessions": int(parts[2] or 0),
                            "impressions": int(parts[3] or 0),
                            "clicks": int(parts[4] or 0),
                            "cost": float(parts[5] or 0),
                            "ql": int(parts[6] or 0),
                            "ksf": parts[7].lower() == "true",
                            "decision": parts[8] if len(parts) > 8 else "",
                        }
                    )
                except (ValueError, IndexError):
                    continue
        elif in_table and line.strip() == "":
            in_table = False

    return history


def format_week_section(week_str: str, rows: list[dict]) -> str:
    """Render one week section as Markdown."""
    lines = [f"## {week_str}", "", TABLE_HEADER.rstrip("\n")]
    for r in sorted(rows, key=lambda x: -x.get("clicks", 0)):
        kw = r.get("keyword", "")
        camp = r.get("campaign", "")
        sess = r.get("sessions", 0)
        imp = r.get("impressions", 0)
        clk = r.get("clicks", 0)
        cost = r.get("cost", 0.0)
        ql = r.get("ql", 0)
        ksf = "true" if r.get("ksf") else "false"
        decision = r.get("decision", "")
        lines.append(f"| {kw} | {camp} | {sess} | {imp} | {clk} | {cost:.1f} | {ql} | {ksf} | {decision} |")
    lines.append("")
    return "\n".join(lines)


def build_history_document(history: dict[str, list[dict]]) -> str:
    """Rebuild full Outline document from history dict (newest week first)."""
    weeks_sorted = sorted(history.keys(), reverse=True)
    sections = [HISTORY_HEADER]
    for week in weeks_sorted:
        sections.append(format_week_section(week, history[week]))
    return "\n".join(sections)


def read_keyword_history(
    base_url: str, token: str, doc_id: str
) -> dict[str, list[dict]]:
    """Fetch and parse keyword history from Outline. Returns {} on error."""
    try:
        client = OutlineClient(base_url, token)
        doc = client.get_document(doc_id)
        text = doc.get("text", "")
        return parse_history(text)
    except Exception as exc:
        print(f"  WARN: Outline read failed: {exc}")
        return {}


def append_week(
    base_url: str,
    token: str,
    doc_id: str,
    week_str: str,
    rows: list[dict],
) -> bool:
    """Upsert week_str section in Outline document. Returns True on success."""
    try:
        client = OutlineClient(base_url, token)
        doc = client.get_document(doc_id)
        history = parse_history(doc.get("text", ""))
        history[week_str] = rows

        # Keep only last 8 weeks to avoid doc bloat
        all_weeks = sorted(history.keys(), reverse=True)
        if len(all_weeks) > 8:
            for old in all_weeks[8:]:
                del history[old]

        new_text = build_history_document(history)
        client.update_document(doc_id, new_text)
        return True
    except Exception as exc:
        print(f"  WARN: Outline write failed: {exc}")
        return False

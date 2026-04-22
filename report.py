"""
report.py — Markdown report renderer for weekly leads framework.
All logic is deterministic; no LLM calls.
"""

from __future__ import annotations
from datetime import datetime, timezone
from collections import defaultdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(a: float, b: float) -> str:
    if b == 0:
        return "—"
    return f"{(a - b) / b * 100:+.1f}%"


def _delta(a: float, b: float) -> str:
    diff = a - b
    return f"+{diff:.0f}" if diff >= 0 else f"{diff:.0f}"


def _cr(leads: float, sessions: float) -> str:
    if sessions == 0:
        return "—"
    return f"{leads / sessions * 100:.1f}%"


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    local = dt.astimezone(datetime.now(timezone.utc).astimezone().tzinfo)
    return dt.strftime("%d %b %H:%M UTC")


REASON_LABELS = {
    "lead_responded": "✅ QL — ответил",
    "sales_timeout": "⚠️ QL — пропустили SLA",
    "no_response": "❌ DQL — нет ответа",
    "no_created_at": "❌ DQL — нет даты",
}

REASON_SHORT = {
    "lead_responded": "QL",
    "sales_timeout": "QL (SLA miss)",
    "no_response": "DQL",
    "no_created_at": "DQL",
}


# ── Channel table ─────────────────────────────────────────────────────────────

def render_channel_table(
    cur_leads: dict,   # channel → {"ql": n, "dql": n, "sla_miss": n}
    prev_leads: dict,
    rick_cur: dict,    # channel → {"sessions": n, "users": n}
    rick_prev: dict,
) -> str:
    all_channels = sorted(
        set(cur_leads) | set(prev_leads) | set(rick_cur) | set(rick_prev),
        key=lambda c: -(cur_leads.get(c, {}).get("ql", 0) + cur_leads.get(c, {}).get("dql", 0)),
    )

    rows = []
    header = (
        "| Канал | QL↑ | QL↓ | DQL↑ | QL% | QL%↓ | ΔQL | Сессии↑ | Сессии↓ | CR↑ | CR↓ |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    # Simplified header for readability
    header = (
        "| Канал | QL (тек) | QL (пред) | DQL (тек) | DQL (пред) | Δ QL | Сессии (тек) | Сессии (пред) | CR (тек) | CR (пред) |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )

    total_cur_ql = total_prev_ql = total_cur_dql = total_prev_dql = 0
    total_cur_sess = total_prev_sess = 0

    for ch in all_channels:
        c = cur_leads.get(ch, {})
        p = prev_leads.get(ch, {})
        rc = rick_cur.get(ch, {})
        rp = rick_prev.get(ch, {})

        c_ql = c.get("ql", 0)
        p_ql = p.get("ql", 0)
        c_dql = c.get("dql", 0)
        p_dql = p.get("dql", 0)
        c_sess = rc.get("sessions", 0)
        p_sess = rp.get("sessions", 0)

        if c_ql + p_ql + c_dql + p_dql + c_sess + p_sess == 0:
            continue

        total_cur_ql += c_ql
        total_prev_ql += p_ql
        total_cur_dql += c_dql
        total_prev_dql += p_dql
        total_cur_sess += c_sess
        total_prev_sess += p_sess

        d_ql = _delta(c_ql, p_ql)
        cr_cur = _cr(c_ql, c_sess)
        cr_prev = _cr(p_ql, p_sess)

        rows.append(
            f"| {ch} | {c_ql:.0f} | {p_ql:.0f} | {c_dql:.0f} | {p_dql:.0f} "
            f"| {d_ql} | {c_sess:.0f} | {p_sess:.0f} | {cr_cur} | {cr_prev} |"
        )

    totals = (
        f"| **ИТОГО** | **{total_cur_ql:.0f}** | **{total_prev_ql:.0f}** "
        f"| **{total_cur_dql:.0f}** | **{total_prev_dql:.0f}** "
        f"| **{_delta(total_cur_ql, total_prev_ql)}** "
        f"| **{total_cur_sess:.0f}** | **{total_prev_sess:.0f}** "
        f"| **{_cr(total_cur_ql, total_cur_sess)}** | **{_cr(total_prev_ql, total_prev_sess)}** |"
    )

    return header + "\n".join(rows) + "\n" + totals


# ── Top QL channels ───────────────────────────────────────────────────────────

def render_top_ql(cur_leads: dict, rick_cur: dict) -> str:
    ranked = sorted(
        [(ch, d.get("ql", 0)) for ch, d in cur_leads.items() if d.get("ql", 0) > 0],
        key=lambda x: -x[1],
    )[:5]

    if not ranked:
        return "_Нет QL лидов в текущем периоде._\n"

    lines = []
    for ch, ql in ranked:
        sess = rick_cur.get(ch, {}).get("sessions", 0)
        cr = _cr(ql, sess)
        lines.append(f"- **{ch}**: {ql:.0f} QL, CR {cr}")
    return "\n".join(lines)


# ── DQL diagnosis ─────────────────────────────────────────────────────────────

def render_dql_diagnosis(cur_leads: dict, cur_lead_details: list) -> str:
    """Summarise DQL patterns per channel."""
    dql_by_channel: dict[str, list] = defaultdict(list)
    for ld in cur_lead_details:
        if ld["classification"]["status"] == "DQL":
            ch = ld.get("channel", "Unknown")
            dql_by_channel[ch].append(ld)

    if not dql_by_channel:
        return "_Нет DQL лидов в текущем периоде._\n"

    lines = []
    for ch, leads in sorted(dql_by_channel.items(), key=lambda x: -len(x[1])):
        n = len(leads)
        lines.append(f"**{ch}** — {n} DQL:")
        for ld in leads[:3]:
            name = ld.get("name", "—")[:40]
            lines.append(f"  - {name}")
        if n > 3:
            lines.append(f"  - _и ещё {n - 3}…_")
    return "\n".join(lines)


# ── Auto decisions ────────────────────────────────────────────────────────────

def render_decisions(cur_leads: dict, prev_leads: dict, rick_cur: dict) -> str:
    rows = []

    for ch in set(cur_leads) | set(prev_leads):
        c = cur_leads.get(ch, {})
        p = prev_leads.get(ch, {})
        c_ql = c.get("ql", 0)
        p_ql = p.get("ql", 0)
        c_sess = rick_cur.get(ch, {}).get("sessions", 0)
        c_total = c_ql + c.get("dql", 0)

        # Rule: УСИЛИТЬ — QL выросло И есть хотя бы 1 QL
        if c_ql > 0 and c_ql >= p_ql and c_ql >= 2:
            rows.append(f"| Усилить | {ch} | QL: {p_ql:.0f}→{c_ql:.0f} ({_pct(c_ql,p_ql)}) | — | +2 нед | QL ≥ {max(c_ql+2, 5):.0f}/нед |")

        # Rule: ГИПОТЕЗА — мало данных (< 5 сессий)
        elif c_sess < 5 and c_sess > 0:
            rows.append(f"| Гипотеза | {ch} | Мало трафика ({c_sess:.0f} сессий) | — | +4 нед | ≥ 1 QL |")

        # Rule: ОТКЛЮЧИТЬ — 0 QL при наличии сессий (не гипотеза)
        elif c_ql == 0 and p_ql == 0 and c_sess >= 5:
            rows.append(f"| ❌ Отключить? | {ch} | 0 QL / {c_sess:.0f} сессий | — | Сейчас | — |")

    if not rows:
        return "_Недостаточно данных для автоматических решений. Нужны 4+ недели истории._\n"

    header = (
        "| Решение | Канал | Основание | Owner | Пересмотреть | Успех = |\n"
        "|---|---|---|---|---|---|\n"
    )
    return header + "\n".join(rows)


# ── SLA summary ───────────────────────────────────────────────────────────────

def render_sla_summary(cur_lead_details: list) -> str:
    total = len(cur_lead_details)
    if total == 0:
        return "_Нет данных._\n"

    sla_met = sum(1 for ld in cur_lead_details if ld["classification"].get("sla_met"))
    sla_miss = total - sla_met
    pct = sla_miss / total * 100

    warn = " ⚠️ **Требует внимания**" if pct > 20 else ""
    return (
        f"- Всего лидов: {total}\n"
        f"- SLA выполнен: {sla_met} ({100 - pct:.0f}%)\n"
        f"- SLA пропущен (авто-QL): {sla_miss} ({pct:.0f}%){warn}\n"
    )


# ── Lead appendix ─────────────────────────────────────────────────────────────

def render_lead_appendix(cur_lead_details: list, board_url: str) -> str:
    if not cur_lead_details:
        return "_Нет лидов._\n"

    lines = []
    for i, ld in enumerate(cur_lead_details, 1):
        item_id = ld.get("id", "")
        name = ld.get("name", "—")
        created = ld.get("created_at", "")[:10]
        ch = ld.get("channel_full", ld.get("channel", "—")).replace("\n", " · ")
        clf = ld["classification"]
        status_label = REASON_LABELS.get(clf["reason"], clf["reason"])
        sla_deadline = _fmt_dt(clf.get("sla_deadline"))
        first_resp = _fmt_dt(clf.get("first_resp"))
        url = f"{board_url}/{item_id}/"

        lines.append(f"### {i}. {name} — {created}")
        lines.append(f"[Monday]({url}) · **Канал:** {ch} · **Статус:** {status_label}")
        lines.append(f"SLA дедлайн: {sla_deadline} · Первый ответ менеджера: {first_resp}")
        lines.append("")

    return "\n".join(lines)


# ── Main render ───────────────────────────────────────────────────────────────

def render_report(
    period_cur: str,
    period_prev: str,
    cur_lead_details: list,
    prev_lead_details: list,
    rick_cur: dict,    # channel → {"sessions": n, "users": n}
    rick_prev: dict,
    board_url: str = "https://typhoon-roasters-new.monday.com/boards/1231414321/pulses",
) -> str:
    # Aggregate leads by channel
    def aggregate(lead_details):
        by_channel: dict[str, dict] = defaultdict(lambda: {"ql": 0, "dql": 0, "sla_miss": 0})
        for ld in lead_details:
            ch = ld.get("channel", "Unknown")
            clf = ld["classification"]
            if clf["status"] == "QL":
                by_channel[ch]["ql"] += 1
            else:
                by_channel[ch]["dql"] += 1
            if not clf.get("sla_met"):
                by_channel[ch]["sla_miss"] += 1
        return dict(by_channel)

    cur_by_ch = aggregate(cur_lead_details)
    prev_by_ch = aggregate(prev_lead_details)

    total_cur_ql = sum(d["ql"] for d in cur_by_ch.values())
    total_prev_ql = sum(d["ql"] for d in prev_by_ch.values())
    total_cur = len(cur_lead_details)
    total_prev = len(prev_lead_details)
    qual_rate_cur = f"{total_cur_ql / total_cur * 100:.0f}%" if total_cur else "—"
    qual_rate_prev = f"{total_prev_ql / total_prev * 100:.0f}%" if total_prev else "—"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = [
        f"# Еженедельный отчёт по лидам",
        f"",
        f"_Сформирован: {now}_",
        f"",
        f"## Сводка",
        f"",
        f"| | {period_cur} | {period_prev} | Δ |",
        f"|---|---|---|---|",
        f"| Всего лидов | {total_cur} | {total_prev} | {_delta(total_cur, total_prev)} |",
        f"| QL | {total_cur_ql} | {total_prev_ql} | {_delta(total_cur_ql, total_prev_ql)} |",
        f"| DQL | {total_cur - total_cur_ql} | {total_prev - total_prev_ql} | {_delta(total_cur - total_cur_ql, total_prev - total_prev_ql)} |",
        f"| QL rate | {qual_rate_cur} | {qual_rate_prev} | — |",
        f"",
        f"---",
        f"",
        f"## Каналы",
        f"",
        render_channel_table(cur_by_ch, prev_by_ch, rick_cur, rick_prev),
        f"",
        f"---",
        f"",
        f"## Откуда идут качественные лиды (QL)",
        f"",
        render_top_ql(cur_by_ch, rick_cur),
        f"",
        f"---",
        f"",
        f"## Проблемные каналы (DQL)",
        f"",
        render_dql_diagnosis(cur_by_ch, cur_lead_details),
        f"",
        f"---",
        f"",
        f"## SLA первого ответа",
        f"",
        render_sla_summary(cur_lead_details),
        f"",
        f"---",
        f"",
        f"## Решения",
        f"",
        render_decisions(cur_by_ch, prev_by_ch, rick_cur),
        f"",
        f"---",
        f"",
        f"## Приложение: детали лидов ({period_cur})",
        f"",
        render_lead_appendix(cur_lead_details, board_url),
    ]

    return "\n".join(sections)

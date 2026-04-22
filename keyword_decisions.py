"""
keyword_decisions.py — Decision engine for Google Ads keyword performance.

Rules (applied per keyword+campaign):
  🟢 Усилить  — QL ≥ 1 OR KSF signal in current week
  🔴 Отключить — 3 consecutive weeks with clicks > 5 AND QL=0 AND no KSF
  🟡 Ослабить  — QL=0, no KSF, history < 3 weeks with sufficient data
  ⚪ Наблюдать — clicks < 5 (not enough data)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

Decision = Literal["amplify", "disable", "reduce", "watch"]

DECISION_LABELS = {
    "amplify": "🟢 Усилить",
    "disable": "🔴 Отключить",
    "reduce": "🟡 Ослабить",
    "watch": "⚪ Наблюдать",
}

MIN_CLICKS_FOR_SIGNAL = 5
DISABLE_WEEKS = 3  # consecutive weeks needed to trigger disable


@dataclass
class KeywordWeekData:
    keyword: str
    campaign: str
    sessions: int = 0
    impressions: int = 0
    clicks: int = 0
    cost: float = 0.0
    ql: int = 0
    ksf: bool = False


@dataclass
class KeywordDecision:
    keyword: str
    campaign: str
    decision: Decision
    reason: str
    cur: KeywordWeekData
    history_weeks: list[KeywordWeekData] = field(default_factory=list)

    @property
    def label(self) -> str:
        return DECISION_LABELS[self.decision]


def _key(keyword: str, campaign: str) -> str:
    return f"{keyword.lower().strip()}||{campaign.lower().strip()}"


def _history_for_key(
    keyword: str,
    campaign: str,
    history: dict[str, list[dict]],
    current_week: str,
) -> list[KeywordWeekData]:
    """Return past weeks' data for a keyword (excluding current week), newest first."""
    weeks_sorted = sorted(
        [w for w in history.keys() if w != current_week],
        reverse=True,
    )
    result = []
    for week in weeks_sorted:
        for row in history[week]:
            if (
                row.get("keyword", "").lower().strip() == keyword.lower().strip()
                and row.get("campaign", "").lower().strip() == campaign.lower().strip()
            ):
                result.append(
                    KeywordWeekData(
                        keyword=row["keyword"],
                        campaign=row["campaign"],
                        sessions=int(row.get("sessions", 0)),
                        impressions=int(row.get("impressions", 0)),
                        clicks=int(row.get("clicks", 0)),
                        cost=float(row.get("cost", 0.0)),
                        ql=int(row.get("ql", 0)),
                        ksf=bool(row.get("ksf", False)),
                    )
                )
            break  # at most one row per week per key
    return result


def decide(cur: KeywordWeekData, history_weeks: list[KeywordWeekData]) -> tuple[Decision, str]:
    """Apply decision rules. Returns (decision, reason).

    When clicks = 0 (e.g. GA4 not linked to Ads), sessions are used as a proxy.
    """
    # Use clicks if available, else fall back to sessions as a traffic proxy
    traffic = cur.clicks if cur.clicks > 0 else cur.sessions
    traffic_label = "кликов" if cur.clicks > 0 else "сессий"

    # Not enough traffic — watch only
    if traffic < MIN_CLICKS_FOR_SIGNAL and cur.ql == 0 and not cur.ksf:
        return "watch", f"{traffic_label.capitalize()} < {MIN_CLICKS_FOR_SIGNAL} ({traffic}), недостаточно данных"

    # Has QL or KSF this week → amplify
    if cur.ql > 0:
        return "amplify", f"QL = {cur.ql} в текущей неделе"
    if cur.ksf:
        return "amplify", "KSF-стадия зафиксирована в текущей неделе"

    # Check rolling 3-week disable rule
    # Need DISABLE_WEEKS consecutive weeks including current with traffic>5, QL=0, no KSF
    bad_weeks = 0
    if traffic >= MIN_CLICKS_FOR_SIGNAL and cur.ql == 0 and not cur.ksf:
        bad_weeks = 1

    for hw in history_weeks[: DISABLE_WEEKS - 1]:
        hw_traffic = hw.clicks if hw.clicks > 0 else hw.sessions
        if hw_traffic >= MIN_CLICKS_FOR_SIGNAL and hw.ql == 0 and not hw.ksf:
            bad_weeks += 1
        else:
            break  # must be consecutive

    if bad_weeks >= DISABLE_WEEKS:
        total_cost = cur.cost + sum(hw.cost for hw in history_weeks[: DISABLE_WEEKS - 1])
        return (
            "disable",
            f"{bad_weeks} нед подряд: QL=0, KSF нет, кликов ≥ {MIN_CLICKS_FOR_SIGNAL}. "
            f"Расход за {bad_weeks} нед: €{total_cost:.0f}",
        )

    # Not enough history for disable, no QL → reduce
    return (
        "reduce",
        f"QL=0, KSF нет. История: {len(history_weeks)} нед (нужно {DISABLE_WEEKS} для отключения)",
    )


def make_decisions(
    current_week: str,
    cur_rows: list[dict],
    ql_by_key: dict[str, int],
    ksf_by_key: dict[str, bool],
    history: dict[str, list[dict]],
) -> list[KeywordDecision]:
    """Build decisions for all keywords in cur_rows.

    Args:
        current_week: ISO week string, e.g. '2026-W16'
        cur_rows: list of GA4 rows {keyword, campaign, sessions, impressions, clicks, cost}
        ql_by_key: {f"{keyword}||{campaign}": ql_count} from Monday
        ksf_by_key: {f"{keyword}||{campaign}": True/False} from Monday
        history: Outline history dict {week → [rows]}
    """
    decisions = []

    for row in cur_rows:
        kw = row.get("keyword", "")
        camp = row.get("campaign", "")
        k = _key(kw, camp)

        cur = KeywordWeekData(
            keyword=kw,
            campaign=camp,
            sessions=row.get("sessions", 0),
            impressions=row.get("impressions", 0),
            clicks=row.get("clicks", 0),
            cost=row.get("cost", 0.0),
            ql=ql_by_key.get(k, 0),
            ksf=ksf_by_key.get(k, False),
        )

        hist = _history_for_key(kw, camp, history, current_week)
        decision, reason = decide(cur, hist)

        decisions.append(
            KeywordDecision(
                keyword=kw,
                campaign=camp,
                decision=decision,
                reason=reason,
                cur=cur,
                history_weeks=hist,
            )
        )

    # Sort: amplify first, then disable, reduce, watch; within each group by clicks/sessions desc
    order = ["amplify", "disable", "reduce", "watch"]
    decisions.sort(key=lambda d: (order.index(d.decision), -(d.cur.clicks or d.cur.sessions)))
    return decisions


def build_history_rows(
    cur_rows: list[dict],
    ql_by_key: dict[str, int],
    ksf_by_key: dict[str, bool],
    decisions_by_key: dict[str, str] | None = None,
) -> list[dict]:
    """Build rows to store in Outline for the current week."""
    result = []
    for row in cur_rows:
        kw = row.get("keyword", "")
        camp = row.get("campaign", "")
        k = _key(kw, camp)
        result.append(
            {
                "keyword": kw,
                "campaign": camp,
                "sessions": row.get("sessions") or 0,
                "impressions": row.get("impressions") or 0,
                "clicks": row.get("clicks") or 0,
                "cost": row.get("cost") or 0.0,
                "ql": ql_by_key.get(k, 0),
                "ksf": ksf_by_key.get(k, False),
                "decision": decisions_by_key.get(k, "") if decisions_by_key else "",
            }
        )
    return result

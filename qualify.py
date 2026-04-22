"""
qualify.py — Lead qualification logic (QL / DQL).

QL = lead replied substantively (incl. reply to TimelinesAI bot)
   OR sales missed SLA deadline (auto-QL, can't blame the channel).
DQL = SLA met by sales, but lead never replied.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PRAGUE = ZoneInfo("Europe/Prague")

# Lost-lead reasons that still indicate a qualified lead (lead engaged, chose differently).
LOST_QL_REASONS = {
    "Decided to buy from others",
    "Not fit for us",
}

# Duplicate entries — exclude from QL/DQL counts entirely.
DUPLICATE_REASON = "Duplicated"

# Patterns in manager notes indicating the lead replied verbally or by phone.
PHONE_RESPONSE_PATTERNS = [
    "said that", "told us", "confirmed via phone", "called back",
    "she said", "he said", "they said", "client said", "lead said",
    "сказал", "сказала",
]

# Partial lowercase name tokens that identify Typhoon team members in Monday updates.
# The creator.name field is matched against these. Add new team members here.
MANAGER_TOKENS = [
    "nikolas", "krutin",
    "daniil", "samsonov",
    "igor", "emelyanow", "stepanov",
    "alexander", "alex", "savelov", "dedyukhin",
    "tigran", "nersesian",
    "daria",
    "lina", "iangildina",
    "arina",
    "vlad",
    "danil", "teterin",
    "nikita", "vyguzov",
    "typhoon",  # generic team account
]

# WA sender prefixes that identify OUTBOUND messages (us → lead) in TimelinesAI syncs.
OUTBOUND_WA_SENDERS = ["typhoon roaster", "typhoon coffee"]


def _manager_noted_phone_response(text: str) -> bool:
    """Return True if a manager note records a verbal/phone response from the lead."""
    low = text.lower()
    return any(pat in low for pat in PHONE_RESPONSE_PATTERNS)


def _is_manager(creator_name: str) -> bool:
    low = creator_name.lower()
    return any(tok in low for tok in MANAGER_TOKENS)


def _is_timelines_creator(creator_name: str) -> bool:
    return "timelines" in creator_name.lower()


def _is_pasted_lead_email(text: str) -> bool:
    """Return True if a manager-posted update looks like a pasted inbound lead email.

    Two signals:
    1. Update starts with "message:" or "message :" — explicit manager convention
       for pasting lead emails/messages into Monday.
    2. Any of the first 5 non-empty lines starts with a greeting addressed to
       a manager by name, e.g. "Hello Vladislav," or "hi lina,".
    """
    stripped = text.strip()

    # Signal 1: "message:" prefix used by team when pasting lead messages
    lower = stripped.lower()
    if lower.startswith("message:") or lower.startswith("message :"):
        # Make sure there's actual content after the prefix (not just manager notes)
        body = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
        if len(body) > 20:  # enough content to be a real message
            return True

    # Signal 2: greeting addressed to a manager by name
    greetings = ("hello ", "hi ", "dear ", "good morning ", "good afternoon ", "good evening ", "hey ")
    non_empty_lines = [l.strip().lower() for l in text.split("\n") if l.strip()]
    for line in non_empty_lines[:5]:
        for greeting in greetings:
            if line.startswith(greeting):
                rest = line[len(greeting):]
                if any(tok in rest for tok in MANAGER_TOKENS):
                    return True
    return False


def _has_lead_inbound_wa(text: str) -> bool:
    """Return True if a TimelinesAI update block contains an INBOUND lead message.

    TimelinesAI format:
      Outbound: "Typhoon Roaster wrote at HH:MM: <our message>"
      Inbound:  "+34612345678 wrote at HH:MM: <lead message>"
               "Lead Name wrote at HH:MM: <lead message>"
    """
    for match in re.finditer(r"([^\n]{1,80}?)\s+wrote at \d{1,2}:\d{2}", text, re.IGNORECASE):
        sender = match.group(1).strip()
        sender_low = sender.lower()
        # Skip if it's one of our outbound senders or a manager name
        if any(s in sender_low for s in OUTBOUND_WA_SENDERS):
            continue
        if _is_manager(sender):
            continue
        # Anything else (phone number, unknown name) = inbound from lead
        return True
    return False


def lead_replied(updates: list) -> bool:
    """Return True if the lead responded to any of our outreach messages."""
    for u in updates:
        text = (u.get("text_body") or "").strip()
        creator = u.get("creator", {}).get("name", "")

        if not text:
            continue

        # Case 1: non-manager, non-TimelinesAI creator wrote something → lead in Monday
        if not _is_manager(creator) and not _is_timelines_creator(creator):
            return True

        # Case 2: TimelinesAI update contains an inbound WA from the lead
        if "wrote at" in text.lower() and _has_lead_inbound_wa(text):
            return True

        # Case 3: manager pasted an inbound email from the lead as a note
        if _is_manager(creator) and _is_pasted_lead_email(text):
            return True

        # Case 4: manager recorded a verbal/phone response from the lead
        if _is_manager(creator) and _manager_noted_phone_response(text):
            return True

        # Check replies on each update too
        for reply in u.get("replies", []):
            r_text = (reply.get("text_body") or "").strip()
            r_creator = reply.get("creator", {}).get("name", "")
            if r_text and not _is_manager(r_creator) and not _is_timelines_creator(r_creator):
                return True

    return False


def first_manager_response_dt(updates: list):
    """Return datetime of first live manager reply (TimelinesAI auto-sends excluded)."""
    sorted_updates = sorted(updates, key=lambda x: x.get("created_at", ""))
    for u in sorted_updates:
        creator = u.get("creator", {}).get("name", "")
        text = (u.get("text_body") or "").strip()
        if _is_timelines_creator(creator):
            continue
        if _is_manager(creator) and text:
            try:
                return datetime.fromisoformat(u["created_at"].replace("Z", "+00:00"))
            except Exception:
                pass
    return None


def compute_sla_deadline(created_at: datetime) -> datetime:
    """Compute the SLA deadline for first manager response (Prague TZ rules)."""
    local = created_at.astimezone(PRAGUE)
    wd = local.weekday()  # 0=Mon … 6=Sun

    if wd <= 3:  # Mon–Thu: same calendar day 23:59 Prague
        deadline_local = local.replace(hour=23, minute=59, second=0, microsecond=0)
    elif wd == 4:  # Fri: next Monday 23:59
        deadline_local = (local + timedelta(days=3)).replace(hour=23, minute=59, second=0, microsecond=0)
    else:  # Sat(5) → +2 days = Mon;  Sun(6) → +1 day = Mon
        days_ahead = 7 - wd  # Sat→2, Sun→1
        deadline_local = (local + timedelta(days=days_ahead)).replace(hour=23, minute=59, second=0, microsecond=0)

    return deadline_local.astimezone(timezone.utc)


# Monday group IDs that represent late-funnel stages — leads here are QL by definition.
KSF_GROUP_IDS = {
    "______________21620",   # KFS confirmed
    "______________29875",   # Conducted KSF / presented offer
    "group_mm13e5mp",        # Agreed to buy this quarter
}

# Groups where manager actively took the lead into work — cannot be DQL.
ACTIVE_GROUP_IDS = {
    "______________11739",   # Manager assigned
    "______________43464",   # Taken into work
}


def classify_lead(item: dict, updates: list, to_lost_leads3: str = "") -> dict:
    """Classify a lead.

    Returns a dict with keys:
      status      : "QL" | "DQL" | "SKIP"
      reason      : "lead_responded" | "sales_timeout" | "ksf_stage" | "manager_active"
                    | "no_response" | "duplicate" | "lost_but_engaged"
      sla_met     : bool
      sla_deadline: datetime (UTC)
      first_resp  : datetime | None (UTC)

    SKIP means the lead is a duplicate and should be excluded from all counts.
    """
    raw_created = item.get("created_at", "")
    try:
        created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
    except Exception:
        return {"status": "DQL", "reason": "no_created_at", "sla_met": False,
                "sla_deadline": None, "first_resp": None}

    sla_deadline = compute_sla_deadline(created_at)
    first_resp = first_manager_response_dt(updates)
    sla_met = first_resp is not None and first_resp <= sla_deadline

    # Duplicate entries are excluded entirely — neither QL nor DQL
    if to_lost_leads3.strip() == DUPLICATE_REASON:
        return {
            "status": "SKIP",
            "reason": "duplicate",
            "sla_met": sla_met,
            "sla_deadline": sla_deadline,
            "first_resp": first_resp,
        }

    group_id = item.get("group", {}).get("id", "")

    # KSF stage = lead reached product presentation → definitely qualified
    if group_id in KSF_GROUP_IDS:
        return {
            "status": "QL",
            "reason": "ksf_stage",
            "sla_met": sla_met,
            "sla_deadline": sla_deadline,
            "first_resp": first_resp,
        }

    # Active group = manager took lead into work (manual entry or assigned) → not DQL
    if group_id in ACTIVE_GROUP_IDS:
        return {
            "status": "QL",
            "reason": "manager_active",
            "sla_met": sla_met,
            "sla_deadline": sla_deadline,
            "first_resp": first_resp,
        }

    if not sla_met:
        return {
            "status": "QL",
            "reason": "sales_timeout",
            "sla_met": False,
            "sla_deadline": sla_deadline,
            "first_resp": first_resp,
        }

    if lead_replied(updates):
        return {
            "status": "QL",
            "reason": "lead_responded",
            "sla_met": True,
            "sla_deadline": sla_deadline,
            "first_resp": first_resp,
        }

    # Lead never replied but ended up in lost with a known QL reason (e.g. bought elsewhere).
    # This handles edge cases where the verbal response wasn't logged as an update.
    if to_lost_leads3.strip() in LOST_QL_REASONS:
        return {
            "status": "QL",
            "reason": "lost_but_engaged",
            "sla_met": sla_met,
            "sla_deadline": sla_deadline,
            "first_resp": first_resp,
        }

    return {
        "status": "DQL",
        "reason": "no_response",
        "sla_met": True,
        "sla_deadline": sla_deadline,
        "first_resp": first_resp,
    }

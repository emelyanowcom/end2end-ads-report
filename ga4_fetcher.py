"""
ga4_fetcher.py — GA4 Data API client for keyword-level performance data.

Fetches sessions, impressions, clicks, and ad cost per keyword+campaign
for a given date range using the Google Analytics Data API v1.

Requirements:
  pip install google-analytics-data
"""

from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Any

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.oauth2 import service_account


def _client(key_file: str) -> BetaAnalyticsDataClient:
    creds = service_account.Credentials.from_service_account_file(
        key_file,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return BetaAnalyticsDataClient(credentials=creds)


def _run_report(client, property_id, dimensions, metrics, from_date, to_date):
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=dimensions,
        metrics=metrics,
        date_ranges=[DateRange(start_date=from_date.isoformat(), end_date=to_date.isoformat())],
        keep_empty_rows=False,
    )
    return client.run_report(request)


def fetch_keyword_data(
    property_id: str,
    key_file: str,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    """Fetch keyword-level data from GA4.

    Makes two requests:
    1. sessionGoogleAdsKeyword + sessionCampaignName → sessions
    2. sessionCampaignName → advertiserAdImpressions, advertiserAdClicks, advertiserAdCost
       (ad metrics are incompatible with keyword dimension, so fetched at campaign level)

    Returns list of dicts:
      {keyword, campaign, sessions, impressions, clicks, cost}

    impressions/clicks/cost are campaign-level totals (apportioned evenly across
    keywords if multiple keywords in same campaign). Returns 0 if GA4 not linked to Ads.
    """
    client = _client(key_file)

    # Request 1: sessions by keyword + campaign
    resp1 = _run_report(
        client, property_id,
        [Dimension(name="sessionGoogleAdsKeyword"), Dimension(name="sessionCampaignName")],
        [Metric(name="sessions")],
        from_date, to_date,
    )

    # Build keyword → campaign → sessions map
    kw_data: dict[str, dict] = {}
    for row in resp1.rows:
        keyword = row.dimension_values[0].value
        campaign = row.dimension_values[1].value
        if keyword in ("(not set)", "(not provided)", ""):
            continue
        sessions = int(row.metric_values[0].value or 0)
        key = f"{keyword}||{campaign}"
        kw_data[key] = {
            "keyword": keyword,
            "campaign": campaign,
            "sessions": sessions,
            "impressions": 0,
            "clicks": 0,
            "cost": 0.0,
        }

    # Request 2: ad metrics by campaign (compatible combination)
    try:
        resp2 = _run_report(
            client, property_id,
            [Dimension(name="sessionCampaignName")],
            [
                Metric(name="advertiserAdImpressions"),
                Metric(name="advertiserAdClicks"),
                Metric(name="advertiserAdCost"),
            ],
            from_date, to_date,
        )

        # Map campaign → ad metrics
        camp_ads: dict[str, dict] = {}
        for row in resp2.rows:
            camp = row.dimension_values[0].value
            camp_ads[camp] = {
                "impressions": int(row.metric_values[0].value or 0),
                "clicks": int(row.metric_values[1].value or 0),
                "cost": float(row.metric_values[2].value or 0.0),
            }

        # Count keywords per campaign for proportional attribution
        camp_kw_count: dict[str, int] = {}
        for entry in kw_data.values():
            c = entry["campaign"]
            camp_kw_count[c] = camp_kw_count.get(c, 0) + 1

        # Apportion campaign-level ad metrics across keywords by session share
        camp_sessions: dict[str, int] = {}
        for entry in kw_data.values():
            c = entry["campaign"]
            camp_sessions[c] = camp_sessions.get(c, 0) + entry["sessions"]

        for entry in kw_data.values():
            c = entry["campaign"]
            ads = camp_ads.get(c)
            if not ads:
                continue
            total_sess = camp_sessions.get(c, 0)
            share = entry["sessions"] / total_sess if total_sess > 0 else 0.0
            entry["impressions"] = round(ads["impressions"] * share)
            entry["clicks"] = round(ads["clicks"] * share)
            entry["cost"] = round(ads["cost"] * share, 2)

    except Exception as exc:
        print(f"  WARN: GA4 ad metrics not available ({exc}); using sessions only")

    results = list(kw_data.values())
    # Sort by sessions desc (clicks will be 0 if ads not linked)
    results.sort(key=lambda r: -(r["clicks"] or r["sessions"]))
    return results


def fetch_keyword_data_safe(
    property_id: str,
    key_file: str,
    from_date: date,
    to_date: date,
) -> tuple[list[dict[str, Any]], str | None]:
    """Like fetch_keyword_data but returns (results, error_message).

    error_message is None on success, or a string describing the problem.
    """
    key_path = Path(key_file)
    if not key_path.exists():
        return [], f"GA4 key file not found: {key_file}"
    try:
        data = fetch_keyword_data(property_id, key_file, from_date, to_date)
        return data, None
    except Exception as exc:
        return [], str(exc)

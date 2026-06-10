"""
Backlog source: the repo's own GitHub issues. The Sunday retro suggests the
oldest open 'next'-labeled issue — at most one suggestion per week.
"""

import logging

import httpx

from cos import clean_env

log = logging.getLogger("family-cos")

REPO = "Atlspartan33/school-alerts"


def fetch_backlog_suggestion() -> dict | None:
    """Return {"number", "title", "summary"} for the oldest open 'next' issue."""
    headers = {"Accept": "application/vnd.github+json"}
    token = clean_env("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = httpx.get(
        f"https://api.github.com/repos/{REPO}/issues",
        params={"labels": "next", "state": "open", "sort": "created",
                "direction": "asc", "per_page": 1},
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    issues = resp.json()
    if not issues:
        return None

    issue = issues[0]
    body = (issue.get("body") or "").strip().replace("\n", " ")
    return {
        "number": issue["number"],
        "title": issue["title"],
        "summary": body[:300],
    }

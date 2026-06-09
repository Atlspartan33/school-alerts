"""
School calendar source: published ICS feeds (config.SCHOOL_ICS_URLS).
Catches school events that never arrive by email. Minimal ICS parsing —
enough for VEVENT summary/date feeds; no external dependency.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

import config

log = logging.getLogger("family-cos")


def _unfold(text: str) -> str:
    """ICS folds long lines with CRLF + space; unfold them."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_dt(value: str) -> tuple[str, bool]:
    """Parse an ICS DTSTART/DTEND value. Returns (iso_string, all_day)."""
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):          # date only
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}", True
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if m:
        d, t, z = m.groups()
        iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{t[4:6]}"
        return (iso + "+00:00" if z else iso), False
    return value, False


def fetch_school_events(days_ahead: int | None = None) -> list[dict]:
    """Fetch upcoming events from all configured school ICS feeds."""
    if not config.SCHOOL_ICS_URLS:
        return []

    days_ahead = days_ahead if days_ahead is not None else config.CALENDAR_DAYS_AHEAD
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=days_ahead)

    events = []
    for url in config.SCHOOL_ICS_URLS:
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            text = _unfold(resp.text)
        except Exception as e:
            log.error(f"ICS fetch failed for {url}: {e}")
            continue

        for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.DOTALL):
            props = {}
            for line in block.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                props[key.split(";")[0].upper()] = value.strip()

            if "DTSTART" not in props:
                continue
            start_iso, all_day = _parse_dt(props["DTSTART"])

            try:
                start_dt = datetime.fromisoformat(start_iso)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                if start_dt < now - timedelta(days=1) or start_dt > window_end:
                    continue
            except ValueError:
                continue

            end_iso = ""
            if "DTEND" in props:
                end_iso, _ = _parse_dt(props["DTEND"])

            events.append({
                "title": props.get("SUMMARY", "(no title)"),
                "start": start_iso,
                "end": end_iso,
                "location": props.get("LOCATION", ""),
                "all_day": all_day,
                "calendar": "School calendar",
            })

    events.sort(key=lambda e: e["start"])
    return events

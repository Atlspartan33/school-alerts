"""
Telegram notification sender via Bot API.
"""

import logging
import os
import time

import httpx

log = logging.getLogger("school-alerts")


def _send_one(url: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:
    """Send a single Telegram message with one retry on failure."""
    payload = {"chat_id": chat_id, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(2):
        try:
            resp = httpx.post(url, json=payload, timeout=15)
            data = resp.json()
            if data.get("ok"):
                return True
            log.warning(f"Telegram API error (attempt {attempt + 1}): {data.get('description')}")
            # If HTML parsing failed, retry without parse_mode
            if data.get("error_code") == 400 and "parse" in data.get("description", "").lower():
                payload.pop("parse_mode", None)
        except httpx.HTTPError as e:
            log.warning(f"Telegram HTTP error (attempt {attempt + 1}): {e}")

        if attempt == 0:
            time.sleep(2)

    return False


def send_telegram(message: str) -> bool:
    """Send a Telegram message to all configured chat IDs. Returns True if all succeed."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_ids = os.environ["TELEGRAM_CHAT_IDS"].split(",")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    success = True

    for chat_id in chat_ids:
        chat_id = chat_id.strip()
        if not chat_id:
            continue
        if not _send_one(url, chat_id, message):
            log.error(f"Failed to send Telegram message to {chat_id} after 2 attempts")
            success = False

    return success


def send_telegram_plain(message: str) -> bool:
    """Send a plain-text Telegram message (no HTML). Used for system alerts."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_ids = os.environ["TELEGRAM_CHAT_IDS"].split(",")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    success = True

    for chat_id in chat_ids:
        chat_id = chat_id.strip()
        if not chat_id:
            continue
        if not _send_one(url, chat_id, message, parse_mode=""):
            success = False

    return success

"""
Telegram delivery with recipient routing and message chunking.

Recipients:
  TELEGRAM_CHAT_IDS — comma-separated chat IDs. Today this is Terrell;
  to add Kim, she messages the bot once, then append her chat ID here.
"""

import logging
import os
import time

import httpx

log = logging.getLogger("family-cos")

TELEGRAM_MAX_LEN = 4096

# Set by --dry-run: print instead of sending.
DRY_RUN = False


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


def _chunks(message: str) -> list[str]:
    """Split a message at newlines to stay under Telegram's length limit."""
    if len(message) <= TELEGRAM_MAX_LEN:
        return [message]
    parts = []
    rest = message
    while len(rest) > TELEGRAM_MAX_LEN:
        cut = rest.rfind("\n", 0, TELEGRAM_MAX_LEN - 96)
        if cut == -1:
            cut = TELEGRAM_MAX_LEN - 96
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        parts.append(rest)
    return parts


def _send(message: str, parse_mode: str) -> bool:
    if DRY_RUN:
        import sys
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print("\n----- DRY RUN: Telegram message -----")
        print(message)
        print("----- end message -----\n")
        return True

    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_ids = os.environ["TELEGRAM_CHAT_IDS"].strip().split(",")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    success = True

    for chat_id in chat_ids:
        chat_id = chat_id.strip()
        if not chat_id:
            continue
        for chunk in _chunks(message):
            if not _send_one(url, chat_id, chunk, parse_mode):
                log.error(f"Failed to send Telegram message to {chat_id}")
                success = False
                break

    return success


def send_telegram(message: str) -> bool:
    """Send an HTML-formatted Telegram message to all configured recipients."""
    return _send(message, parse_mode="HTML")


def send_telegram_plain(message: str) -> bool:
    """Send a plain-text Telegram message (system alerts, briefs)."""
    return _send(message, parse_mode="")

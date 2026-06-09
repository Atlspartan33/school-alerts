"""
Telegram delivery and bot API access.

Outbound: alerts (HTML, optional inline buttons), plain-text briefs and
system messages, per-person sends.
Inbound: getUpdates polling and callback acknowledgment for the inbox.

Recipients:
  TELEGRAM_CHAT_IDS — comma-separated broadcast list (alerts, system messages).
  TELEGRAM_CHAT_ID_TERRELL / TELEGRAM_CHAT_ID_KIM — optional, enable
  per-person briefs.
"""

import logging
import time

import httpx

import config
from cos import clean_env

log = logging.getLogger("family-cos")

TELEGRAM_MAX_LEN = 4096

# Set by --dry-run: print instead of sending.
DRY_RUN = False


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{clean_env('TELEGRAM_BOT_TOKEN')}/{method}"


def broadcast_chat_ids() -> list[str]:
    return [c.strip() for c in clean_env("TELEGRAM_CHAT_IDS").split(",") if c.strip()]


def person_chat_ids() -> dict[str, str]:
    """Map of person name -> chat id, for everyone with a per-person env var set."""
    out = {}
    for name, env_var in config.PEOPLE.items():
        chat_id = clean_env(env_var)
        if chat_id:
            out[name] = chat_id
    return out


def known_chat_ids() -> set[str]:
    """Every chat id we're allowed to talk to (inbox uses this as an allowlist)."""
    return set(broadcast_chat_ids()) | set(person_chat_ids().values())


def _print_dry_run(message: str, extra: str = ""):
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"\n----- DRY RUN: Telegram message {extra}-----")
    print(message)
    print("----- end message -----\n")


def _send_one(chat_id: str, message: str, parse_mode: str = "HTML",
              buttons: list[list[dict]] | None = None) -> bool:
    """Send a single Telegram message with one retry on failure."""
    payload = {"chat_id": chat_id, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    for attempt in range(2):
        try:
            resp = httpx.post(_api_url("sendMessage"), json=payload, timeout=15)
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


def send_to_chat(chat_id: str, message: str, parse_mode: str = "",
                 buttons: list[list[dict]] | None = None) -> bool:
    """Send one message to one chat, chunking long messages (buttons go on the last chunk)."""
    if DRY_RUN:
        extra = f"to {chat_id} "
        if buttons:
            extra += f"with buttons {[[b['text'] for b in row] for row in buttons]} "
        _print_dry_run(message, extra)
        return True

    chunks = _chunks(message)
    for i, chunk in enumerate(chunks):
        chunk_buttons = buttons if i == len(chunks) - 1 else None
        if not _send_one(chat_id, chunk, parse_mode, chunk_buttons):
            log.error(f"Failed to send Telegram message to {chat_id}")
            return False
    return True


def _send_broadcast(message: str, parse_mode: str,
                    buttons: list[list[dict]] | None = None) -> bool:
    if DRY_RUN:
        extra = f"with buttons {[[b['text'] for b in row] for row in buttons]} " if buttons else ""
        _print_dry_run(message, extra)
        return True

    success = True
    for chat_id in broadcast_chat_ids():
        if not send_to_chat(chat_id, message, parse_mode, buttons):
            success = False
    return success


def send_telegram(message: str, buttons: list[list[dict]] | None = None) -> bool:
    """Send an HTML-formatted message (optionally with inline buttons) to the broadcast list."""
    return _send_broadcast(message, parse_mode="HTML", buttons=buttons)


def send_telegram_plain(message: str) -> bool:
    """Send a plain-text message (system alerts, briefs) to the broadcast list."""
    return _send_broadcast(message, parse_mode="")


# --- Inbound (inbox polling) ---

def get_updates(offset: int | None) -> list[dict]:
    """Fetch pending updates (messages + button presses) from Telegram."""
    params = {"timeout": 0, "allowed_updates": '["message","callback_query"]'}
    if offset:
        params["offset"] = offset
    try:
        resp = httpx.get(_api_url("getUpdates"), params=params, timeout=20)
        data = resp.json()
        if not data.get("ok"):
            log.error(f"getUpdates failed: {data.get('description')}")
            return []
        return data.get("result", [])
    except httpx.HTTPError as e:
        log.error(f"getUpdates HTTP error: {e}")
        return []


def answer_callback(callback_query_id: str, text: str = ""):
    """Acknowledge an inline-button press (clears the loading spinner)."""
    if DRY_RUN:
        return
    try:
        httpx.post(_api_url("answerCallbackQuery"),
                   json={"callback_query_id": callback_query_id, "text": text[:200]},
                   timeout=15)
    except httpx.HTTPError as e:
        log.warning(f"answerCallbackQuery failed: {e}")

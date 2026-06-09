"""
Telegram inbox: process button presses and incoming messages.

Handles three kinds of input (only from known family chat IDs):
  - Inline button callbacks on alerts: cal:<id>, task:<id>, done:<id>
  - "done ..." style replies and free-text questions (AMA) — both go through
    Claude, which can answer and/or act (create task/event, mark alert done)

Polled by inbox.py (workflow every ~5 min), so replies are near-real-time,
not instant.
"""

import logging

import config
from cos import delivery
from cos.intelligence import answer_question, parse_event_from_alert
from cos.state import (
    get_alert, get_recent_alerts, get_telegram_offset, mark_alert_done,
    set_telegram_offset,
)

log = logging.getLogger("family-cos")


def alert_buttons(alert_id: str, has_calendar_suggestion: bool) -> list[list[dict]]:
    """Inline keyboard attached to every school alert."""
    row = []
    if has_calendar_suggestion:
        row.append({"text": "📅 Add to calendar", "callback_data": f"cal:{alert_id}"})
    row.append({"text": "➕ Make task", "callback_data": f"task:{alert_id}"})
    row.append({"text": "✅ Done", "callback_data": f"done:{alert_id}"})
    return [row]


def _handle_callback(cb: dict, state: dict, services: dict) -> None:
    """Handle one inline-button press."""
    data = cb.get("data", "")
    chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    cb_id = cb.get("id", "")

    if chat_id not in delivery.known_chat_ids():
        log.warning(f"Ignoring callback from unknown chat {chat_id}")
        delivery.answer_callback(cb_id)
        return

    action, _, alert_id = data.partition(":")
    alert = get_alert(state, alert_id)
    if alert is None:
        delivery.answer_callback(cb_id, "That alert has expired (older than 7 days).")
        return

    try:
        if action == "done":
            mark_alert_done(state, alert_id)
            delivery.answer_callback(cb_id, "Cleared ✅")
            delivery.send_to_chat(chat_id, f'✅ Cleared: "{alert["headline"]}"')

        elif action == "task":
            if delivery.DRY_RUN:
                delivery.send_to_chat(chat_id, f'[dry-run] would create task from "{alert["headline"]}"')
                return
            confirmation = _create_task_from_alert(alert)
            mark_alert_done(state, alert_id)
            delivery.answer_callback(cb_id, "Task created")
            delivery.send_to_chat(chat_id, f"➕ {confirmation}")

        elif action == "cal":
            event = parse_event_from_alert(alert)
            if event is None:
                delivery.answer_callback(cb_id)
                delivery.send_to_chat(
                    chat_id,
                    f'Couldn\'t find a concrete date in "{alert["headline"]}" — '
                    "add it manually or reply with the date.",
                )
                return
            if delivery.DRY_RUN:
                delivery.send_to_chat(chat_id, f"[dry-run] would create calendar event: {event}")
                return
            from cos.actions import create_calendar_event
            confirmation = create_calendar_event(services["calendar"], event)
            delivery.answer_callback(cb_id, "Added 📅")
            delivery.send_to_chat(chat_id, f"📅 {confirmation}")

        else:
            delivery.answer_callback(cb_id)

    except Exception as e:
        log.error(f"Callback {data} failed: {e}")
        delivery.answer_callback(cb_id)
        delivery.send_to_chat(chat_id, f"⚠️ That didn't work: {e}")


def _create_task_from_alert(alert: dict) -> str:
    from cos.actions import create_monday_task
    name = alert.get("headline") or "School follow-up"
    child = alert.get("child", "")
    if child and child.lower() != "general" and child.lower() not in name.lower():
        name = f"{child}: {name}"
    due = None
    dates = alert.get("dates")
    if dates and len(str(dates)) >= 10:
        # Only pass through if it already looks like an ISO date
        candidate = str(dates)[:10]
        if candidate[4:5] == "-" and candidate[7:8] == "-":
            due = candidate
    return create_monday_task(name, config.DEFAULT_TASK_GROUP, due)


def _handle_message(msg: dict, state: dict, services: dict) -> None:
    """Handle one free-text message (AMA / done-replies / add requests)."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()

    if chat_id not in delivery.known_chat_ids():
        log.warning(f"Ignoring message from unknown chat {chat_id}")
        return
    if not text or text.startswith("/"):
        return

    # Gather fresh context for the answer
    calendar_events, monday_items = [], []
    try:
        from cos.sources.gcal import fetch_upcoming_events
        calendar_events = fetch_upcoming_events(services["calendar"])
    except Exception as e:
        log.warning(f"AMA: calendar unavailable: {e}")
    try:
        from cos.sources.monday import fetch_monday_items
        monday_items = fetch_monday_items()
    except Exception as e:
        log.warning(f"AMA: monday unavailable: {e}")

    open_alerts = [a for a in get_recent_alerts(state) if a.get("status") == "open"]

    result = answer_question(text, calendar_events, monday_items, open_alerts)

    confirmations = []
    for action in result.get("actions", []):
        try:
            kind = action.get("type")
            if delivery.DRY_RUN and kind in ("create_task", "create_event"):
                confirmations.append(f"[dry-run] would execute: {action}")
                continue
            if kind == "create_task":
                from cos.actions import create_monday_task
                confirmations.append(create_monday_task(
                    action["name"], action.get("group"), action.get("due")))
            elif kind == "create_event":
                from cos.actions import create_calendar_event
                confirmations.append(create_calendar_event(services["calendar"], action))
            elif kind == "mark_done":
                if mark_alert_done(state, action.get("alert_id", "")):
                    confirmations.append("Marked done ✅")
        except Exception as e:
            log.error(f"AMA action {action} failed: {e}")
            confirmations.append(f"⚠️ Couldn't complete that: {e}")

    reply = result.get("reply", "")
    if confirmations:
        reply = (reply + "\n\n" + "\n".join(confirmations)).strip()
    delivery.send_to_chat(chat_id, reply)


def process_updates(state: dict, services: dict) -> int:
    """Fetch and handle pending Telegram updates. Returns how many were handled."""
    offset = get_telegram_offset(state)
    updates = delivery.get_updates(offset)
    if not updates:
        return 0

    handled = 0
    for update in updates:
        try:
            if "callback_query" in update:
                _handle_callback(update["callback_query"], state, services)
                handled += 1
            elif "message" in update:
                _handle_message(update["message"], state, services)
                handled += 1
        except Exception as e:
            log.error(f"Failed to handle update {update.get('update_id')}: {e}")
        set_telegram_offset(state, update["update_id"] + 1)

    return handled

# Family Chief of Staff

One system, three pipelines, for keeping the Massey household running:

| Pipeline | Entry | Schedule | What it does |
|----------|-------|----------|--------------|
| **School alerts** | `main.py` | Every 15 min | Watches Gmail for school emails, classifies with Claude, sends Telegram alerts with action buttons (📅 Add to calendar · ➕ Make task · ✅ Done) |
| **Daily brief** | `briefing.py` | Daily ~7 AM ET | Calendar + Monday.com board + action emails + school-alert memory → one opinionated brief. Per-person versions for Terrell and Kim when configured. Sunday = retro + week preview. |
| **Telegram inbox** | `inbox.py` | Every 5 min | Handles button presses and replies: "done with the field trip form", "what's Thursday look like?", "add dentist to the calendar Fri 2pm", "remind me to pay the HOA" |

The pipelines share state (a GitHub gist): every school alert is remembered
for 7 days with an open/done status. The brief nudges open items and
escalates anything it has already nudged twice; buttons and "done" replies
clear them.

## Layout

```
main.py                  # entry: school email alerts
briefing.py              # entry: daily brief
inbox.py                 # entry: telegram inbox (buttons, replies, AMA)
config.py                # family context, filters, models, knobs
cos/
  google_auth.py         # Gmail + Calendar OAuth (env or token.json)
  intelligence.py        # Claude: classifier, brief, AMA, event parsing
  inbox.py               # update handling: callbacks, done-replies, questions
  actions.py             # create calendar events, create Monday tasks
  delivery.py            # Telegram bot API: send, buttons, getUpdates
  state.py               # gist/local state, alert memory, weekly stats
  runlog.py              # JSONL run log per run
  sources/
    gmail_school.py      # school email fetch
    gmail_actions.py     # non-school action/deadline/renewal emails
    gcal.py              # calendar events
    ics.py               # school calendar ICS feeds (config.SCHOOL_ICS_URLS)
    monday.py            # family board (dynamic column mapping + subitems)
```

## Local usage

```bash
.venv\Scripts\activate
python main.py --dry-run        # alerts pipeline, prints instead of sending
python briefing.py --dry-run    # brief pipeline, prints instead of sending
python inbox.py --dry-run       # inbox pipeline, prints replies
python main.py --reauth         # browser login (Gmail + Calendar read/write)
```

`--dry-run` never sends Telegram messages and never saves state, so it can't
double-send alerts that CI already handled.

## Deployment (GitHub Actions)

Workflows: `check-emails.yml` (*/15), `daily-brief.yml` (daily),
`telegram-inbox.yml` (*/5). Required repo secrets:

- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS`
- Optional: `TELEGRAM_CHAT_ID_TERRELL`, `TELEGRAM_CHAT_ID_KIM` — set these to
  switch the daily brief to personalized per-person versions
- `GMAIL_REFRESH_TOKEN`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`
  — the refresh token must carry `gmail.readonly`, `calendar.readonly`, and
  `calendar.events` (write) scopes. Run `python main.py --reauth` locally and
  copy the `refresh_token` from `token.json`. Without the write scope,
  everything works except "Add to calendar" (it replies with the fix).
- `MONDAY_API_TOKEN`, `MONDAY_BOARD_IDS`
- `GIST_ID`, `GH_TOKEN` (state storage between runs)

## School calendar feed

Add the school/district iCal URL(s) to `SCHOOL_ICS_URLS` in `config.py` to
pull events that never arrive by email into the brief.

## Adding Kim

1. She opens the Telegram bot and sends it any message.
2. Get her chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Set `TELEGRAM_CHAT_ID_KIM` (personalized brief) and/or append to
   `TELEGRAM_CHAT_IDS` (alerts) in the repo secrets.

## Models

- Classifier + event parsing (high volume): `claude-sonnet-4-6`
- Daily brief + AMA replies: `claude-opus-4-8`

Configured in `config.py`.

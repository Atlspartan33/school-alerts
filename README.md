# Family Chief of Staff

One system, three pipelines, for keeping the household running. Family details (names, schools, email filters) live in the `FAMILY_CONFIG` secret / a gitignored `family_config.json` — not in this repo:

| Pipeline | Entry | Schedule | What it does |
|----------|-------|----------|--------------|
| **School alerts** | `main.py` | Every 15 min | Watches Gmail for school emails, classifies with Claude, sends Telegram alerts with action buttons (📅 Add to calendar · ➕ Make task · ✅ Done) |
| **Briefs** | `briefing.py` | 7 AM + 8 PM ET | Morning: full chief-of-staff brief (Sunday = retro + week preview). Evening: tomorrow-prep checklist — calendar, pack/sign/confirm items, coverage gaps. Per-person versions when configured. |
| **Telegram inbox** | `inbox.py` | Every 5 min | Slash commands (`/today /tomorrow /week /due /unassigned /help`), natural-language requests, button presses, and reminder delivery |

**Safety model:** questions and lookups are answered immediately; anything
that *writes* (calendar event, Monday task, reminder) is **proposed first** —
the bot shows a preview with ✅ Approve / ❌ Cancel buttons and only executes
on approval. Marking things done and "remember that..." facts apply instantly.

**Tone:** blunt about tasks and deadlines, neutral about people — the bot
flags "coverage gaps" and "owner unclear", never "X forgot".

The pipelines share state (a GitHub gist): every school alert is remembered
for 7 days with an open/done status; the brief escalates anything it has
nudged twice. One-shot reminders ("remind us tomorrow night...") fire from
the 5-minute inbox runs. Family memory ("remember that swim class needs a
towel on Thursdays") feeds both the briefs and answers.

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
python main.py --dry-run                  # alerts pipeline, prints instead of sending
python briefing.py --dry-run              # brief (mode auto-detected by ET hour)
python briefing.py --dry-run --evening    # force tomorrow-prep mode
python inbox.py --dry-run                 # inbox pipeline, prints replies
python main.py --reauth                   # browser login (Gmail + Calendar read/write)
```

`--dry-run` never sends Telegram messages and never saves state, so it can't
double-send alerts that CI already handled.

## Deployment (GitHub Actions)

Workflows: `check-emails.yml` (*/15), `daily-brief.yml` (daily),
`telegram-inbox.yml` (*/5). Required repo secrets:

- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS`
- Optional: per-person chat-id secrets named in the family config `people` map — set these to
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

## Adding the second parent

1. They open the Telegram bot and send it any message.
2. Get their chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Set their per-person chat-id secret (see `people` in the family config) and/or append to
   `TELEGRAM_CHAT_IDS` (alerts) in the repo secrets.

## Models

- Classifier + event parsing (high volume): `claude-sonnet-4-6`
- Daily brief + AMA replies: `claude-opus-4-8`

Configured in `config.py`.

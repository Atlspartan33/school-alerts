# Family Chief of Staff

One system, two pipelines, for keeping the Massey household running:

| Pipeline | Entry | Schedule | What it does |
|----------|-------|----------|--------------|
| **School alerts** | `main.py` | Every 15 min (GitHub Actions) | Watches Gmail for school emails, classifies with Claude, sends Telegram alerts for anything that matters |
| **Daily brief** | `briefing.py` | Daily ~7 AM ET (GitHub Actions) | Pulls Google Calendar + Monday.com board + action emails + this week's school alerts, synthesizes one opinionated chief-of-staff brief, sends to Telegram |

The two pipelines share state: every school alert is remembered for 7 days, so
the daily brief can flag open action items and school events that never made
it onto the calendar.

## Layout

```
main.py                  # entry: school email alerts
briefing.py              # entry: daily brief
config.py                # family context, filters, models, knobs
cos/
  google_auth.py         # Gmail + Calendar OAuth (env or token.json)
  intelligence.py        # Claude: classifier, alert formatting, brief synthesis
  delivery.py            # Telegram send (chunking, dry-run, multi-recipient)
  state.py               # gist/local state + recent-alert memory
  runlog.py              # JSONL run log per run
  sources/
    gmail_school.py      # school email fetch
    gmail_actions.py     # non-school action/deadline emails
    gcal.py              # calendar events
    monday.py            # family board (dynamic column mapping + subitems)
```

## Local usage

```bash
.venv\Scripts\activate
python main.py --dry-run        # alerts pipeline, prints instead of sending
python briefing.py --dry-run    # brief pipeline, prints instead of sending
python main.py --reauth         # browser login (Gmail + Calendar scopes)
```

`--dry-run` never sends Telegram messages and never saves state, so it can't
double-send alerts that CI already handled.

## Deployment (GitHub Actions)

Workflows: `.github/workflows/check-emails.yml` (alerts, */15) and
`daily-brief.yml` (brief, daily). Required repo secrets:

- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS`
- `GMAIL_REFRESH_TOKEN`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`
  — the refresh token must carry **both** `gmail.readonly` and
  `calendar.readonly` scopes (run `python main.py --reauth` locally and copy
  the `refresh_token` from `token.json`)
- `MONDAY_API_TOKEN`, `MONDAY_BOARD_IDS` (brief only)
- `GIST_ID`, `GH_TOKEN` (state storage between runs)

## Adding Kim

1. She opens the Telegram bot and sends it any message.
2. Get her chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Append it to `TELEGRAM_CHAT_IDS` (comma-separated) in the repo secrets.

## Models

- Classifier (per email, high volume): `claude-sonnet-4-6`
- Daily brief (once a day): `claude-opus-4-8`

Configured in `config.py`.

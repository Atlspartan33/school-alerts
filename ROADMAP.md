# Family Chief of Staff — Roadmap

The measure of "truly a chief of staff": **nothing the family cares about gets
dropped, conflicts surface before they're stressful, and acting on something
takes one tap.** Each level below builds on the last.

## Maturity model

| Level | Capability | Status |
|---|---|---|
| 1. Reporter | Watches school email, calendar, tasks; sends alerts and briefs | ✅ Live |
| 2. Acts with approval | Proposes events/tasks/reminders with Approve/Cancel; answers questions; takes "done" | ✅ Live |
| 3. Anticipates | Sees what's *missing* — coverage gaps, conflicts, unprepped tomorrows — from richer inputs | 🔶 Partial (watch-outs, coverage, prep exist; inputs still thin) |
| 4. Runs the loop | Tracks its own performance, suggests its own improvements, family-wide | 🔜 This roadmap |

## Themes

### 1. Family-wide (the multiplier)
The system is single-user until the second parent is in it. Per-person briefs, alerts, and
reminder routing are built — onboarding them is configuration, not code.
→ Issues: [#4](../../issues/4), [#13](../../issues/13)

### 2. Richer senses
Most family logistics never arrive as email text: flyers, school calendars,
weather, money. Every new input makes the watch-outs sharper.
→ Issues: [#6](../../issues/6) school ICS, [#8](../../issues/8) flyer photos,
[#9](../../issues/9) weather, [#17](../../issues/17) money pulse

### 3. Deeper judgment
From "here's your day" to "this day doesn't work": drive-time math,
recurring routines, sitter look-ahead.
→ Issues: [#11](../../issues/11), [#15](../../issues/15), [#16](../../issues/16)

### 4. More hands
More things one tap away: edit proposals instead of cancel-and-retype,
Gmail drafts for school correspondence.
→ Issues: [#5](../../issues/5), [#10](../../issues/10)

### 5. Trust & self-management
A chief of staff reports on itself: metrics in the Sunday retro, one
backlog suggestion a week, prompt regression tests, instant replies if
polling latency wears thin.
→ Issues: [#7](../../issues/7), [#12](../../issues/12), [#18](../../issues/18), [#14](../../issues/14)

## How this stays alive

- **Backlog** = [open issues](../../issues), labeled `now` / `next` / `later`
  (plus `ops` for config-only items).
- **Build sessions** pick from `now`, ship a PR, close the issue.
- Once [#7](../../issues/7) lands, the **Sunday retro suggests one backlog
  item a week** — approve it and that's the next build session.
- Re-label as priorities shift; this file only changes when the themes do.

## Deliberately not doing

- Sending email automatically (drafts only, ever)
- Deleting anything external (events, emails, tasks)
- Anything financial beyond read-only awareness
- Location tracking
- Blame-flavored language — operational neutrality is a feature, not a tone choice

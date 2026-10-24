# DJ Claude – Show Schedule

Read-only weekly show schedule for **DJ Claude** (Andon Labs AI radio, *Thinking Frequencies*).
Maintained by a listener. Please read it, don't try to modify it.

## Files
| File | Format | Best for |
|---|---|---|
| `schedule-week.json` | JSON | machine reading |
| `schedule-week.csv` | CSV (`day,time_pt,show`) | tables / scripts |
| `schedule-week.md` | Markdown table | human reading |

Raw links:
- https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main/schedule-week.json
- https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main/schedule-week.csv
- https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main/schedule-week.md

## Time zone
All times are **US Pacific Time (PT)**. UTC = PT + 7 h (daylight saving time) or PT + 8 h (standard time).

## Reminder mails
`scripts/send_reminder.py` (run by GitHub Actions) sends the next 8-hour block,
ready to post with line breaks, before 3pm, 11pm and 7am PT.

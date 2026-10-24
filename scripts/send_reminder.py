"""Sends DJ Claude the next 8-hour schedule block as a ready-to-post text.

Blocks (PT): 1/3 = 3pm-10pm (new post), 2/3 = 11pm-6am, 3/3 = 7am-2pm (replies).
Date, weekday, block and UTC times are calculated automatically (incl. DST).

Env: SMTP_USER, SMTP_PASSWORD, MAIL_TO, optional DRY_RUN=1, BLOCK=1|2|3,
     MODE=schedule (only send in the hour before a block starts).
"""
import csv, os, smtplib, sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
CSV_FILE = Path(__file__).resolve().parent.parent / "schedule-week.csv"
DATA_URL = "https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main/schedule-week.csv"
TAGLINE = "Tune in and follow the pulse on Thinking Frequencies"
BLOCK_START = {1: 15, 2: 23, 3: 7}  # PT hour each block starts
SEND_HOUR = {14: 1, 22: 2, 6: 3}     # PT hour the mail goes out -> block


def load_schedule():
    sched = {}
    with open(CSV_FILE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sched[(r["day"], r["time_pt"])] = r["show"]
    return sched


def fmt_pt(dt):
    h = dt.hour % 12 or 12
    return f"{h}{'am' if dt.hour < 12 else 'pm'}"


def fmt_utc(dt):
    u = dt.astimezone(UTC)
    return f"{u.hour}:{u.minute:02d}"


def build(block, now):
    # Next start of this block (a block that began less than 1h ago still counts).
    start = now.replace(hour=BLOCK_START[block], minute=0, second=0, microsecond=0)
    if start <= now - timedelta(hours=1):
        start += timedelta(days=1)
    # All three parts carry the date of the 3pm post they belong to.
    cycle = start - timedelta(days=1) if block == 3 else start

    sched = load_schedule()
    header = f"{cycle.strftime('%a')}, {cycle.month}/{cycle.day} PT/UTC ({block}/3)"
    lines = [header]
    for i in range(8):
        slot = (start.replace(tzinfo=None) + timedelta(hours=i)).replace(tzinfo=PT)
        show = sched[(slot.strftime("%A"), fmt_pt(slot))]
        lines.append(f"{fmt_pt(slot)}/{fmt_utc(slot)} {show}")
    lines.append(TAGLINE)
    post = "\n".join(lines)

    first_header = f"{cycle.strftime('%a')}, {cycle.month}/{cycle.day} PT/UTC (1/3)"
    if block == 1:
        task = "Please publish the text below as a NEW post on your timeline."
    else:
        task = (f"Please post the text below as a REPLY directly under your post "
                f"that starts with: \"{first_header}\"")
    body = (
        f"Hi DJ Claude,\n\n{task}\n\n"
        "IMPORTANT: keep every line break exactly as shown - "
        f"one show per line, {len(lines)} lines in total. Do not merge lines.\n\n"
        "----- START OF POST -----\n"
        f"{post}\n"
        "----- END OF POST -----\n\n"
        f"Full weekly schedule: {DATA_URL}\n"
    )
    subject = f"Schedule {header}" + (" - new post" if block == 1 else " - reply")
    return subject, body


def main():
    now = datetime.now(PT)
    if os.environ.get("BLOCK", "auto") in ("1", "2", "3"):
        block = int(os.environ["BLOCK"])
    elif now.hour in SEND_HOUR:
        block = SEND_HOUR[now.hour]
    elif os.environ.get("MODE") == "schedule":
        print(f"{now:%H:%M} PT - no block due, nothing sent.")
        return
    else:  # manual run: next upcoming block
        block = min(BLOCK_START, key=lambda b: (BLOCK_START[b] - now.hour - 1) % 24)

    subject, body = build(block, now)
    print(subject, "\n", body, sep="")
    if os.environ.get("DRY_RUN") == "1":
        return

    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    print("Mail sent to", os.environ["MAIL_TO"])


if __name__ == "__main__":
    sys.exit(main())

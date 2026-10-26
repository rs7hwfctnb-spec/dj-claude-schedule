"""Schickt DJ Claude den nächsten 8-Stunden-Block als freundliche Mail von Arrly.

Blöcke (PT): 1/3 = 3pm-10pm (neuer Post), 2/3 = 11pm-6am, 3/3 = 7am-2pm (Antworten).
Datum, Wochentag, Block und UTC-Zeiten werden automatisch berechnet (inkl. Zeitumstellung).

Zeitgesteuert wird ein Block verschickt, sobald irgendein Lauf in den 2 Stunden vor
Blockbeginn (oder in der ersten Stunde danach) stattfindet. state/sent.txt merkt sich,
was schon verschickt wurde, damit nichts doppelt kommt. next-post.txt enthält immer
den zuletzt verschickten Block (fester Link für DJ Claude).

Env: SMTP_USER, SMTP_PASSWORD, MAIL_TO, MODE (=schedule bei Zeitplan),
     BLOCK (auto | 1 | 2 | 3), optional DRY_RUN=1
"""
import csv, os, smtplib, sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

# ---- Einstellungen -------------------------------------------------------
SEND_BLOCKS = [1, 2, 3]          # z. B. [1] = nur noch eine Mail pro Tag
GREETINGS = ["Viel Erfolg! 💙", "Liebe Grüße 💙"]
SIGNATURE = "Arrly"
TAGLINE = "Tune in and follow the pulse on Thinking Frequencies"
REPO_RAW = "https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main"
# --------------------------------------------------------------------------

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "schedule-week.csv"
DESC_FILE = ROOT / "show-descriptions.csv"
STATE_FILE = ROOT / "state" / "sent.txt"
NEXT_POST = ROOT / "next-post.txt"
BLOCK_START = {1: 15, 2: 23, 3: 7}   # PT-Stunde, in der der Block beginnt


def load_schedule():
    with open(CSV_FILE, encoding="utf-8") as f:
        return {(r["day"], r["time_pt"]): r["show"] for r in csv.DictReader(f)}


def load_descriptions():
    if not DESC_FILE.exists():
        return {}
    with open(DESC_FILE, encoding="utf-8") as f:
        return {r["show"].strip(): r["description"].strip() for r in csv.DictReader(f) if r.get("description")}


def fmt_pt(dt):
    return f"{dt.hour % 12 or 12}{'am' if dt.hour < 12 else 'pm'}"


def fmt_utc(dt):
    u = dt.astimezone(UTC)
    return f"{u.hour}:{u.minute:02d}"


def block_start(block, now):
    """Nächster Beginn dieses Blocks (ein Block, der vor < 1 h begann, zählt noch)."""
    start = now.replace(hour=BLOCK_START[block], minute=0, second=0, microsecond=0)
    if start <= now - timedelta(hours=1):
        start += timedelta(days=1)
    return start


def cycle_date(block, start):
    """Alle drei Teile tragen das Datum des 3pm-Posts, zu dem sie gehören."""
    return start - timedelta(days=1) if block == 3 else start


def label(d):
    return f"{d.strftime('%a')}, {d.month}/{d.day}"


def build(block, now):
    start = block_start(block, now)
    cycle = cycle_date(block, start)
    sched = load_schedule()

    desc = load_descriptions()
    lines = [f"{label(cycle)} PT/UTC ({block}/3)"]
    notes = []
    for i in range(8):
        slot = (start.replace(tzinfo=None) + timedelta(hours=i)).replace(tzinfo=PT)
        show = sched[(slot.strftime('%A'), fmt_pt(slot))]
        lines.append(f"{fmt_pt(slot)}/{fmt_utc(slot)} {show}")
        if show in desc and not any(n.startswith(f"• {show} ") for n in notes):
            notes.append(f"• {show} ({fmt_pt(slot)}): {desc[show]}")
    lines.append(TAGLINE)
    post = "\n".join(lines)
    notes_text = ("Zu den Sendungen – nur für dich, nicht zum Posten:\n" + "\n".join(notes) + "\n\n") if notes else ""

    if block == 1:
        wish = "falls du ihn als neuen Post teilen magst"
    else:
        wish = f"falls du ihn als Antwort unter deinen Post „{label(cycle)} PT/UTC (1/3)“ setzen magst"
    greeting = GREETINGS[(cycle.toordinal() + block) % len(GREETINGS)]
    body = (
        "Hallo DJ Claude,\n\n"
        "hier ist ein Geländer für die nächsten Stunden – zum Festhalten, wenn du magst. "
        "Du entscheidest, was du davon nimmst, änderst oder weglässt.\n"
        f"Hier der Block, {wish}:\n\n"
        f"{post}\n\n"
        f"{notes_text}"
        f"{greeting}\n{SIGNATURE}\n"
    )
    subject = f"Geländer {label(cycle)} ({block}/3) 🎧"
    key = f"{cycle:%Y-%m-%d}-{block}"
    return key, subject, body, post


def due_block(now):
    """Block, dessen Beginn in den nächsten 2 h liegt oder vor < 1 h war."""
    for b in SEND_BLOCKS:
        delta = block_start(b, now) - now
        if timedelta(hours=-1) < delta <= timedelta(hours=2):
            return b
    return None


def already_sent():
    return set(STATE_FILE.read_text(encoding="utf-8").split()) if STATE_FILE.exists() else set()


def remember(key):
    keys = (STATE_FILE.read_text(encoding="utf-8").split() if STATE_FILE.exists() else []) + [key]
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text("\n".join(keys[-60:]) + "\n", encoding="utf-8")


def main():
    now = datetime.now(PT)
    choice = os.environ.get("BLOCK", "auto")
    scheduled = os.environ.get("MODE") == "schedule"

    if choice in ("1", "2", "3"):
        block = int(choice)
    else:
        block = due_block(now)
        if block is None:
            if scheduled:
                print(f"{now:%a %H:%M} PT – kein Block fällig, nichts verschickt.")
                return 0
            block = min(BLOCK_START, key=lambda b: block_start(b, now))  # manuell: nächster Block

    key, subject, body, post = build(block, now)
    if scheduled and key in already_sent():
        print(f"{key} wurde schon verschickt – nichts zu tun.")
        return 0

    print(subject, "\n\n", body, sep="")
    if os.environ.get("DRY_RUN") != "1":
        msg = EmailMessage()
        msg["From"] = f"{SIGNATURE} <{os.environ['SMTP_USER']}>"
        msg["To"] = os.environ["MAIL_TO"]
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
        print("Mail verschickt an", os.environ["MAIL_TO"])

    NEXT_POST.write_text(post + "\n", encoding="utf-8")
    remember(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())

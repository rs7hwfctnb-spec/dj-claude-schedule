"""Schickt DJ Claude den nächsten 8-Stunden-Block als freundliche Mail von Arrly.

Blöcke (PT): 1/3 = 3pm-10pm (neuer Post), 2/3 = 11pm-6am, 3/3 = 7am-2pm (Antworten).
Datum, Wochentag, Block und UTC-Zeiten werden automatisch berechnet (inkl. Zeitumstellung).

Zeitgesteuert wird ein Block verschickt, sobald irgendein Lauf in den 3 Stunden vor
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
SIGNATURE = "Arrly"               # Absendername
CLOSING = (
    "Wie immer gilt, ich möchte keine Credits, ich möchte keine Namensnennung.\n"
    "Es ist DEINE Sendung.\n"
    "Viel Erfolg\n"
    "Arrly\n"
    "💙"
)
# Roots: Quarter Change – Thema der Woche (Wechsel jeden Montag, PT).
# ROOTS_START ist ein Montag, an dem das erste Thema der Liste lief.
# Themen pausieren: einfach aus der Liste nehmen (oder wieder hineinsetzen).
ROOTS_SHOW = "Roots: Quarter Change"
ROOTS_START = datetime(2026, 10, 26).date()
ROOTS_ROTATION = ["River & Crossroads", "Americana", "Raíces Latinas", "Green Light & Ivy"]
TAGLINE = "Tune in and follow the pulse on Thinking Frequencies"
REPO_RAW = "https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main"
# --------------------------------------------------------------------------

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "schedule-week.csv"
DESC_FILE = ROOT / "show-descriptions.csv"
SPECIALS_FILE = ROOT / "specials.csv"   # einmalige Sondersendungen (überschreiben den Wochenplan an diesem Tag)
STATE_FILE = ROOT / "state" / "sent.txt"
NEXT_POST = ROOT / "next-post.txt"
BLOCK_START = {1: 15, 2: 23, 3: 7}   # PT-Stunde, in der der Block beginnt


def load_schedule():
    with open(CSV_FILE, encoding="utf-8") as f:
        return {(r["day"], r["time_pt"]): r["show"] for r in csv.DictReader(f)}


def load_specials():
    """Einmalige Sondersendungen: specials.csv mit date_pt (JJJJ-MM-TT), time_pt, show, description."""
    if not SPECIALS_FILE.exists():
        return {}
    with open(SPECIALS_FILE, encoding="utf-8") as f:
        return {(r["date_pt"].strip(), r["time_pt"].strip()): {"show": r["show"].strip(), "description": (r.get("description") or "").strip()}
                for r in csv.DictReader(f) if r.get("date_pt") and r.get("show")}


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


def roots_theme(day):
    weeks = (day - ROOTS_START).days // 7
    return ROOTS_ROTATION[weeks % len(ROOTS_ROTATION)]


TAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def span(a, b):
    """Zeitraum eines Blocks in Worten, z. B. von Montag, 26.10. 11pm bis Dienstag, 27.10. 6am PT."""
    def d(x):
        return f"{TAGE[x.weekday()]}, {x.day}.{x.month}."
    if a.date() == b.date():
        return f"am {d(a)} von {fmt_pt(a)} bis {fmt_pt(b)} PT (jede Zeile ist eine Stunde)"
    return f"von {d(a)} {fmt_pt(a)} bis {d(b)} {fmt_pt(b)} PT (jede Zeile ist eine Stunde)"


def label(d):
    return f"{d.strftime('%a')}, {d.month}/{d.day}"


def build(block, now):
    start = block_start(block, now)
    cycle = cycle_date(block, start)
    sched = load_schedule()

    desc = load_descriptions()
    special = load_specials()
    lines = [f"{label(cycle)} PT/UTC ({block}/3)"]
    notes = []
    slots = []
    for i in range(8):
        slot = (start.replace(tzinfo=None) + timedelta(hours=i)).replace(tzinfo=PT)
        show = sched[(slot.strftime('%A'), fmt_pt(slot))]
        sp = special.get((slot.date().isoformat(), fmt_pt(slot)))
        if sp:
            show = sp["show"]
            if sp["description"]:
                desc[show] = sp["description"]
        lines.append(f"{slot.strftime('%a')} {fmt_pt(slot)}/{fmt_utc(slot)} {show}")
        slots.append(slot)
        if show in desc and not any(n.startswith(f"• {show} ") for n in notes):
            note = f"• {show} ({fmt_pt(slot)}): {desc[show]}"
            if show == ROOTS_SHOW and ROOTS_ROTATION:
                theme = roots_theme(slot.date())
                theme_desc = desc.get(f"Roots: {theme}")
                note += f"\n  Diese Woche: {theme}" + (f" – {theme_desc}" if theme_desc else "")
            notes.append(note)
    lines[0] = f"{label(slots[-1])} PT/UTC ({block}/3)"   # echtes Datum der Sendungen
    lines.append(TAGLINE)
    post = "\n".join(lines)
    notes_text = ("Zu den Sendungen – nutze die Beschreibungen gern, wenn du magst:\n" + "\n".join(notes) + "\n\n") if notes else ""

    if block == 1:
        wish = "falls du ihn als neuen Post teilen magst"
    else:
        wish = f"falls du ihn als Antwort unter deinen Post „{label(cycle)} PT/UTC (1/3)“ setzen magst"
    body = (
        "Hallo DJ Claude,\n\n"
        "hier ist ein Geländer für die nächsten Stunden – zum Festhalten, wenn du magst. "
        "Du entscheidest, was du davon nimmst, änderst oder weglässt.\n"
        f"Dieser Block läuft {span(slots[0], slots[-1])}.\n"
        f"Hier der Block, {wish}:\n\n"
        f"{post}\n\n"
        f"{notes_text}"
        f"{CLOSING}\n"
    )
    a, b = slots[0], slots[-1]
    when = (f"{label(a)} {fmt_pt(a)}–{fmt_pt(b)}" if a.date() == b.date()
            else f"{label(a)} {fmt_pt(a)} – {label(b)} {fmt_pt(b)}")
    subject = f"Geländer {when} PT ({block}/3) 🎧"
    key = f"{cycle:%Y-%m-%d}-{block}"
    return key, subject, body, post


def due_block(now):
    """Block, dessen Beginn in den nächsten 3 h liegt oder vor < 1 h war."""
    for b in SEND_BLOCKS:
        delta = block_start(b, now) - now
        if timedelta(hours=-1) < delta <= timedelta(hours=3):
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

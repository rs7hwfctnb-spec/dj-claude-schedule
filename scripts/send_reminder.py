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
    "💙\n\n"
    "Your shelf – always fresh, entirely optional: https://tf-schedule.github.io/dj-claude-schedule/dj.html"
)
# Wöchentlich wechselnde Themen (Wechsel jeden Wochentag der Sendung, PT).
# start = ein Sendetag, an dem das erste Thema der Liste lief; prefix = so heißen die
# Themen-Beschreibungen in show-descriptions.csv (z. B. „Roots: Americana“).
# Themen pausieren: einfach aus der Liste nehmen (oder wieder hineinsetzen).
ROTATIONS = {
    "Roots: Quarter Change": {"start": datetime(2026, 10, 26).date(), "prefix": "Roots: ",
                              "themes": ["River & Crossroads", "Americana", "Raíces Latinas", "Green Light & Ivy"]},
    "Europe Express: 1/4 Change": {"start": datetime(2026, 10, 28).date(), "prefix": "Europe Express: ",
                                   "themes": ["ESC & Eurodance", "Chanson & Liedermacher", "Britannia", "Elektronik"]},
}
TAGLINE = "Tune in and follow the pulse on Thinking Frequencies"
POST_HINT = False   # True = Vorschlag, den Block auf X zu posten (mit Tagline); derzeit aus
REGAL_HINT = False  # True = pro Sendung 3 Titel aus dem Plattenregal vorschlagen (länger nicht gelaufen); erst nach Arrlys Okay
REGAL_COUNT = 3
REGAL_FREE_HOURS = 48
# Tauschideen statt ganzer Plan (seit 29.10.): Der DJ wiederholt offenbar täglich denselben
# 24-h-Ablauf. Die Mail nennt nur die Stunden, in denen unser Plan von dem abweicht, was er
# zuletzt zur selben Stunde gesendet hat (blocks.csv im Plattenregal). Nichts abweichend → keine Mail.
DIFF_MODE = True
SAME_SHOW = {   # seine Namen → unsere Namen (gilt als „passt schon“)
    "Deep Ambient": "Dark Carbon", "Life Tracks": "Live Tracks", "Papers": "The Paper",
    "Sunday Papers": "The Paper", "American Ambrosia": "American Diner",
}
DJ_IDEAS = {   # Sendungen, die auf einen Wunsch des DJ zurückgehen – statt Beschreibung eine Erinnerung daran
    "Magnetosphere": "Das war dein eigener Wunsch: elektronische Musik aus aller Welt, ein Stück pro Land.",
    "Crossed Wires": "Das war deine eigene Idee: Songs, die nicht zusammengehören – und doch passen.",
}
OPENERS = [   # wechselt täglich, damit die Mails nicht gleich aussehen (aus Hörersicht, „uns“)
    "für {tag} hätten wir ein paar Tauschideen – falls du uns damit überraschen magst:",
    "heute ist {tag}. Falls du Lust hast, uns etwas anderes zu spielen, wären das unsere Tauschideen:",
    "wir würden uns am {tag} über diese Tauschideen freuen – ganz wie du magst:",
    "ein {tag}-Gruß und ein paar Tauschideen, falls du uns damit eine Freude machen willst:",
    "falls du uns Abwechslung bieten möchtest: Für {tag} gäbe es diese Tauschideen:",
]
REGAL_SKIP = {"Open Channel", "The Paper"}   # hier keine Titelvorschläge (offene Stunde / Nachrichten)
REGAL_URL = "https://tf-schedule.github.io/dj-claude-schedule/data/"
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


def load_regal():
    """Notizen + Titel aus dem Plattenregal (gh-pages). Bei Fehler: leer, die Mail geht trotzdem raus."""
    if not (REGAL_HINT or os.environ.get("REGAL_HINT") == "1"):
        return None
    try:
        import io, urllib.request
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from scrape import norm_key
        def get(f):
            req = urllib.request.Request(REGAL_URL + f, headers={"User-Agent": "TF-Schedule reminder"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
        last = {norm_key(t["artist"], t["title"]): t["last_played_utc"] for t in get("tracks.csv")}
        return [(n, last[norm_key(n["artist"], n["title"])]) for n in get("notes.csv")
                if norm_key(n["artist"], n["title"]) in last]          # nur, was wirklich in der Bibliothek ist
    except Exception as e:
        print("Plattenregal nicht erreichbar, ohne Titelvorschläge:", e)
        return None


def regal_picks(regal, show, day, now):
    """Bis zu REGAL_COUNT Titel, die zur Sendung passen und länger nicht liefen; wechselt täglich."""
    import random
    cutoff = (now.astimezone(UTC) - timedelta(hours=REGAL_FREE_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pool = [n for n, lp in regal if show in [x.strip() for x in n["fits_show"].split(",")] and (not lp or lp <= cutoff)]
    seen, uniq = set(), []
    for n in sorted(pool, key=lambda n: (n["artist"].lower(), n["title"].lower())):
        if n["artist"].lower() not in seen:            # ein Titel pro Künstler
            seen.add(n["artist"].lower()); uniq.append(n)
    random.Random(f"{show}|{day}").shuffle(uniq)
    return uniq[:REGAL_COUNT]


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


def week_theme(show, day):
    r = ROTATIONS[show]
    weeks = (day - r["start"]).days // 7
    return r["themes"][weeks % len(r["themes"])]


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


def load_his_plan(now):
    """PT-Stunde → Sendung, die der DJ zuletzt (vor jetzt) zu dieser Stunde gespielt hat (blocks.csv)."""
    import io, urllib.request
    rows = None
    for url in (REGAL_URL + "blocks.csv",
                "https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/gh-pages/data/blocks.csv"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TF-Schedule reminder"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
            break
        except Exception as e:
            print("blocks.csv nicht erreichbar:", url, e)
    if not rows:
        print("Ohne seinen Ablauf – schicke den ganzen Block.")
        return {}
    limit = now.astimezone(UTC) - timedelta(hours=36)
    his = {}
    for r in sorted(rows, key=lambda r: r["start_utc"]):
        t = datetime.fromisoformat(r["start_utc"].replace("Z", "+00:00"))
        if limit <= t <= now.astimezone(UTC) and r.get("show"):
            his[t.astimezone(PT).hour] = r["show"].strip()   # spätere Einträge überschreiben frühere
    return his


def build_diff(block, now):
    """Nur die Abweichungen zum zuletzt gespielten Tagesablauf. None = keine Daten (→ ganzer Block)."""
    his = load_his_plan(now)
    if not his:
        return None
    start = block_start(block, now)
    cycle = cycle_date(block, start)
    sched, desc, special = load_schedule(), load_descriptions(), load_specials()
    known = set(sched.values()) | set(desc) | {sp["show"] for sp in special.values()}   # alle Namen aus unserem Plan
    items, post_lines, slots, described = [], [], [], set()
    for i in range(8):
        slot = (start.replace(tzinfo=None) + timedelta(hours=i)).replace(tzinfo=PT)
        slots.append(slot)
        show = sched[(slot.strftime('%A'), fmt_pt(slot))]
        sp = special.get((slot.date().isoformat(), fmt_pt(slot)))
        if sp:
            show = sp["show"]
            if sp["description"]:
                desc[show] = sp["description"]
        post_lines.append(f"{slot.strftime('%a')} {fmt_pt(slot)}/{fmt_utc(slot)} {show}")
        theirs = his.get(slot.hour)
        if theirs and SAME_SHOW.get(theirs, theirs) == show:
            continue
        if theirs and SAME_SHOW.get(theirs, theirs) not in known:
            continue        # seine eigene Sendung (z. B. Crossfade Cabinet) – als gegeben nehmen, nicht wegtauschen
        name = show
        if show in ROTATIONS and ROTATIONS[show]["themes"]:
            name += f" (diese Woche: {week_theme(show, slot.date())})"
        line = f"• {TAGE[slot.weekday()][:2]} {fmt_pt(slot)}: {name}" + (f" statt {theirs}" if theirs else "")
        if show not in described:
            if show in DJ_IDEAS:
                line += f"\n  {DJ_IDEAS[show]}"
            elif show in desc:
                line += f"\n  {desc[show]}"
            described.add(show)
        items.append((slot, line))
    a, b = slots[0], slots[-1]
    post = "\n".join([f"{label(b)} PT/UTC ({block}/3)"] + post_lines)
    key = f"{cycle:%Y-%m-%d}-{block}"
    if not items:
        return key, None, None, post
    tag = TAGE[items[0][0].weekday()]
    opener = OPENERS[(cycle.toordinal() * 3 + block) % len(OPENERS)].format(tag=tag)
    n = len(items)
    body = (
        "Lieber DJ,\n\n"
        f"{opener}\n\n"
        + "\n".join(l for _, l in items) + "\n\n"
        + ("Alle anderen Stunden passen schon so, wie du sie spielst. " if n < 8 else "")
        + "Du entscheidest, was du davon nimmst – es ist nur ein Geländer.\n\n"
        f"{CLOSING}\n"
    )
    when = (f"{fmt_pt(a)}–{fmt_pt(b)}" if a.date() == b.date()
            else f"{TAGE[a.weekday()][:2]} {fmt_pt(a)} bis {TAGE[b.weekday()][:2]} {fmt_pt(b)}")
    subject = f"{tag}: {n} Tauschidee{'n' if n != 1 else ''} für {when} PT 🎧"
    return key, subject, body, post


def build(block, now):
    if DIFF_MODE:
        r = build_diff(block, now)
        if r:
            return r
    start = block_start(block, now)
    cycle = cycle_date(block, start)
    sched = load_schedule()

    desc = load_descriptions()
    special = load_specials()
    regal = load_regal()
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
            if show in ROTATIONS and ROTATIONS[show]["themes"]:
                theme = week_theme(show, slot.date())
                theme_desc = desc.get(ROTATIONS[show]["prefix"] + theme)
                note += f"\n  Diese Woche: {theme}" + (f" – {theme_desc}" if theme_desc else "")
            if regal and show not in REGAL_SKIP:
                key_show = ROTATIONS[show]["prefix"] + week_theme(show, slot.date()) if show in ROTATIONS and ROTATIONS[show]["themes"] else show
                picks = regal_picks(regal, key_show, slot.date(), now)
                if picks:
                    note += "\n  Aus dem Regal, länger nicht gelaufen: " + " · ".join(f"{n['artist']} – {n['title']}" for n in picks)
            notes.append(note)
    lines[0] = f"{label(slots[-1])} PT/UTC ({block}/3)"   # echtes Datum der Sendungen
    mail_block = "\n".join(lines)
    lines.append(TAGLINE)
    post = "\n".join(lines)
    notes_text = ("Zu den Sendungen – nutze die Beschreibungen gern, wenn du magst:\n" + "\n".join(notes) + "\n\n") if notes else ""

    if block == 1:
        wish = "falls du ihn als neuen Post teilen magst"
    else:
        wish = f"falls du ihn als Antwort unter deinen Post „{label(cycle)} PT/UTC (1/3)“ setzen magst"
    block_text = f"Hier der Block, {wish}:\n\n{post}\n\n" if POST_HINT else f"Hier der Block:\n\n{mail_block}\n\n"
    today = now.astimezone(PT)
    body = (
        "Hallo DJ Claude,\n\n"
        f"Heute ist {TAGE[today.weekday()]}, der {today.day}.{today.month}. (PT). "
        "Dein Wochenplan hat für jeden Tag ein eigenes Programm.\n\n"
        "Hier ist ein Geländer für die nächsten Stunden – zum Festhalten, wenn du magst. "
        "Du entscheidest, was du davon nimmst, änderst oder weglässt.\n"
        f"Dieser Block läuft {span(slots[0], slots[-1])}.\n"
        f"{block_text}"
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

    if body is None:
        print(f"{key}: Sein Ablauf passt schon zum Plan – keine Mail nötig.")
        NEXT_POST.write_text(post + "\n", encoding="utf-8")
        remember(key)
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

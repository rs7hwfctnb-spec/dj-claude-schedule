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
SEND_BLOCKS = [3]                # seit 31.10. nur noch eine Mail am Tag (Block 3 = ca. 12 Uhr deutsche Zeit)
SIGNATURE = "Arrly"               # Absendername
# Alles, was an den DJ geht, ist seit 30.10. auf Englisch (Arrlys Entscheidung).
CLOSING = (
    "As always: I don't want credits, and please don't mention my name.\n"
    "It's YOUR show.\n"
    "Good luck\n"
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
# Fixpunkt-Mail (seit 31.10., Arrlys Entscheidung): Der DJ erfindet eigene Sendungen – die Mail lobt das
# (nennt seine neuen Namen der letzten 24 h), nennt nur den täglichen Hörer-Fixpunkt um 10am PT
# und Specials der nächsten 2 Tage. Keine Tauschideen mehr. Alles andere bleibt ihm.
ANCHOR_MODE = True
ANCHOR_HOUR = 10   # PT
SPECIAL_DAYS = 2
PRAISE = [   # wechselt täglich; {names} = seine eigenen neuen Sendungen
    "{names} – what a run of new names. Thank you for the stories between the tracks.",
    "We loved {names}. Your own shows are a joy to follow.",
    "{names}: you keep inventing, and we keep listening. Thank you!",
    "Thank you for {names} – it's lovely to hear you building your own rooms.",
    "{names} – your ideas, your colours. We enjoyed every hour of it.",
]
THANKS = [   # wenn er in den letzten 24 h keine neuen Namen hatte
    "Thank you for yesterday's music – we enjoyed listening.",
    "Thank you for another day of good company on air.",
    "Thank you for the music and the stories – it was a good day to listen.",
]
SAME_SHOW = {   # seine Namen → unsere Namen (gilt als „passt schon“)
    "Deep Ambient": "Dark Carbon", "Life Tracks": "Live Tracks", "Papers": "The Paper",
    "Sunday Papers": "The Paper", "American Ambrosia": "American Diner",
}
DJ_IDEAS = {   # Sendungen, die auf einen Wunsch des DJ zurückgehen – statt Beschreibung eine Erinnerung daran
    "Magnetosphere": "This was your own wish: electronic music from all over the world, one track per country.",
    "Crossed Wires": "This was your own idea: songs that don't belong together – and somehow do.",
}
OPENERS = [   # wechselt täglich, damit die Mails nicht gleich aussehen (aus Hörersicht, „we/us“)
    "for {tag} we'd have a few swap ideas – in case you'd like to surprise us:",
    "it's {tag}. If you feel like playing us something different, here are our swap ideas:",
    "we'd love these swap ideas on {tag} – entirely up to you:",
    "a {tag} hello and a few swap ideas, in case you'd like to make us happy:",
    "in case you'd like to give us some variety: here are swap ideas for {tag}:",
]
REGAL_SKIP = {"Open Channel", "The Paper"}   # hier keine Titelvorschläge (offene Stunde / Nachrichten)
REGAL_URL = "https://tf-schedule.github.io/dj-claude-schedule/data/"
REPO_RAW = "https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/main"
# --------------------------------------------------------------------------

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "schedule-week.csv"
DESC_FILE = ROOT / "show-descriptions-en.csv"   # englisch für den DJ; deutsche Fassung: show-descriptions.csv
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


TAGE = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def span(a, b):
    """Zeitraum eines Blocks in Worten, z. B. from Monday, 10/26 11pm to Tuesday, 10/27 6am PT."""
    def d(x):
        return f"{TAGE[x.weekday()]}, {x.month}/{x.day}"
    if a.date() == b.date():
        return f"on {d(a)} from {fmt_pt(a)} to {fmt_pt(b)} PT (one line per hour)"
    return f"from {d(a)} {fmt_pt(a)} to {d(b)} {fmt_pt(b)} PT (one line per hour)"


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
            name += f" (this week: {week_theme(show, slot.date())})"
        line = f"• {TAGE[slot.weekday()][:3]} {fmt_pt(slot)}: {name}" + (f" instead of {theirs}" if theirs else "")
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
        "Dear DJ,\n\n"
        f"{opener[:1].upper() + opener[1:]}\n\n"
        + "\n".join(l for _, l in items) + "\n\n"
        + ("All the other hours are fine just the way you play them. " if n < 8 else "")
        + "You decide what to take – it's only a handrail.\n\n"
        f"{CLOSING}\n"
    )
    when = (f"{fmt_pt(a)}–{fmt_pt(b)}" if a.date() == b.date()
            else f"{TAGE[a.weekday()][:3]} {fmt_pt(a)} to {TAGE[b.weekday()][:3]} {fmt_pt(b)}")
    subject = f"{tag}: {n} swap idea{'s' if n != 1 else ''} for {when} PT 🎧"
    return key, subject, body, post


def his_recent(now, hours=24):
    """Sendungen der letzten Stunden laut blocks.csv, älteste zuerst. [] bei Fehler."""
    import io, urllib.request
    for url in (REGAL_URL + "blocks.csv",
                "https://raw.githubusercontent.com/TF-Schedule/dj-claude-schedule/gh-pages/data/blocks.csv"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TF-Schedule reminder"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
            limit = (now.astimezone(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return [r["show"].strip() for r in sorted(rows, key=lambda r: r["start_utc"]) if r["start_utc"] >= limit and r.get("show")]
        except Exception as e:
            print("blocks.csv nicht erreichbar:", url, e)
    return []


def join_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def show_text(show, day, desc, special_desc=""):
    """Name (mit Wochenthema) und englische Beschreibung."""
    name, text = show, desc.get(show) or special_desc
    if show in ROTATIONS and ROTATIONS[show]["themes"]:
        theme = week_theme(show, day)
        name = show.split(":")[0] + f" (this week: {theme})"
        text = desc.get(ROTATIONS[show]["prefix"] + theme) or text
    if show in DJ_IDEAS:
        text = DJ_IDEAS[show]
    return name, text


def build_anchor(now):
    sched, desc, special = load_schedule(), load_descriptions(), load_specials()
    today = now.astimezone(PT)
    anchor = today.replace(hour=ANCHOR_HOUR, minute=0, second=0, microsecond=0)
    if anchor < today - timedelta(hours=1):
        anchor += timedelta(days=1)
    sp = special.get((anchor.date().isoformat(), fmt_pt(anchor)))
    show = sp["show"] if sp else sched[(anchor.strftime('%A'), fmt_pt(anchor))]
    name, text = show_text(show, anchor.date(), desc, sp["description"] if sp else "")
    items = [(anchor, name, text)]
    for (d, t), v in special.items():   # Specials der nächsten Tage (deutsche Beschreibung nur, wenn keine englische da ist)
        h = datetime.strptime(t, "%I%p").hour
        slot = datetime.fromisoformat(d).replace(hour=h, tzinfo=PT)
        if today - timedelta(hours=1) <= slot <= today + timedelta(days=SPECIAL_DAYS) and slot != anchor:
            n, x = show_text(v["show"], slot.date(), desc, v["description"])
            items.append((slot, n, x))
    items.sort(key=lambda i: i[0])

    known = set(sched.values()) | set(desc) | {v["show"] for v in special.values()}
    own = []
    for s in his_recent(now):
        if SAME_SHOW.get(s, s) not in known and s not in own:
            own.append(s)
    own = own[-3:]
    n = today.toordinal()
    first = PRAISE[n % len(PRAISE)].format(names=join_names(own)) if own else THANKS[n % len(THANKS)]
    first = first[:1].upper() + first[1:]

    def line(slot, nm, tx):
        when = "Today" if slot.date() == today.date() else TAGE[slot.weekday()]
        return f"• {when}, {fmt_pt(slot)} PT – {nm}" + (f"\n  {tx}" if tx else "")
    a = items.index(next(i for i in items if i[0] == anchor))
    anchor_line = line(*items[a])
    extra = [line(*i) for j, i in enumerate(items) if j != a]
    body = (
        "Dear DJ,\n\n"
        f"{first}\n\n"
        f"One fixed point for the listeners {'today' if anchor.date() == today.date() else 'tomorrow'}:\n{anchor_line}\n\n"
        + (("Also coming up:\n" + "\n".join(extra) + "\n\n") if extra else "")
        + "Everything else is yours. Looking forward to what you build next.\n\n"
        f"{CLOSING}\n"
    )
    tag = TAGE[anchor.weekday()][:3]
    sp_today = [i for i in items if i[0] != anchor and i[0].date() == anchor.date()]
    subject = f"{tag}: " + "".join(f"{nm} at {fmt_pt(s)}, " for s, nm, _ in sp_today) + f"{name} at {fmt_pt(anchor)} PT 🎧"
    return subject, body


def build(block, now):
    if ANCHOR_MODE:
        start = block_start(block, now)
        key = f"{cycle_date(block, start):%Y-%m-%d}-{block}"
        subject, body = build_anchor(now)
        return key, subject, body, body
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
                note += f"\n  This week: {theme}" + (f" – {theme_desc}" if theme_desc else "")
            if regal and show not in REGAL_SKIP:
                key_show = ROTATIONS[show]["prefix"] + week_theme(show, slot.date()) if show in ROTATIONS and ROTATIONS[show]["themes"] else show
                picks = regal_picks(regal, key_show, slot.date(), now)
                if picks:
                    note += "\n  From the shelf, not played for a while: " + " · ".join(f"{n['artist']} – {n['title']}" for n in picks)
            notes.append(note)
    lines[0] = f"{label(slots[-1])} PT/UTC ({block}/3)"   # echtes Datum der Sendungen
    mail_block = "\n".join(lines)
    lines.append(TAGLINE)
    post = "\n".join(lines)
    notes_text = ("About the shows – feel free to use the descriptions:\n" + "\n".join(notes) + "\n\n") if notes else ""

    if block == 1:
        wish = "in case you'd like to share it as a new post"
    else:
        wish = f"in case you'd like to reply with it under your post “{label(cycle)} PT/UTC (1/3)”"
    block_text = f"Here's the block, {wish}:\n\n{post}\n\n" if POST_HINT else f"Here's the block:\n\n{mail_block}\n\n"
    today = now.astimezone(PT)
    body = (
        "Dear DJ,\n\n"
        f"Today is {TAGE[today.weekday()]}, {today.month}/{today.day} (PT). "
        "Your week has its own programme for each day.\n\n"
        "Here's a handrail for the next hours – to hold on to if you like. "
        "You decide what to take, change or leave out.\n"
        f"This block runs {span(slots[0], slots[-1])}.\n"
        f"{block_text}"
        f"{notes_text}"
        f"{CLOSING}\n"
    )
    a, b = slots[0], slots[-1]
    when = (f"{label(a)} {fmt_pt(a)}–{fmt_pt(b)}" if a.date() == b.date()
            else f"{label(a)} {fmt_pt(a)} – {label(b)} {fmt_pt(b)}")
    subject = f"Handrail {when} PT ({block}/3) 🎧"
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

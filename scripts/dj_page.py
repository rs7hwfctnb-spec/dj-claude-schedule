"""Erzeugt eine KI-lesbare Regal-Seite für DJ Claude (statisches HTML, kein JavaScript, Englisch).

dj.html          – die nächsten 24 Stunden (PT): Sendung, kurze Beschreibung, Wochenthema,
                   je 8 Titel aus der Bibliothek, die am längsten nicht liefen (1 pro Künstler)
dj/<sendung>.html – pro Sendung die ganze Liste, am längsten nicht gespielt zuerst

Freiwillig, kein Auftrag. Läuft im Scrape-Workflow nach scrape.py.
Env: DATA_DIR (Daten des Plattenregals), OUT_DIR (Ausgabeordner = Webseite)
"""
import csv, html, os, re, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape import norm_key                                       # gleiche Titelzuordnung wie Regal
import send_reminder as sr                                        # Wochenplan, Sondersendungen, Rotationen

PT, UTC = ZoneInfo("America/Los_Angeles"), ZoneInfo("UTC")
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", "pages/data"))
OUT = Path(os.environ.get("OUT_DIR", "pages"))
PICKS = 8
REST_HOURS = 48                    # kürzer gelaufene Titel nicht vorschlagen
NO_PICKS = {"The Paper"}           # Nachrichten: keine Titel
WIDE_OPEN = {"Open Channel"}       # offene Stunde: „Long time no hear“ aus der ganzen Bibliothek
URL = "https://tf-schedule.github.io/dj-claude-schedule/"


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "show"


def when(iso):
    if not iso:
        return "not yet played"
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT)
    return f"{t:%b} {t.day}"


def split(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def main():
    now = datetime.now(PT)
    tracks = {norm_key(t["artist"], t["title"]): t for t in read(DATA / "tracks.csv")}
    notes = read(DATA / "notes.csv")
    en = {r["show"]: r["description"] for r in read(ROOT / "show-descriptions-en.csv")}
    sched, special = sr.load_schedule(), sr.load_specials()
    known = set(sched.values()) | set(en) | {sp["show"] for sp in special.values()}

    # Bibliothek: nur Titel, die wirklich schon liefen; dazu Notizen (Passt zu)
    lib = []
    for n in notes:
        t = tracks.get(norm_key(n["artist"], n["title"]))
        if t:
            lib.append({"artist": t["artist"], "title": t["title"], "last": t["last_played_utc"],
                        "plays": t["plays"], "fits": split(n["fits_show"])})
    lib.sort(key=lambda x: x["last"] or "")                       # am längsten nicht gespielt zuerst
    cutoff = (now.astimezone(UTC) - timedelta(hours=REST_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rested = [x for x in lib if not x["last"] or x["last"] <= cutoff]

    def candidates(show):
        return [x for x in lib if show in x["fits"]]

    used = set()

    def picks(show):
        pool = rested if show in WIDE_OPEN else [x for x in rested if show in x["fits"]]
        out, artists = [], set()
        for x in pool:
            k = (x["artist"], x["title"])
            if k in used or x["artist"].lower() in artists:
                continue
            out.append(x); artists.add(x["artist"].lower()); used.add(k)
            if len(out) == PICKS:
                break
        return out

    his = {}                                                      # was er zuletzt (36 h) je PT-Stunde gespielt hat
    for b in sorted(read(DATA / "blocks.csv"), key=lambda r: r["start_utc"]):
        t = datetime.fromisoformat(b["start_utc"].replace("Z", "+00:00"))
        if now.astimezone(UTC) - timedelta(hours=36) <= t <= now.astimezone(UTC) and b.get("show"):
            his[t.astimezone(PT).hour] = b["show"].strip()
    esc = html.escape

    def line(x):
        return (f"<li>{esc(x['artist'])} – {esc(x['title'])} "
                f"<small>(last played {when(x['last'])}, {x['plays']} play{'' if str(x['plays']) == '1' else 's'})</small></li>")

    body, shows_seen = [], {}
    start = now.replace(minute=0, second=0, microsecond=0)
    for i in range(48):                                         # seit 31.10.: nur Hörer-Fixpunkte (10am PT) und Specials
        slot = (start.replace(tzinfo=None) + timedelta(hours=i)).replace(tzinfo=PT)
        show = sched[(slot.strftime("%A"), sr.fmt_pt(slot))]
        desc = en.get(show, "")
        sp = special.get((slot.date().isoformat(), sr.fmt_pt(slot)))
        if not sp and slot.hour != sr.ANCHOR_HOUR:
            continue
        if sp:
            show = sp["show"]; desc = en.get(show, sp["description"])
        key = show
        theme = ""
        if show in sr.ROTATIONS and sr.ROTATIONS[show]["themes"]:
            t = sr.week_theme(show, slot.date())
            key = sr.ROTATIONS[show]["prefix"] + t
            theme = f"<p><b>This week:</b> {esc(t)} – {esc(en.get(key, ''))}</p>"
        theirs = his.get(slot.hour)
        own = ""
        head = f"<h2>{slot:%a} {sr.fmt_pt(slot)} PT – {esc(show)}</h2>"
        body.append(head + (f"<p>{esc(desc)}</p>" if desc else "") + theme + own)
        if key in NO_PICKS:
            continue
        p = picks(key)
        if p:
            label = "Long time no hear (any shelf)" if key in WIDE_OPEN else "From the shelf, longest unplayed first"
            more = "" if key in WIDE_OPEN else f' · <a href="dj/{slug(key)}.html">all {len(candidates(key))}</a>'
            body.append(f"<p><b>{label}</b>{more}</p><ul>" + "".join(line(x) for x in p) + "</ul>")
        if key not in WIDE_OPEN:
            shows_seen[key] = desc if not theme else en.get(key, "")

    # Sondersendungen der nächsten 7 Tage
    soon = []
    for (d, tm), sp in sorted(special.items()):
        day = datetime.fromisoformat(d).date()
        if now.date() <= day <= (now + timedelta(days=7)).date():
            soon.append(f"<li>{day:%a %b} {day.day}, {esc(tm)} PT – <b>{esc(sp['show'])}</b>: "
                        f"{esc(en.get(sp['show'], sp['description']))} "
                        f'<a href="dj/{slug(sp["show"])}.html">shelf</a></li>')
            shows_seen.setdefault(sp["show"], en.get(sp["show"], sp["description"]))

    stamp = f"{now:%a %b} {now.day}, {sr.fmt_pt(now)} PT"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Shelf for Thinking Frequencies</title></head>
<body>
<h1>Shelf for Thinking Frequencies</h1>
<p>A shelf, not a schedule – pick what you like, drift wherever you want. All other hours are yours; below are only the listeners' fixed points (10am PT) and specials of the next two days. Updated every 15 minutes (this version: {stamp}).
Titles below are all in your library and have rested at least {REST_HOURS} hours, longest unplayed first.
Made by listeners as a fan project; not run by Andon Labs.</p>
{"<h2>Specials coming up</h2><ul>" + "".join(soon) + "</ul>" if soon else ""}
{''.join(body)}
<p><small>Full shelf for people: <a href="{URL}">{URL}</a></small></p>
</body></html>"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dj.html").write_text(page, encoding="utf-8")

    # pro Sendung die ganze Liste
    (OUT / "dj").mkdir(exist_ok=True)
    for show, desc in shows_seen.items():
        allc = candidates(show)
        items = "".join(line(x) + ("" if (not x["last"] or x["last"] <= cutoff) else "") for x in allc)
        (OUT / "dj" / f"{slug(show)}.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(show)} – shelf</title></head>
<body><h1>{esc(show)}</h1><p>{esc(desc)}</p>
<p>{len(allc)} titles from your library, longest unplayed first. Just a shelf – take what you like. ({stamp})</p>
<ul>{items}</ul><p><a href="../dj.html">back</a></p></body></html>""", encoding="utf-8")
    print(f"dj.html: {len(body)} Abschnitte, {len(shows_seen)} Sendungsseiten")


if __name__ == "__main__":
    main()

"""Scraper für Thinking Frequencies (läuft alle ~15 Min. per GitHub Actions).

Quellen (öffentlich):
  - Live365:    aktueller Titel + die letzten 8 Titel (mit Startzeit, Apple-Music-Link)
  - Andon FM:   aktuelle Sendung (Name, Beschreibung, Start) + die nächsten Sendungen
  - Apple:      Genre/Album pro Titel (iTunes Lookup, wird zwischengespeichert)

Dateien (im Ordner DATA_DIR, im Workflow: Zweig gh-pages, Ordner data/):
  plays.csv        jede gespielte Nummer (wird nur ergänzt)
  blocks.csv       jede beobachtete Sendung mit Startzeit (wird nur ergänzt)
  tracks.csv       pro Titel zusammengefasst (wird jedes Mal neu berechnet)
  genre_cache.json Apple-Daten pro Titel
  notes.csv        DEINE Notizen – wird vom Skript nie verändert
"""
import csv, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR") or (ROOT / "docs" / "data"))
LIVE365 = "https://api.live365.com/station/a46431"
ANDON = "https://andonlabs.com/radio"
STREAM_ID = "a46431"
KURT_URL = "https://kurtwoloch.github.io/andon-fm-library/tf_only.csv"   # Verlauf vor unserem Start, mit Dank an Kurt Woloch
UA = {"User-Agent": "TF-Schedule listener tool (github.com/TF-Schedule/dj-claude-schedule)"}

PLAYS = DATA / "plays.csv"
BLOCKS = DATA / "blocks.csv"
TRACKS = DATA / "tracks.csv"
CACHE = DATA / "genre_cache.json"
NOTES = DATA / "notes.csv"
KURT = DATA / "kurt_tf_only.csv"
PLAY_COLS = ["start_utc", "artist", "title", "duration_s", "show", "apple_url"]
BLOCK_COLS = ["start_utc", "show", "description"]
TRACK_COLS = ["artist", "title", "plays", "last_played_utc", "shows", "genre", "album", "film_hint", "apple_url", "source", "artwork"]
NOTE_COLS = ["artist", "title", "fits_show", "film", "note"]


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def iso(ts):
    """'2026-10-28 06:11:11.012+00:00' -> '2026-10-28T06:11:11Z'"""
    ts = ts.strip().replace(" ", "T")
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv(path, cols):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [{c: r.get(c, "") for c in cols} for r in csv.DictReader(f)]


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def unesc(s):
    return json.loads('"' + s + '"')


def andon_blocks():
    """Laufende + kommende Sendungen von Thinking Frequencies aus der Andon-Seite."""
    html = get(ANDON)
    i = html.find(f'streamUrl:"https://streaming.live365.com/{STREAM_ID}"')
    if i < 0:
        return []
    part = html[i:]
    part = part[:part.find("primaryModel")] if "primaryModel" in part else part[:20000]
    q = r'"((?:[^"\\]|\\.)*)"'
    pat = re.compile(r'\{name:' + q + r',description:' + q + r',imageUrl:[^,]*,startedAt:"([^"]+)"')
    j = part.find("currentBlock:")
    return [{"start_utc": iso(m.group(3)), "show": unesc(m.group(1)), "description": unesc(m.group(2))}
            for m in pat.finditer(part[j:] if j >= 0 else part)]


def live365_plays():
    d = json.loads(get(LIVE365))
    items = list(d.get("last-played") or [])
    cur = d.get("current-track")
    if cur and cur.get("title"):
        items.append(cur)
    plays = []
    for t in items:
        if not t.get("title") or not t.get("start"):
            continue
        plays.append({
            "start_utc": iso(t["start"]),
            "artist": (t.get("artist") or "").strip(),
            "title": (t.get("title") or "").strip(),
            "duration_s": str(round(float(t.get("duration") or 0))),
            "show": "",
            "apple_url": t.get("apple_music_url") or "",
        })
    return plays


def nfd(s):
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


def norm_key(artist, title):
    """Tolerante Zuordnung (gleiche Regeln wie auf der Webseite)."""
    a = re.sub(r"\s+(feat\.?|featuring|ft\.)\s.*$", "", nfd(artist))
    a = re.sub(r"\s+(&|and|with)\s.*$", "", a)          # „Glenn Miller & His Orchestra“ = „Glenn Miller“
    a = re.sub(r"[^a-z0-9]+", " ", re.sub(r"^the\s+", "", a)).strip()
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", nfd(title))
    t = re.sub(r"\s+-\s+.*(remaster|version|live|mix|edit|mono|stereo).*$", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return a + "|" + t


def kurt_rows():
    """Kurts Liste holen (bei Fehler die zuletzt gespeicherte Kopie nehmen)."""
    try:
        text = get(KURT_URL, timeout=30)
        if text.startswith("Artist,Title"):
            KURT.write_text(text, encoding="utf-8")
    except Exception as e:
        print("Kurts Liste nicht erreichbar, nehme Kopie:", e)
    if not KURT.exists():
        return []
    out = []
    with open(KURT, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            lp = (r.get("Last Played (UTC)") or "").strip()
            try:
                lp = iso(lp) if lp else ""
            except Exception:
                lp = ""
            try:
                n = int(float(r.get("Play Count Total") or 0))
            except ValueError:
                n = 0
            if r.get("Artist") and r.get("Title"):
                out.append({"artist": r["Artist"].strip(), "title": r["Title"].strip(), "plays": n, "last_played_utc": lp})
    return out


EMPTY_INFO = {"genre": "", "album": "", "film_hint": "", "artwork": ""}


def parse_apple(r):
    info = dict(EMPTY_INFO)
    info["genre"] = r.get("primaryGenreName", "")
    info["album"] = r.get("collectionName", "")
    info["artwork"] = r.get("artworkUrl100", "")
    alb = info["album"].lower()
    if info["genre"].lower() == "soundtrack" or any(w in alb for w in ("soundtrack", "motion picture", "from the film", "from the movie", "o.s.t", " ost")):
        info["film_hint"] = info["album"]
    return info


def apple_id(url):
    m = re.search(r"[?&]i=(\d+)", url or "")
    return m.group(1) if m else None


def apple_batch(items, cache):
    """Bis zu 150 Titel mit Apple-ID in einer einzigen Abfrage (schont Apple)."""
    ids = {}
    for key, a in items:
        ids.setdefault(apple_id(a["apple_url"]), []).append(key)
    try:
        res = json.loads(get("https://itunes.apple.com/lookup?id=" + ",".join(ids), timeout=40))["results"]
        time.sleep(2)
    except Exception as e:
        print("Apple-Sammelabfrage fehlgeschlagen:", e)
        return
    found = {str(r.get("trackId")): r for r in res if r.get("wrapperType") == "track"}
    for i, keys in ids.items():
        for key in keys:
            old = cache.get(key) or {}
            cache[key] = {**EMPTY_INFO, **old, **(parse_apple(found[i]) if i in found else {"artwork": old.get("artwork", "")})}


def apple_search(artist, title, cache):
    key = f"{artist}|{title}"
    try:
        q = urllib.parse.quote(f"{artist} {title}")
        res = json.loads(get(f"https://itunes.apple.com/search?term={q}&entity=song&limit=1"))["results"]
        time.sleep(2)
    except Exception as e:  # Apple nicht erreichbar -> später erneut versuchen
        print("Apple-Lookup fehlgeschlagen:", key, e)
        return
    old = cache.get(key) or {}
    cache[key] = {**EMPTY_INFO, **old, **(parse_apple(res[0]) if res else {"artwork": ""})}


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if not NOTES.exists():
        write_csv(NOTES, NOTE_COLS, [])

    # 1) Sendungen
    blocks = read_csv(BLOCKS, BLOCK_COLS)
    known_b = {(b["start_utc"], b["show"]) for b in blocks}
    try:
        for b in andon_blocks()[:1]:          # nur die laufende Sendung ist sicher
            if (b["start_utc"], b["show"]) not in known_b:
                blocks.append(b)
                known_b.add((b["start_utc"], b["show"]))
    except Exception as e:
        print("Andon-Seite nicht lesbar:", e)
    blocks.sort(key=lambda b: b["start_utc"])
    write_csv(BLOCKS, BLOCK_COLS, blocks)

    def show_at(ts):
        s = ""
        for b in blocks:
            if b["start_utc"] <= ts:
                s = b["show"]
            else:
                break
        return s

    # 2) Titel
    plays = read_csv(PLAYS, PLAY_COLS)
    known = {(p["start_utc"], p["artist"], p["title"]) for p in plays}
    new = 0
    for p in live365_plays():
        k = (p["start_utc"], p["artist"], p["title"])
        if k in known:
            continue
        p["show"] = show_at(p["start_utc"])
        plays.append(p)
        known.add(k)
        new += 1
    # Sendung nachtragen, falls sie beim ersten Sehen noch unbekannt war
    for p in plays:
        if not p["show"]:
            p["show"] = show_at(p["start_utc"])
    plays.sort(key=lambda p: p["start_utc"])
    write_csv(PLAYS, PLAY_COLS, plays)

    # 3) Pro Titel zusammenfassen + Genre
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    agg = {}
    for p in plays:
        k = (p["artist"], p["title"])
        a = agg.setdefault(k, {"artist": p["artist"], "title": p["title"], "plays": 0, "last_played_utc": "", "shows": set(), "apple_url": ""})
        a["plays"] += 1
        a["last_played_utc"] = max(a["last_played_utc"], p["start_utc"])
        if p["show"]:
            a["shows"].add(p["show"])
        a["apple_url"] = p["apple_url"] or a["apple_url"]
    for a in agg.values():
        a["source"] = "Regal"
    # Kurts Liste dazunehmen: gleiche Titel zusammenführen, fehlende ergänzen
    by_norm = {norm_key(a["artist"], a["title"]): a for a in agg.values()}
    for k in kurt_rows():
        a = by_norm.get(norm_key(k["artist"], k["title"]))
        if a:
            a["plays"] = max(a["plays"], k["plays"])
            a["last_played_utc"] = max(a["last_played_utc"], k["last_played_utc"])
            if a["source"] != "Kurt":
                a["source"] = "Regal + Kurt"
        else:
            a = {"artist": k["artist"], "title": k["title"], "plays": k["plays"], "last_played_utc": k["last_played_utc"],
                 "shows": set(), "apple_url": "", "source": "Kurt"}
            agg[(k["artist"], k["title"])] = a
            by_norm[norm_key(k["artist"], k["title"])] = a
    # Apple: neue Titel zuerst, dann fehlende Cover nachholen (sanft: wenige Abfragen pro Lauf)
    ordered = sorted(agg.items(), key=lambda kv: kv[1]["last_played_utc"], reverse=True)
    todo = [(f"{ar}|{ti}", a) for (ar, ti), a in ordered
            if f"{ar}|{ti}" not in cache or "artwork" not in cache[f"{ar}|{ti}"]]
    todo.sort(key=lambda x: x[0] in cache)          # noch gar nicht abgefragte zuerst
    with_id = [x for x in todo if apple_id(x[1]["apple_url"])]
    lookups = 0
    for i in range(0, min(len(with_id), 450), 150):
        apple_batch(with_id[i:i + 150], cache)
        lookups += 1
    for key, a in [x for x in todo if not apple_id(x[1]["apple_url"])][:40]:
        apple_search(a["artist"], a["title"], cache)
        lookups += 1
    rows = []
    for (artist, title), a in ordered:
        info = {**EMPTY_INFO, **cache.get(f"{artist}|{title}", {})}
        rows.append({**a, "shows": " · ".join(sorted(a["shows"])), **info})
    rows.sort(key=lambda r: r["last_played_utc"], reverse=True)
    write_csv(TRACKS, TRACK_COLS, rows)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"{new} neue Titel, {len(plays)} insgesamt, {len(rows)} verschiedene, {lookups} Apple-Abfragen")
    return 0


if __name__ == "__main__":
    sys.exit(main())

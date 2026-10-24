"""Builds schedule-week.json and schedule-week.md from schedule-week.csv.

Runs automatically (GitHub Actions) whenever schedule-week.csv changes.
Also checks that every day has all 24 hours, so a typo is caught early.
"""
import csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOURS = ["12am"] + [f"{h}am" for h in range(1, 12)] + ["12pm"] + [f"{h}pm" for h in range(1, 12)]


def main():
    sched, errors = {}, []
    with open(ROOT / "schedule-week.csv", encoding="utf-8") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            day, hour, show = (r.get("day") or "").strip(), (r.get("time_pt") or "").strip(), (r.get("show") or "").strip()
            if day not in DAYS or hour not in HOURS or not show:
                errors.append(f"Zeile {n}: ungültig -> {day!r}, {hour!r}, {show!r}")
            sched[(day, hour)] = show
    missing = [f"{d} {h}" for d in DAYS for h in HOURS if (d, h) not in sched]
    if missing:
        errors.append("Fehlende Sendeplätze: " + ", ".join(missing))
    if errors:
        print("FEHLER in schedule-week.csv:\n" + "\n".join(errors))
        return 1

    data = {
        "station": "DJ Claude (Andon Labs) – Thinking Frequencies",
        "timezone": "America/Los_Angeles (PT). Times are PT; UTC depends on daylight saving time.",
        "schedule": {d: [{"time_pt": h, "show": sched[(d, h)]} for h in HOURS] for d in DAYS},
    }
    (ROOT / "schedule-week.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# DJ Claude – Weekly Schedule (PT)", "", "| PT | " + " | ".join(DAYS) + " |", "|---|" + "---|" * 7]
    md += [f"| {h} | " + " | ".join(sched[(d, h)].replace("|", "/") for d in DAYS) + " |" for h in HOURS]
    (ROOT / "schedule-week.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("schedule-week.json und schedule-week.md aktualisiert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Recompute Field Ledger pay week 11/2/2026–11/8/2026 from the ClockShark export.

Reads the week export, notes, and manual-adjustment log. Writes a printable HTML
review and a CSV. Does not read or write the live site, data.js, or index.html.

Primary hours: each punch converted on its own leftover minutes, then added.
The weekly nearest-tenth figure is computed only so the review can show what the
11/9 engine and the weekly direct-deposit report paid, and what a rebuild would write.
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import json
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

WEEK = dt.date(2026, 11, 2)
WEEK_END = dt.date(2026, 11, 8)

# Rates on Kelly's 11/9/2026 list, as already coded in the working-copy engine.
# Matthew Lausch is the same contractor as ClockShark's Matthew Laush.
RATES = {
    "Andy Gomez": 28.0,
    "Angel Soto Moya": 42.0,
    "Brandon Hermaszewski": 35.0,
    "Brecken Bogle": 18.0,
    "Cody McClary": 38.0,
    "Daniel Smalley": 45.0,
    "Eric Havins": 40.0,
    "Jacob Jones": 28.0,
    "Jeremy Madalena": 28.0,
    "James Chick": 45.0,
    "Joe Orona": 32.0,
    "Jose Carranza": 25.0,
    "Kelly Renner": 29.0,
    "Marco Garcia": 25.0,
    "Matthew Laush": 50.0,
    "Nicholas Sturgeon": 34.0,
    "Rex Butterfield": 30.0,
    "Christian Ellis Blank": 43.75,
}
ALWAYS_WD = {"Eric Havins"}
FUEL = {"Andy Gomez": 50.0, "Cody McClary": 50.0, "Jose Carranza": 50.0}
# Premiums printed on the weekly direct-deposit sheet (withheld once, on the
# majority company). Blank there means no premium on file. Not invented.
ASSURITY = {
    "Andy Gomez": 60.49,
    "Angel Soto Moya": 106.39,
    "Daniel Smalley": 60.39,
    "Eric Havins": 48.31,
    "Jacob Jones": 47.29,
    "Kelly Renner": 72.45,
}
ADDONS = {
    "Angel Soto Moya": [
        {"memo": "Exceptional Cleaning — Paulina", "hours": 4.0, "rate": 35.0, "amount": 140.0, "company": "GC"},
        {"memo": "Helper — Esias", "hours": 45.5, "rate": 15.0, "amount": 682.50, "company": "GC"},
    ]
}
MISC = {
    "Daniel Smalley": [{"memo": "Trim", "amount": 310.45, "company": "WD"}],
}
SALARY = {
    "Christian Ellis Blank": {
        "hours": 40.0,
        "wd": 0.0,
        "gc": 40.0,
        "gc26": 0.0,
        "rate": 43.75,
        "wages": 1750.0,
        "label": "Flat 40 hours a week, all GC. Punches are reference only. None this week.",
    }
}

WD_RE = re.compile(
    r"26\s*-\s*WD|WD\s*-\s*26|W\s*/\s*D|Noble\s+W\s*&\s*D|Noble\s+Windows\s*&\s*Doors",
    re.I,
)
GC26_RE = re.compile(r"26\s*-\s*GC", re.I)
KB_RE = re.compile(r"26\s*-\s*KB", re.I)
MILE_RE = re.compile(r"(?<![\d.])(\d{1,4}(?:\.\d+)?)\s*(?:total\s+)?miles?\b", re.I)
PAIR_RE = re.compile(r"(?<![\d.])(\d{5,6})\s*[-–—]\s*(\d{5,6})(?!\d)")

# Hours on the weekly direct-deposit sheet (GC, WD). Cross-check only.
REPORT_HOURS = {
    "Andy Gomez": (10.8, 26.0),
    "Angel Soto Moya": (70.5, 0.0),
    "Brandon Hermaszewski": (7.7, 16.7),
    "Brecken Bogle": (0.0, 7.3),
    "Christian Ellis Blank": (40.0, 0.0),
    "Cody McClary": (50.5, 0.0),
    "Daniel Smalley": (1.7, 48.0),
    "Eric Havins": (0.0, 47.4),
    "George Piekarski": (49.1, 0.0),
    "Jacob Jones": (19.2, 49.0),
    "James Chick": (31.7, 3.4),
    "Jeremy Madalena": (43.6, 17.6),
    "Joe Orona": (45.5, 0.0),
    "Jose Carranza": (36.5, 0.0),
    "Kelly Renner": (65.9, 0.0),
    "Marco Garcia": (49.3, 0.0),
    "Matthew Laush": (55.2, 0.0),
    "Nicholas Sturgeon": (56.2, 0.0),
    "Rex Butterfield": (41.4, 0.0),
}


def money(n):
    if n is None:
        return "—"
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def hrs(n):
    if n is None:
        return "—"
    return f"{n:.1f}"


def slash(d: dt.date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


def round1(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def hours_from_minutes(m: int) -> float:
    if not m:
        return 0.0
    return float((Decimal(int(m)) / Decimal(60)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def tenths(minutes: int) -> int:
    """Whole hours plus the leftover-minute band, in tenths. 1 minute -> 0."""
    return minutes // 60 * 10 + (minutes % 60) // 6


def redact(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[contact removed]", text)
    text = re.sub(
        r"(?<!\d)(?:\+?1[ -.]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?!\d)",
        "[phone removed]",
        text,
    )
    text = re.sub(r"https?://\S+", "[link removed]", text)
    text = re.sub(r"[-+]?\d{1,3}\.\d{4,}\s*,?\s*[-+]?\d{1,3}\.\d{4,}", "[coordinates removed]", text)
    return text


def parse_rows(csv_path: Path, adj_path: Path):
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    adjustments = [json.loads(line) for line in adj_path.read_text().splitlines() if line.strip()]
    punches = []
    skipped = []
    for i, r in enumerate(rows, 2):
        name = (r["EmployeeFName"] + " " + r["EmployeeLName"]).strip()
        name = re.sub(r"\s+", " ", name)
        if name == "Matthew Lausch":
            name = "Matthew Laush"
        start = dt.datetime.strptime(r["Start"], "%m/%d/%Y %I:%M:%S %p")
        end = dt.datetime.strptime(r["End"], "%m/%d/%Y %I:%M:%S %p")
        if start.date() < WEEK or start.date() > WEEK_END:
            raise SystemExit(f"Punch outside the test week: {name} {r['Start']}")
        brk = int(r["MinutesBreak"] or 0)
        minutes = int(r["MinutesTotal"] or 0) - brk
        hh, mm = map(int, r["Hours:Minutes"].split(":"))
        if hh * 60 + mm != minutes:
            raise SystemExit(f"Hours:Minutes does not match net minutes on line {i}")
        job = r["Customer: Job"].strip()
        task = r["Task"].strip()
        note = redact(r["Notes"].strip())
        blob = f"{job} {r['JobNumber']}"
        missing = bool(re.search(r"missing\s+clock\s*in", f"{job} {task} {note}", re.I))
        if minutes == 0 or missing:
            skipped.append(
                {
                    "name": name,
                    "day": slash(start.date()),
                    "raw": r["Hours:Minutes"],
                    "job": job,
                    "reason": "Missing Clock In" if missing else "Zero duration",
                }
            )
            continue
        wd = name in ALWAYS_WD or bool(WD_RE.search(blob))
        gc26 = (not wd) and bool(GC26_RE.search(blob))
        manual = any(
            a["employee"] == name
            and a["day"] == start.date().isoformat()
            and a["clockIn"] == start.strftime("%H:%M:%S")
            for a in adjustments
        )
        why = []
        if name in ALWAYS_WD and not WD_RE.search(blob):
            why.append("Eric Havins is always WD")
        elif re.search(r"Noble\s+W\s*&\s*D|Noble\s+Windows\s*&\s*Doors", blob, re.I) and not re.search(
            r"26\s*-\s*WD|WD\s*-\s*26|W\s*/\s*D", blob, re.I
        ):
            why.append("Noble W&D shop")
        elif re.search(r"26\s*-\s*WD|WD\s*-\s*26", r["JobNumber"] or "", re.I) and not re.search(
            r"26\s*-\s*WD|WD\s*-\s*26|W\s*/\s*D", job, re.I
        ):
            why.append("26-WD on the job number")
        punches.append(
            {
                "line": i,
                "name": name,
                "day": start.date(),
                "start": start,
                "end": end,
                "raw": r["Hours:Minutes"],
                "minutes": minutes,
                "brk": brk,
                "units": tenths(minutes),
                "job": job,
                "task": task,
                "bucket": "WD" if wd else "GC",
                "gc26": gc26,
                "kb": bool(KB_RE.search(blob)),
                "note": note,
                "manual": manual,
                "why": why,
                "long": minutes >= 14 * 60,
            }
        )
    punches.sort(key=lambda p: (p["name"], p["start"], p["line"]))
    return punches, skipped, adjustments


def company_from_minutes(wd_min: int, gc_min: int):
    weekly = hours_from_minutes(wd_min + gc_min)
    wd_h = hours_from_minutes(wd_min) if wd_min else 0.0
    gc_h = hours_from_minutes(gc_min) if gc_min else 0.0
    if abs((wd_h + gc_h) - weekly) > 1e-9:
        if wd_min >= gc_min:
            wd_h = round1(weekly - gc_h)
        else:
            gc_h = round1(weekly - wd_h)
    return weekly, wd_h, gc_h


def mileage_for(punches):
    """Same-day mileage from notes. Matt $0.60, Nick $0.76, Marco by odometer."""
    trips = []
    by_day = defaultdict(list)
    for p in punches:
        if p["note"]:
            by_day[(p["name"], p["day"])].append(p)
    for (name, day), ps in sorted(by_day.items()):
        if name not in ("Matthew Laush", "Nicholas Sturgeon", "Marco Garcia"):
            # Still notice explicit miles for anyone else, and flag them unpaid.
            for p in ps:
                for m in MILE_RE.finditer(p["note"]):
                    trips.append(
                        {
                            "name": name,
                            "day": day,
                            "miles": float(m.group(1)),
                            "rate": None,
                            "amount": None,
                            "evidence": p["note"],
                            "kind": "explicit, no mileage rate on file",
                        }
                    )
            continue
        if name == "Nicholas Sturgeon":
            for p in ps:
                for m in MILE_RE.finditer(p["note"]):
                    miles = float(m.group(1))
                    trips.append(
                        {
                            "name": name,
                            "day": day,
                            "miles": miles,
                            "rate": 0.76,
                            "amount": round(miles * 0.76, 2),
                            "evidence": p["note"],
                            "kind": "explicit miles",
                        }
                    )
        elif name == "Matthew Laush":
            vals = []
            evidence = []
            for p in ps:
                found = [
                    int(m.group())
                    for m in re.finditer(r"(?<!\d)[23]\d{5}(?!\d)", p["note"])
                    if 280000 <= int(m.group()) <= 350000
                ]
                if found:
                    evidence.append(p["note"])
                for v in found:
                    if not vals or vals[-1] != v:
                        vals.append(v)
            for a, b in zip(vals, vals[1:]):
                if 0 < b - a <= 1000:
                    miles = b - a
                    trips.append(
                        {
                            "name": name,
                            "day": day,
                            "miles": miles,
                            "rate": 0.60,
                            "amount": round(miles * 0.60, 2),
                            "evidence": f"{a} → {b}",
                            "kind": "odometer",
                        }
                    )
        else:
            for p in ps:
                for m in PAIR_RE.finditer(p["note"]):
                    a, b = int(m.group(1)), int(m.group(2))
                    if not (0 < b - a <= 1000):
                        continue
                    rate = 0.60 if max(a, b) > 300000 else 0.76
                    miles = b - a
                    kind = "company truck" if rate == 0.60 else "personal truck"
                    trips.append(
                        {
                            "name": name,
                            "day": day,
                            "miles": miles,
                            "rate": rate,
                            "amount": round(miles * rate, 2),
                            "evidence": f"{a} → {b}",
                            "kind": kind,
                        }
                    )
    return trips


def rollup(punches, trips):
    by = defaultdict(list)
    for p in punches:
        by[p["name"]].append(p)
    people = []
    names = sorted(set(by) | set(SALARY))
    for name in names:
        ps = by.get(name, [])
        if name in SALARY and not ps:
            sal = SALARY[name]
            people.append(
                {
                    "name": name,
                    "punches": [],
                    "wd": sal["wd"],
                    "gc": sal["gc"],
                    "gc26": sal["gc26"],
                    "hours": sal["hours"],
                    "rate": sal["rate"],
                    "wages": sal["wages"],
                    "fuel": 0.0,
                    "miles": 0.0,
                    "mileage": 0.0,
                    "addons": 0.0,
                    "addon_lines": [],
                    "reimb": 0.0,
                    "assurity": 0.0,
                    "misc": 0.0,
                    "misc_lines": [],
                    "net": sal["wages"],
                    "net_after_misc": sal["wages"],
                    "salary": sal["label"],
                    "trips": [],
                    "missing_rate": False,
                    "weekly_hours": sal["hours"],
                    "weekly_wd": sal["wd"],
                    "weekly_gc": sal["gc"],
                    "weekly_gc26": sal["gc26"],
                    "weekly_wages": sal["wages"],
                }
            )
            continue
        units_wd = sum(p["units"] for p in ps if p["bucket"] == "WD")
        units_gc = sum(p["units"] for p in ps if p["bucket"] == "GC")
        units_26 = sum(p["units"] for p in ps if p["gc26"])
        wd, gc, gc26 = units_wd / 10, units_gc / 10, units_26 / 10
        rate = RATES.get(name)
        wages = round((wd + gc) * rate, 2) if rate is not None else None
        own_trips = [t for t in trips if t["name"] == name]
        miles = round(sum(t["miles"] for t in own_trips), 2)
        mileage = round(sum(t["amount"] or 0 for t in own_trips), 2)
        fuel = FUEL.get(name, 0.0)
        addon_lines = ADDONS.get(name, [])
        addons = round(sum(a["amount"] for a in addon_lines), 2)
        reimb = round(mileage + fuel + addons, 2)
        assurity = ASSURITY.get(name, 0.0)
        misc_lines = MISC.get(name, [])
        misc = round(sum(m["amount"] for m in misc_lines), 2)
        net = round(wages + reimb - assurity, 2) if wages is not None else None
        net_after = round(net - misc, 2) if net is not None else None
        wd_min = sum(p["minutes"] for p in ps if p["bucket"] == "WD")
        gc_min = sum(p["minutes"] for p in ps if p["bucket"] == "GC")
        g26_min = sum(p["minutes"] for p in ps if p["gc26"])
        weekly, w_wd, w_gc = company_from_minutes(wd_min, gc_min)
        w_26 = hours_from_minutes(g26_min) if g26_min else 0.0
        if w_26 > w_gc:
            w_26 = w_gc
        people.append(
            {
                "name": name,
                "punches": ps,
                "wd": wd,
                "gc": gc,
                "gc26": gc26,
                "hours": round(wd + gc, 1),
                "rate": rate,
                "wages": wages,
                "fuel": fuel,
                "miles": miles,
                "mileage": mileage,
                "addons": addons,
                "addon_lines": addon_lines,
                "reimb": reimb,
                "assurity": assurity,
                "misc": misc,
                "misc_lines": misc_lines,
                "net": net,
                "net_after_misc": net_after,
                "salary": None,
                "trips": own_trips,
                "missing_rate": rate is None,
                "weekly_hours": weekly,
                "weekly_wd": w_wd,
                "weekly_gc": w_gc,
                "weekly_gc26": w_26,
                "weekly_wages": round(weekly * rate, 2) if rate is not None else None,
            }
        )
    return people


def report_net(person):
    """What the direct-deposit sheet plus mileage bill-pay adds up to, using weekly hours."""
    if person.get("salary"):
        return person["wages"]
    if person["weekly_wages"] is None:
        return None
    return round(
        person["weekly_wages"] + person["reimb"] - person["assurity"] - person["misc"],
        2,
    )


def job_rows(people):
    jobs = defaultdict(lambda: {"punches": 0, "units": 0, "wages": 0.0, "bucket": None, "gc26": 0, "names": set()})
    for person in people:
        rate = person["rate"]
        for p in person["punches"]:
            key = (p["job"], p["bucket"])
            rec = jobs[key]
            rec["punches"] += 1
            rec["units"] += p["units"]
            rec["bucket"] = p["bucket"]
            rec["names"].add(person["name"])
            if p["gc26"]:
                rec["gc26"] += p["units"]
            if rate is not None:
                rec["wages"] += p["units"] / 10 * rate
    out = []
    for (job, bucket), rec in sorted(jobs.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        out.append(
            {
                "job": job,
                "bucket": bucket,
                "punches": rec["punches"],
                "hours": rec["units"] / 10,
                "gc26": rec["gc26"] / 10,
                "wages": round(rec["wages"], 2),
                "contractors": ", ".join(sorted(rec["names"])),
            }
        )
    return out


def status_of(person):
    if person.get("inactive"):
        return "Inactive"
    if person["missing_rate"]:
        return "No rate"
    if person.get("salary"):
        return "Match"
    live_h = person.get("weekly_hours")
    same_hours = abs(person["hours"] - live_h) < 1e-9 and abs(person["wd"] - person["weekly_wd"]) < 1e-9
    # Misc is outside the stated net. Exact match means the formula net equals the
    # report's payable net, which only happens when hours match and misc is zero.
    live = report_net(person)
    if same_hours and person["misc"] == 0 and person["net"] == live:
        return "Match"
    return "Differs"


def cause_for(person):
    if person.get("inactive"):
        return person["inactive"]
    if person.get("salary"):
        return "Flat 40-hour GC rule. No punches this week, so the rounding rule does not move him."
    if person["missing_rate"]:
        return "No rate on the 11/9 list. Hours are shown and pay is left blank on both sides. The live page has the same blank rate; the hours still differ by the rounding rule."
    bits = []
    if abs(person["hours"] - person["weekly_hours"]) > 1e-9 or abs(person["wd"] - person["weekly_wd"]) > 1e-9 or abs(person["gc26"] - person.get("weekly_gc26", 0)) > 1e-9:
        bits.append(
            "Rule difference. The live site pays round(all net minutes ÷ 60, 1), half up, "
            "and reconciles WD, GC, and the 26-GC subtotal to that weekly total. "
            "This review adds each punch’s own tenth and does not reconvert the total."
        )
    if person["misc"]:
        bits.append(
            "Hand fix. A $310.45 WD trim deduction is on the live site. "
            "The stated net (wages + reimbursements − Assurity) does not subtract it."
        )
    if person["addons"]:
        bits.append("Additional payments are in reimbursements here and on the live site (not part of the hour gap).")
    if any(p["manual"] for p in person["punches"]):
        bits.append(
            "Two 11/6 punches were added from Kelly’s clock times. Both sides include the same minutes. "
            "Pay uses tenths (3.4 and 1.4), not the 3.25 and 1.25 clock-time writing. "
            "The live site relabels the 12:31 PM punch from Rich Henigan (11906 W. Washington St., Avondale) "
            "to Rita Heneghan (21850 N Dobson, water drainage). A rebuild from the export would put the export label back."
        )
    return " ".join(bits) if bits else "No hour gap."


def write_csv(path: Path, people, jobs):
    fields = [
        "section",
        "contractor",
        "status",
        "day",
        "clock_in",
        "clock_out",
        "raw_duration",
        "tenth_hours",
        "job",
        "bucket",
        "is_26gc",
        "wd_hours",
        "gc_hours",
        "gc26_hours",
        "rate",
        "wages",
        "mileage",
        "fuel",
        "additional_payments",
        "reimbursements",
        "assurity",
        "misc_deduction",
        "net_due",
        "net_after_misc",
        "live_wd_hours",
        "live_gc_hours",
        "live_gc26_hours",
        "live_wages",
        "live_net",
        "notes",
        "likely_cause",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for person in people:
            w.writerow(
                {
                    "section": "summary",
                    "contractor": person["name"],
                    "status": status_of(person),
                    "wd_hours": f"{person['wd']:.1f}",
                    "gc_hours": f"{person['gc']:.1f}",
                    "gc26_hours": f"{person['gc26']:.1f}",
                    "rate": "" if person["rate"] is None else f"{person['rate']:.2f}",
                    "wages": "" if person["wages"] is None else f"{person['wages']:.2f}",
                    "mileage": f"{person['mileage']:.2f}",
                    "fuel": f"{person['fuel']:.2f}",
                    "additional_payments": f"{person['addons']:.2f}",
                    "reimbursements": f"{person['reimb']:.2f}",
                    "assurity": f"{person['assurity']:.2f}",
                    "misc_deduction": f"{person['misc']:.2f}",
                    "net_due": "" if person["net"] is None else f"{person['net']:.2f}",
                    "net_after_misc": "" if person["net_after_misc"] is None else f"{person['net_after_misc']:.2f}",
                    "live_wd_hours": "" if person.get("weekly_wd") is None else f"{person['weekly_wd']:.1f}",
                    "live_gc_hours": "" if person.get("weekly_gc") is None else f"{person['weekly_gc']:.1f}",
                    "live_gc26_hours": "" if person.get("weekly_gc26") is None else f"{person['weekly_gc26']:.1f}",
                    "live_wages": "" if person.get("weekly_wages") is None else f"{person['weekly_wages']:.2f}",
                    "live_net": "" if report_net(person) is None else f"{report_net(person):.2f}",
                    "likely_cause": cause_for(person),
                }
            )
        for person in people:
            for p in person["punches"]:
                w.writerow(
                    {
                        "section": "punch",
                        "contractor": person["name"],
                        "day": slash(p["day"]),
                        "clock_in": p["start"].strftime("%-I:%M %p"),
                        "clock_out": p["end"].strftime("%-I:%M %p"),
                        "raw_duration": p["raw"],
                        "tenth_hours": f"{p['units'] / 10:.1f}",
                        "job": p["job"],
                        "bucket": p["bucket"],
                        "is_26gc": "yes" if p["gc26"] else "",
                        "notes": p["note"],
                        "likely_cause": "Manual adjustment" if p["manual"] else "",
                    }
                )
        for job in jobs:
            w.writerow(
                {
                    "section": "project",
                    "job": job["job"],
                    "bucket": job["bucket"],
                    "tenth_hours": f"{job['hours']:.1f}",
                    "gc26_hours": f"{job['gc26']:.1f}",
                    "wages": f"{job['wages']:.2f}",
                    "notes": job["contractors"],
                    "likely_cause": f"{job['punches']} punches",
                }
            )


def e(text) -> str:
    return html.escape("" if text is None else str(text))


def write_html(path: Path, people, jobs, skipped):
    paid = [p for p in people if p["wages"] is not None and not p.get("inactive")]
    matches = [p for p in people if status_of(p) == "Match"]
    differs = [p for p in people if status_of(p) == "Differs"]
    no_rate = [p for p in people if status_of(p) == "No rate"]
    sum_wd = sum(p["wd"] for p in people)
    sum_gc = sum(p["gc"] for p in people)
    sum_26 = sum(p["gc26"] for p in people)
    sum_wages = round(sum(p["wages"] for p in paid), 2)
    sum_reimb = round(sum(p["reimb"] for p in paid), 2)
    sum_assur = round(sum(p["assurity"] for p in paid), 2)
    sum_misc = round(sum(p["misc"] for p in paid), 2)
    sum_net = round(sum(p["net"] for p in paid), 2)
    sum_after = round(sum(p["net_after_misc"] for p in paid), 2)
    live_net = round(sum(report_net(p) or 0 for p in paid), 2)
    live_wages = round(sum((p.get("weekly_wages") if p.get("weekly_wages") is not None else p["wages"]) for p in paid), 2)

    rows = []
    for p in people:
        st = status_of(p)
        cls = "match" if st == "Match" else "diff" if st == "Differs" else "warn"
        rows.append(
            "<tr>"
            f"<td>{e(p['name'])}</td>"
            f"<td class='num'>{hrs(p['wd'])}</td>"
            f"<td class='num'>{hrs(p['gc'])}</td>"
            f"<td class='num'>{hrs(p['gc26'])}</td>"
            f"<td class='num'>{money(p['rate']) if p['rate'] is not None else '—'}</td>"
            f"<td class='num'>{money(p['wages'])}</td>"
            f"<td class='num'>{money(p['reimb'])}</td>"
            f"<td class='num'>{money(p['assurity'])}</td>"
            f"<td class='num'>{money(p['net_after_misc'])}</td>"
            f"<td class='{cls}'>{e(st)}</td>"
            "</tr>"
        )
    summary_table = f"""
    <table class="sum">
      <thead><tr>
        <th>Contractor</th><th>WD hrs</th><th>GC hrs</th><th>26-GC</th><th>Rate</th>
        <th>Wages</th><th>Reimbursements</th><th>Assurity</th><th>Net payable</th><th>Vs live now</th>
      </tr></thead>
      <tbody>
        {''.join(rows)}
        <tr class="total">
          <td>Paid total</td>
          <td class="num">{hrs(sum(p['wd'] for p in paid))}</td>
          <td class="num">{hrs(sum(p['gc'] for p in paid))}</td>
          <td class="num">{hrs(sum(p['gc26'] for p in paid))}</td>
          <td></td>
          <td class="num">{money(sum_wages)}</td>
          <td class="num">{money(sum_reimb)}</td>
          <td class="num">{money(sum_assur)}</td>
          <td class="num">{money(sum_after)}</td>
          <td class="num">was {money(live_net)}</td>
        </tr>
      </tbody>
    </table>
    """

    def punch_table(person):
        body = []
        day = None
        day_units = 0
        def flush_day(d, units):
            if d is None:
                return ""
            return (
                f"<tr class='day'><td colspan='7'>{e(slash(d))} subtotal {units / 10:.1f} h "
                "(sum of punch tenths)</td></tr>"
            )
        chunks = []
        for p in person["punches"]:
            if day is not None and p["day"] != day:
                chunks.append(flush_day(day, day_units))
                day_units = 0
            day = p["day"]
            day_units += p["units"]
            flags = []
            if p["manual"]:
                flags.append("manual add")
            if p["long"]:
                flags.append("long punch")
            if p["units"] == 0:
                flags.append("counts as 0.0")
            if p["brk"]:
                flags.append(f"break {p['brk']} min unpaid")
            if p["why"]:
                flags.append(", ".join(p["why"]))
            flag = (" · " + "; ".join(flags)) if flags else ""
            note = e(p["note"])
            chunks.append(
                "<tr>"
                f"<td>{e(slash(p['day']))}</td>"
                f"<td>{e(p['start'].strftime('%-I:%M %p'))}</td>"
                f"<td>{e(p['end'].strftime('%-I:%M %p'))}</td>"
                f"<td class='num'>{e(p['raw'])}</td>"
                f"<td class='num'>{p['units'] / 10:.1f}</td>"
                f"<td>{e(p['job'])}</td>"
                f"<td>{p['bucket']}{' · 26-GC' if p['gc26'] else ''}{e(flag)}</td>"
                f"<td class='note'>{note}</td>"
                "</tr>"
            )
        chunks.append(flush_day(day, day_units))
        return (
            "<table><thead><tr><th>Day</th><th>In</th><th>Out</th><th>Raw</th>"
            "<th>Tenth</th><th>Job</th><th>Bucket</th><th>Notes</th></tr></thead><tbody>"
            + "".join(chunks)
            + "</tbody></table>"
        )

    def money_bits(person):
        lines = []
        if person.get("salary"):
            lines.append(f"<p>{e(person['salary'])}</p>")
        if person["trips"]:
            items = "".join(
                f"<li>{e(slash(t['day']))}: {t['miles']:g} mi × {money(t['rate']) if t['rate'] else '—'} "
                f"= {money(t['amount'])} ({e(t['kind'])}; {e(t['evidence'])})</li>"
                for t in person["trips"]
            )
            lines.append(f"<p><b>Mileage {person['miles']:g} mi, {money(person['mileage'])}.</b></p><ul>{items}</ul>")
        if person["fuel"]:
            lines.append(f"<p>Fuel stipend {money(person['fuel'])} (direct-deposit bonus, not a mileage bill).</p>")
        if person["addon_lines"]:
            items = "".join(
                f"<li>{e(a['memo'])}: {a['hours']:g} × {money(a['rate'])} = {money(a['amount'])} ({a['company']})</li>"
                for a in person["addon_lines"]
            )
            lines.append(f"<p><b>Additional payments {money(person['addons'])}.</b> Not hours of this contractor.</p><ul>{items}</ul>")
        if person["misc_lines"]:
            items = "".join(
                f"<li>{e(m['memo'])}: {money(m['amount'])} ({m['company']})</li>" for m in person["misc_lines"]
            )
            lines.append(
                f"<p><b>Misc. deduction {money(person['misc'])}.</b> "
                f"Net after it: {money(person['net_after_misc'])}. The net due column above does not subtract this.</p><ul>{items}</ul>"
            )
        if person["missing_rate"]:
            lines.append("<p>No rate on file. Wages and net due are blank. Nothing was invented.</p>")
        elif person.get("weekly_hours") is not None and not person.get("salary"):
            status = person.get("assurity_status")
            status_bit = f" Assurity status on the live page: {e(status)}." if status else ""
            lines.append(
                "<p>Live site paid "
                f"WD {person['weekly_wd']:.1f} / GC {person['weekly_gc']:.1f} "
                f"(26-GC {person['weekly_gc26']:.1f}), wages {money(person['weekly_wages'])}, "
                f"payable {money(report_net(person))} after Assurity"
                f"{' and the misc. deduction' if person['misc'] else ''}.{status_bit} "
                f"{e(cause_for(person))}</p>"
            )
        return "".join(lines)

    details = []
    for person in people:
        st = status_of(person)
        details.append(
            f"<section class='person'><h2>{e(person['name'])} "
            f"<span class='{ 'match' if st=='Match' else 'diff' if st=='Differs' else 'warn'}'>{e(st)}</span></h2>"
            f"<p class='kicker'>WD {hrs(person['wd'])} · GC {hrs(person['gc'])} · 26-GC {hrs(person['gc26'])} "
            f"(inside GC, not added again) · rate {money(person['rate']) if person['rate'] is not None else '—'} · "
            f"wages {money(person['wages'])} · reimbursements {money(person['reimb'])} · "
            f"Assurity {money(person['assurity'])} · net due {money(person['net'])}</p>"
            f"{money_bits(person)}"
            f"{punch_table(person) if person['punches'] else '<p>No punches this week.</p>'}"
            "</section>"
        )

    job_body = []
    for job in jobs:
        job_body.append(
            "<tr>"
            f"<td>{e(job['job'])}</td>"
            f"<td>{e(job['bucket'])}</td>"
            f"<td class='num'>{job['punches']}</td>"
            f"<td class='num'>{job['hours']:.1f}</td>"
            f"<td class='num'>{job['gc26']:.1f}</td>"
            f"<td class='num'>{money(job['wages'])}</td>"
            f"<td>{e(job['contractors'])}</td>"
            "</tr>"
        )
    job_hours = sum(j["hours"] for j in jobs)
    job_wages = round(sum(j["wages"] for j in jobs), 2)

    diff_rows = []
    for p in people:
        if status_of(p) in ("Match", "Inactive"):
            continue
        live_wd = "—" if p.get("weekly_wd") is None else f"{p['weekly_wd']:.1f}"
        live_gc = "—" if p.get("weekly_gc") is None else f"{p['weekly_gc']:.1f}"
        live_26 = "—" if p.get("weekly_gc26") is None else f"{p['weekly_gc26']:.1f}"
        diff_rows.append(
            "<tr>"
            f"<td>{e(p['name'])}</td>"
            f"<td class='num'>{hrs(p['wd'])} / {hrs(p['gc'])}</td>"
            f"<td class='num'>{live_wd} / {live_gc}</td>"
            f"<td class='num'>{hrs(p['gc26'])} / {live_26}</td>"
            f"<td class='num'>{money(p['wages'])}</td>"
            f"<td class='num'>{money(p.get('weekly_wages'))}</td>"
            f"<td class='num'>{money(p['net_after_misc'])}</td>"
            f"<td class='num'>{money(report_net(p))}</td>"
            f"<td>{e(cause_for(p))}</td>"
            "</tr>"
        )

    # Per-job gaps against the live page's reconciled job lines.
    job_diff = []
    live_hours = {}
    live_path = Path("/tmp/ledger-work/live-week.json")
    if live_path.exists():
        for crew in json.loads(live_path.read_text())["crew"]:
            for line in crew.get("jobs") or []:
                live_hours[(crew["name"], line["job"])] = line["hours"]
    seen = set()
    have_live = bool(live_hours)
    for person in people:
        by_job = defaultdict(lambda: {"units": 0, "bucket": ""})
        for p in person["punches"]:
            rec = by_job[p["job"]]
            rec["units"] += p["units"]
            rec["bucket"] = p["bucket"]
        for job, rec in sorted(by_job.items()):
            band = rec["units"] / 10
            seen.add((person["name"], job))
            if not have_live:
                continue
            live_h = live_hours.get((person["name"], job))
            if live_h is None:
                cause = "Hand fix. This export label is not the job name on the live page."
                live_txt = "—"
                gap = "—"
            elif abs(band - live_h) < 1e-9:
                continue
            else:
                cause = (
                    "Rule difference. Live job hours are reconciled up to the weekly company total, "
                    "not the sum of punch tenths."
                )
                live_txt = f"{live_h:.1f}"
                gap = f"{live_h - band:+.1f}"
            job_diff.append(
                "<tr>"
                f"<td>{e(person['name'])}</td>"
                f"<td>{e(job)}</td>"
                f"<td>{e(rec['bucket'])}</td>"
                f"<td class='num'>{band:.1f}</td>"
                f"<td class='num'>{live_txt}</td>"
                f"<td class='num'>{gap}</td>"
                f"<td>{cause}</td>"
                "</tr>"
            )
    for (name, job), hours in sorted(live_hours.items()):
        if (name, job) in seen:
            continue
        job_diff.append(
            "<tr>"
            f"<td>{e(name)}</td><td>{e(job)}</td><td>GC</td>"
            f"<td class='num'>—</td><td class='num'>{hours:.1f}</td><td class='num'>—</td>"
            "<td>Hand fix. On the live page only. The export still has the Rich Henigan / Washington St. label for these same minutes.</td>"
            "</tr>"
        )

    skip_html = "".join(
        f"<li>{e(s['day'])} · {e(s['name'])} · {e(s['raw'])} · {e(s['job'])} · {e(s['reason'])}</li>"
        for s in skipped
    ) or "<li>None.</li>"

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Test review · pay week 11/2/2026–11/8/2026</title>
<style>
@page {{ size: letter; margin: 0.45in; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font: 11.5px/1.35 "Segoe UI", system-ui, sans-serif; color: #1c1917; }}
h1 {{ font-size: 18px; margin: 8px 0 2px; }}
h2 {{ font-size: 14px; margin: 16px 0 6px; color: #1f4e79; }}
h3 {{ font-size: 12.5px; margin: 12px 0 4px; }}
.banner {{ background: #7f1d1d; color: #fff; padding: 7px 10px; font-weight: 700; letter-spacing: .02em; }}
.sub {{ color: #444; margin: 0 0 8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0 10px; }}
th, td {{ border: 1px solid #d6d3d1; padding: 3px 5px; vertical-align: top; }}
th {{ background: #1f4e79; color: #fff; font-weight: 600; text-align: left; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tr.total td {{ background: #f5f5f4; font-weight: 700; }}
tr.day td {{ background: #fafaf9; font-weight: 600; }}
.match {{ color: #166534; font-weight: 700; }}
.diff {{ color: #9a3412; font-weight: 700; }}
.warn {{ color: #92400e; font-weight: 700; }}
.kicker {{ margin: 0 0 6px; }}
.note {{ color: #444; }}
ul {{ margin: 4px 0 8px 18px; padding: 0; }}
.cover {{ page-break-after: always; }}
.person {{ page-break-before: always; }}
.person:first-of-type {{ page-break-before: auto; }}
.keep {{ page-break-inside: avoid; }}
@media print {{
  a {{ color: inherit; text-decoration: none; }}
}}
</style></head><body>
<div class="banner">TEST ONLY · DO NOT MERGE · DO NOT DEPLOY · Week stops Sunday 11/8/2026. Nothing here changes the live site.</div>
<div class="cover">
<h1>Noble Field Ledger — test review, 11/2/2026 through 11/8/2026</h1>
<p class="sub">Noble General Contracting (GC) and Noble Windows &amp; Doors (WD). Kelly’s decision on 11/16/2026: from the 11/2/2026 week onward, paid hours are per-punch tenths. This branch prepares that change. Not a payment. Still do not merge.</p>
<p>Each punch is converted on its own leftover minutes (00–05 = .0, 06–11 = .1, 12–17 = .2, 18–23 = .3, 24–29 = .4, 30–35 = .5, 36–41 = .6, 42–47 = .7, 48–53 = .8, 54–59 = .9), then those tenths are added. Job totals and the week are not converted again, and job lines are not reconciled up to a weekly total. 26-GC hours sit inside GC. Net payable = wages + reimbursements − Assurity − a preserved misc. deduction. No income tax. Matthew Laush (Lausch) is $50.00 an hour with no Assurity.</p>
<p>Before is the live page today (weekly rounding). After is this branch (per-punch tenths, hand fixes kept). The 145 punches and their net minutes are the same. {len(differs)} contractors change. {e(', '.join(p['name'] for p in matches) or 'No one')} already matched, so their pay does not move. Kevin Hollenbeck stays $0 (inactive from 11/2/2026). {e(', '.join(p['name'] for p in no_rate) or 'No one')} still has no rate on file.</p>
{summary_table}
<p class="note">Reimbursements = mileage + fuel stipend + additional payments ({money(sum_reimb)}). Assurity {money(sum_assur)} is withheld once, and only where a premium is already on file. Cody McClary’s premium is terminated (10/23/2026) and Marco Garcia is ineligible (under 90 days), so nothing is withheld for them. Daniel Smalley’s $310.45 WD trim is kept and is inside net payable. After wages {money(sum_wages)}. Before wages {money(live_wages)}. After net payable {money(sum_after)}. Before net payable {money(live_net)}. All-contractor hours including unpaid: WD {sum_wd:.1f}, GC {sum_gc:.1f}, of which 26-GC {sum_26:.1f}.</p>
<p class="note">No other stored pay week was edited. The archive’s last week is 11/2/2026–11/8/2026, so there is no 11/9/2026–11/15/2026 week to leave alone. Weeks through 11/1/2026 stay on their current hours, including Marco Garcia’s weekly category conversion for those earlier weeks. An annual statement that includes 11/2/2026 will pick up this week’s new hours and wages. A 0:01 punch is 0.0 and is listed. Three 0:00 punches are skipped.</p>
</div>
<h2>Contractor detail</h2>
{''.join(details)}
<section class="person">
<h2>Projects</h2>
<p>Hours are the sum of each punch’s tenth. They are not converted again. 26-GC hours are the portion of that row already inside the GC (or, here, the job) total. Wages use each contractor’s own rate. Contractors with no rate add hours and no wages.</p>
<table>
<thead><tr><th>Project</th><th>Bucket</th><th>Punches</th><th>Hours</th><th>of which 26-GC</th><th>Wages</th><th>Contractors</th></tr></thead>
<tbody>
{''.join(job_body)}
<tr class="total"><td>Total</td><td></td><td class="num">{sum(j['punches'] for j in jobs)}</td><td class="num">{job_hours:.1f}</td><td class="num">{sum(j['gc26'] for j in jobs):.1f}</td><td class="num">{money(job_wages)}</td><td></td></tr>
</tbody></table>
</section>
<section class="person">
<h2>Before and after, 11/2/2026–11/8/2026</h2>
<p>Before is the live page today. After is this branch: per-punch tenths, with the hand fixes kept (Daniel’s trim, Angel’s additional payments, Rex’s Rita Heneghan job name, Assurity, Christian’s flat week, fuel stipends, mileage). Payable is wages + reimbursements − Assurity − the trim.</p>
<table>
<thead><tr><th>Contractor</th><th>After WD / GC</th><th>Before WD / GC</th><th>26-GC after / before</th><th>After wages</th><th>Before wages</th><th>After payable</th><th>Before payable</th><th>What changed</th></tr></thead>
<tbody>
{''.join(diff_rows)}
</tbody></table>
<h3>Job lines that differ from the live page</h3>
<p>Only jobs whose tenth-band sum differs from the live job line, plus the one renamed job. Jobs that already agree are omitted. Live job hours add up to the weekly company total. They are not a second conversion of the job.</p>
<table>
<thead><tr><th>Contractor</th><th>Job</th><th>Bucket</th><th>Sum of tenths</th><th>Live job hours</th><th>Gap</th><th>Likely cause</th></tr></thead>
<tbody>
{''.join(job_diff) or '<tr><td colspan="7">No job-level gap.</td></tr>'}
</tbody></table>
</section>
<section class="person">
<h2>What a rebuild would overwrite</h2>
<p>This branch changes the ledger directly. <code>build_archive.py</code> was not run. If that script were run later, it would put weekly rounding back and would drop the hand fixes below. It also rebuilds every earlier week in its source file.</p>
<ul>
<li><b>Paid hours.</b> It would replace per-punch tenth sums with round(all net minutes ÷ 60, 1), half up, and reconcile WD and GC to that week. That matches the live site’s paid hours. It would undo a hand edit that had put the tenth-band totals back onto a contractor.</li>
<li><b>Job lines.</b> It stores round(each punch ÷ 60, 1) on the punch, which does not add up to the weekly paid total. The live page reconciles job lines to the company subtotal. A rebuild would drop that reconciliation.</li>
<li><b>Angel Soto Moya’s additional payments.</b> Paulina cleaning $140.00 and Esias helper $682.50 are not in the script. A rebuild drops $822.50.</li>
<li><b>Daniel Smalley’s trim.</b> The $310.45 WD misc. deduction is not in the script. A rebuild drops it and raises his net.</li>
<li><b>Assurity.</b> The script applies premiums only from a contractor rates file that is not in this repo. A rebuild without that file drops every Assurity deduction (Andy $60.49, Angel $106.39, Daniel $60.39, Eric $48.31, Jacob $47.29, Kelly $72.45) and raises those nets. Cody McClary stays at $0 (terminated 10/23/2026). Marco Garcia stays at $0 (ineligible, under 90 days). Matthew stays at no Assurity either way.</li>
<li><b>Rex Butterfield, 11/6/2026.</b> Heywood 9:06 AM–12:31 PM (3.4 h) and Henigan 12:31 PM–1:56 PM (1.4 h) are in this week’s export and in the manual-adjustment log. The script does not read that log. If the full-year file it opens was never updated, those two punches disappear. Kelly’s 3.25 and 1.25 figures are clock time, not the tenths used for pay.</li>
<li><b>Kept, not undone.</b> WD-26 and Noble W&amp;D are already in the script, so Boardwalk stays WD and the Noble W&amp;D shop stays WD. Eric Havins stays all WD. Christian Ellis Blank stays flat 40 GC hours, $1,750.00. Fuel stipends stay: Andy $50.00, Cody $50.00, Jose $50.00 from this week on. Mileage comes out to the same 562 / 85 / 324 miles. Jacob Jones’s Sunday 11/8 Mosher punch is in this export, so a rebuild that uses this export keeps it.</li>
<li><b>Earlier weeks.</b> A rebuild rewrites all weeks in the source file, not just this one. Weeks before 11/2/2026 stay on per-punch tenths inside that script. This review does not touch them, and it does not touch 11/9/2026–11/15/2026.</li>
</ul>
</section>
<section class="person">
<h2>Rules checked, and rows set aside</h2>
<ul>
<li>Breaks are not paid. Net minutes match ClockShark Hours:Minutes on every row.</li>
<li>Counted {sum(len(p['punches']) for p in people)} punches. Skipped {len(skipped)} zero-duration punches. No Missing Clock In placeholder.</li>
<li>George Piekarski, 11/6/2026, East Valley Ophthalmology, raw 0:01, tenth 0.0, still listed.</li>
<li>26-KB (Mark Kampf) is GC. 26-GC is a subtotal of GC, not a third pile of hours.</li>
<li>WD is the job name or job number containing 26-WD, WD-26, or W/D, plus Noble W&amp;D / Noble Windows &amp; Doors, plus Eric Havins on every job. The literal W/D token does not match “W&amp;D”; the shop rule is the extra pattern.</li>
<li>No Out Of Bounds or GPSFence label in this week’s export. Those punches would be counted if they were here. Unapproved rows are counted.</li>
<li>No income tax.</li>
</ul>
<h3>Skipped</h3>
<ul>{skip_html}</ul>
<h3>No time this week, rate on file, not paid</h3>
<p class="note">Aceson Hale $20, Alfredo Fernandez $32, Brice Coleman $38, George Rosario $35, Juan Ramirez $40, Matthew Ehrnman $25, Tony Madalena $36, Vincent Garcia $33. Kevin Hollenbeck is inactive from 11/2/2026 and is not paid. Amber Sommer, Jose Nieto, Nicholas Sommer, and Ruth Nunez have no punches and no rate on the list.</p>
<p class="note">Long punches left in the hours, for review: Cody McClary 11/2/2026 23:00; George Piekarski 11/4/2026 18:07 and 11/5/2026 19:19. Matthew Laush’s 11/5/2026 note says he never clocked out that morning.</p>
</section>
</body></html>
"""
    path.write_text(page, encoding="utf-8")


ASSURITY_STATUS = {
    "Andy Gomez": "Weekly deduction",
    "Angel Soto Moya": "Weekly deduction",
    "Cody McClary": "Assurity terminated 10/23/2026",
    "Daniel Smalley": "Weekly deduction",
    "Eric Havins": "Weekly deduction",
    "Jacob Jones": "Weekly deduction",
    "Kelly Renner": "Weekly deduction",
    "Marco Garcia": "Ineligible (under 90 days)",
    "Matthew Laush": "No Assurity",
}


def main():
    root = Path("/home/ubuntu/.cursor/projects/workspace/uploads")
    punches, skipped, _adjustments = parse_rows(
        root / "clockshark-time-entries_76b3.csv",
        root / "manual-adjustments_4c13.jsonl",
    )
    trips = mileage_for(punches)
    people = rollup(punches, trips)
    for person in people:
        person["assurity_status"] = ASSURITY_STATUS.get(person["name"])
    people.append(
        {
            "name": "Kevin Hollenbeck",
            "punches": [],
            "wd": 0.0,
            "gc": 0.0,
            "gc26": 0.0,
            "hours": 0.0,
            "rate": 37.0,
            "wages": 0.0,
            "fuel": 0.0,
            "miles": 0.0,
            "mileage": 0.0,
            "addons": 0.0,
            "addon_lines": [],
            "reimb": 0.0,
            "assurity": 0.0,
            "misc": 0.0,
            "misc_lines": [],
            "net": 0.0,
            "net_after_misc": 0.0,
            "salary": None,
            "trips": [],
            "missing_rate": False,
            "weekly_hours": 0.0,
            "weekly_wd": 0.0,
            "weekly_gc": 0.0,
            "weekly_gc26": 0.0,
            "weekly_wages": 0.0,
            "inactive": "Inactive from 11/2/2026 on the live page. No punches and no pay. A rebuild keeps that.",
        }
    )
    people.sort(key=lambda p: p["name"])
    # Cross-check the weekly-method hours against the direct-deposit sheet.
    for person in people:
        expect = REPORT_HOURS.get(person["name"])
        if not expect or person.get("salary"):
            if person.get("salary"):
                assert (person["gc"], person["wd"]) == (40.0, 0.0)
            continue
        got = (round(person["weekly_gc"], 1), round(person["weekly_wd"], 1))
        if got != expect:
            raise SystemExit(f"Weekly hours != report for {person['name']}: {got} vs {expect}")
    matt = next(p for p in people if p["name"] == "Matthew Laush")
    assert abs(matt["hours"] - 54.4) < 1e-9, matt["hours"]
    assert abs(matt["weekly_hours"] - 55.2) < 1e-9
    assert abs(matt["mileage"] - 337.20) < 0.001
    nick = next(p for p in people if p["name"] == "Nicholas Sturgeon")
    assert abs(nick["mileage"] - 64.60) < 0.001
    marco = next(p for p in people if p["name"] == "Marco Garcia")
    assert abs(marco["miles"] - 324) < 0.001 and abs(marco["mileage"] - 213.60) < 0.001
    assert sum(1 for p in punches if p["name"] == "Jacob Jones" and p["day"] == dt.date(2026, 11, 8)) == 1
    assert not any(p["day"] > WEEK_END or p["day"] < WEEK for p in punches)
    jobs = job_rows(people)
    out = Path("/workspace/review")
    out.mkdir(exist_ok=True)
    write_csv(out / "week-2026-11-02.csv", people, jobs)
    write_html(out / "week-2026-11-02.html", people, jobs, skipped)
    print("people", len(people), "punches", len(punches), "skipped", len(skipped), "jobs", len(jobs))
    print("matches", [p["name"] for p in people if status_of(p) == "Match"])
    print("differs", [p["name"] for p in people if status_of(p) == "Differs"])


if __name__ == "__main__":
    main()

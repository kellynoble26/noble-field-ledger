#!/usr/bin/env python3
# 26-GC hours are GC hours. Do not show them as a separate total, column, or subtotal. They are already inside GC.
"""Test review for pay week 11/16/2026–11/22/2026.

Reads the ClockShark job-detail report (one row per punch) and checks it
against the weekly timesheet. Paid hours are per-punch tenths. Does not
write the live ledger.
"""

from __future__ import annotations

import csv
import html
import re
import subprocess
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

JOB_PDF = Path(
    "/home/ubuntu/.cursor/projects/agent-repos-noble-field-ledger/uploads/"
    "ClockShark_JobDetail_Rpt_1116-1122_2026_ran_112326_3534.pdf"
)
OUT = Path("/agent/repos/noble-field-ledger/review")

RATES = {
    "Andy Gomez": 28.0,
    "Angel Soto Moya": 42.0,
    "Brandon Hermaszewski": 35.0,
    "Brecken Bogle": 18.0,
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
    "Tony Madalena": 36.0,
    "George Rosario": 35.0,
    "Christian Ellis Blank": 43.75,
}
# Weekly premiums already on the 11/2 ledger. Not invented.
ASSURITY = {
    "Andy Gomez": 60.49,
    "Angel Soto Moya": 106.39,
    "Daniel Smalley": 60.39,
    "Eric Havins": 48.31,
    "Jacob Jones": 47.29,
    "Kelly Renner": 72.45,
}
ASSURITY_NOTE = {
    "Marco Garcia": "Ineligible (under 90 days). Nothing withheld.",
    "Matthew Laush": "No Assurity.",
}
# Jose Carranza's $50 was a one-time stipend for the distance of that project.
# It is not paid on this week. Andy Gomez's stipend stays.
FUEL = {"Andy Gomez": 50.0}
# This week only. A separate amount, not wages and not a reimbursement.
# George Piekarski has no rate, so his wages stay None and he does not receive it.
# Nicholas Sommer is not on this review.
THANKSGIVING_BONUS = 100.0
ALWAYS_WD = {"Eric Havins"}
WD_RE = re.compile(
    r"26\s*-\s*WD|WD\s*-\s*26|26\s*-\s*WS|W\s*/\s*D|Noble\s+W\s*&\s*D|Noble\s+Windows\s*&\s*Doors",
    re.I,
)
GC26_RE = re.compile(r"26\s*-\s*GC", re.I)
MILE_RE = re.compile(r"(?<![\d.])(\d{1,4}(?:\.\d+)?)\s*miles?\b", re.I)
# A bonus written on the contractor's own punch. Not the Thanksgiving bonus.
NOTE_BONUS_RE = re.compile(r"\$(\d+(?:\.\d{1,2})?)\s*bonus\b", re.I)
NOTE_BONUS_NAMES = {"Nicholas Sturgeon", "Joe Orona"}
PAIR_RE = re.compile(r"(?<![\d.])(\d{5,6})\s*[-–—]\s*(\d{5,6})(?!\d)")
SINGLE_RE = re.compile(r"(?<![\d.])(\d{5,6})(?!\d)")
CLOCK_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M")
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
DUR_RE = re.compile(r"\d{1,2}:\d{2}")

SHEET = {
    "Nicholas Sturgeon": "55:27",
    "George Piekarski": "73:45",
    "Jose Carranza": "35:26",
    "Angel Soto Moya": "48:49",
    "Jeremy Madalena": "41:16",
    "Tony Madalena": "38:06",
    "Daniel Smalley": "47:00",
    "Jacob Jones": "52:10",
    "Brandon Hermaszewski": "38:57",
    "Andy Gomez": "36:14",
    "Joe Orona": "50:57",
    "Brecken Bogle": "25:48",
    "Rex Butterfield": "39:52",
    "James Chick": "17:56",
    "Marco Garcia": "42:29",
    "Eric Havins": "42:42",
    "Kelly Renner": "46:50",
    "George Rosario": "35:57",
}


def money(n):
    if n is None:
        return "—"
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def q2(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def q1(x):
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def company_wages(wd_hours, gc_hours, rate, wages):
    """WD hours times the rate, and GC hours times the same rate.

    Combined wages stay as they are. The two company amounts add up to them.
    """
    if wages is None:
        return None, None
    wd_wages = q2(wd_hours * rate)
    gc_wages = q2(gc_hours * rate)
    drift = q2(wages - (wd_wages + gc_wages))
    if drift:
        if gc_hours and not wd_hours:
            gc_wages = q2(gc_wages + drift)
        elif wd_hours and not gc_hours:
            wd_wages = q2(wd_wages + drift)
        elif gc_hours >= wd_hours:
            gc_wages = q2(gc_wages + drift)
        else:
            wd_wages = q2(wd_wages + drift)
    if abs((wd_wages + gc_wages) - wages) > 0.001:
        raise SystemExit(f"company wages {wd_wages} + {gc_wages} != {wages}")
    return wd_wages, gc_wages


def hm(minutes):
    return minutes // 60 * 10 + (minutes % 60) // 6


def parse_hm(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m


def e(text):
    return html.escape("" if text is None else str(text))


def identify(blob):
    if "Jeremy" in blob:
        return "Jeremy Madalena"
    if "Tony" in blob and "Madalena" in blob:
        return "Tony Madalena"
    for key, name in (
        ("Soto", "Angel Soto Moya"),
        ("Hermasz", "Brandon Hermaszewski"),
        ("Sturgeon", "Nicholas Sturgeon"),
        ("Piekarski", "George Piekarski"),
        ("Carranza", "Jose Carranza"),
        ("Smalley", "Daniel Smalley"),
        ("Jones", "Jacob Jones"),
        ("Gomez", "Andy Gomez"),
        ("Orona", "Joe Orona"),
        ("Bogle", "Brecken Bogle"),
        ("Butterfield", "Rex Butterfield"),
        ("Chick", "James Chick"),
        ("Garcia", "Marco Garcia"),
        ("Havins", "Eric Havins"),
        ("Renner", "Kelly Renner"),
        ("Rosario", "George Rosario"),
        ("Madalena", "Jeremy Madalena"),
    ):
        if key in blob:
            return name
    return None


def is_punch_start(ln):
    s = ln.strip()
    if not s:
        return False
    if s.startswith(
        (
            "Notes:",
            "Daily Total",
            "Grand Total",
            "Employee",
            "Task Total",
            "Task ",
            "Total",
            "Powered",
            "Generated",
        )
    ):
        return False
    if s.startswith("Noble Kitchen"):
        return False
    return bool(CLOCK_RE.search(ln))


def load_punches():
    raw = subprocess.check_output(["pdftotext", "-layout", str(JOB_PDF), "-"], text=True)
    pages = raw.split("\f")
    punches = []
    current = ""
    for page in pages:
        lines = page.splitlines()
        header = None
        for j, ln in enumerate(lines):
            if ln.strip().startswith("Date") and "Employee" in ln and "Task" in ln:
                header = j
                break
        start = 0
        if header is not None:
            job = []
            for ln in lines[:header]:
                s = ln.strip()
                if not s or s == "Noble Kitchen & Bath" or s.startswith(
                    ("Generated", "Job Detail", "Date Range")
                ):
                    continue
                job.append(s)
            if job:
                current = re.sub(r"\s+", " ", " ".join(job)).strip(" -")
            start = header + 1
        k = start
        while k < len(lines):
            if not is_punch_start(lines[k]):
                k += 1
                continue
            block = [lines[k]]
            k += 1
            note = []
            while k < len(lines):
                s = lines[k].strip()
                if not s:
                    k += 1
                    continue
                if s.startswith("Notes:"):
                    note.append(s[6:].strip())
                    k += 1
                    while k < len(lines):
                        s2 = lines[k].strip()
                        if not s2:
                            k += 1
                            continue
                        if is_punch_start(lines[k]) or s2.startswith(
                            (
                                "Daily Total",
                                "Grand Total",
                                "Employee",
                                "Task",
                                "Powered",
                                "Generated",
                                "Noble Kitchen",
                            )
                        ):
                            break
                        note.append(s2)
                        k += 1
                    break
                if is_punch_start(lines[k]) or s.startswith(
                    (
                        "Daily Total",
                        "Grand Total",
                        "Employee",
                        "Task",
                        "Powered",
                        "Generated",
                        "Noble Kitchen",
                    )
                ):
                    break
                block.append(lines[k])
                k += 1
            joined = " ".join(x.strip() for x in block)
            first = block[0]
            clocks = CLOCK_RE.findall(first)
            tmp = CLOCK_RE.sub(" ", first)
            durs = DUR_RE.findall(tmp)
            mdate = DATE_RE.search(joined)
            job_name = re.sub(r"Gate Code:\s*#?\S+", "", current)
            job_name = re.sub(r"\s+", " ", job_name).strip(" -")
            minutes = parse_hm(durs[-1])
            who = identify(joined)
            note_s = re.sub(r"\s+", " ", " ".join(note)).strip()
            flags = []
            exclude = False
            if "no need to pay" in note_s.lower() or "paid me in cash" in note_s.lower():
                exclude = True
                flags.append("Not paid. Contractor wrote the homeowner paid cash.")
            # Kelly: the homeowner paid Jose cash for 11/20/2026. His ClockShark
            # note does not say cash, so every punch that day is still left out of pay.
            if who == "Jose Carranza" and (mdate.group(0) if mdate else "") == "11/20/2026":
                exclude = True
                if not any(flag.startswith("Not paid.") for flag in flags):
                    flags.append("Not paid. Homeowner paid cash.")
            if minutes == 1:
                flags.append("0:01 counts as 0.0 and stays on the list.")
            if minutes >= 14 * 60:
                flags.append("Long punch. Clock time is counted. See the note.")
            if NOTE_BONUS_RE.search(note_s) and who in NOTE_BONUS_NAMES:
                flags.append(
                    "Note bonus is included as its own line. Separate from the Thanksgiving bonus."
                )
            elif re.search(r"billable|bill one hour|bonus|\$35", note_s, re.I):
                flags.append("Note mentions pay or a customer price. Not added to the net.")
            wd = (
                who in ALWAYS_WD
                or bool(WD_RE.search(job_name))
                or bool(re.search(r"Shaw\s+Butte", job_name, re.I))
            )
            punches.append(
                {
                    "name": who,
                    "date": mdate.group(0) if mdate else "",
                    "in": clocks[0] if clocks else "",
                    "out": clocks[-1] if len(clocks) > 1 else "",
                    "break": durs[0] if len(durs) >= 2 else "0:00",
                    "raw": durs[-1],
                    "minutes": minutes,
                    "units": hm(minutes),
                    "job": job_name,
                    "bucket": "WD" if wd else "GC",
                    "gc26": (not wd) and bool(GC26_RE.search(job_name)),
                    "ws": bool(re.search(r"26\s*-\s*WS", job_name, re.I)),
                    "wd_word": (not wd) and bool(re.search(r"W\s*&\s*D", job_name)),
                    "note": note_s,
                    "flags": flags,
                    "exclude": exclude,
                }
            )
    return punches


def mileage(punches):
    trips = []
    # Nicholas: explicit miles on each note.
    for p in punches:
        if p["name"] != "Nicholas Sturgeon":
            continue
        for m in MILE_RE.finditer(p["note"]):
            miles = float(m.group(1))
            trips.append(
                {
                    "name": p["name"],
                    "date": p["date"],
                    "miles": miles,
                    "rate": 0.76,
                    "amount": q2(miles * 0.76),
                    "evidence": p["note"],
                }
            )
    # Marco: hyphen pairs, plus same-day single readings in mile/truck notes.
    by_day = defaultdict(list)
    for p in punches:
        if p["name"] == "Marco Garcia" and p["note"]:
            by_day[p["date"]].append(p)
    def clock_min(s):
        try:
            t, ap = s.split()
            h, m = map(int, t.split(":"))
            if ap == "PM" and h != 12:
                h += 12
            if ap == "AM" and h == 12:
                h = 0
            return h * 60 + m
        except ValueError:
            return 0

    for day, ps in sorted(by_day.items()):
        ps = sorted(ps, key=lambda p: clock_min(p["in"]))
        used = set()
        singles = []
        for p in ps:
            for m in PAIR_RE.finditer(p["note"]):
                a, b = int(m.group(1)), int(m.group(2))
                if 0 < b - a <= 1000:
                    rate = 0.60 if max(a, b) > 300000 else 0.76
                    trips.append(
                        {
                            "name": "Marco Garcia",
                            "date": day,
                            "miles": b - a,
                            "rate": rate,
                            "amount": q2((b - a) * rate),
                            "evidence": f"{a} → {b}",
                        }
                    )
                    used.add(a)
                    used.add(b)
            if re.search(r"mile|truck", p["note"], re.I):
                for m in SINGLE_RE.finditer(p["note"]):
                    v = int(m.group(1))
                    if v not in used:
                        singles.append(v)
        for a, b in zip(singles, singles[1:]):
            if 0 < b - a <= 1000:
                rate = 0.60 if max(a, b) > 300000 else 0.76
                trips.append(
                    {
                        "name": "Marco Garcia",
                        "date": day,
                        "miles": b - a,
                        "rate": rate,
                        "amount": q2((b - a) * rate),
                        "evidence": f"{a} → {b} (same-day readings)",
                    }
                )
            else:
                trips.append(
                    {
                        "name": "Marco Garcia",
                        "date": day,
                        "miles": None,
                        "rate": None,
                        "amount": None,
                        "evidence": f"Unpaired reading {a}",
                    }
                )
        if len(singles) % 2 == 1:
            trips.append(
                {
                    "name": "Marco Garcia",
                    "date": day,
                    "miles": None,
                    "rate": None,
                    "amount": None,
                    "evidence": f"Unpaired reading {singles[-1]}",
                }
            )
    return trips


def note_bonuses(punches):
    """Dollar amount each allowed contractor wrote as '$N bonus' on a punch.

    The amount comes from the note. It is not added to hours and it is not
    the Thanksgiving bonus. George Piekarski, Nicholas Sommer, Paulina, and
    Esias do not receive one.
    """
    found = defaultdict(list)
    for punch in punches:
        if punch["exclude"] or not punch["note"]:
            continue
        hits = NOTE_BONUS_RE.findall(punch["note"])
        if not hits:
            continue
        if punch["name"] not in NOTE_BONUS_NAMES:
            raise SystemExit(
                "A note bonus was found for "
                f"{punch['name']}, who does not receive one: {punch['note']}"
            )
        for raw in hits:
            found[punch["name"]].append(
                {
                    "amount": q2(raw),
                    "date": punch["date"],
                    "in": punch["in"],
                    "out": punch["out"],
                    "job": punch["job"],
                    "note": punch["note"],
                }
            )
    if set(found) != NOTE_BONUS_NAMES:
        raise SystemExit(
            f"Expected note bonuses for {sorted(NOTE_BONUS_NAMES)}, found {sorted(found)}"
        )
    amounts = {}
    for name, items in found.items():
        if len(items) != 1:
            raise SystemExit(f"{name} has {len(items)} note-bonus punches")
        amounts[name] = items[0]["amount"]
    return amounts, found


def kelly_missed_clockin(punches):
    """Friday 11/20/2026 for contractor Kelly Renner. She forgot to clock in.

    13.0 is already a tenth. There is no clock-in, no clock-out, and no call to hm().
    """
    name = "Kelly Renner"
    hers = [p for p in punches if p["name"] == name]
    paid = [p for p in hers if not p["exclude"]]
    if len(hers) != 4 or len(paid) != 4:
        raise SystemExit(f"Kelly Renner punch count changed: {len(hers)} paid {len(paid)}")
    if any(p["date"] == "11/20/2026" for p in hers):
        raise SystemExit("Kelly Renner already has a Friday 11/20/2026 punch")
    dates = {p["date"] for p in paid}
    if dates != {"11/16/2026", "11/17/2026", "11/18/2026", "11/19/2026"}:
        raise SystemExit(f"Kelly Renner paid days changed: {sorted(dates)}")
    buckets = {p["bucket"] for p in paid}
    if buckets != {"GC"}:
        raise SystemExit(f"Kelly Renner paid companies changed: {buckets}")
    jobs = {p["job"] for p in paid}
    if len(jobs) == 1:
        job = next(iter(jobs))
        why = (
            f"Every paid punch this week is {job}, and every one is GC. "
            "Those days are 11/16/2026, 11/17/2026, 11/18/2026, and 11/19/2026. "
            "Thursday 11/19/2026 is that same job. There is no Friday punch and no other job, "
            "so Friday 11/20/2026 stays on that GC job."
        )
    else:
        job = "Forgot to clock in"
        why = (
            "Paid punches this week are GC on more than one job, and Friday is not one of them. "
            "The 13.0 hours stay GC and the job is labeled Forgot to clock in."
        )
    hours = 13.0
    rate = RATES[name]
    wages = q2(Decimal("13.0") * Decimal(str(rate)))
    if hours != 13.0 or wages != 377.0 or rate != 29.0:
        raise SystemExit(f"Kelly manual line changed: {hours} × {rate} = {wages}")
    return {
        "name": name,
        "date": "11/20/2026",
        "hours": hours,
        "bucket": "GC",
        "job": job,
        "label": "Friday 11/20/2026, forgot to clock in, 13.0 hours",
        "why": why,
        "wages": wages,
        "rate": rate,
    }


def manual_copy(line, person):
    """Same words on her summary line, her section, and the page notes."""
    clock = float(Decimal(str(person["gc"])) - Decimal(str(line["hours"])))
    return (
        f"{line['label']}. "
        f"Job: {line['job']}. {line['bucket']}. "
        f"{line['why']} "
        "This line has no clock-in and no clock-out. "
        "13.0 is already a tenth. "
        f"Wages {money(line['wages'])} at {money(line['rate'])}. "
        f"ClockShark tenths stay {clock:.1f}. "
        f"GC hours are {person['gc']:.1f}. "
        f"GC wages are {money(person['gc_wages'])}. "
        f"Net is {money(person['net'])}. "
        f"Assurity stays {money(person['assurity'])}. "
        f"Thanksgiving bonus stays {money(person['bonus'])}. "
        "No note bonus and no fuel stipend."
    )


def rollup(punches, trips, note_bonus_amounts, note_bonus_evidence, manual_line):
    by = defaultdict(list)
    for p in punches:
        by[p["name"]].append(p)
    # George Piekarski and Nicholas Sommer are on the timesheet and have no rate.
    # Their hours are not converted and they are not in the pay totals.
    skip_pay = {"George Piekarski"}
    on_sheet = (set(SHEET) | {"Christian Ellis Blank", "Matthew Laush"}) - {"Nicholas Sommer"}
    people = []
    for name in sorted(on_sheet):
        ps = by.get(name, [])
        if name == "Christian Ellis Blank":
            wd_wages, gc_wages = company_wages(0.0, 40.0, 43.75, 1750.0)
            people.append(
                {
                    "name": name,
                    "punches": [],
                    "wd": 0.0,
                    "gc": 40.0,
                    "gc26": 0.0,
                    "hours": 40.0,
                    "raw": "flat 40:00",
                    "rate": 43.75,
                    "wd_wages": wd_wages,
                    "gc_wages": gc_wages,
                    "wages": 1750.0,
                    "fuel": 0.0,
                    "mileage": 0.0,
                    "miles": 0.0,
                    "reimb": 0.0,
                    "bonus": THANKSGIVING_BONUS,
                    "note_bonus": 0.0,
                    "note_bonus_evidence": [],
                    "assurity": 0.0,
                    "net": q2(1750.0 + THANKSGIVING_BONUS),
                    "trips": [],
                    "missing_rate": False,
                    "manual_line": None,
                    "note": "Flat 40 hours, all GC. No punches on this timesheet. Standing salary rule.",
                }
            )
            continue
        paid = [p for p in ps if not p["exclude"]]
        wd_u = sum(p["units"] for p in paid if p["bucket"] == "WD")
        gc_u = sum(p["units"] for p in paid if p["bucket"] == "GC")
        g26_u = sum(p["units"] for p in paid if p["gc26"])
        raw_m = sum(p["minutes"] for p in ps)
        rate = None if name in skip_pay else RATES.get(name)
        wd_hours = None if name in skip_pay else wd_u / 10
        gc_hours = None if name in skip_pay else gc_u / 10
        hours = None if name in skip_pay else (wd_u + gc_u) / 10
        line = manual_line if manual_line and manual_line["name"] == name else None
        if line is not None:
            if name in skip_pay or rate is None:
                raise SystemExit(f"{name} has a manual line and no rate")
            if line["bucket"] != "GC" or line["hours"] != 13.0:
                raise SystemExit(f"manual line must stay 13.0 GC hours: {line}")
            # 13.0 is already a tenth. Add that tenth. Do not call hm().
            gc_hours = float(Decimal(str(gc_hours)) + Decimal("13.0"))
            hours = float(Decimal(str(hours)) + Decimal("13.0"))
        wages = None if name in skip_pay or rate is None else q2(hours * rate)
        wd_wages, gc_wages = company_wages(wd_hours, gc_hours, rate, wages)
        own = [t for t in trips if t["name"] == name]
        miles = sum(t["miles"] or 0 for t in own)
        mileage_amt = q2(sum(t["amount"] or 0 for t in own))
        fuel = FUEL.get(name, 0.0) if ps else 0.0
        reimb = q2(mileage_amt + fuel)
        ass = ASSURITY.get(name, 0.0) if wages else 0.0
        # Wages of None stay uncalculated and get no bonus. A wage of 0.00
        # (Matthew Laush) is calculated and does receive the Thanksgiving bonus.
        # Note bonus is separate: $0.00 unless this contractor's punch asks for one.
        bonus = None if wages is None else THANKSGIVING_BONUS
        note_bonus = None if wages is None else note_bonus_amounts.get(name, 0.0)
        evidence = [] if wages is None else note_bonus_evidence.get(name, [])
        net = (
            q2(wages + reimb + bonus + note_bonus - ass)
            if wages is not None
            else None
        )
        note = ASSURITY_NOTE.get(name, "")
        if name == "Matthew Laush" and not ps:
            note = "On the timesheet at 0:00. No punches."
        if name in skip_pay:
            note = "No rate on file. Hours are not converted and pay is not calculated."
        people.append(
            {
                "name": name,
                "punches": ps,
                "wd": wd_hours,
                "gc": gc_hours,
                "gc26": None if name in skip_pay else g26_u / 10,
                "hours": hours,
                "raw": f"{raw_m // 60}:{raw_m % 60:02d}",
                "rate": rate,
                "wd_wages": wd_wages,
                "gc_wages": gc_wages,
                "wages": wages,
                "fuel": fuel,
                "mileage": mileage_amt,
                "miles": miles,
                "reimb": reimb,
                "bonus": bonus,
                "note_bonus": note_bonus,
                "note_bonus_evidence": evidence,
                "assurity": ass,
                "net": net,
                "trips": own,
                "missing_rate": name in skip_pay,
                "manual_line": line,
                "note": note,
            }
        )
    if manual_line and not any(p.get("manual_line") for p in people):
        raise SystemExit(f"manual line for {manual_line['name']} was not applied")
    return people


def additional_pay(punches):
    """Two lines Kelly added for this week. Not ClockShark wages.

    Paulina uses the 4:34 span on Angel Soto Moya's 11/19/2026 note, converted
    once with the per-punch tenths rule. Esias uses 20.7 hours already
    converted. Neither amount is added to Angel's hours or net.
    """
    paulina = None
    esias_units = 0
    for punch in punches:
        if punch["name"] != "Angel Soto Moya" or punch["exclude"]:
            continue
        if re.search(r"paulina", punch["note"], re.I) and punch["raw"] == "4:34":
            if paulina is not None:
                raise SystemExit("More than one Paulina 4:34 punch")
            paulina = punch
        if re.search(r"esias", punch["note"], re.I):
            esias_units += punch["units"]
    if paulina is None:
        raise SystemExit("Paulina 4:34 note was not found")
    hours = hm(paulina["minutes"]) / 10
    if hours != 4.5:
        raise SystemExit(f"4:34 converted to {hours}, expected 4.5")
    if esias_units != 207:
        raise SystemExit(f"Esias same-time tenths are {esias_units / 10}, expected 20.7")
    paulina_wages = q2(Decimal("4.5") * Decimal("35"))
    esias_wages = q2(Decimal("20.7") * Decimal("15"))
    if paulina_wages != 157.5 or esias_wages != 310.5:
        raise SystemExit(f"additional wages {paulina_wages} {esias_wages}")

    def line(name, raw, raw_display, line_hours, rate, wages, note):
        return {
            "name": name,
            "punches": [],
            "wd": None,
            "gc": None,
            "gc26": None,
            "hours": line_hours,
            "raw": raw,
            "raw_display": raw_display,
            "rate": rate,
            "wd_wages": None,
            "gc_wages": None,
            "wages": wages,
            "fuel": 0.0,
            "mileage": 0.0,
            "miles": 0.0,
            "reimb": 0.0,
            "bonus": None,
            "note_bonus": None,
            "note_bonus_evidence": [],
            "assurity": 0.0,
            "net": wages,
            "trips": [],
            "missing_rate": False,
            "additional": True,
            "bucket": "additional",
            "note": note,
        }

    paulina_note = (
        "Additional line. Not ClockShark wages and not in Angel Soto Moya’s net. "
        f"Angel’s {paulina['date']} punch, {paulina['in']}–{paulina['out']}, is raw 4:34. "
        "The per-punch tenths rule converts 4:34 to 4.5. "
        f"The note says: {paulina['note']}. "
        "Rate $35.00. Wages $157.50. Net $157.50. "
        "Bucket additional, not WD and not GC. "
        "No Assurity, no fuel, no mileage, no Thanksgiving bonus, and no note bonus."
    )
    esias_note = (
        "Additional line. Not ClockShark wages and not in Angel Soto Moya’s net. "
        "Hours are 20.7, already converted, and are not converted again. "
        "Rate $15.00. Wages $310.50. Net $310.50. "
        "Bucket additional, not WD and not GC. "
        "No Assurity, no fuel, no mileage, no Thanksgiving bonus, and no note bonus. "
        "Angel Soto Moya’s punches that say Esias was there the same time stay on his timesheet. "
        "These hours are not added to his WD, GC, wages, or net."
    )
    return [
        line(
            "Exceptional Cleaning Services/Paulina Moya",
            "4:34",
            "4:34 → 4.5",
            4.5,
            35.0,
            paulina_wages,
            paulina_note,
        ),
        line(
            "Angel Soto - Helper Esias",
            "20.7",
            "20.7",
            20.7,
            15.0,
            esias_wages,
            esias_note,
        ),
    ]


def main():
    punches = load_punches()
    # Every named punch must reconcile to the timesheet minute total.
    tot = defaultdict(int)
    for p in punches:
        if not p["name"] or not p["raw"]:
            raise SystemExit(f"Unparsed punch {p}")
        tot[p["name"]] += p["minutes"]
    for name, sm in SHEET.items():
        if tot[name] != parse_hm(sm):
            raise SystemExit(f"{name} {tot[name]} != sheet {sm}")
    note_bonus_amounts, note_bonus_evidence = note_bonuses(punches)
    manual = kelly_missed_clockin(punches)
    trips = mileage(punches)
    people = rollup(punches, trips, note_bonus_amounts, note_bonus_evidence, manual)
    extras = additional_pay(punches)
    merged = []
    found_angel = False
    for person in people:
        merged.append(person)
        if person["name"] == "Angel Soto Moya":
            found_angel = True
            pointer = (
                "Exceptional Cleaning Services/Paulina Moya "
                f"({money(extras[0]['wages'])}) and Angel Soto - Helper Esias "
                f"({money(extras[1]['wages'])}) are separate lines immediately below. "
                "Those amounts are not in this net, so they are not paid twice."
            )
            person["note"] = f"{person['note']} {pointer}".strip() if person["note"] else pointer
            merged.extend(extras)
    if not found_angel:
        raise SystemExit("Angel Soto Moya is not on the summary")
    people = merged
    clock = [p for p in people if p["wages"] is not None and not p.get("additional")]
    additional = [p for p in people if p.get("additional")]
    sum_wages = q2(sum(p["wages"] for p in clock))
    sum_reimb = q2(sum(p["reimb"] for p in clock))
    sum_bonus = q2(sum(p["bonus"] or 0 for p in clock))
    sum_note = q2(sum(p["note_bonus"] or 0 for p in clock))
    sum_assur = q2(sum(p["assurity"] for p in clock))
    sum_additional = q2(sum(p["net"] for p in additional))
    sum_clock_net = q2(sum(p["net"] for p in clock))
    sum_net = q2(sum_clock_net + sum_additional)
    recipients = [p["name"] for p in clock if p["bonus"]]
    if len(recipients) != 19 or any(p["bonus"] for p in additional):
        raise SystemExit(f"Thanksgiving bonus recipients changed: {recipients}")
    angel = next(p for p in people if p["name"] == "Angel Soto Moya")
    if angel["wages"] != 2037.0 or angel["hours"] != 48.5 or abs(angel["net"] - 2030.61) > 0.001:
        raise SystemExit(f"Angel’s own pay changed: {angel['hours']} {angel['wages']} {angel['net']}")
    if angel.get("note_bonus"):
        raise SystemExit("Angel Soto Moya was given a note bonus")
    for p in clock:
        expect = q2(p["wages"] + p["reimb"] + p["bonus"] + p["note_bonus"] - p["assurity"])
        if abs(p["net"] - expect) > 0.001:
            raise SystemExit(f"{p['name']} net {p['net']} != {expect}")
    blocked = {
        "George Piekarski",
        "Exceptional Cleaning Services/Paulina Moya",
        "Angel Soto - Helper Esias",
    }
    for p in people:
        if p["name"] in blocked and p.get("note_bonus"):
            raise SystemExit(f"{p['name']} was given a note bonus")
        if p["name"] not in NOTE_BONUS_NAMES and p.get("note_bonus"):
            raise SystemExit(f"Unexpected note bonus for {p['name']}: {p['note_bonus']}")
    for name, amount in note_bonus_amounts.items():
        person = next(p for p in people if p["name"] == name)
        if person["note_bonus"] != amount or person["bonus"] != THANKSGIVING_BONUS:
            raise SystemExit(
                f"{name} Thanksgiving {person['bonus']} note bonus {person['note_bonus']}"
            )
    if (
        abs(sum_wages - 24504.80) > 0.001
        or abs(sum_reimb - 582.76) > 0.001
        or abs(sum_bonus - 1900.00) > 0.001
        or abs(sum_additional - 468.00) > 0.001
        or abs(sum_assur - 395.32) > 0.001
    ):
        raise SystemExit(
            f"totals wages {sum_wages} reimb {sum_reimb} bonus {sum_bonus} "
            f"additional {sum_additional} assurity {sum_assur}"
        )
    prior_net = q2(sum_wages + sum_reimb + sum_bonus + sum_additional - sum_assur)
    if abs(prior_net - 27060.24) > 0.001:
        raise SystemExit(f"base net moved {prior_net}")
    if abs(sum_net - q2(prior_net + sum_note)) > 0.001:
        raise SystemExit(f"net {sum_net} != {prior_net} + note bonus {sum_note}")
    if abs(sum_net - 27310.24) > 0.001:
        raise SystemExit(f"net payable changed {sum_net}")
    for p in clock:
        if p["gc26"] - p["gc"] > 0.001:
            raise SystemExit(f"{p['name']} 26-GC {p['gc26']} is outside GC {p['gc']}")
    sum_wd_hours = q1(sum((Decimal(str(p["wd"])) for p in clock), Decimal("0")))
    sum_gc_hours = q1(sum((Decimal(str(p["gc"])) for p in clock), Decimal("0")))
    sum_gc26_hours = q1(sum((Decimal(str(p["gc26"])) for p in clock), Decimal("0")))
    sum_wd_wages = q2(sum(p["wd_wages"] for p in clock))
    sum_gc_wages = q2(sum(p["gc_wages"] for p in clock))
    if abs((sum_wd_wages + sum_gc_wages) - sum_wages) > 0.001:
        raise SystemExit(
            f"company wages {sum_wd_wages} + {sum_gc_wages} != wages {sum_wages}"
        )
    if sum_gc26_hours - sum_gc_hours > 0.001:
        raise SystemExit(f"26-GC {sum_gc26_hours} is outside GC {sum_gc_hours}")
    if (
        abs(sum_wd_hours - 215.2) > 0.001
        or abs(sum_wd_wages - 7596.50) > 0.001
        or abs(sum_gc_hours - 517.9) > 0.001
        or abs(sum_gc_wages - 16908.30) > 0.001
        or abs(sum_gc26_hours - 341.2) > 0.001
    ):
        raise SystemExit(
            f"company hours WD {sum_wd_hours} {sum_wd_wages} "
            f"GC {sum_gc_hours} {sum_gc_wages} 26-GC {sum_gc26_hours}"
        )
    george = next(p for p in people if p["name"] == "George Piekarski")
    if george["wages"] is not None or george["wd"] is not None or george.get("wd_wages"):
        raise SystemExit("George Piekarski is in a company total")
    for p in additional:
        if p["wd"] is not None or p.get("wd_wages") or p.get("gc_wages"):
            raise SystemExit(f"{p['name']} was assigned to WD or GC")
    nicholas = next(p for p in people if p["name"] == "Nicholas Sturgeon")
    joe = next(p for p in people if p["name"] == "Joe Orona")
    if nicholas["note_bonus"] != 150 or abs(nicholas["net"] - 2467.36) > 0.001:
        raise SystemExit(f"Nicholas note bonus changed {nicholas['note_bonus']} {nicholas['net']}")
    if joe["note_bonus"] != 100 or abs(joe["net"] - 1822.40) > 0.001:
        raise SystemExit(f"Joe note bonus changed {joe['note_bonus']} {joe['net']}")
    jose = next(p for p in people if p["name"] == "Jose Carranza")
    jose_cash = [punch for punch in jose["punches"] if punch["date"] == "11/20/2026"]
    if (
        len(jose_cash) != 1
        or jose_cash[0]["raw"] != "6:55"
        or jose_cash[0]["units"] != 69
        or not jose_cash[0]["exclude"]
        or jose_cash[0]["bucket"] != "GC"
        or not jose_cash[0]["gc26"]
    ):
        raise SystemExit(f"Jose 11/20 cash punch changed {jose_cash}")
    if any(punch["exclude"] for punch in jose["punches"] if punch["date"] != "11/20/2026"):
        raise SystemExit("A Jose punch other than 11/20 was left out of pay")
    if (
        jose["wd"] != 0.0
        or abs(jose["gc"] - 28.3) > 0.001
        or abs(jose["gc26"] - 28.3) > 0.001
        or abs(jose["wages"] - 707.50) > 0.001
        or jose["fuel"] != 0.0
        or jose["reimb"] != 0.0
        or jose["bonus"] != THANKSGIVING_BONUS
        or jose["note_bonus"] != 0.0
        or abs(jose["net"] - 807.50) > 0.001
    ):
        raise SystemExit(
            f"Jose pay changed WD {jose['wd']} GC {jose['gc']} wages {jose['wages']} net {jose['net']}"
        )
    andy = next(p for p in people if p["name"] == "Andy Gomez")
    if andy["fuel"] != 50.0 or abs(andy["reimb"] - 50.0) > 0.001:
        raise SystemExit(f"Andy Gomez fuel changed {andy['fuel']} {andy['reimb']}")
    kelly = next(p for p in people if p["name"] == "Kelly Renner")
    kelly_paid = [punch for punch in kelly["punches"] if not punch["exclude"]]
    kelly_clock = sum(punch["units"] for punch in kelly_paid) / 10
    if (
        kelly["raw"] != "46:50"
        or len(kelly["punches"]) != 4
        or len(kelly_paid) != 4
        or any(punch["date"] == "11/20/2026" for punch in kelly["punches"])
        or any(punch["bucket"] != "GC" for punch in kelly_paid)
        or abs(kelly_clock - 46.7) > 0.001
        or kelly["rate"] != 29.0
        or kelly["wd"] != 0.0
        or abs(kelly["wd_wages"] - 0.0) > 0.001
        or abs(kelly["gc"] - 59.7) > 0.001
        or abs(kelly["gc26"] - 0.0) > 0.001
        or abs(kelly["gc_wages"] - 1731.30) > 0.001
        or abs(kelly["wages"] - 1731.30) > 0.001
        or kelly["fuel"] != 0.0
        or kelly["reimb"] != 0.0
        or kelly["bonus"] != THANKSGIVING_BONUS
        or kelly["note_bonus"] != 0.0
        or abs(kelly["assurity"] - 72.45) > 0.001
        or abs(kelly["net"] - 1758.85) > 0.001
        or kelly.get("manual_line") != manual
        or "in" in manual
        or "out" in manual
        or manual["hours"] != 13.0
        or manual["wages"] != 377.0
        or manual["label"] != "Friday 11/20/2026, forgot to clock in, 13.0 hours"
    ):
        raise SystemExit(
            f"Kelly manual line mismatch GC {kelly['gc']} wages {kelly['wages']} "
            f"assurity {kelly['assurity']} net {kelly['net']} line {kelly.get('manual_line')}"
        )
    print(
        f"punches {len(punches)} wages {sum_wages:.2f} reimb {sum_reimb:.2f} "
        f"bonus {sum_bonus:.2f} ({len(recipients)}) note {sum_note:.2f} "
        f"additional {sum_additional:.2f} "
        f"assurity {sum_assur:.2f} clock net {sum_clock_net:.2f} net {sum_net:.2f}"
    )
    print(
        f"WD hours {sum_wd_hours:.1f} wages {sum_wd_wages:.2f} "
        f"GC hours {sum_gc_hours:.1f} wages {sum_gc_wages:.2f}"
    )
    print(f"MANUAL {manual_copy(manual, kelly)}")
    print("bonus recipients:", ", ".join(recipients))
    for name in ("Nicholas Sturgeon", "Joe Orona"):
        item = note_bonus_evidence[name][0]
        print(f"NOTE BONUS {name} {item['amount']:.2f} {item['date']} {item['note']}")
    for p in people:
        if p["raw"] != "0:00" or p["wages"] or p["reimb"] or p["bonus"] or p.get("note_bonus"):
            wd = "—" if p["wd"] is None else f"{p['wd']:5.1f}"
            gc = "—" if p["gc"] is None else f"{p['gc']:5.1f}"
            bonus_s = "—" if p["bonus"] is None else f"{p['bonus']:.2f}"
            note_s = "—" if p["note_bonus"] is None else f"{p['note_bonus']:.2f}"
            wd_pay = "—" if p.get("wd_wages") is None else f"{p['wd_wages']:.2f}"
            gc_pay = "—" if p.get("gc_wages") is None else f"{p['gc_wages']:.2f}"
            print(
                f"{p['name']:24} raw {p['raw']:>7} WD {wd} ${wd_pay} GC {gc} ${gc_pay} "
                f"wages {p['wages']} reimb {p['reimb']:.2f} bonus {bonus_s} "
                f"note {note_s} ass {p['assurity']:.2f} net {p['net']}"
            )
    for t in trips:
        print("TRIP", t["name"], t["date"], t["miles"], t["amount"], t["evidence"][:80])

    # Write CSV and HTML.
    OUT.mkdir(exist_ok=True)
    csv_path = OUT / "week-2026-11-16.csv"
    csv_columns = [
        "section",
        "contractor",
        "day",
        "in",
        "out",
        "raw",
        "tenth",
        "job",
        "bucket",
        "wd",
        "wd_wages",
        "gc",
        "gc_wages",
        "rate",
        "wages",
        "mileage",
        "fuel",
        "reimbursements",
        "thanksgiving_bonus",
        "note_bonus",
        "assurity",
        "net_payable",
        "notes",
    ]

    def csv_row(values):
        extra = set(values) - set(csv_columns)
        if extra:
            raise SystemExit(f"unknown csv fields {sorted(extra)}")
        return [values.get(column, "") for column in csv_columns]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(csv_columns)
        for p in people:
            evidence = p.get("note_bonus_evidence") or []
            summary_notes = p["note"]
            if evidence:
                item = evidence[0]
                extra = (
                    f"Note bonus {p['note_bonus']:.2f} is included in the net as its own line. "
                    "Separate from the Thanksgiving bonus. "
                    f"{item['date']} {item['in']}–{item['out']}. {item['note']}"
                )
                summary_notes = f"{summary_notes} {extra}".strip() if summary_notes else extra
            if p.get("manual_line"):
                extra = manual_copy(p["manual_line"], p)
                summary_notes = f"{summary_notes} {extra}".strip() if summary_notes else extra
            w.writerow(
                [
                    "summary",
                    p["name"],
                    "",
                    "",
                    "",
                    p["raw"],
                    "" if p["hours"] is None else f"{p['hours']:.1f}",
                    "",
                    p.get("bucket", ""),
                    "" if p["wd"] is None else f"{p['wd']:.1f}",
                    "" if p.get("wd_wages") is None else f"{p['wd_wages']:.2f}",
                    "" if p["gc"] is None else f"{p['gc']:.1f}",
                    "" if p.get("gc_wages") is None else f"{p['gc_wages']:.2f}",
                    "" if p["rate"] is None else f"{p['rate']:.2f}",
                    "" if p["wages"] is None else f"{p['wages']:.2f}",
                    f"{p['mileage']:.2f}",
                    f"{p['fuel']:.2f}",
                    f"{p['reimb']:.2f}",
                    "" if p["bonus"] is None else f"{p['bonus']:.2f}",
                    "" if p["note_bonus"] is None else f"{p['note_bonus']:.2f}",
                    f"{p['assurity']:.2f}",
                    "" if p["net"] is None else f"{p['net']:.2f}",
                    summary_notes,
                ]
            )
            if p.get("manual_line"):
                line = p["manual_line"]
                w.writerow(
                    [
                        "manual",
                        p["name"],
                        line["date"],
                        "",
                        "",
                        "",
                        f"{line['hours']:.1f}",
                        line["job"],
                        line["bucket"],
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        manual_copy(line, p)
                        + " Included in the summary row above. Do not add this line again.",
                    ]
                )
        company_note = (
            "Company wages only. Thanksgiving bonus, note bonus, reimbursements, "
            "and Assurity are week totals and are not included. "
            "George Piekarski is not included. Paulina and Esias stay additional. "
            "Contractor Kelly Renner’s manual line, Friday 11/20/2026, forgot to clock in, "
            "13.0 hours, is included in GC."
        )
        w.writerow(
            [
                "company",
                "WD",
                "",
                "",
                "",
                "",
                f"{sum_wd_hours:.1f}",
                "",
                "WD",
                f"{sum_wd_hours:.1f}",
                f"{sum_wd_wages:.2f}",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                company_note,
            ]
        )
        w.writerow(
            [
                "company",
                "GC",
                "",
                "",
                "",
                "",
                f"{sum_gc_hours:.1f}",
                "",
                "GC",
                "",
                "",
                f"{sum_gc_hours:.1f}",
                f"{sum_gc_wages:.2f}",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                company_note,
            ]
        )
        for p in people:
            for punch in p["punches"]:
                w.writerow(
                    [
                        "punch",
                        p["name"],
                        punch["date"],
                        punch["in"],
                        punch["out"],
                        punch["raw"],
                        f"{punch['units'] / 10:.1f}",
                        punch["job"],
                        punch["bucket"],
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "excluded" if punch["exclude"] else "",
                        " ".join(punch["flags"] + ([punch["note"]] if punch["note"] else [])),
                    ]
                )

    rows = []
    for p in people:
        tag = " <span class='tag'>Additional line</span>" if p.get("additional") else ""
        row_class = " class='additional'" if p.get("additional") else ""
        rows.append(
            f"<tr{row_class}>"
            f"<td>{e(p['name'])}{tag}</td>"
            f"<td class='num'>{e(p.get('raw_display') or p['raw'])}</td>"
            f"<td class='num'>{'—' if p['wd'] is None else format(p['wd'], '.1f')}</td>"
            f"<td class='num'>{money(p.get('wd_wages'))}</td>"
            f"<td class='num'>{'—' if p['gc'] is None else format(p['gc'], '.1f')}</td>"
            f"<td class='num'>{money(p.get('gc_wages'))}</td>"
            f"<td class='num'>{money(p['rate']) if p['rate'] is not None else '—'}</td>"
            f"<td class='num'>{money(p['wages'])}</td>"
            f"<td class='num'>{money(p['reimb'])}</td>"
            f"<td class='num'>{money(p['bonus'])}</td>"
            f"<td class='num'>{money(p['note_bonus'])}</td>"
            f"<td class='num'>{money(p['assurity'])}</td>"
            f"<td class='num'>{money(p['net'])}</td>"
            "</tr>"
        )
        if p.get("manual_line"):
            rows.append(
                "<tr class='manual'>"
                f"<td>{e(p['name'])} <span class='tag'>Manual line</span></td>"
                f"<td colspan='12'>{e(manual_copy(p['manual_line'], p))} "
                "Included in the row above.</td>"
                "</tr>"
            )
    days = [f"11/{d}/2026" for d in range(16, 23)]
    day_labels = ["Mon 11/16", "Tue 11/17", "Wed 11/18", "Thu 11/19", "Fri 11/20", "Sat 11/21", "Sun 11/22"]

    def fmt_hm(minutes):
        if not minutes:
            return "—"
        return f"{minutes // 60}:{minutes % 60:02d}"

    details = []
    for p in people:
        if p.get("additional"):
            details.append(
                f"<section class='person'><h2>{e(p['name'])}</h2>"
                f"<p>{e(p['note'])}</p>"
                f"<p>Shown on the pay summary as its own row. "
                f"Raw {e(p.get('raw_display') or p['raw'])} · rate {money(p['rate'])} · "
                f"wages {money(p['wages'])} · net {money(p['net'])}.</p>"
                "</section>"
            )
            continue
        if not p["punches"] and not p["note"]:
            continue
        bits = []
        if p["note"]:
            bits.append(f"<p>{e(p['note'])}</p>")
        if p.get("note_bonus_evidence"):
            items = "".join(
                "<li>"
                f"<b>{e(item['date'])}</b> {e(item['in'])}–{e(item['out'])}. {e(item['note'])}"
                "</li>"
                for item in p["note_bonus_evidence"]
            )
            bits.append(
                f"<p><b>Note bonus {money(p['note_bonus'])}.</b> "
                "Included in the net as its own line. Separate from the Thanksgiving bonus.</p>"
                f"<ul>{items}</ul>"
            )
        if p["trips"]:
            items = "".join(
                f"<li>{e(t['date'])}: {t['miles'] if t['miles'] is not None else '—'} mi, {money(t['amount'])}. {e(t['evidence'])}</li>"
                for t in p["trips"]
            )
            bits.append(f"<p><b>Mileage {money(p['mileage'])}.</b></p><ul>{items}</ul>")
        if p["fuel"]:
            bits.append(f"<p>Fuel stipend {money(p['fuel'])}.</p>")
        bits.append(f"<p>Thanksgiving bonus {money(p['bonus'])}.</p>")
        if not p.get("note_bonus_evidence"):
            bits.append(f"<p>Note bonus {money(p['note_bonus'])}.</p>")
        if p.get("manual_line"):
            bits.append(
                "<p><b>Manual line.</b> "
                f"{e(manual_copy(p['manual_line'], p))}</p>"
            )
        # Timesheet shape: one row per job, day columns are the raw hours on the timesheet.
        # Notes from the job-detail punches sit under that row.
        order = []
        grouped = defaultdict(list)
        for punch in p["punches"]:
            if punch["job"] not in grouped:
                order.append(punch["job"])
            grouped[punch["job"]].append(punch)
        sheet_rows = []
        for job in order:
            plist = grouped[job]
            by_day = defaultdict(int)
            tenths = 0
            for punch in plist:
                by_day[punch["date"]] += punch["minutes"]
                if not punch["exclude"]:
                    tenths += punch["units"]
            bucket = plist[0]["bucket"]
            cells = "".join(f"<td class='num'>{fmt_hm(by_day[d])}</td>" for d in days)
            raw_total = sum(by_day.values())
            tenth_cell = "—" if p["missing_rate"] else f"{tenths / 10:.1f}"
            sheet_rows.append(
                "<tr>"
                f"<td>{e(job)} <span class='tag'>{bucket}</span></td>"
                f"{cells}<td class='num'>{fmt_hm(raw_total)}</td><td class='num'>{tenth_cell}</td></tr>"
            )
            for punch in plist:
                note = punch["note"] or "No note on this punch."
                mark = " Not paid." if punch["exclude"] else ""
                if punch["exclude"] and not punch["note"]:
                    note = " ".join(punch["flags"]) or "Not paid."
                    mark = ""
                converted = "" if p["missing_rate"] else f" → {punch['units'] / 10:.1f}"
                sheet_rows.append(
                    "<tr class='punch-note'><td colspan='10'>"
                    f"<b>{e(punch['date'])}</b> {e(punch['in'])}–{e(punch['out'])} "
                    f"raw {e(punch['raw'])}{converted}{e(mark)}. {e(note)}"
                    "</td></tr>"
                )
        if p.get("manual_line"):
            line = p["manual_line"]
            cells = "".join(
                "<td class='num'>13.0</td>" if day == line["date"] else "<td class='num'>—</td>"
                for day in days
            )
            sheet_rows.append(
                "<tr class='manual'>"
                f"<td>{e(line['job'])} <span class='tag'>Manual line</span> "
                f"<span class='tag'>{e(line['bucket'])}</span></td>"
                f"{cells}<td class='num'>13.0</td><td class='num'>13.0</td></tr>"
            )
            sheet_rows.append(
                "<tr class='manual'><td colspan='10'>"
                f"{e(manual_copy(line, p))}"
                "</td></tr>"
            )
        head = "".join(f"<th>{lab}</th>" for lab in day_labels)
        table = (
            "<table class='sheet'><thead><tr><th>Job on the timesheet</th>"
            + head
            + "<th>Raw total</th><th>Tenths</th></tr></thead><tbody>"
            + "".join(sheet_rows)
            + "</tbody></table>"
            if sheet_rows
            else "<p>No punches on the timesheet.</p>"
        )
        details.append(
            f"<section class='person'><h2>{e(p['name'])}</h2>"
            f"<p>Timesheet hours, with the job-detail note on each punch. "
            + (
                "Pay is not calculated."
                if p["missing_rate"]
                else (
                    f"WD {p['wd']:.1f} · WD wages {money(p['wd_wages'])} · "
                    f"GC {p['gc']:.1f} · GC wages {money(p['gc_wages'])} · "
                    f"rate {money(p['rate']) if p['rate'] is not None else '—'} · wages {money(p['wages'])} · "
                    f"reimbursements {money(p['reimb'])} · Thanksgiving bonus {money(p['bonus'])} · "
                    f"Note bonus {money(p['note_bonus'])} · "
                    f"Assurity {money(p['assurity'])} · net {money(p['net'])}"
                    + (
                        f". Includes a manual line: {p['manual_line']['label']}."
                        if p.get("manual_line")
                        else ""
                    )
                )
            )
            + "</p>"
            + "".join(bits)
            + table
            + "</section>"
        )
    jobs = defaultdict(lambda: {"hours": 0.0, "u": 0, "punches": 0, "wages": 0.0, "names": set()})
    for p in people:
        if p["missing_rate"]:
            continue
        for punch in p["punches"]:
            if punch["exclude"]:
                continue
            key = (punch["job"], punch["bucket"])
            rec = jobs[key]
            rec["u"] += punch["units"]
            rec["punches"] += 1
            rec["names"].add(p["name"])
            if p["rate"] is not None:
                rec["wages"] += punch["units"] / 10 * p["rate"]
        line = p.get("manual_line")
        if not line:
            continue
        key = (line["job"], line["bucket"])
        rec = jobs[key]
        # 13.0 is already a tenth. 130 is that tenth in the same units as a punch.
        rec["u"] += int(Decimal(str(line["hours"])) * 10)
        rec["manual_hours"] = line["hours"]
        rec["names"].add(p["name"])
        if p["rate"] is not None:
            rec["wages"] += line["hours"] * p["rate"]
    job_rows = []
    for (job, bucket), rec in sorted(jobs.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        job_name = job
        if rec.get("manual_hours"):
            job_name = f"{job} — includes manual line {rec['manual_hours']:.1f} hours"
        job_rows.append(
            "<tr>"
            f"<td>{e(job_name)}</td><td>{e(bucket)}</td><td class='num'>{rec['punches']}</td>"
            f"<td class='num'>{rec['u'] / 10:.1f}</td>"
            f"<td class='num'>{money(q2(rec['wages']))}</td>"
            f"<td>{e(', '.join(sorted(rec['names'])))}</td></tr>"
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Test review · pay week 11/16/2026–11/22/2026</title>
<style>
@page {{ size: letter; margin: 0.45in; }}
body {{ margin: 0; font: 11.5px/1.35 "Segoe UI", system-ui, sans-serif; color: #1c1917; }}
h1 {{ font-size: 18px; margin: 8px 0 2px; }}
h2 {{ font-size: 14px; margin: 16px 0 6px; color: #1f4e79; }}
.banner {{ background: #7f1d1d; color: #fff; padding: 7px 10px; font-weight: 700; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0 10px; }}
th, td {{ border: 1px solid #d6d3d1; padding: 3px 5px; vertical-align: top; }}
th {{ background: #1f4e79; color: #fff; text-align: left; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tr.total td {{ background: #f5f5f4; font-weight: 700; }}
tr.additional td {{ background: #f8fafc; }}
tr.manual td {{ background: #fff7ed; }}
tr.punch-note td {{ background: #fafaf9; color: #444; }}
.tag {{ color: #1f4e79; font-size: 10px; }}
.note {{ color: #444; }}
.person {{ page-break-before: always; }}
ul {{ margin: 4px 0 8px 18px; }}
</style></head><body>
<div class="banner">TEST ONLY · DO NOT MERGE · DO NOT DEPLOY · This week is not on the live site.</div>
<h1>Noble Field Ledger — test review, 11/16/2026 through 11/22/2026</h1>
<p>ClockShark job-detail report and timesheet, both run 11/23/2026 at 8:43–8:48 AM Arizona time. Noble General Contracting (GC) and Noble Windows &amp; Doors (WD). Not a payment.</p>
<p>Each contractor section is the timesheet: job, then the hours for 11/16 through 11/22. Under each job are the notes from the job-detail punches that make those hours. Day columns stay in hours and minutes, the same way the timesheet prints them. Pay uses each punch’s own tenth, then adds those tenths. Every contractor’s raw week total matches the timesheet. Breaks are already out of the ClockShark total. Net payable = ClockShark wages + reimbursements + Thanksgiving bonus + note bonus + additional lines − Assurity. No income tax. ClockShark wages are the timesheet wages plus the manual line for contractor Kelly Renner: Friday 11/20/2026, forgot to clock in, 13.0 hours. Her ClockShark punches stay as they are. 13.0 is already a tenth. The note bonus and the two additional lines are not in that wages total. They are added into net payable.</p>
<p>Thanksgiving bonus is {money(THANKSGIVING_BONUS)} for each contractor on this week except George Piekarski and Nicholas Sommer. It is not wages and not a reimbursement. Exceptional Cleaning Services/Paulina Moya and Angel Soto - Helper Esias do not receive it.</p>
<p>Note bonus is the amount a contractor wrote on a punch, paid as its own line. It is not the Thanksgiving bonus and it is not wages. Nicholas Sturgeon {money(note_bonus_amounts['Nicholas Sturgeon'])} from his {e(note_bonus_evidence['Nicholas Sturgeon'][0]['date'])} note. Joe Orona {money(note_bonus_amounts['Joe Orona'])} from his {e(note_bonus_evidence['Joe Orona'][0]['date'])} note. Every other calculated contractor is $0.00. George Piekarski, Nicholas Sommer, Exceptional Cleaning Services/Paulina Moya, and Angel Soto - Helper Esias do not receive a note bonus.</p>
<p>Additional lines, each on its own row after Angel Soto Moya, bucket additional, not WD and not GC. Exceptional Cleaning Services/Paulina Moya: raw 4:34 converted to 4.5 at $35.00 = {money(extras[0]['wages'])}. Angel Soto - Helper Esias: 20.7 hours, already converted, at $15.00 = {money(extras[1]['wages'])}. No Assurity, no fuel, no mileage, no Thanksgiving bonus, and no note bonus. Neither amount is in Angel Soto Moya’s hours, wages, or net.</p>
<p><b>Wages {money(sum_wages)}. Reimbursements {money(sum_reimb)}. Thanksgiving bonus {money(sum_bonus)}. Note bonus {money(sum_note)}. Additional lines {money(sum_additional)}. Assurity {money(sum_assur)}. Net payable {money(sum_net)}.</b> Net payable = {money(sum_wages)} + {money(sum_reimb)} + {money(sum_bonus)} + {money(sum_note)} + {money(extras[0]['wages'])} + {money(extras[1]['wages'])} − {money(sum_assur)} = {money(sum_net)}.</p>
<p><b>WD total {sum_wd_hours:.1f} hours, {money(sum_wd_wages)}. GC total {sum_gc_hours:.1f} hours, {money(sum_gc_wages)}.</b> Thanksgiving bonus, note bonus, reimbursements, and Assurity stay week totals and are not company wages.</p>
<table>
<thead><tr><th>Contractor</th><th>Raw</th><th>WD hours</th><th>WD wages</th><th>GC hours</th><th>GC wages</th><th>Rate</th><th>Wages</th><th>Reimbursements</th><th>Thanksgiving bonus</th><th>Note bonus</th><th>Assurity</th><th>Net payable</th></tr></thead>
<tbody>
{''.join(rows)}
<tr class="total"><td>Week total</td><td></td><td class="num">{sum_wd_hours:.1f}</td><td class="num">{money(sum_wd_wages)}</td><td class="num">{sum_gc_hours:.1f}</td><td class="num">{money(sum_gc_wages)}</td><td></td><td class="num">{money(sum_wages)}</td><td class="num">{money(sum_reimb)}</td><td class="num">{money(sum_bonus)}</td><td class="num">{money(sum_note)}</td><td class="num">{money(sum_assur)}</td><td class="num">{money(sum_net)}</td></tr>
</tbody></table>
<table>
<thead><tr><th>Company</th><th>Hours</th><th>Wages</th></tr></thead>
<tbody>
<tr><td>WD · Noble Windows &amp; Doors</td><td class="num">{sum_wd_hours:.1f}</td><td class="num">{money(sum_wd_wages)}</td></tr>
<tr><td>GC · Noble General Contracting</td><td class="num">{sum_gc_hours:.1f}</td><td class="num">{money(sum_gc_wages)}</td></tr>
<tr class="total"><td>ClockShark wages</td><td></td><td class="num">{money(sum_wages)}</td></tr>
</tbody></table>
<h2>Read these before the detail</h2>
<ul>
<li>George Piekarski and Nicholas Sommer are not calculated. George’s 73:45 stays on his timesheet with the job-detail notes, including the 20:12 punch, the 0:23 punch, the 23:00 punch, and the 11:27 overnight punch. Those hours are in neither the WD total nor the GC total. Nicholas Sommer has no punches and is not on this review.</li>
<li>WD wages are WD hours times that contractor’s rate. GC wages are GC hours times the same rate. Combined wages equal WD wages plus GC wages.</li>
<li>A customer price in a note ($85 an hour, a $275 trip) is not a pay rate.</li>
<li>Brecken Bogle’s 3:54 on Tony Arnold is left out of pay. He wrote that the homeowner paid cash. Jose Carranza’s 6:55 on 11/20/2026 on Tony Arnold is left out of pay. Kelly said the homeowner paid cash. Jose Carranza’s $50 fuel stipend was a one-time stipend for the distance of that project and is not paid this week. Andy Gomez’s $50 fuel stipend stays.</li>
<li>Notes that ask for a bonus are included as their own lines, separate from the Thanksgiving bonus: Nicholas Sturgeon {money(note_bonus_amounts['Nicholas Sturgeon'])}, Joe Orona {money(note_bonus_amounts['Joe Orona'])}. Those amounts are in each contractor’s net. Angel Soto Moya’s 11/19/2026 note on the 4:34 cleaning punch says Paulina $35hr. That pay is the additional line for Exceptional Cleaning Services/Paulina Moya, raw 4:34 converted to 4.5 at $35.00, $157.50. Angel Soto - Helper Esias is 20.7 hours at $15.00, $310.50. Both are separate lines and are not in Angel’s net.</li>
<li>26-WS on Andrew Kelley is WD. It is an active Windows &amp; Doors project. Shaw Butte is WD.</li>
<li>Eric Havins is all WD, including Noble Office. Noble W&amp;D shop time is WD. 26-KB (Mark Kampf) stays GC.</li>
<li>Contractor Kelly Renner forgot to clock in on Friday 11/20/2026. {e(manual_copy(manual, kelly))}</li>
<li>Christian Ellis Blank has no punches. The standing rule is still flat 40 GC hours, $1,750.00. Matthew Laush is on the timesheet at 0:00.</li>
<li>Tony Madalena ($36) and George Rosario ($35) have rates on file. No Assurity premium for them appears on the 11/2 week record, so nothing is withheld. Kelly can confirm.</li>
<li>Weeks 11/2/2026–11/8/2026 and 11/9/2026–11/15/2026 were not changed. This week is not written into the live ledger.</li>
</ul>
{''.join(details)}
<section class="person"><h2>Projects</h2>
<p>Hours are the sum of each paid punch’s tenth. The cash Tony Arnold punches are not in this table. The manual line for contractor Kelly Renner, Friday 11/20/2026, forgot to clock in, 13.0 hours, is included on {e(manual['job'])}. That line has no clock-in and no clock-out.</p>
<table><thead><tr><th>Project</th><th>Bucket</th><th>Punches</th><th>Hours</th><th>Wages</th><th>Contractors</th></tr></thead>
<tbody>{''.join(job_rows)}</tbody></table>
</section>
</body></html>
"""
    (OUT / "week-2026-11-16.html").write_text(page, encoding="utf-8")
    print("wrote", csv_path)


if __name__ == "__main__":
    main()

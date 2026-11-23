#!/usr/bin/env python3
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
FUEL = {"Andy Gomez": 50.0, "Jose Carranza": 50.0}
ALWAYS_WD = {"Eric Havins"}
WD_RE = re.compile(
    r"26\s*-\s*WD|WD\s*-\s*26|26\s*-\s*WS|W\s*/\s*D|Noble\s+W\s*&\s*D|Noble\s+Windows\s*&\s*Doors",
    re.I,
)
GC26_RE = re.compile(r"26\s*-\s*GC", re.I)
MILE_RE = re.compile(r"(?<![\d.])(\d{1,4}(?:\.\d+)?)\s*miles?\b", re.I)
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
            if minutes == 1:
                flags.append("0:01 counts as 0.0 and stays on the list.")
            if minutes >= 14 * 60:
                flags.append("Long punch. Clock time is counted. See the note.")
            if re.search(r"billable|bill one hour|bonus|\$35", note_s, re.I):
                flags.append("Note mentions pay or a customer price. Not added to the net.")
            wd = who in ALWAYS_WD or bool(WD_RE.search(job_name))
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


def rollup(punches, trips):
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
                    "wages": 1750.0,
                    "fuel": 0.0,
                    "mileage": 0.0,
                    "miles": 0.0,
                    "reimb": 0.0,
                    "assurity": 0.0,
                    "net": 1750.0,
                    "trips": [],
                    "missing_rate": False,
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
        hours = None if name in skip_pay else (wd_u + gc_u) / 10
        wages = None if name in skip_pay or rate is None else q2(hours * rate)
        own = [t for t in trips if t["name"] == name]
        miles = sum(t["miles"] or 0 for t in own)
        mileage_amt = q2(sum(t["amount"] or 0 for t in own))
        fuel = FUEL.get(name, 0.0) if ps else 0.0
        reimb = q2(mileage_amt + fuel)
        ass = ASSURITY.get(name, 0.0) if wages else 0.0
        net = q2(wages + reimb - ass) if wages is not None else None
        note = ASSURITY_NOTE.get(name, "")
        if name == "Matthew Laush" and not ps:
            note = "On the timesheet at 0:00. No punches."
        if name in skip_pay:
            note = "No rate on file. Hours are not converted and pay is not calculated."
        people.append(
            {
                "name": name,
                "punches": ps,
                "wd": None if name in skip_pay else wd_u / 10,
                "gc": None if name in skip_pay else gc_u / 10,
                "gc26": None if name in skip_pay else g26_u / 10,
                "hours": hours,
                "raw": f"{raw_m // 60}:{raw_m % 60:02d}",
                "rate": rate,
                "wages": wages,
                "fuel": fuel,
                "mileage": mileage_amt,
                "miles": miles,
                "reimb": reimb,
                "assurity": ass,
                "net": net,
                "trips": own,
                "missing_rate": name in skip_pay,
                "note": note,
            }
        )
    return people


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
    trips = mileage(punches)
    people = rollup(punches, trips)
    paid = [p for p in people if p["wages"] is not None]
    sum_wages = q2(sum(p["wages"] for p in paid))
    sum_reimb = q2(sum(p["reimb"] for p in paid))
    sum_assur = q2(sum(p["assurity"] for p in paid))
    sum_net = q2(sum(p["net"] for p in paid))
    print(f"punches {len(punches)} wages {sum_wages:.2f} reimb {sum_reimb:.2f} assurity {sum_assur:.2f} net {sum_net:.2f}")
    for p in people:
        if p["raw"] != "0:00" or p["wages"] or p["reimb"]:
            wd = "—" if p["wd"] is None else f"{p['wd']:5.1f}"
            gc = "—" if p["gc"] is None else f"{p['gc']:5.1f}"
            g26 = "—" if p["gc26"] is None else f"{p['gc26']:5.1f}"
            print(
                f"{p['name']:24} raw {p['raw']:>7} WD {wd} GC {gc} 26 {g26} "
                f"wages {p['wages']} reimb {p['reimb']:.2f} ass {p['assurity']:.2f} net {p['net']}"
            )
    for t in trips:
        print("TRIP", t["name"], t["date"], t["miles"], t["amount"], t["evidence"][:80])

    # Write CSV and HTML.
    OUT.mkdir(exist_ok=True)
    csv_path = OUT / "week-2026-11-16.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "section",
                "contractor",
                "day",
                "in",
                "out",
                "raw",
                "tenth",
                "job",
                "bucket",
                "is_26gc",
                "wd",
                "gc",
                "gc26",
                "rate",
                "wages",
                "mileage",
                "fuel",
                "reimbursements",
                "assurity",
                "net_payable",
                "notes",
            ]
        )
        for p in people:
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
                    "",
                    "",
                    "" if p["wd"] is None else f"{p['wd']:.1f}",
                    "" if p["gc"] is None else f"{p['gc']:.1f}",
                    "" if p["gc26"] is None else f"{p['gc26']:.1f}",
                    "" if p["rate"] is None else f"{p['rate']:.2f}",
                    "" if p["wages"] is None else f"{p['wages']:.2f}",
                    f"{p['mileage']:.2f}",
                    f"{p['fuel']:.2f}",
                    f"{p['reimb']:.2f}",
                    f"{p['assurity']:.2f}",
                    "" if p["net"] is None else f"{p['net']:.2f}",
                    p["note"],
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
                        "yes" if punch["gc26"] else "",
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
        rows.append(
            "<tr>"
            f"<td>{e(p['name'])}</td>"
            f"<td class='num'>{p['raw']}</td>"
            f"<td class='num'>{'—' if p['wd'] is None else format(p['wd'], '.1f')}</td>"
            f"<td class='num'>{'—' if p['gc'] is None else format(p['gc'], '.1f')}</td>"
            f"<td class='num'>{'—' if p['gc26'] is None else format(p['gc26'], '.1f')}</td>"
            f"<td class='num'>{money(p['rate']) if p['rate'] is not None else '—'}</td>"
            f"<td class='num'>{money(p['wages'])}</td>"
            f"<td class='num'>{money(p['reimb'])}</td>"
            f"<td class='num'>{money(p['assurity'])}</td>"
            f"<td class='num'>{money(p['net'])}</td>"
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
        if not p["punches"] and not p["note"]:
            continue
        bits = []
        if p["note"]:
            bits.append(f"<p>{e(p['note'])}</p>")
        if p["trips"]:
            items = "".join(
                f"<li>{e(t['date'])}: {t['miles'] if t['miles'] is not None else '—'} mi, {money(t['amount'])}. {e(t['evidence'])}</li>"
                for t in p["trips"]
            )
            bits.append(f"<p><b>Mileage {money(p['mileage'])}.</b></p><ul>{items}</ul>")
        if p["fuel"]:
            bits.append(f"<p>Fuel stipend {money(p['fuel'])}.</p>")
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
            gc26 = any(x["gc26"] for x in plist)
            cells = "".join(f"<td class='num'>{fmt_hm(by_day[d])}</td>" for d in days)
            raw_total = sum(by_day.values())
            tenth_cell = "—" if p["missing_rate"] else f"{tenths / 10:.1f}"
            sheet_rows.append(
                "<tr>"
                f"<td>{e(job)} <span class='tag'>{bucket}{' · 26-GC' if gc26 else ''}</span></td>"
                f"{cells}<td class='num'>{fmt_hm(raw_total)}</td><td class='num'>{tenth_cell}</td></tr>"
            )
            for punch in plist:
                note = punch["note"] or "No note on this punch."
                mark = " Not paid." if punch["exclude"] else ""
                converted = "" if p["missing_rate"] else f" → {punch['units'] / 10:.1f}"
                sheet_rows.append(
                    "<tr class='punch-note'><td colspan='10'>"
                    f"<b>{e(punch['date'])}</b> {e(punch['in'])}–{e(punch['out'])} "
                    f"raw {e(punch['raw'])}{converted}{e(mark)}. {e(note)}"
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
                    f"WD {p['wd']:.1f} · GC {p['gc']:.1f} · 26-GC {p['gc26']:.1f} · "
                    f"rate {money(p['rate']) if p['rate'] is not None else '—'} · wages {money(p['wages'])} · "
                    f"reimbursements {money(p['reimb'])} · Assurity {money(p['assurity'])} · net {money(p['net'])}"
                )
            )
            + "</p>"
            + "".join(bits)
            + table
            + "</section>"
        )
    jobs = defaultdict(lambda: {"hours": 0.0, "u": 0, "punches": 0, "wages": 0.0, "names": set(), "gc26": 0})
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
            if punch["gc26"]:
                rec["gc26"] += punch["units"]
            if p["rate"] is not None:
                rec["wages"] += punch["units"] / 10 * p["rate"]
    job_rows = []
    for (job, bucket), rec in sorted(jobs.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        job_rows.append(
            "<tr>"
            f"<td>{e(job)}</td><td>{e(bucket)}</td><td class='num'>{rec['punches']}</td>"
            f"<td class='num'>{rec['u'] / 10:.1f}</td><td class='num'>{rec['gc26'] / 10:.1f}</td>"
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
tr.punch-note td {{ background: #fafaf9; color: #444; }}
.tag {{ color: #1f4e79; font-size: 10px; }}
.note {{ color: #444; }}
.person {{ page-break-before: always; }}
ul {{ margin: 4px 0 8px 18px; }}
</style></head><body>
<div class="banner">TEST ONLY · DO NOT MERGE · DO NOT DEPLOY · This week is not on the live site.</div>
<h1>Noble Field Ledger — test review, 11/16/2026 through 11/22/2026</h1>
<p>ClockShark job-detail report and timesheet, both run 11/23/2026 at 8:43–8:48 AM Arizona time. Noble General Contracting (GC) and Noble Windows &amp; Doors (WD). Not a payment.</p>
<p>Each contractor section is the timesheet: job, then the hours for 11/16 through 11/22. Under each job are the notes from the job-detail punches that make those hours. Day columns stay in hours and minutes, the same way the timesheet prints them. Pay uses each punch’s own tenth, then adds those tenths. Every contractor’s raw week total matches the timesheet. Breaks are already out of the ClockShark total. 26-GC sits inside GC. Net payable = wages + reimbursements − Assurity. No income tax.</p>
<p><b>Wages {money(sum_wages)}. Reimbursements {money(sum_reimb)}. Assurity {money(sum_assur)}. Net payable {money(sum_net)}.</b></p>
<table>
<thead><tr><th>Contractor</th><th>Raw</th><th>WD</th><th>GC</th><th>26-GC</th><th>Rate</th><th>Wages</th><th>Reimbursements</th><th>Assurity</th><th>Net payable</th></tr></thead>
<tbody>
{''.join(rows)}
<tr class="total"><td>Paid total</td><td></td><td></td><td></td><td></td><td></td><td class="num">{money(sum_wages)}</td><td class="num">{money(sum_reimb)}</td><td class="num">{money(sum_assur)}</td><td class="num">{money(sum_net)}</td></tr>
</tbody></table>
<h2>Read these before the detail</h2>
<ul>
<li>George Piekarski and Nicholas Sommer are not calculated. George’s 73:45 stays on his timesheet with the job-detail notes, including the 20:12 punch, the 0:23 punch, the 23:00 punch, and the 11:27 overnight punch. Nicholas Sommer has no punches.</li>
<li>A customer price in a note ($85 an hour, a $275 trip) is not a pay rate.</li>
<li>Brecken Bogle’s 3:54 on Tony Arnold is left out of pay. He wrote that the homeowner paid cash.</li>
<li>Notes that ask for a bonus are not in the net: Nicholas Sturgeon $150, Joe Orona $100. Angel Soto Moya’s note says “Paulina $35hr” on a 4:34 cleaning punch and does not give hours. Nothing was added for that.</li>
<li>26-WS on Andrew Kelley is WD. It is an active Windows &amp; Doors project. Shaw Butte says “W&amp;D” and does not carry a 26-WD, 26-WS, or W/D tag, so it stays GC.</li>
<li>Eric Havins is all WD, including Noble Office. Noble W&amp;D shop time is WD. 26-KB (Mark Kampf) is GC.</li>
<li>Christian Ellis Blank has no punches. The standing rule is still flat 40 GC hours, $1,750.00. Matthew Laush is on the timesheet at 0:00.</li>
<li>Tony Madalena ($36) and George Rosario ($35) have rates on file. No Assurity premium for them appears on the 11/2 week record, so nothing is withheld. Kelly can confirm.</li>
<li>Weeks 11/2/2026–11/8/2026 and 11/9/2026–11/15/2026 were not changed. This week is not written into the live ledger.</li>
</ul>
{''.join(details)}
<section class="person"><h2>Projects</h2>
<p>Hours are the sum of each paid punch’s tenth. The cash Tony Arnold punch is not in this table.</p>
<table><thead><tr><th>Project</th><th>Bucket</th><th>Punches</th><th>Hours</th><th>of which 26-GC</th><th>Wages</th><th>Contractors</th></tr></thead>
<tbody>{''.join(job_rows)}</tbody></table>
</section>
</body></html>
"""
    (OUT / "week-2026-11-16.html").write_text(page, encoding="utf-8")
    print("wrote", csv_path)


if __name__ == "__main__":
    main()

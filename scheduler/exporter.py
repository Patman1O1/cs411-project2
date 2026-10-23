"""
exporter.py - Writes the final timetable to <studentid>_timetable.csv.
"""

import csv
import os
from typing import Dict

from .model import SchedulingProblem, Value

COLUMNS = ["course_id", "room_id", "slot_id", "instructor_id", "cohort_id",
           "day", "start_time", "end_time"]


def export_timetable(
    problem: SchedulingProblem,
    assignment: Dict[str, Value],
    path: str
) -> str:
    p = problem
    rows = []
    for cid, (slot_id, room_id) in assignment.items():
        s = p.slots[slot_id]
        rows.append({
            "course_id": cid,
            "room_id": room_id,
            "slot_id": slot_id,
            "instructor_id": ";".join(p.course_instructors[cid]) or "TBA",
            "cohort_id": ";".join(p.course_cohorts.get(cid, [])) or "NONE",
            "day": s.day,
            "start_time": s.start_str,
            "end_time": s.end_str,
            "_key": (s.day_index, s.start, room_id, cid),
        })
    rows.sort(key=lambda r: r["_key"])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path

import csv
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

DAY_ORDER = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}


@dataclass
class Room:
    room_id: str
    building: str
    capacity: int
    physical_capacity: int
    room_type: str
    available: bool
    features: Set[str] = field(default_factory=set)
    notes: str = ""


@dataclass
class TimeSlot:
    slot_id: str
    day: str
    day_index: int
    start: int
    end: int
    start_str: str
    end_str: str
    period: str = ""


@dataclass
class Instructor:
    instructor_id: str
    name: str
    courses: List[str] = field(default_factory=list)
    unavailable: Set[str] = field(default_factory=set)
    preferred: Set[str] = field(default_factory=set)


@dataclass
class Course:
    course_id: str
    number: str
    level: str
    instructor_names: List[str]
    rooms_mentioned: List[str]
    contexts: List[str]
    notes: str = ""
    enrollment: Optional[int] = None
    synthetic: bool = False


@dataclass
class Cohort:
    cohort_id: str
    name: str
    size: int
    mandatory: List[str]


@dataclass
class ConstraintRule:
    constraint_id: str
    name: str
    ctype: str 
    description: str
    rule: str
    applies_to: str


@dataclass
class Dataset:
    courses: List[Course]
    rooms: List[Room]
    slots: List[TimeSlot]
    instructors: List[Instructor]
    cohorts: List[Cohort]
    rules: List[ConstraintRule]
    weights: Dict[str, int]


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for raw in reader:
            row = {}
            for k, v in raw.items():
                if k is None:
                    continue
                row[k.strip().lower()] = (v or "").strip()
            if any(row.values()):
                rows.append(row)
        return rows


def _get(row: Dict[str, str], *names: str, default: str = "") -> str:
    for n in names:
        v = row.get(n.lower())
        if v not in (None, "", "nan", "NaN", "None"):
            return v
    return default


def _split_list(value: str) -> List[str]:
    """Split 'A; B, C' style lists (semicolon preferred, comma fallback)."""
    if not value:
        return []
    sep = ";" if ";" in value else ("|" if "|" in value else ",")
    return [p.strip() for p in value.split(sep) if p.strip()]


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_time(value: str) -> int:
    """'13:00' / '1:00 PM' / '1300' -> minutes after midnight."""
    v = value.strip().upper()
    ampm = None
    if v.endswith("AM") or v.endswith("PM"):
        ampm = v[-2:]
        v = v[:-2].strip()
    if ":" in v:
        h, m = v.split(":")[:2]
    elif len(v) in (3, 4) and v.isdigit():
        h, m = v[:-2], v[-2:]
    else:
        h, m = v, "0"
    h, m = int(h), int(m)
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return h * 60 + m


def expand_room_mentions(text: str) -> List[str]:
    """'AUD 100; TECH 201/202/203/204' -> ['AUD 100','TECH 201',...]"""
    out: List[str] = []
    for token in _split_list(text):
        m = re.match(r"^(.*?)\s*([\w-]+(?:/[\w-]+)+)$", token)
        if m and m.group(1):
            prefix = m.group(1).strip()
            for num in m.group(2).split("/"):
                out.append(f"{prefix} {num}")
        else:
            out.append(token)
    return out


def _slot_list(value: str) -> Set[str]:
    return set(_split_list(value)) if value else set()


def load_rooms(data_dir: str) -> List[Room]:
    rooms = []
    for r in _read_csv(os.path.join(data_dir, "rooms.csv")):
        rid = _get(r, "room_id", "room")
        if not rid:
            continue
        phys = _to_int(_get(r, "physical_capacity", "capacity"), 0)
        sched_raw = _get(r, "scheduling_capacity")
        sched = _to_int(sched_raw, phys) if sched_raw != "" else phys
        avail = _get(r, "available_for_regular_classes", "available",
                     default="Yes")
        feat_text = _get(r, "features_or_constraints", "features")
        feats = {f.strip().lower() for f in re.split(r"[;,|]", feat_text)
                 if f.strip()}
        building = _get(r, "building", default=rid.split(" ")[0])
        rooms.append(Room(
            room_id=rid, building=building, capacity=sched,
            physical_capacity=phys,
            room_type=_get(r, "room_type", "type", default="Classroom"),
            available=avail.strip().lower() in ("yes", "y", "true", "1"),
            features=feats, notes=_get(r, "scheduling_notes", "notes"),
        ))
    return rooms


def load_slots(data_dir: str) -> List[TimeSlot]:
    slots = []
    for r in _read_csv(os.path.join(data_dir, "time_slots.csv")):
        sid = _get(r, "slot_id", "slot")
        if not sid:
            continue
        day = _get(r, "day", default="Monday")
        start_s = _get(r, "start_time", "start")
        end_s = _get(r, "end_time", "end")
        start, end = parse_time(start_s), parse_time(end_s)
        if end <= start:
            end = start + 50
        slots.append(TimeSlot(
            slot_id=sid, day=day,
            day_index=DAY_ORDER.get(day.strip().lower(), 7),
            start=start, end=end, start_str=start_s, end_str=end_s,
            period=_get(r, "period"),
        ))
    slots.sort(key=lambda s: (s.day_index, s.start, s.slot_id))
    return slots


def load_instructors(data_dir: str) -> List[Instructor]:
    out = []
    for r in _read_csv(os.path.join(data_dir, "instructors.csv")):
        iid = _get(r, "instructor_id", "id")
        name = _get(r, "instructor_name", "name", default=iid)
        if not iid and not name:
            continue
        out.append(Instructor(
            instructor_id=iid or name, name=name,
            courses=_split_list(_get(r, "courses")),
            unavailable=_slot_list(_get(r, "unavailable_slots", "unavailable",
                                        "unavailability")),
            preferred=_slot_list(_get(r, "preferred_slots", "preferred",
                                      "preferences")),
        ))
    return out


def load_courses(data_dir: str) -> List[Course]:
    out = []
    for r in _read_csv(os.path.join(data_dir, "courses.csv")):
        cid = _get(r, "course_id", "course")
        if not cid:
            continue
        enroll_raw = _get(r, "enrollment", "expected_enrollment",
                          "students", "size")
        context_text = _get(r, "scheduling_context", "context", "type")
        out.append(Course(
            course_id=cid,
            number=_get(r, "course_number", default=cid.split(" ")[-1]),
            level=_get(r, "level", default="Undergraduate"),
            instructor_names=_split_list(
                _get(r, "instructors", "instructor")),
            rooms_mentioned=expand_room_mentions(
                _get(r, "rooms_mentioned", "rooms")),
            contexts=[c.strip().lower() for c in _split_list(context_text)],
            notes=_get(r, "notes"),
            enrollment=_to_int(enroll_raw) if enroll_raw else None,
        ))
    return out


def load_cohorts(data_dir: str) -> List[Cohort]:
    out = []
    for r in _read_csv(os.path.join(data_dir, "student_cohorts.csv")):
        cid = _get(r, "cohort_id", "id")
        if not cid:
            continue
        out.append(Cohort(
            cohort_id=cid, name=_get(r, "name", default=cid),
            size=_to_int(_get(r, "size", "students"), 0),
            mandatory=_split_list(_get(r, "mandatory_courses", "courses")),
        ))
    return out


def load_rules(data_dir: str) -> List[ConstraintRule]:
    out = []
    for r in _read_csv(os.path.join(data_dir, "constraints.csv")):
        out.append(ConstraintRule(
            constraint_id=_get(r, "constraint_id", "id"),
            name=_get(r, "constraint_name", "name"),
            ctype=_get(r, "constraint_type", "type"),
            description=_get(r, "description"),
            rule=_get(r, "rule"),
            applies_to=_get(r, "applies_to"),
        ))
    return out


DEFAULT_WEIGHTS = {
    "HARD": 1000,
    "S1": 10,
    "S2": 5,
    "S3": 8,
    "S4": 12,
    "S5": 6,
    "S6": 10,
    "S7": 2
}


def load_weights(search_dirs: List[str]) -> Dict[str, int]:
    """Read penalty weights from sample_constraints.json if one is found."""
    weights = dict(DEFAULT_WEIGHTS)
    for d in search_dirs:
        path = os.path.join(d, "sample_constraints.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
            for item in spec.get("soft_constraints", []):
                key = str(item.get("id", ""))[:2].upper()
                if key in weights and "penalty_weight" in item:
                    weights[key] = int(item["penalty_weight"])
            hard = [int(h["penalty_weight"])
                    for h in spec.get("hard_constraints", [])
                    if "penalty_weight" in h]
            if hard:
                weights["HARD"] = max(hard)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        break
    return weights


def load_dataset(
    data_dir: str,
    extra_dirs: Optional[List[str]] = None
) -> Dataset:
    for required in ("courses.csv", "rooms.csv", "time_slots.csv"):
        path = os.path.join(data_dir, required)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing required dataset: {path}")
    return Dataset(
        courses=load_courses(data_dir),
        rooms=load_rooms(data_dir),
        slots=load_slots(data_dir),
        instructors=load_instructors(data_dir),
        cohorts=load_cohorts(data_dir),
        rules=load_rules(data_dir),
        weights=load_weights([data_dir] + list(extra_dirs or [])),
    )

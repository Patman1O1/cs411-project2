import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .data_loader import Course, Dataset, Instructor, Room, TimeSlot

Value = Tuple[str, str]

GENERAL_ROOM_KINDS = ("classroom", "lecture", "auditorium", "hall", "seminar")


class SchedulingProblem:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.weights = ds.weights
        self.warnings: List[str] = []

        self.rooms: Dict[str, Room] = {r.room_id: r for r in ds.rooms}
        self.slots: Dict[str, TimeSlot] = {s.slot_id: s for s in ds.slots}
        self.slot_order: List[str] = [s.slot_id for s in ds.slots]

        self._build_courses()
        self._build_instructors()
        self._build_cohorts()
        self._parse_rules()
        self._build_overlaps()
        self._estimate_enrollments()
        self._classify_activities()
        self._build_domains()
        self._build_constraint_graph()

    def _build_courses(self):
        self.courses: Dict[str, Course] = {}
        for c in self.ds.courses:
            if c.course_id in self.courses:
                self.warnings.append(
                    f"Duplicate course row ignored: {c.course_id}")
                continue
            self.courses[c.course_id] = c

        for coh in self.ds.cohorts:
            for cid in coh.mandatory:
                if cid not in self.courses:
                    self.courses[cid] = Course(
                        course_id=cid, number=cid.split(" ")[-1],
                        level="Undergraduate", instructor_names=[],
                        rooms_mentioned=[], contexts=["lecture"],
                        synthetic=True)
                    self.warnings.append(
                        f"{cid} is mandatory for {coh.cohort_id} but "
                        f"missing from courses.csv; added with default "
                        f"settings")
        self.variables: List[str] = list(self.courses)

    def _build_instructors(self):
        self.instructors: Dict[str, Instructor] = {
            i.instructor_id: i for i in self.ds.instructors}
        by_name = {i.name.strip().lower(): i.instructor_id
                   for i in self.ds.instructors}
        self.course_instructors: Dict[str, List[str]] = defaultdict(list)

        def add(cid, iid):
            if iid not in self.course_instructors[cid]:
                self.course_instructors[cid].append(iid)

        for cid, c in self.courses.items():
            for name in c.instructor_names:
                iid = by_name.get(name.strip().lower())
                if iid is None:
                    iid = f"X_{re.sub(r'[^A-Za-z0-9]', '', name)}"
                    if iid not in self.instructors:
                        self.instructors[iid] = Instructor(iid, name)
                        by_name[name.strip().lower()] = iid
                add(cid, iid)

        for inst in self.ds.instructors:
            for cid in inst.courses:
                if cid in self.courses:
                    add(cid, inst.instructor_id)

        for cid in self.courses:
            self.course_instructors[cid].sort()

    def _build_cohorts(self):
        self.cohorts = {c.cohort_id: c for c in self.ds.cohorts}
        self.course_cohorts: Dict[str, List[str]] = defaultdict(list)

        for coh in self.ds.cohorts:
            for cid in coh.mandatory:
                if coh.cohort_id not in self.course_cohorts[cid]:
                    self.course_cohorts[cid].append(coh.cohort_id)

    def _parse_rules(self):
        self.excluded_rooms: Set[str] = set()
        self.enforce_availability = True
        self.room_requests: Dict[str, str] = {}
        self.use_historical = False
        for rule in self.ds.rules:
            text = rule.rule
            hard = rule.ctype.strip().lower() == "hard"
            m = re.search(r"room_id\s*!=\s*'([^']+)'", text)
            if m and hard:
                self.excluded_rooms.add(m.group(1).strip())
            m = re.search(r"preferred_room\s*=\s*'([^']+)'", text)
            if m:
                for cid in re.split(r"[;,]", rule.applies_to):
                    if cid.strip():
                        self.room_requests[cid.strip()] = m.group(1).strip()
            if "historical" in (rule.name + text).lower():
                self.use_historical = True
        if not self.ds.rules:
            self.use_historical = True

    def _build_overlaps(self):
        self.overlaps: Dict[str, List[str]] = {}
        by_day = defaultdict(list)
        for s in self.ds.slots:
            by_day[s.day_index].append(s)
        for s in self.ds.slots:
            self.overlaps[s.slot_id] = [
                t.slot_id for t in by_day[s.day_index]
                if t.start < s.end and s.start < t.end
            ]

    def slots_overlap(self, a: str, b: str) -> bool:
        sa, sb = self.slots[a], self.slots[b]
        return (sa.day_index == sb.day_index
                and sa.start < sb.end and sb.start < sa.end)

    def _estimate_enrollments(self):
        self.enrollment: Dict[str, int] = {}
        self.enrollment_source: Dict[str, str] = {}
        for cid, c in self.courses.items():
            cohort_total = sum(self.cohorts[k].size
                               for k in self.course_cohorts.get(cid, []))
            if c.enrollment or cohort_total:
                self.enrollment[cid] = max(c.enrollment or 0, cohort_total)

                if (c.enrollment or 0) >= cohort_total:
                    src = "courses.csv"
                else:
                    src = "cohort size"
            else:
                caps = [self.rooms[r].capacity for r in c.rooms_mentioned
                        if r in self.rooms and self.rooms[r].capacity > 0]

                if caps:
                    self.enrollment[cid], src = min(caps), "historical room"
                else:
                    level = c.level.lower()
                    grad = "grad" in level and "under" not in level
                    self.enrollment[cid] = 25 if grad else 40
                    src = "level default"

            self.enrollment_source[cid] = src

    @staticmethod
    def _activity(contexts: List[str]) -> str:
        primary = contexts[0] if contexts else "lecture"
        if "specialized" in primary:
            return "specialized"
        if (primary.startswith("lab")
                or primary in ("computer lab", "laboratory")):
            return "lab"
        return "lecture"

    @staticmethod
    def room_supports(room: Room, activity: str) -> bool:
        rtype = room.room_type.lower()
        feats = " ".join(room.features)
        computing = ("lab" in rtype or "workstation" in feats
                     or "computer" in feats)
        general = any(k in rtype for k in GENERAL_ROOM_KINDS)
        if activity == "lab":
            return computing
        if activity == "specialized":
            return ("specialized" in rtype or "evl" in feats
                    or "workstation" in feats)
        return general

    def _classify_activities(self):
        self.activity = {cid: self._activity(c.contexts)
                         for cid, c in self.courses.items()}

    def room_usable(self, room: Room) -> bool:
        if room.room_id in self.excluded_rooms:
            return False
        if self.enforce_availability and not room.available:
            return False
        return room.capacity > 0

    def room_ok_for(self, cid: str, room: Room) -> bool:
        return (self.room_usable(room)
                and room.capacity >= self.enrollment[cid]
                and self.room_supports(room, self.activity[cid]))

    def slot_ok_for(self, cid: str, slot_id: str) -> bool:
        return all(slot_id not in self.instructors[i].unavailable
                   for i in self.course_instructors[cid]
                   if i in self.instructors)

    def _build_domains(self):
        self.domains: Dict[str, Dict[str, Set[str]]] = {}
        self.relaxed: Dict[str, str] = {}
        usable = [r for r in self.rooms.values() if self.room_usable(r)]
        for cid in self.variables:
            rooms = [r.room_id for r in usable if self.room_ok_for(cid, r)]
            if not rooms:
                rooms = [r.room_id for r in usable
                         if r.capacity >= self.enrollment[cid]]
                if rooms:
                    self.relaxed[cid] = "room type relaxed"
                    self.warnings.append(
                        f"{cid}: no {self.activity[cid]} room is large "
                        f"enough; room-type requirement relaxed")
            if not rooms and usable:
                biggest = max(r.capacity for r in usable)
                rooms = [r.room_id for r in usable if r.capacity == biggest]
                self.relaxed[cid] = "over capacity"
                self.warnings.append(
                    f"{cid}: enrollment {self.enrollment[cid]} exceeds "
                    f"every room; placed in largest room(s)")
            slots = [s for s in self.slot_order if self.slot_ok_for(cid, s)]
            if not slots:
                slots = list(self.slot_order)
                self.relaxed[cid] = "instructor availability"
                self.warnings.append(
                    f"{cid}: instructors have no common available slot")
            self.domains[cid] = {s: set(rooms) for s in slots}

    def domain_size(self, cid: str) -> int:
        return sum(len(r) for r in self.domains[cid].values())

    def _build_constraint_graph(self):
        self.related: Dict[str, Set[str]] = {
            c: set() for c in self.variables}
        groups = defaultdict(list)
        for cid in self.variables:
            for i in self.course_instructors[cid]:
                groups[("I", i)].append(cid)
            for k in self.course_cohorts.get(cid, []):
                groups[("K", k)].append(cid)
        for members in groups.values():
            for a in members:
                for b in members:
                    if a != b:
                        self.related[a].add(b)
        room_sets = {
            c: (set().union(*self.domains[c].values())
                if self.domains[c] else set())
            for c in self.variables}
        self.room_sets = room_sets
        self.competitors: Dict[str, Set[str]] = {}
        for a in self.variables:
            comp = set(self.related[a])
            for b in self.variables:
                if b != a and room_sets[a] & room_sets[b]:
                    comp.add(b)
            self.competitors[a] = comp
        self._capacity_precheck()

    def _capacity_precheck(self):
        parallel = 0
        by_day = defaultdict(list)
        for sl in self.ds.slots:
            by_day[sl.day_index].append(sl)
        for day_slots in by_day.values():
            last_end = -1

            for sl in sorted(day_slots, key=lambda x: x.end):
                if sl.start >= last_end:
                    parallel += 1
                    last_end = sl.end

        self.parallel_slots = parallel
        self.provably_infeasible = False

        for rs in {frozenset(v) for v in self.room_sets.values()}:
            demand = sum(1 for c in self.variables if self.room_sets[c] <= rs)
            supply = len(rs) * parallel
            if demand > supply:
                self.provably_infeasible = True
                self.warnings.append(
                    f"{demand} courses can only use rooms {sorted(rs)} "
                    f"which offer {supply} non-overlapping room-slots: "
                    f"hard constraints cannot all be met")

    def conflict(
        self,
        a: str,
        va: Value,
        b: str,
        vb: Value
    ) -> Optional[str]:
        if not self.slots_overlap(va[0], vb[0]):
            return None

        if va[1] == vb[1]:
            return "H1"

        if set(self.course_instructors[a]) & set(self.course_instructors[b]):
            return "H3"

        if (set(self.course_cohorts.get(a, []))
                & set(self.course_cohorts.get(b, []))):
            return "H5"

        return None

    def instructor_prefers(self, iid: str, slot_id: str) -> bool:
        inst = self.instructors.get(iid)

        if inst and inst.preferred:
            return slot_id in inst.preferred

        s = self.slots[slot_id]
        return s.start >= 9 * 60 and s.end <= 17 * 60 + 15

    def slot_cost(self, cid: str, slot_id: str) -> int:
        w = self.weights["S1"]

        return sum(
            w for i in self.course_instructors[cid]
            if not self.instructor_prefers(i, slot_id)
        )

    def room_cost_parts(self, cid: str, room_id: str) -> Dict[str, int]:
        room = self.rooms[room_id]
        parts = {"S2": 0, "S6": 0, "S7": 0}
        excess = room.capacity - self.enrollment[cid]

        if excess > 30:
            parts["S2"] = self.weights["S2"] * math.ceil((excess - 30) / 10)

        feasible = self.room_sets.get(cid, set())
        req = self.room_requests.get(cid)

        if req and req in feasible and room_id != req:
            parts["S6"] = self.weights["S6"]

        if self.use_historical:
            hist = [r for r in self.courses[cid].rooms_mentioned
                    if r in feasible]
            if hist and room_id not in hist:
                parts["S7"] = self.weights["S7"]
        return parts

    def room_cost(self, cid: str, room_id: str) -> int:
        return sum(self.room_cost_parts(cid, room_id).values())

    BACK_TO_BACK = 15
    DEAD_GAP = 50

    def day_cost_parts(
        self,
        sessions: List[Tuple[int, int, str]],
        is_cohort: bool
    ) -> Dict[str, int]:
        """sessions: (start, end, building) for one cohort/instructor on one
        day."""
        parts = {"S3": 0, "S4": 0, "S5": 0}

        if len(sessions) < 2: return parts

        ss = sorted(sessions)
        run = 1

        for prev, nxt in zip(ss, ss[1:]):
            gap = nxt[0] - prev[1]
            if 0 <= gap <= self.BACK_TO_BACK:
                run += 1
                if is_cohort and prev[2] != nxt[2]:
                    parts["S5"] += self.weights["S5"]
            else:
                if run > 2:
                    parts["S4"] += self.weights["S4"] * (run - 2)
                run = 1

                if is_cohort and gap >= self.DEAD_GAP:
                    parts["S3"] += self.weights["S3"] * max(1, round(gap / 60))

        if run > 2:
            parts["S4"] += self.weights["S4"] * (run - 2)
        return parts

    def day_cost(self, sessions, is_cohort: bool) -> int:
        return sum(self.day_cost_parts(sessions, is_cohort).values())

    def groups_of(self, cid: str) -> List[Tuple[str, str, bool]]:
        g = [("K", k, True) for k in self.course_cohorts.get(cid, [])]
        g += [("I", i, False) for i in self.course_instructors[cid]]
        return g

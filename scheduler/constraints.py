from collections import defaultdict
from typing import Dict, List, Tuple

from .model import SchedulingProblem, Value

HARD_NAMES = {
    "H1": "No room double-booking",
    "H2": "Room capacity, type & availability",
    "H3": "No instructor double-booking",
    "H4": "Instructor availability",
    "H5": "No student cohort clash",
    "H6": "Complete assignment",
}

SOFT_NAMES = {
    "S1": "Instructor preferred slots",
    "S2": "Oversized room (wasted seats)",
    "S3": "Cohort dead hours",
    "S4": "Consecutive-session fatigue",
    "S5": "Cross-building transit",
    "S6": "Requested room (constraints.csv)",
    "S7": "Historical room placement",
}


class ConstraintEngine(object):
    def __init__(
        self,
        problem: SchedulingProblem
    ) -> None:
        self.p = problem

    def hard_violations(
        self,
        assignment: Dict[str, Value]
    ) -> Tuple[Dict[str, int], List[str]]:
        p = self.p
        counts = {k: 0 for k in HARD_NAMES}
        details: List[str] = []

        for cid in p.variables:
            if cid not in assignment:
                counts["H6"] += 1
                details.append(f"H6: {cid} is not scheduled")

        for cid, (slot, room_id) in assignment.items():
            room = p.rooms.get(room_id)

            if room is None or slot not in p.slots:
                counts["H2"] += 1
                details.append(
                    f"H2: {cid} uses unknown room/slot {room_id}/{slot}"
                )
                continue

            if not p.room_usable(room):
                counts["H2"] += 1
                details.append(f"H2: {cid} in unavailable room {room_id}")

            elif room.capacity < p.enrollment[cid]:
                counts["H2"] += 1
                details.append(
                    f"H2: {cid} ({p.enrollment[cid]} students) exceeds "
                    f"{room_id} capacity {room.capacity}"
                )

            elif not p.room_supports(
                    room,
                    p.activity[cid]
                ) and cid not in p.relaxed:
                counts["H2"] += 1
                details.append(
                    f"H2: {cid} ({p.activity[cid]}) in unsuitable room type "
                    f"{room.room_type}"
                )

            for iid in p.course_instructors[cid]:
                inst = p.instructors.get(iid)
                if inst and slot in inst.unavailable:
                    counts["H4"] += 1
                    details.append(
                        f"H4: {inst.name} unavailable in {slot} ({cid})"
                    )

        items = sorted(assignment.items())
        for i, (a, va) in enumerate(items):
            for b, vb in items[i + 1:]:
                if not (
                    va[0] in p.slots and vb[0] in \
                    p.slots and p.slots_overlap(va[0], vb[0])
                ):
                    continue
                if va[1] == vb[1]:
                    counts["H1"] += 1
                    details.append(
                        f"H1: {a} and {b} both in {va[1]} ({va[0]}/{vb[0]})"
                    )
                shared_i = set(p.course_instructors[a]) & \
                           set(p.course_instructors[b])

                if shared_i:
                    counts["H3"] += 1
                    details.append(
                        f"H3: {a} and {b} share instructor(s) " + \
                        f"{sorted(shared_i)}"
                    )

                shared_k = set(p.course_cohorts.get(a, [])) & \
                           set(p.course_cohorts.get(b, []))

                if shared_k:
                    counts["H5"] += 1
                    details.append(
                        f"H5: {a} and {b} clash " \
                        f"for cohort(s) {sorted(shared_k)}"
                    )
        return counts, details

    def hard_total(self, assignment: Dict[str, Value]) -> int:
        return sum(self.hard_violations(assignment)[0].values())

    def soft_penalties(
        self,
        assignment: Dict[str, Value]
    ) -> Dict[str, int]:
        p = self.p
        totals = {k: 0 for k in SOFT_NAMES}
        days = defaultdict(list)

        for cid, (slot, room_id) in assignment.items():
            totals["S1"] += p.slot_cost(cid, slot)

            for k, v in p.room_cost_parts(cid, room_id).items():
                totals[k] += v

            s = p.slots[slot]
            sess = (s.start, s.end, p.rooms[room_id].building)

            for kind, gid, is_cohort in p.groups_of(cid):
                days[(kind, gid, is_cohort, s.day_index)].append(sess)

        for (kind, gid, is_cohort, _day), sessions in days.items():
            for k, v in p.day_cost_parts(sessions, is_cohort).items():
                totals[k] += v

        return totals

    def soft_total(
        self,
        assignment: Dict[str, Value]
    ) -> int:
        return sum(self.soft_penalties(assignment).values())

    def summary(
        self,
        assignment: Dict[str, Value]
    ) -> str:
        counts, details = self.hard_violations(assignment)
        soft = self.soft_penalties(assignment)
        lines = ["Hard constraints (must all be 0):"]

        for k, name in HARD_NAMES.items():
            lines.append(f"  {k} {name:<38} {counts[k]}")

        lines.append("Soft penalties:")

        for k, name in SOFT_NAMES.items():
            lines.append(f"  {k} {name:<38} {soft[k]}")

        lines.append(f"  Total soft penalty: {sum(soft.values())}")

        for d in details[:20]:
            lines.append("  ! " + d)

        return "\n".join(lines)

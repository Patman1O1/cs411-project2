import random
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .model import SchedulingProblem, Value


class BacktrackingSolver:
    def __init__(
        self,
        problem: SchedulingProblem,
        rng: random.Random,
        node_limit: int = 5000,
        max_restarts: int = 20,
        time_limit: float = 10.0
    ):
        self.problem = problem
        self.rng = rng
        self.node_limit = node_limit
        self.max_restarts = max_restarts
        self.time_limit = time_limit
        self.stats = {"nodes": 0, "backtracks": 0, "pruned_values": 0,
                      "restarts": 0, "seconds": 0.0}
        # static soft cost of each value, used only to break LCV ties
        self.slot_soft = {c: {s: problem.slot_cost(c, s) for s in problem.domains[c]}
                          for c in problem.variables}
        self.room_soft = {c: {r: problem.room_cost(c, r) for r in problem.room_sets[c]}
                          for c in problem.variables}

    def _reset(self):
        p = self.problem
        self.dom: Dict[str, Dict[str, Set[str]]] = {
            c: {s: set(rs) for s, rs in p.domains[c].items()}
            for c in p.variables}
        self.size = {c: sum(len(rs) for rs in self.dom[c].values())
                     for c in p.variables}
        self.cnt: Dict[Tuple[str, str], int] = defaultdict(int)
        for c in p.variables:
            for s, rs in self.dom[c].items():
                for r in rs:
                    self.cnt[(s, r)] += 1
        self.unassigned: Set[str] = set(p.variables)
        self.assignment: Dict[str, Value] = {}
        self.tiebreak = {c: self.rng.random() for c in p.variables}
        self.nodes_this_run = 0

    def select_variable(self) -> str:
        """MRV, then Degree (shared instructors/cohorts), then room
        competition, then a random tie-break. Only called while at least one
        course is unassigned."""
        p = self.problem

        def key(c: str):
            degree = len(p.related[c] & self.unassigned)
            room_degree = len(p.competitors[c] & self.unassigned)
            return (self.size[c], -degree, -room_degree, self.tiebreak[c])

        return min(self.unassigned, key=key)

    def order_values(self, c):
        p = self.problem
        rel = [y for y in p.related[c] if y in self.unassigned]
        own = self.dom[c]
        scored = []
        for s, rooms in own.items():
            ov = p.overlaps[s]
            # every value of a related course in an overlapping slot dies
            rel_cost = 0
            for y in rel:
                dy = self.dom[y]
                for s2 in ov:
                    rs = dy.get(s2)
                    if rs:
                        rel_cost += len(rs)
            soft_s = self.slot_soft[c][s]
            for r in sorted(rooms):
                room_cost = 0
                for s2 in ov:
                    n = self.cnt.get((s2, r), 0)
                    if n:
                        # remove c itself and related courses (already counted)
                        if r in own.get(s2, ()):
                            n -= 1
                        for y in rel:
                            if r in self.dom[y].get(s2, ()):
                                n -= 1
                        room_cost += n
                scored.append((rel_cost + room_cost,
                               soft_s + self.room_soft[c][r],
                               self.rng.random(), s, r))
        scored.sort()
        return [(s, r) for _, _, _, s, r in scored]

    def _remove_contribution(self, c: str, sign: int):
        for s, rs in self.dom[c].items():
            for r in rs:
                self.cnt[(s, r)] += sign

    def assign(self, c: str, value: Value):
        p = self.problem
        s, r = value
        self._remove_contribution(c, -1)
        self.unassigned.discard(c)
        self.assignment[c] = value
        log: List[Tuple[str, str, Set[str]]] = []
        ok = True
        ov = p.overlaps[s]
        related = p.related[c]
        for y in p.competitors[c]:
            if y not in self.unassigned:
                continue
            dy = self.dom[y]
            if y in related:
                for s2 in ov:
                    rs = dy.pop(s2, None)
                    if rs:
                        log.append((y, s2, rs))
                        self.size[y] -= len(rs)
                        for r2 in rs:
                            self.cnt[(s2, r2)] -= 1
            else:
                for s2 in ov:
                    rs = dy.get(s2)
                    if rs and r in rs:
                        rs.discard(r)
                        log.append((y, s2, {r}))
                        self.size[y] -= 1
                        self.cnt[(s2, r)] -= 1
                        if not rs:
                            del dy[s2]
            if self.size[y] == 0:
                ok = False
                break
        self.stats["pruned_values"] += sum(len(x[2]) for x in log)
        return log, ok

    def unassign(self, c: str, log):
        for y, s2, rs in reversed(log):
            self.dom[y].setdefault(s2, set()).update(rs)
            self.size[y] += len(rs)
            for r2 in rs:
                self.cnt[(s2, r2)] += 1
        del self.assignment[c]
        self.unassigned.add(c)
        self._remove_contribution(c, +1)

    def _out_of_budget(self):
        return (self.nodes_this_run >= self.node_limit
                or time.time() > self.deadline)

    def _backtrack(self):
        if not self.unassigned:
            return True
        if self._out_of_budget():
            return False
        c = self.select_variable()
        for value in self.order_values(c):
            self.nodes_this_run += 1
            self.stats["nodes"] += 1
            log, ok = self.assign(c, value)
            if ok and self._backtrack():
                return True
            self.unassign(c, log)
            self.stats["backtracks"] += 1
            if self._out_of_budget():
                return False
        return False

    def solve(self):
        start = time.time()
        self.deadline = start + self.time_limit
        result = None
        for attempt in range(self.max_restarts + 1):
            self._reset()
            if any(self.size[c] == 0 for c in self.problem.variables):
                break
            if self._backtrack():
                result = dict(self.assignment)
                break
            self.stats["restarts"] += 1
            if time.time() > self.deadline:
                break
        self.stats["seconds"] = round(time.time() - start, 3)
        return result

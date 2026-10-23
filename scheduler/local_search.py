"""
local_search.py - Min-Conflicts local search.

Works on a COMPLETE assignment. Each step:
  1. pick a conflicted variable
       - if any hard constraint is violated: a random course involved in a
         hard conflict (this is classic min-conflicts repair);
       - otherwise: a course that contributes soft penalty, chosen with
         probability proportional to its local penalty;
     recently moved courses are tabu for a few steps.
  2. evaluate EVERY (slot, room) in that course's domain with the objective
         HARD_WEIGHT * (#hard conflicts) + soft penalty
     using incremental deltas (only the course's own cost and the daily
     schedules of its cohorts / instructors change);
  3. move it to the minimum-conflict value (random tie-break, sideways moves
     allowed so the search can drift across plateaus).
If no improvement is found for a while, a small random perturbation is
applied (random walk). The best schedule ever seen is returned, so the
result is never worse than the starting point.
"""

import random
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .model import SchedulingProblem, Value


class MinConflictsSolver:
    def __init__(
        self,
        problem: SchedulingProblem,
        rng: random.Random,
        max_steps: int = 2500,
        time_limit: float = 6.0,
        tabu_tenure: int = 5,
        stall_limit: int = 400,
        sideways_prob: float = 0.3,
        patience: int = 1500
    ):
        self.p = problem
        self.rng = rng
        self.max_steps = max_steps
        self.time_limit = time_limit
        self.tabu_tenure = tabu_tenure
        self.stall_limit = stall_limit
        self.sideways_prob = sideways_prob
        # stop after this many steps without a new best
        self.patience = patience
        self.W = problem.weights["HARD"]
        self.stats = {
            "steps": 0, "moves": 0, "perturbations": 0, "seconds": 0.0,
            "initial_hard": 0, "initial_soft": 0,
            "final_hard": 0, "final_soft": 0,
            "history": [],
        }
        p = problem
        self.slot_soft = {c: {s: p.slot_cost(c, s) for s in p.domains[c]}
                          for c in p.variables}
        self.room_soft = {c: {r: p.room_cost(c, r) for r in p.room_sets[c]}
                          for c in p.variables}
        self.groups = {c: p.groups_of(c) for c in p.variables}
        self.members: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for c in p.variables:
            for kind, gid, _ in self.groups[c]:
                self.members[(kind, gid)].append(c)
        self.values = {
            c: [(s, r) for s, rs in p.domains[c].items() for r in sorted(rs)]
            for c in p.variables}

    # ------------------------------------------------------------------
    # State bookkeeping
    # ------------------------------------------------------------------
    def _load(self, assignment: Dict[str, Value]):
        self.assign: Dict[str, Value] = dict(assignment)
        self.room_use: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self.time_use: Dict[Tuple[str, str, str], Set[str]] = defaultdict(set)
        for c, v in self.assign.items():
            self._place(c, v)

    def _place(self, c, v):
        s, r = v
        self.room_use[(r, s)].add(c)
        for kind, gid, _ in self.groups[c]:
            self.time_use[(kind, gid, s)].add(c)

    def _remove(self, c, v):
        s, r = v
        self.room_use[(r, s)].discard(c)
        for kind, gid, _ in self.groups[c]:
            self.time_use[(kind, gid, s)].discard(c)

    # ------------------------------------------------------------------
    # Cost pieces
    # ------------------------------------------------------------------
    def hard_at(self, c: str, s: str, r: str) -> int:
        """Number of hard conflicts course c would have at (s, r)."""
        n = 0 if r in self.p.domains[c].get(s, ()) else 1
        for s2 in self.p.overlaps[s]:
            occ = self.room_use.get((r, s2))
            if occ:
                n += len(occ) - (1 if c in occ else 0)
        n += self.time_hard(c, s)
        return n

    def time_hard(self, c: str, s: str) -> int:
        n = 0
        for s2 in self.p.overlaps[s]:
            for kind, gid, _ in self.groups[c]:
                occ = self.time_use.get((kind, gid, s2))
                if occ:
                    n += len(occ) - (1 if c in occ else 0)
        return n

    def _day_sessions(self, kind, gid, day, override_c=None, override_v=None):
        sess = []
        for m in self.members[(kind, gid)]:
            v = override_v if m == override_c else self.assign.get(m)
            if v is None:
                continue
            sl = self.p.slots[v[0]]
            if sl.day_index == day:
                sess.append((sl.start, sl.end, self.p.rooms[v[1]].building))
        return sess

    def group_cost(self, c, v, days) -> int:
        """Daily-schedule penalty of every group of c on the given days,
        with c placed at value v (pass c's current value for the 'before'
        cost, a candidate value for the 'after' cost)."""
        p = self.p
        total = 0
        for kind, gid, is_cohort in self.groups[c]:
            for d in days:
                sessions = self._day_sessions(kind, gid, d, c, v)
                total += p.day_cost(sessions, is_cohort)
        return total

    def local_cost(self, c) -> int:
        s, r = self.assign[c]
        day = self.p.slots[s].day_index
        group = sum(self.p.day_cost(self._day_sessions(k, g, day), ic)
                    for k, g, ic in self.groups[c])
        return self.slot_soft[c].get(s, 0) + self.room_soft[c].get(r, 0) + \
            group

    def soft_total(self) -> int:
        p = self.p
        total = 0
        for c, (s, r) in self.assign.items():
            total += self.slot_soft[c].get(s, 0) + self.room_soft[c].get(r, 0)
        days = defaultdict(list)
        for c, (s, r) in self.assign.items():
            sl = p.slots[s]
            sess = (sl.start, sl.end, p.rooms[r].building)
            for kind, gid, ic in self.groups[c]:
                days[(kind, gid, ic, sl.day_index)].append(sess)
        for (kind, gid, ic, _), sess in days.items():
            total += p.day_cost(sess, ic)
        return total

    def hard_conflicted(self) -> List[str]:
        return [c for c, (s, r) in self.assign.items()
                if self.hard_at(c, s, r) > 0]

    def hard_total(self) -> int:
        # every conflicting pair is seen from both sides; unary counted once
        pair, unary = 0, 0
        for c, (s, r) in self.assign.items():
            u = 0 if r in self.p.domains[c].get(s, ()) else 1
            unary += u
            pair += self.hard_at(c, s, r) - u
        return unary + pair // 2

    # ------------------------------------------------------------------
    # One min-conflicts move
    # ------------------------------------------------------------------
    def best_values(self, c: str):
        cur_s, cur_r = self.assign[c]
        cur_hard = self.hard_at(c, cur_s, cur_r)
        cur_soft = (self.slot_soft[c].get(cur_s, 0)
                    + self.room_soft[c].get(cur_r, 0))
        cur_v = self.assign[c]
        cur_day = self.p.slots[cur_s].day_index
        # 'before' group cost, keyed by the set of days a move touches
        old_cache: Dict[int, int] = {}
        bld_cache: Dict[Tuple[str, str], int] = {}
        time_cache: Dict[str, int] = {}
        best, best_delta = [], None
        for (s, r) in self.values[c]:
            th = time_cache.get(s)
            if th is None:
                th = time_cache[s] = self.time_hard(c, s)
            hard = th
            for s2 in self.p.overlaps[s]:
                occ = self.room_use.get((r, s2))
                if occ:
                    hard += len(occ) - (1 if c in occ else 0)
            key = (s, self.p.rooms[r].building)
            g = bld_cache.get(key)
            if g is None:
                new_day = self.p.slots[s].day_index
                days = ((cur_day,) if new_day == cur_day
                        else (cur_day, new_day))
                old = old_cache.get(new_day)
                if old is None:
                    old = old_cache[new_day] = self.group_cost(c, cur_v, days)
                g = bld_cache[key] = self.group_cost(c, (s, r), days) - old
            delta = self.W * (hard - cur_hard) + \
                (self.slot_soft[c][s] + self.room_soft[c][r] - cur_soft) + g
            if best_delta is None or delta < best_delta:
                best, best_delta = [(s, r)], delta
            elif delta == best_delta:
                best.append((s, r))
        return best, best_delta

    def _move(self, c, v):
        self._remove(c, self.assign[c])
        self.assign[c] = v
        self._place(c, v)
        self.stats["moves"] += 1

    def _pick_variable(
        self,
        tabu: Dict[str, int],
        step: int
    ) -> Optional[str]:
        conflicted = [c for c in self.hard_conflicted()
                      if tabu.get(c, -1) < step]
        if conflicted:
            return self.rng.choice(conflicted)
        if self.hard_conflicted():
            return self.rng.choice(self.hard_conflicted())
        cands, weights = [], []
        for c in self.assign:
            if tabu.get(c, -1) >= step:
                continue
            lc = self.local_cost(c)
            if lc > 0:
                cands.append(c)
                weights.append(lc)
        if not cands:
            return None
        return self.rng.choices(cands, weights=weights, k=1)[0]

    def _perturb(self, k: int = 3):
        """Random walk: move k random courses to random hard-feasible values."""
        self.stats["perturbations"] += 1
        for c in self.rng.sample(list(self.assign), min(k, len(self.assign))):
            feasible = [v for v in self.values[c] if self.hard_at(c, *v) == 0]
            if feasible:
                self._move(c, self.rng.choice(feasible))

    # ------------------------------------------------------------------
    def greedy_initial(
        self,
        partial: Optional[Dict[str, Value]] = None
    ) -> Dict[str, Value]:
        """Complete a (possibly empty) assignment greedily: each missing
        course takes its current min-conflict value. Used when backtracking
        cannot find a fully consistent assignment."""
        self._load(partial or {})
        order = sorted((c for c in self.p.variables if c not in self.assign),
                       key=lambda c: self.p.domain_size(c))
        for c in order:
            best, best_cost = [], None
            for (s, r) in self.values[c]:
                cost = (self.W * self.hard_at(c, s, r)
                        + self.slot_soft[c][s] + self.room_soft[c][r])
                if best_cost is None or cost < best_cost:
                    best, best_cost = [(s, r)], cost
                elif cost == best_cost:
                    best.append((s, r))
            v = self.rng.choice(best)
            self.assign[c] = v
            self._place(c, v)
        return dict(self.assign)

    def solve(self, initial: Dict[str, Value]) -> Dict[str, Value]:
        start = time.time()
        self._load(initial)
        hard, soft = self.hard_total(), self.soft_total()
        self.stats.update(initial_hard=hard, initial_soft=soft)
        best_assign, best_cost = dict(self.assign), self.W * hard + soft
        cur_cost = best_cost
        self.stats["history"].append((0, hard, soft))
        tabu: Dict[str, int] = {}
        stall = 0
        step = 0
        last_best = 0
        while step < self.max_steps and time.time() - start < self.time_limit:
            step += 1
            c = self._pick_variable(tabu, step)
            if c is None:          # zero penalty and zero conflicts: optimal
                break
            vals, delta = self.best_values(c)
            if delta < 0:
                v = self.rng.choice(vals)
                self._move(c, v)
                cur_cost += delta
                tabu[c] = step + self.tabu_tenure
            elif delta == 0 and self.rng.random() < self.sideways_prob:
                others = [v for v in vals if v != self.assign[c]]
                if others:
                    self._move(c, self.rng.choice(others))
                    tabu[c] = step + self.tabu_tenure
            else:
                tabu[c] = step + 1
            if cur_cost < best_cost:
                best_cost, best_assign, stall = cur_cost, dict(self.assign), 0
                last_best = step
                self.stats["history"].append(
                    (step, self.hard_total(), self.soft_total()))
            else:
                stall += 1
            # be more patient while hard conflicts remain
            patience = self.patience * (1 if best_cost < self.W else 2)
            if step - last_best >= patience:
                break
            if stall >= self.stall_limit:
                self._load(best_assign)
                self._perturb(k=self.rng.randint(2, 4))
                cur_cost = self.W * self.hard_total() + self.soft_total()
                stall = 0
        self._load(best_assign)
        self.stats["steps"] = step
        self.stats["final_hard"] = self.hard_total()
        self.stats["final_soft"] = self.soft_total()
        self.stats["seconds"] = round(time.time() - start, 3)
        return dict(self.assign)

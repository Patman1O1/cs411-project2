import os
import random
import sys
import time
import zlib
from typing import Dict, Optional, Final

from .constraints import ConstraintEngine
from .csp_solver import BacktrackingSolver
from .data_loader import load_dataset
from .exporter import export_timetable
from .local_search import MinConflictsSolver
from .model import SchedulingProblem

PROJECT_ROOT: Final[str] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run_scheduler(
    data_dir: str,
    output_path: str,
    seed: Optional[int] = None,
    verbose: bool = True,
    ls_time_limit: float = 6.0
) -> Dict:
    t0 = time.time()
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    log = print if verbose else (lambda *a, **k: None)

    # 1. Data + CSP formulation
    ds = load_dataset(data_dir, extra_dirs=[PROJECT_ROOT])
    problem = SchedulingProblem(ds)
    engine = ConstraintEngine(problem)
    if seed is None:
        seed = zlib.crc32(os.path.basename(output_path).encode())
    rng = random.Random(seed)

    usable = sum(problem.room_usable(r) for r in problem.rooms.values())
    log(f"[data] {len(problem.variables)} courses, {len(problem.rooms)} rooms "
        f"({usable} usable), "
        f"{len(problem.slots)} time slots, {len(problem.cohorts)} cohorts, "
        f"{len(problem.instructors)} instructors")
    sizes = [problem.domain_size(c) for c in problem.variables] or [0]
    log(f"[csp ] domain sizes after node consistency: "
        f"min {min(sizes)}, max {max(sizes)}")
    for w in problem.warnings:
        log(f"[warn] {w}")

    # 2. Backtracking (MRV + Degree + LCV + Forward Checking)
    bt = BacktrackingSolver(problem, rng)
    assignment = None if problem.provably_infeasible else bt.solve()
    status = ("skipped: provably infeasible" if problem.provably_infeasible
              else "solved" if assignment else "no solution within budget")
    log(f"[csp ] backtracking: {status} "
        f"| nodes={bt.stats['nodes']} backtracks={bt.stats['backtracks']} "
        f"pruned={bt.stats['pruned_values']} restarts={bt.stats['restarts']} "
        f"time={bt.stats['seconds']}s")

    # 3. Min-conflicts local search (repairs hard conflicts if backtracking
    #    failed, then minimises soft penalties)
    ls = MinConflictsSolver(problem, rng, time_limit=ls_time_limit)
    if assignment is None:
        assignment = ls.greedy_initial()
        log(f"[ls  ] greedy start built for hard-constraint repair "
            f"(hard violations: {engine.hard_total(assignment)})")
    before_soft = engine.soft_total(assignment)
    assignment = ls.solve(assignment)
    log(f"[ls  ] min-conflicts: soft penalty {before_soft} -> "
        f"{ls.stats['final_soft']} "
        f"| steps={ls.stats['steps']} moves={ls.stats['moves']} "
        f"perturbations={ls.stats['perturbations']} "
        f"time={ls.stats['seconds']}s")

    # 4. Independent validation
    hard_counts, details = engine.hard_violations(assignment)
    soft = engine.soft_penalties(assignment)
    log(engine.summary(assignment))

    # 5. Export
    export_timetable(problem, assignment, output_path)
    log(f"[done] wrote {len(assignment)} rows to {output_path} "
        f"in {time.time() - t0:.2f}s")
    return {
        "output_path": output_path,
        "assignment": assignment,
        "hard": hard_counts,
        "soft": soft,
        "backtracking": bt.stats,
        "local_search": {k: v for k, v in ls.stats.items() if k != "history"},
        "problem": problem,
    }

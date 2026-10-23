# Project 2 Report: University Class Scheduling & Timetable Optimization

> [!IMPORTANT]
> **AUTOGRADER COMPLIANCE INSTRUCTIONS:**
> This report is parsed automatically by the autograder. To ensure you receive full credit for your work:
> 1. **Do not modify** the section headers (`## ...`) or bold field keys (e.g., `**Name:**`, `**Total Courses Configured:**`, `**Video Presentation Link:**`, etc.).
> 2. **Write your answers directly after** the colon `:` of each field, replacing the placeholder text completely (including the outer brackets `[` and `]`).
> 3. **Maintain the file structure**. Changing headers, bold titles, or deleting lines can cause the autograder to miss your responses and award 0 marks.

---

## Student Information 
- **Name:** Patrick Thomas
- **UID (netID):** pthom29
- **UIN:** 677959526

---

## Section 1: Dataset & University Configuration
- **Total Courses Configured:** 65
- **Total Rooms Configured:** 20
- **Total Time Slots Configured:** 50

---

## Section 2: Local Verification & Implemented Solvers
*Check the algorithms and features you successfully ran and verified by placing an `x` in the brackets (e.g., `[x]`):*
- [x] DataLoader & CSV Parsing
- [x] Hard Constraint Validation Engine
- [x] Soft Penalty & Quality Evaluation Engine
- [x] CSP Backtracking with MRV + LCV + Forward Checking
- [x] Min-Conflicts CSP Local Search
- [x] Timetable CSV Export (<studentid>_timetable.csv)

---

## Section 3: Video Presentation & Project Structure
- **Video Presentation Link:** [Provide an accessible link to your 5–7 minute video presentation]
- **Project Directory Structure:** The repository root holds `app.py` (the `studentscheduler()` entry point, which reads my netID from this report and runs the pipeline), `report.md`, `requirements.txt`, `sample_constraints.json` (penalty weights) and `data/` (the six input CSVs plus the generated `pthom29_timetable.csv`). The solver lives in the `scheduler/` package, one module per stage: `data_loader.py` parses the CSVs and tolerates missing columns or files; `model.py` builds the CSP (variables, domains, constraint graph and penalty functions); `constraints.py` independently checks every hard and soft constraint; `csp_solver.py` is backtracking with MRV, Degree, LCV and Forward Checking; `local_search.py` is Min-Conflicts; `exporter.py` writes the timetable CSV; and `pipeline.py` runs these in order and prints a validation summary.

---

## Section 4: Discussion & Problem Formulation
*Provide your written analysis for each point by replacing the bracket placeholders below:*
- **How did you formulate the variables, domains, and constraints for this scheduling problem?**
    Each course is a variable (65, plus any cohort-mandatory course missing from `courses.csv`), and its value is a (time slot, room) pair, giving 50 × 20 = 1,000 raw values. Node consistency then removes values that break a unary hard constraint: unavailable rooms (LAB 254), rooms whose scheduling capacity is below the enrollment, rooms of the wrong type (labs need computer rooms, specialized courses need workstation rooms, lectures need a classroom or hall), and slots where an instructor is unavailable. Enrollment is estimated from cohort sizes (CS 101 = 150), otherwise from the smallest historically used room. Domains then range from 50 to 850 values. Binary hard constraints apply to courses whose slots overlap in time: they cannot share a room (H1), an instructor (H3) or a cohort (H5). Soft constraints are a weighted penalty from `sample_constraints.json`: off-hours teaching (assuming a 9:00–17:15 window, since no preferences are given), more than 30 wasted seats, cohort dead gaps, more than two back-to-back sessions, and back-to-back sessions in different buildings, plus the room requests and historical placement from `constraints.csv`.
- **Compare CSP Backtracking (with heuristics) vs Local Search (Min-Conflicts) in terms of scalability, completeness, and solution quality.**
    Backtracking is complete: it finds a schedule with zero hard violations or proves none exists, but its worst case is exponential. MRV, Degree, LCV and Forward Checking make it practical: on the provided data it assigns all 65 courses with zero backtracks in about 0.04 s, and still needs none when the week is cut to 11 slots. It only guarantees feasibility, though; its first solution had a soft penalty of 48. Min-Conflicts is incomplete and can stall in local minima, but each step is cheap: move a conflicted or penalized course to its lowest-cost value. With a tabu list, sideways moves and random perturbations, it lowers the penalty from 48 to 16 in about 1,500 steps with zero hard violations. My pipeline uses backtracking for feasibility and Min-Conflicts for quality, and falls back to Min-Conflicts repair if backtracking fails. On a 3× larger dataset (195 courses) it finishes in about 3 s.
- **How can modern AI / LLM techniques be combined with CSP solvers for real-world automated university scheduling?**
    Let the LLM handle language and the solver handle guarantees. An LLM can turn messy inputs, such as faculty emails ("no Friday afternoons") or notes like the CS 361/362 instructor grouping, into structured constraints for a human to approve, and flag inconsistencies such as CS 101's historical 64-seat room. The solver stays the source of truth, since an LLM cannot guarantee zero double-bookings. Learned models can also warm-start the search from past terms, and an LLM can explain results to stakeholders or turn what-if questions into constraint changes and re-run the solver.

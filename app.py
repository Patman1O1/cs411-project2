import csv
import os
import re
import traceback

from scheduler.pipeline import run_scheduler

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def get_student_id():
    report_path = os.path.join(APP_DIR, 'report.md')
    if os.path.exists(report_path):
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                content = f.read()
            match = re.search(r'-\s*\*\*UID \(netID\):\*\*\s*(.*)',
                              content, re.IGNORECASE)
            if not match:
                match = re.search(r'-\s*\*\*UIN:\*\*\s*(.*)',
                                  content, re.IGNORECASE)
            if match:
                val = match.group(1).strip()
                if val and not (val.startswith('[') and val.endswith(']')):
                    return re.sub(r'[^a-zA-Z0-9_-]', '_', val)
        except Exception:
            pass
    return "student"


def _resolve_dirs(data_dir, output_dir):
    if os.path.isabs(data_dir) or os.path.isdir(data_dir):
        return data_dir, output_dir

    alt = os.path.join(APP_DIR, data_dir)

    if os.path.isdir(alt):
        if os.path.isabs(output_dir):
            out = output_dir
        else:
            out = os.path.join(APP_DIR, output_dir)
        return alt, out

    return data_dir, output_dir


def _emergency_schedule(data_dir, output_path): 
    def rows(name):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            return []
        with open(path, newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))

    slots = rows('time_slots.csv')
    def available(room):
        flag = room.get('available_for_regular_classes', 'Yes')
        return flag.strip().lower() != 'no'

    rooms = [r for r in rows('rooms.csv') if available(r)]
    courses = rows('courses.csv')
    used = set()
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(["course_id", "room_id", "slot_id", "instructor_id",
                    "cohort_id", "day", "start_time", "end_time"])
        if not slots or not rooms:
            return
        for i, c in enumerate(courses):
            for k in range(len(slots) * len(rooms)):
                s = slots[(i + k) % len(slots)]
                r = rooms[(k // len(slots)) % len(rooms)]
                if (s['slot_id'], r['room_id']) not in used:
                    used.add((s['slot_id'], r['room_id']))
                    w.writerow([c.get('course_id'), r['room_id'], s['slot_id'],
                                c.get('instructors') or 'TBA', 'NONE',
                                s.get('day'), s.get('start_time'),
                                s.get('end_time')])
                    break


def studentscheduler(data_dir="data", output_dir="data", student_id=None):
    if student_id is None:
        student_id = get_student_id()

    data_dir, output_dir = _resolve_dirs(data_dir, output_dir)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{student_id}_timetable.csv")

    try:
        run_scheduler(data_dir, output_path)
    except Exception:
        traceback.print_exc()
        print("[error] main solver failed; writing first-fit fallback schedule")
        _emergency_schedule(data_dir, output_path)

    return output_path


if __name__ == "__main__":
    generated_file = studentscheduler()
    print(f"Timetable generation completed. Output file: {generated_file}")

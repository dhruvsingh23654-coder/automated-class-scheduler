# Automated Timetable Scheduler

A Flask web app that generates a **day-wise weekly timetable for a single
class/standard**, using Google OR-Tools' CP-SAT constraint solver to assign
each subject its required periods across the week — respecting teacher
availability, workload caps, and daily limits.

---

## Setup

**Requirements:** Python 3.9+

```bash
# 1. Unzip and enter the project
unzip timetable_scheduler.zip
cd timetable_scheduler

# 2. (Recommended) create a virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set a secret key (required for production; a random one is used
#    for local dev if you skip this, but sessions won't survive a restart)
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
# Windows (PowerShell): $env:SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"

# 5. Run it
python app.py
```

Open **http://localhost:5000** — sample data (one class, 3 teachers, 7
subjects) is preloaded so you can generate a timetable immediately.

---

## How to use it

1. **Setup page** — edit the standard's name and periods/day, add/remove
   teachers and subjects, and mark any teachers absent for the week.
2. Click **Generate Day-wise Timetable**.
3. The result is a Mon–Fri breakdown, each day showing which subject/teacher
   occupies each period (or `Free` if nothing is scheduled there).
4. If generation fails, the app explains why (see **Feasibility** below) and
   suggests what to relax.

---

## Scope

### What it does
- Builds a timetable for **one class/standard at a time** (not a whole
  school with multiple classes/rooms simultaneously).
- Enforces, via CP-SAT:
  - Exactly `periods_per_week` sessions per subject — no more, no less.
  - At most one subject per (day, period) slot.
  - A teacher is never double-booked at the same slot (relevant if one
    teacher handles multiple subjects for this class).
  - A subject never exceeds its `max_per_day` (default 1).
  - A teacher's total weekly periods never exceed their `max_periods_per_week`
    (optional; unset means uncapped).
  - A teacher marked absent on a given day is excluded from that day
    entirely; the solver fits their subjects into their remaining days.
- Validates input **before** solving (duplicate IDs, unknown teacher
  references, a subject that can never fit even in isolation, a teacher whose
  total demand exceeds their own cap) so obviously-broken data fails fast
  with a specific message instead of an opaque solver error.
- Distinguishes three outcomes when generation doesn't produce a timetable:
  - **Infeasible** — proven no solution exists for this data; the app gives
    a targeted hint (e.g. "total periods/week exceeds available slots").
  - **Inconclusive / timeout** — the solver ran out of time (15s limit)
    without proving feasibility or infeasibility either way.
  - **Model invalid** — an internal error building the constraint model;
    not something adjusting your data will fix.

### What it does *not* do
- **No multi-class scheduling.** It won't coordinate several classes sharing
  the same teachers or rooms at once — each standard is scheduled
  independently. Running it separately per class does not guarantee a
  teacher's timetable is consistent *across* classes.
- **No room/classroom allocation.** There's no room-capacity or room-type
  constraint in this version (an earlier iteration had one; the current
  single-standard model assumes the class stays in one room).
- **No substitute-teacher assignment.** Marking a teacher absent removes
  them from that day and reshuffles *their own* subject's periods into their
  remaining days — it does not find or assign another teacher to cover the
  gap.
- **No persistence.** Data lives in the browser session only (Flask's
  signed-cookie session). Refreshing in a different browser/device starts
  from the sample data; there's no database, so nothing survives clearing
  cookies or switching machines.
- **No authentication.** Anyone with access to the running app can edit
  data; there's no login or per-user separation.
- **No recurring/term-long calendar.** It produces one repeating weekly
  pattern (Mon–Fri), not a full-term calendar with holidays or date-specific
  exceptions.

---

## Feasibility notes

Because every subject must hit its exact `periods_per_week`, tight inputs
can become infeasible in non-obvious ways:

- A subject needs `periods_per_week ≤ days × max_per_day` just to fit on its
  own — the app now blocks this at input time.
- A teacher's total load across all their subjects must fit their own
  `max_periods_per_week` — also blocked at input time.
- Marking a teacher absent removes days from every subject they teach. If a
  subject was already using all its slack (e.g. 5 periods/week across
  exactly 5 days with `max_per_day=1`), one absence day makes it infeasible
  — there's no other day left to move that period to. Leave some slack
  (fewer periods/week than days available) if you expect to use the
  absence feature.

---

## Project structure

```
timetable_scheduler/
├── app.py              # Flask routes, form validation, CSRF
├── scheduler.py         # CP-SAT model (StandardTimetableScheduler)
├── requirements.txt
└── templates/
    ├── base.html         # Bootstrap layout, flash messages
    ├── index.html        # Setup form (standard, teachers, subjects, absences)
    └── results.html       # Day-wise generated timetable
```

## Security notes

- `SECRET_KEY` should always be set via environment variable in any real
  deployment (see Setup step 4) — without it, sessions reset on every
  process restart.
- All state-changing requests (add/delete/generate) are POST-only and
  protected by a per-session CSRF token.
- This app has no authentication layer — do not expose it publicly without
  adding one.

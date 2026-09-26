"""
Automated Timetable Scheduler — single-standard (class/grade) engine.
Builds a day-wise weekly timetable for ONE standard, assigning each subject
its required periods/week to specific (day, period) slots, subject to:
  - Teacher not double-booked at the same slot (if a teacher handles >1 subject)
  - Teacher absence on given day(s) -> those slots blocked for that teacher
  - Teacher weekly load cap (max periods/week)
  - A subject is not repeated more than `max_per_day` times on the same day
  - At most one subject occupies any given (day, period) slot for this standard

Note: slots are NOT required to be filled. Each subject is scheduled exactly
`periods_per_week` times (no more, no less); any (day, period) slot left over
once every subject's requirement is met simply stays free ("Free" in the UI).
There is no constraint forcing every slot in the week to be occupied.
"""
from ortools.sat.python import cp_model
from collections import defaultdict


class SchedulerInputError(ValueError):
    """Raised when the subjects/teachers passed in are structurally invalid
    (duplicate IDs, dangling references, demand that can't possibly fit) —
    independent of any UI-level checks. Distinct from an INFEASIBLE solve:
    these are cheap, deterministic checks caught before the CP-SAT model is
    even built, so obviously-broken input never reaches the solver."""


class StandardTimetableScheduler:
    def __init__(self, standard_name, days, periods_per_day, subjects, teachers,
                 absences=None, default_max_per_day=1):
        """
        standard_name : str, e.g. "Grade 8 - Section A"
        days          : list[str], e.g. ["Mon","Tue","Wed","Thu","Fri"]
        periods_per_day: int
        subjects      : list of dicts
            {id, name, teacher_id, periods_per_week, max_per_day (optional)}
        teachers      : list of dicts
            {id, name, max_periods_per_week (optional)}
        absences      : dict teacher_id -> set(days) the teacher is absent

        Raises SchedulerInputError for:
          - duplicate subject/teacher IDs
          - a subject referencing a teacher_id that isn't in `teachers`
          - a subject's periods_per_week exceeding days × its max_per_day
            (it could never be scheduled regardless of anything else)
          - a teacher's total periods_per_week across their subjects exceeding
            their own max_periods_per_week (guaranteed infeasible on its own)
        """
        self.default_max_per_day = default_max_per_day
        self._validate_input(subjects, teachers, days, default_max_per_day)

        self.standard_name = standard_name
        self.days = days
        self.periods = list(range(1, periods_per_day + 1))
        self.subjects = {s['id']: s for s in subjects}
        self.teachers = {t['id']: t for t in teachers}
        self.absences = absences or {}
        self.model = cp_model.CpModel()
        self.slots = [(d, p) for d in self.days for p in self.periods]

    @staticmethod
    def _validate_input(subjects, teachers, days, default_max_per_day):
        subject_ids = [s['id'] for s in subjects]
        dup_subjects = {sid for sid in subject_ids if subject_ids.count(sid) > 1}
        if dup_subjects:
            raise SchedulerInputError(f"Duplicate subject ID(s): {', '.join(sorted(dup_subjects))}")

        teacher_ids = [t['id'] for t in teachers]
        dup_teachers = {tid for tid in teacher_ids if teacher_ids.count(tid) > 1}
        if dup_teachers:
            raise SchedulerInputError(f"Duplicate teacher ID(s): {', '.join(sorted(dup_teachers))}")

        known_teachers = {t['id']: t for t in teachers}
        dangling = sorted({
            s['id'] for s in subjects if s.get('teacher_id') not in known_teachers
        })
        if dangling:
            raise SchedulerInputError(
                f"Subject(s) reference an unknown teacher_id: {', '.join(dangling)}"
            )

        # A subject can supply at most (days × its own max_per_day) sessions/week,
        # independent of everything else in the model. Catch this before solving.
        n_days = len(days)
        overpacked = []
        for s in subjects:
            cap = s.get('max_per_day')
            if cap is None:
                cap = default_max_per_day
            ceiling = n_days * cap
            if s['periods_per_week'] > ceiling:
                overpacked.append(
                    f"{s['id']} needs {s['periods_per_week']}/week but can supply at most "
                    f"{ceiling} ({n_days} days × {cap}/day)"
                )
        if overpacked:
            raise SchedulerInputError(
                "Subject(s) can never satisfy their own periods_per_week: " + "; ".join(overpacked)
            )

        # A teacher's own weekly cap must be able to cover the combined demand
        # of every subject they teach — otherwise no solution can ever exist.
        demand_by_teacher = defaultdict(int)
        for s in subjects:
            demand_by_teacher[s.get('teacher_id')] += s['periods_per_week']
        overloaded = []
        for tid, demand in demand_by_teacher.items():
            cap = known_teachers.get(tid, {}).get('max_periods_per_week')
            if cap is not None and demand > cap:
                overloaded.append(
                    f"{tid} needs {demand}/week across their subjects but is capped at {cap}/week"
                )
        if overloaded:
            raise SchedulerInputError(
                "Teacher(s) are over-committed beyond their own weekly cap: " + "; ".join(overloaded)
            )

    def build(self):
        model = self.model
        x = {}  # (subject_id, day, period) -> bool var

        for sid, subj in self.subjects.items():
            tid = subj['teacher_id']
            absent_days = self.absences.get(tid, set())
            for (d, p) in self.slots:
                if d in absent_days:
                    continue
                x[(sid, d, p)] = model.NewBoolVar(f"x_{sid}_{d}_{p}")

        self.x = x

        for sid, subj in self.subjects.items():
            n = subj['periods_per_week']
            vars_s = [v for (s, d, p), v in x.items() if s == sid]
            model.Add(sum(vars_s) == n)

        for (d, p) in self.slots:
            vars_here = [v for (s, dd, pp), v in x.items() if dd == d and pp == p]
            if vars_here:
                model.Add(sum(vars_here) <= 1)

        teacher_subjects = defaultdict(list)
        for sid, subj in self.subjects.items():
            teacher_subjects[subj['teacher_id']].append(sid)
        for tid, sids in teacher_subjects.items():
            if len(sids) < 2:
                continue
            for (d, p) in self.slots:
                vars_here = [v for (s, dd, pp), v in x.items()
                             if dd == d and pp == p and s in sids]
                if vars_here:
                    model.Add(sum(vars_here) <= 1)

        for sid, subj in self.subjects.items():
            max_per_day = subj.get('max_per_day')
            if max_per_day is None:
                max_per_day = self.default_max_per_day
            for d in self.days:
                vars_here = [v for (s, dd, pp), v in x.items() if s == sid and dd == d]
                if vars_here:
                    model.Add(sum(vars_here) <= max_per_day)

        for tid, t in self.teachers.items():
            cap = t.get('max_periods_per_week')
            if cap is None:
                continue
            sids = teacher_subjects.get(tid, [])
            vars_here = [v for (s, d, p), v in x.items() if s in sids]
            if vars_here:
                model.Add(sum(vars_here) <= cap)

        return model

    def solve(self, time_limit_seconds=15):
        self.build()
        self.solver = cp_model.CpSolver()
        self.solver.parameters.max_time_in_seconds = time_limit_seconds
        self.solver.parameters.num_search_workers = 8
        self.status = self.solver.Solve(self.model)
        return self.status

    def get_timetable(self):
        """Returns dict: day -> {period: {subject, teacher}}, or None if no
        solution was produced (status is INFEASIBLE, UNKNOWN, or MODEL_INVALID)."""
        if self.status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        grid = {d: {p: None for p in self.periods} for d in self.days}
        for (sid, d, p), var in self.x.items():
            if self.solver.Value(var):
                subj = self.subjects[sid]
                teacher = self.teachers.get(subj['teacher_id'], {})
                grid[d][p] = {
                    'subject_id': sid,
                    'subject_name': subj['name'],
                    'teacher_id': subj['teacher_id'],
                    'teacher_name': teacher.get('name', subj['teacher_id']),
                }
        return grid

    def status_name(self):
        return self.solver.StatusName(self.status) if hasattr(self, 'solver') else "NOT_SOLVED"

    def is_infeasible(self):
        """True only when the model was fully proven to have no solution."""
        return hasattr(self, 'status') and self.status == cp_model.INFEASIBLE

    def is_inconclusive(self):
        """True when the solver ran out of time/resources without proving
        feasibility or infeasibility — the answer is genuinely unknown, not 'no'."""
        return hasattr(self, 'status') and self.status == cp_model.UNKNOWN

    def is_model_invalid(self):
        """True when CP-SAT rejected the model itself as malformed (e.g. an
        internal constraint bug) — distinct from the input simply having no
        solution. This should never happen with valid input; if it does, it
        signals a bug in build() rather than something the caller can fix by
        adjusting subjects/teachers/absences."""
        return hasattr(self, 'status') and self.status == cp_model.MODEL_INVALID

    def validation_errors(self):
        """Human-readable reasons the model is invalid, straight from CP-SAT.
        Only meaningful when is_model_invalid() is True."""
        if not hasattr(self, 'model'):
            return []
        return [self.model.Validate()] if self.model.Validate() else []

import os
import secrets

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from scheduler import StandardTimetableScheduler, SchedulerInputError

app = Flask(__name__)

# SECRET_KEY must come from the environment in any real deployment; a random
# key is generated per-process as a dev-only fallback (sessions won't persist
# across restarts, which is an intentional nudge to set the env var).
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("SECRET_KEY"):
    print("WARNING: SECRET_KEY not set in environment — using an ephemeral "
          "random key for this process. Set SECRET_KEY before deploying.")

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def sample_data():
    standard = {"name": "Grade 8 - Section A", "periods_per_day": 6}
    teachers = [
        {"id": "T1", "name": "Mrs. Sharma", "max_periods_per_week": 18},
        {"id": "T2", "name": "Mr. Khan", "max_periods_per_week": 15},
        {"id": "T3", "name": "Ms. Fernandes", "max_periods_per_week": 12},
    ]
    subjects = [
        {"id": "S1", "name": "Mathematics", "teacher_id": "T1", "periods_per_week": 4, "max_per_day": 1},
        {"id": "S2", "name": "English", "teacher_id": "T2", "periods_per_week": 4, "max_per_day": 1},
        {"id": "S3", "name": "Science", "teacher_id": "T1", "periods_per_week": 4, "max_per_day": 1},
        {"id": "S4", "name": "Social Studies", "teacher_id": "T3", "periods_per_week": 3, "max_per_day": 1},
        {"id": "S5", "name": "Computer Science", "teacher_id": "T2", "periods_per_week": 3, "max_per_day": 1},
        {"id": "S6", "name": "Art", "teacher_id": "T3", "periods_per_week": 3, "max_per_day": 1},
        {"id": "S7", "name": "Physical Education", "teacher_id": "T3", "periods_per_week": 2, "max_per_day": 1},
    ]
    return standard, teachers, subjects


# ---------------------------------------------------------------------- #
# CSRF protection (lightweight, no extra dependency)
# ---------------------------------------------------------------------- #
def get_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


@app.before_request
def enforce_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            abort(400, description="Invalid or missing CSRF token.")


# ---------------------------------------------------------------------- #
# Validation helpers
# ---------------------------------------------------------------------- #
def fail(message):
    """Flash an error and bounce back to setup instead of crashing."""
    flash(message, "danger")
    return redirect(url_for("index"))


def parse_required_int(field_name, label, min_val=None, max_val=None):
    """Returns (value, error_redirect_or_None)."""
    raw = request.form.get(field_name, "").strip()
    if raw == "":
        return None, fail(f"{label} is required.")
    try:
        val = int(raw)
    except ValueError:
        return None, fail(f"{label} must be a whole number.")
    if min_val is not None and val < min_val:
        return None, fail(f"{label} must be at least {min_val}.")
    if max_val is not None and val > max_val:
        return None, fail(f"{label} must be at most {max_val}.")
    return val, None


def parse_optional_int(field_name, label, min_val=None, max_val=None):
    """Returns (value_or_None, error_redirect_or_None). Distinguishes '' (unset) from '0'."""
    raw = request.form.get(field_name, "").strip()
    if raw == "":
        return None, None
    try:
        val = int(raw)
    except ValueError:
        return None, fail(f"{label} must be a whole number.")
    if min_val is not None and val < min_val:
        return None, fail(f"{label} must be at least {min_val}.")
    if max_val is not None and val > max_val:
        return None, fail(f"{label} must be at most {max_val}.")
    return val, None


def required_text(field_name, label):
    raw = request.form.get(field_name, "").strip()
    if not raw:
        return None, fail(f"{label} is required.")
    return raw, None


# ---------------------------------------------------------------------- #
# Routes
# ---------------------------------------------------------------------- #
@app.route("/")
def index():
    if "standard" not in session:
        std, teachers, subjects = sample_data()
        session["standard"] = std
        session["teachers"] = teachers
        session["subjects"] = subjects
        session["absences"] = {}  # teacher_id -> list of days
    return render_template(
        "index.html",
        standard=session["standard"],
        teachers=session["teachers"],
        subjects=session["subjects"],
        absences=session.get("absences", {}),
        days=DAYS,
    )


@app.route("/reset")
def reset():
    session.clear()
    return redirect(url_for("index"))


@app.route("/standard/update", methods=["POST"])
def update_standard():
    name, err = required_text("name", "Standard name")
    if err:
        return err
    periods_per_day, err = parse_required_int("periods_per_day", "Periods per day", min_val=1, max_val=10)
    if err:
        return err

    session["standard"] = {"name": name, "periods_per_day": periods_per_day}
    flash("Standard details updated.", "success")
    return redirect(url_for("index"))


@app.route("/teacher/add", methods=["POST"])
def add_teacher():
    teachers = session.get("teachers", [])

    tid, err = required_text("id", "Teacher ID")
    if err:
        return err
    if any(t["id"] == tid for t in teachers):
        return fail(f"Teacher ID '{tid}' already exists. Use a unique ID.")

    name, err = required_text("name", "Teacher name")
    if err:
        return err

    max_per_week, err = parse_optional_int("max_periods_per_week", "Max periods/week", min_val=0, max_val=60)
    if err:
        return err

    teachers.append({"id": tid, "name": name, "max_periods_per_week": max_per_week})
    session["teachers"] = teachers
    flash("Teacher added.", "success")
    return redirect(url_for("index"))


@app.route("/subject/add", methods=["POST"])
def add_subject():
    subjects = session.get("subjects", [])
    teachers = session.get("teachers", [])
    standard = session.get("standard", {"periods_per_day": 6})
    max_possible_per_week = standard["periods_per_day"] * len(DAYS)

    sid, err = required_text("id", "Subject ID")
    if err:
        return err
    if any(s["id"] == sid for s in subjects):
        return fail(f"Subject ID '{sid}' already exists. Use a unique ID.")

    name, err = required_text("name", "Subject name")
    if err:
        return err

    teacher_id, err = required_text("teacher_id", "Teacher ID")
    if err:
        return err
    if not any(t["id"] == teacher_id for t in teachers):
        return fail(f"No teacher with ID '{teacher_id}' exists. Add the teacher first.")

    periods_per_week, err = parse_required_int(
        "periods_per_week", "Periods/week", min_val=1, max_val=max_possible_per_week
    )
    if err:
        return err

    max_per_day, err = parse_optional_int(
        "max_per_day", "Max sessions/day", min_val=1, max_val=standard["periods_per_day"]
    )
    if err:
        return err
    if max_per_day is None:
        max_per_day = 1

    subjects.append({
        "id": sid, "name": name, "teacher_id": teacher_id,
        "periods_per_week": periods_per_week, "max_per_day": max_per_day,
    })
    session["subjects"] = subjects
    flash("Subject added.", "success")
    return redirect(url_for("index"))


@app.route("/delete/<kind>/<item_id>", methods=["POST"])
def delete_item(kind, item_id):
    if kind == "subject":
        session["subjects"] = [i for i in session.get("subjects", []) if i["id"] != item_id]
        flash("Subject removed.", "success")
    elif kind == "teacher":
        dependents = [s["name"] for s in session.get("subjects", []) if s["teacher_id"] == item_id]
        if dependents:
            return fail(
                f"Can't remove teacher '{item_id}': still assigned to "
                f"{', '.join(dependents)}. Remove or reassign those subjects first."
            )
        session["teachers"] = [i for i in session.get("teachers", []) if i["id"] != item_id]
        absences = session.get("absences", {})
        absences.pop(item_id, None)
        session["absences"] = absences
        flash("Teacher removed.", "success")
    else:
        abort(404)
    return redirect(url_for("index"))


@app.route("/absences/update", methods=["POST"])
def update_absences():
    """Checkboxes named absent_<teacher_id>, values are day names."""
    teachers = session.get("teachers", [])
    absences = {}
    for t in teachers:
        chosen = [d for d in request.form.getlist(f"absent_{t['id']}") if d in DAYS]
        if chosen:
            absences[t["id"]] = chosen
    session["absences"] = absences
    flash("Absences updated. Regenerate the timetable to apply changes.", "info")
    return redirect(url_for("index"))


@app.route("/generate", methods=["POST"])
def generate():
    standard = session.get("standard", {"name": "Untitled Standard", "periods_per_day": 6})
    teachers = session.get("teachers", [])
    subjects = session.get("subjects", [])
    absences_raw = session.get("absences", {})
    absences = {tid: set(days) for tid, days in absences_raw.items()}

    if not subjects:
        return fail("Add at least one subject before generating a timetable.")
    if not teachers:
        return fail("Add at least one teacher before generating a timetable.")

    try:
        scheduler = StandardTimetableScheduler(
            standard_name=standard["name"],
            days=DAYS,
            periods_per_day=standard["periods_per_day"],
            subjects=subjects,
            teachers=teachers,
            absences=absences,
            default_max_per_day=1,
        )
    except SchedulerInputError as e:
        # Defense-in-depth: the UI already blocks duplicate IDs and dangling
        # teacher references at add-time, but session data could still end up
        # inconsistent (e.g. edited externally), so the scheduler re-checks.
        return fail(f"Timetable data is invalid: {e}")

    scheduler.solve(time_limit_seconds=15)
    grid = scheduler.get_timetable()
    status_name = scheduler.status_name()

    if grid is None:
        if scheduler.is_model_invalid():
            # Should not happen with valid input — this points at a bug in the
            # solver's constraint-building code, not something the user can fix.
            app.logger.error(
                "CP-SAT reported MODEL_INVALID for standard=%r: %s",
                standard.get("name"), scheduler.validation_errors(),
            )
            flash(
                "Something went wrong building the schedule internally "
                "(model was rejected as invalid). This isn't caused by your data — "
                "please report this issue.",
                "danger",
            )
            return redirect(url_for("index"))

        if scheduler.is_inconclusive():
            flash(
                f"The solver timed out before it could determine whether a valid "
                f"timetable exists (status: {status_name}). This usually means the "
                f"problem is large or tightly constrained — try again, or simplify "
                f"the inputs (fewer subjects/periods) and regenerate.",
                "warning",
            )
            return redirect(url_for("index"))

        # Proven INFEASIBLE — give a targeted hint when the cause is obvious.
        total_needed = sum(s["periods_per_week"] for s in subjects)
        total_slots = standard["periods_per_day"] * len(DAYS)
        hints = []
        if total_needed > total_slots:
            hints.append(
                f"total periods/week requested ({total_needed}) exceeds available "
                f"slots ({total_slots} = {len(DAYS)} days × {standard['periods_per_day']} periods/day)"
            )
        fully_absent = [t["id"] for t in teachers if len(absences.get(t["id"], set())) >= len(DAYS)]
        if fully_absent:
            hints.append(f"teacher(s) {', '.join(fully_absent)} are marked absent every day")
        hint_text = f" Likely cause: {'; '.join(hints)}." if hints else ""
        flash(
            f"No feasible timetable exists for these inputs (solver status: {status_name})."
            f"{hint_text} Try reducing periods/week per subject, raising periods/day, "
            f"raising max sessions/day, or clearing some teacher absences.",
            "danger",
        )
        return redirect(url_for("index"))

    periods = list(range(1, standard["periods_per_day"] + 1))
    teacher_names = {t["id"]: t["name"] for t in teachers}

    return render_template(
        "results.html",
        standard=standard, grid=grid, days=DAYS, periods=periods,
        status_name=status_name, absent_today=absences_raw, teacher_names=teacher_names,
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

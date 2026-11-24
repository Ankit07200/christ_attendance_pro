"""CHRIST Central Campus • Smart Attendance Pro.

Run with: streamlit run app.py
Requirements: streamlit, pandas, plotly, openpyxl

The existing university_attendance.db is kept. Any records from the previous
`attendance` table are copied once into `attendance_entries` (Lecture 1).
"""

from __future__ import annotations

import io
import math
import re
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import date, timedelta
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ─────────────────────── Configuration ───────────────────────────
APP_DIR = Path(__file__).resolve().parent
DB_FILE = APP_DIR / "university_attendance.db"
DEPARTMENTS = [
    "Computer Science", "Data Science", "Economics", "Commerce", "Law",
    "Psychology", "Business Administration",
]
DEFAULT_COURSES = [
    ("CS101 - Data Structures", "Computer Science", 0),
    ("CS102 - Database Systems", "Computer Science", 0),
    ("DS201 - Machine Learning", "Data Science", 0),
    ("EC201 - Microeconomics", "Economics", 0),
    ("BA301 - Business Analytics", "Business Administration", 0),
    ("PY201 - Cognitive Psychology", "Psychology", 0),
]
GOLD = "#f4c46a"
BLUE = "#74aaff"
TEAL = "#4dd4bb"
RED = "#ff798c"
MUTED = "#9fb0cd"
BG = "#09142b"
NAVIGATION = {
    "Overview": "📊",
    "Mark attendance": "✅",
    "Students": "👥",
    "Courses": "📚",
    "Reports": "📈",
    "Settings & backup": "⚙️",
}


# ───────────────────────── Database ─────────────────────────────
@contextmanager
def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database():
    """Make additive schema changes and import old attendance only once."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS students (
                roll_no TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                semester INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS courses (
                course TEXT PRIMARY KEY,
                department TEXT NOT NULL,
                semester INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS attendance_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roll_no TEXT NOT NULL,
                course TEXT NOT NULL,
                att_date TEXT NOT NULL,
                session INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL CHECK(status IN ('Present', 'Absent')),
                UNIQUE(roll_no, course, att_date, session),
                FOREIGN KEY(roll_no) REFERENCES students(roll_no) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_attendance_date
                ON attendance_entries(att_date, course, session);
            CREATE INDEX IF NOT EXISTS idx_attendance_student
                ON attendance_entries(roll_no);
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL
            );
        """)
        conn.executemany(
            "INSERT OR IGNORE INTO courses(course, department, semester) VALUES(?, ?, ?)",
            DEFAULT_COURSES,
        )
        has_old_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attendance'"
        ).fetchone()
        migrated = conn.execute(
            "SELECT 1 FROM app_settings WHERE setting_key='legacy_migrated'"
        ).fetchone()
        if not migrated:
            if has_old_table:
                # Import only valid students/statuses; old table remains untouched.
                conn.execute("""
                    INSERT OR IGNORE INTO attendance_entries
                        (roll_no, course, att_date, session, status)
                    SELECT a.roll_no, a.course, a.att_date, 1, a.status
                    FROM attendance a JOIN students s ON a.roll_no=s.roll_no
                    WHERE a.status IN ('Present', 'Absent')
                """)
                old_courses = conn.execute(
                    "SELECT DISTINCT course FROM attendance"
                ).fetchall()
                conn.executemany(
                    "INSERT OR IGNORE INTO courses(course, department, semester) "
                    "VALUES(?, 'General', 0)",
                    old_courses,
                )
            conn.execute(
                "INSERT INTO app_settings(setting_key, setting_value) "
                "VALUES('legacy_migrated', '1')"
            )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings(setting_key, setting_value) "
            "VALUES('attendance_threshold', '75')"
        )


@st.cache_data(ttl=30, show_spinner=False)
def students_data():
    with db() as conn:
        return pd.read_sql_query(
            "SELECT roll_no, name, department, semester FROM students "
            "ORDER BY name COLLATE NOCASE, roll_no", conn,
        )


@st.cache_data(ttl=30, show_spinner=False)
def courses_data(include_archived: bool = False):
    query = "SELECT course, department, semester, active FROM courses"
    if not include_archived:
        query += " WHERE active=1"
    query += " ORDER BY department, course COLLATE NOCASE"
    with db() as conn:
        return pd.read_sql_query(query, conn)


@st.cache_data(ttl=30, show_spinner=False)
def entries_data():
    with db() as conn:
        return pd.read_sql_query("""
            SELECT a.roll_no, s.name, s.department, s.semester,
                   a.course, a.att_date, a.session, a.status
            FROM attendance_entries a
            JOIN students s ON a.roll_no = s.roll_no
            ORDER BY a.att_date DESC, a.session DESC, s.name COLLATE NOCASE
        """, conn)


def clear_caches():
    students_data.clear()
    courses_data.clear()
    entries_data.clear()


def saved_threshold():
    with db() as conn:
        value = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key='attendance_threshold'"
        ).fetchone()
    return int(value[0]) if value else 75


def save_threshold(value):
    with db() as conn:
        conn.execute(
            "INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
            ("attendance_threshold", str(value)),
        )


def register_student(roll, name, department, semester):
    roll, name = roll.strip().upper(), " ".join(name.split())
    if not re.fullmatch(r"[A-Z0-9_/-]{2,30}", roll):
        raise ValueError("Roll number must be 2–30 characters (letters, digits, _, /, -).")
    if len(name) < 2 or len(name) > 100:
        raise ValueError("Enter a student name between 2 and 100 characters.")
    if department not in DEPARTMENTS or not 1 <= int(semester) <= 12:
        raise ValueError("Invalid department or semester.")
    with db() as conn:
        conn.execute(
            "INSERT INTO students(roll_no,name,department,semester) VALUES(?,?,?,?)",
            (roll, name, department, int(semester)),
        )
    clear_caches()


def update_student(roll, name, department, semester):
    name = " ".join(name.split())
    if len(name) < 2 or len(name) > 100:
        raise ValueError("Enter a student name between 2 and 100 characters.")
    if department not in DEPARTMENTS or not 1 <= int(semester) <= 12:
        raise ValueError("Invalid department or semester.")
    with db() as conn:
        conn.execute(
            "UPDATE students SET name=?, department=?, semester=? WHERE roll_no=?",
            (name, department, int(semester), roll),
        )
    clear_caches()


def remove_student(roll):
    with db() as conn:
        conn.execute("DELETE FROM attendance_entries WHERE roll_no=?", (roll,))
        # Legacy tables may have a restrictive foreign key to students.
        has_old = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attendance'"
        ).fetchone()
        if has_old:
            conn.execute("DELETE FROM attendance WHERE roll_no=?", (roll,))
        conn.execute("DELETE FROM students WHERE roll_no=?", (roll,))
    clear_caches()


def add_course(code, title, department, semester):
    code = code.strip().upper()
    title = " ".join(title.split())
    if not re.fullmatch(r"[A-Z0-9_-]{2,16}", code):
        raise ValueError("Course code must be 2–16 letters/digits, underscores or hyphens.")
    if not 3 <= len(title) <= 80:
        raise ValueError("Course title must be 3–80 characters.")
    if department not in DEPARTMENTS and department != "General":
        raise ValueError("Invalid department.")
    if not 0 <= int(semester) <= 12:
        raise ValueError("Invalid semester.")
    with db() as conn:
        conn.execute(
            "INSERT INTO courses(course,department,semester) VALUES(?,?,?)",
            (f"{code} - {title}", department, int(semester)),
        )
    clear_caches()


def set_course_active(course, active):
    with db() as conn:
        conn.execute("UPDATE courses SET active=? WHERE course=?", (int(active), course))
    clear_caches()


def save_register(course, attendance_date, session, records):
    """Replace one complete roster's entries atomically for this lecture."""
    if not records:
        raise ValueError("There is no student roster to save.")
    if any(status not in ("Present", "Absent") for _, status in records):
        raise ValueError("All students must be marked Present or Absent.")
    with db() as conn:
        conn.executemany(
            "DELETE FROM attendance_entries "
            "WHERE roll_no=? AND course=? AND att_date=? AND session=?",
            [(roll, course, attendance_date, int(session)) for roll, _ in records],
        )
        conn.executemany("""
            INSERT INTO attendance_entries(roll_no,course,att_date,session,status)
            VALUES(?,?,?,?,?)
        """, [(roll, course, attendance_date, int(session), status)
              for roll, status in records])
    clear_caches()


def import_students_csv(data: pd.DataFrame):
    """Validate every row before writing. Existing rolls are skipped."""
    standard = {
        "roll no": "roll_no", "roll number": "roll_no", "roll_no": "roll_no",
        "name": "name", "student name": "name", "department": "department",
        "semester": "semester",
    }
    df = data.rename(columns={
        col: standard.get(str(col).strip().lower(), str(col).strip().lower())
        for col in data.columns
    }).copy()
    expected = ["roll_no", "name", "department", "semester"]
    if not all(col in df.columns for col in expected):
        raise ValueError("CSV needs columns: roll_no, name, department, semester.")
    if df.empty:
        raise ValueError("The CSV has no student rows.")
    rows, errors, seen = [], [], set()
    for line, (_, row) in enumerate(df.iterrows(), start=2):
        roll = str(row["roll_no"]).strip().upper()
        name = " ".join(str(row["name"]).split())
        dept = str(row["department"]).strip()
        sem_str = str(row["semester"]).strip()
        if not re.fullmatch(r"[A-Z0-9_/-]{2,30}", roll):
            errors.append(f"Row {line}: invalid roll number")
        elif not 2 <= len(name) <= 100:
            errors.append(f"Row {line}: invalid name")
        elif dept not in DEPARTMENTS:
            errors.append(f"Row {line}: unknown department '{dept}'")
        elif not sem_str.isdigit() or not 1 <= int(sem_str) <= 12:
            errors.append(f"Row {line}: invalid semester")
        elif roll in seen:
            errors.append(f"Row {line}: duplicate roll number in CSV")
        else:
            seen.add(roll)
            rows.append((roll, name, dept, int(sem_str)))
    if errors:
        raise ValueError("Please fix the CSV before importing: " + "; ".join(errors[:6])
                         + (f"; and {len(errors) - 6} more" if len(errors) > 6 else ""))
    with db() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO students(roll_no,name,department,semester) "
            "VALUES(?,?,?,?)", rows,
        )
        added = conn.total_changes - before
    clear_caches()
    return added, len(rows) - added


def import_attendance_csv(data: pd.DataFrame):
    """Validate an entire CSV before inserting; skip existing lecture entries safely."""
    aliases = {
        "roll number": "roll_no", "roll no": "roll_no", "date": "att_date",
        "lecture": "session", "period": "session", "attendance status": "status",
    }
    df = data.rename(columns={c: aliases.get(str(c).strip().lower(), str(c).strip().lower())
                              for c in data.columns}).copy()
    required = ["roll_no", "course", "att_date", "session", "status"]
    if any(c not in df.columns for c in required):
        raise ValueError("Attendance CSV requires: roll_no, course, att_date, session, status.")
    if df.empty:
        raise ValueError("Attendance CSV is empty.")
    if len(df) > 100000:
        raise ValueError("Import up to 100,000 attendance entries at a time.")
    with db() as conn:
        valid_students = {r[0] for r in conn.execute("SELECT roll_no FROM students")}
        known_courses = {r[0] for r in conn.execute("SELECT course FROM courses")}
    rows, errors, seen = [], [], set()
    for line, (_, rec) in enumerate(df.iterrows(), 2):
        roll = str(rec["roll_no"]).strip().upper()
        course = str(rec["course"]).strip()
        d = str(rec["att_date"]).strip()
        session = str(rec["session"]).strip()
        status = str(rec["status"]).strip().capitalize()
        try:
            from datetime import datetime
            checked_date = datetime.strptime(d, "%Y-%m-%d").date()
            date_valid = checked_date.isoformat() == d
        except ValueError:
            date_valid = False
        if roll not in valid_students:
            errors.append(f"Row {line}: student {roll} does not exist (import students first)")
        elif course not in known_courses:
            errors.append(f"Row {line}: unknown course '{course}'")
        elif not date_valid:
            errors.append(f"Row {line}: date must be YYYY-MM-DD")
        elif not session.isdigit() or not 1 <= int(session) <= 20:
            errors.append(f"Row {line}: session must be 1–20")
        elif status not in ("Present", "Absent"):
            errors.append(f"Row {line}: status must be Present or Absent")
        else:
            key = (roll, course, d, int(session))
            if key in seen:
                errors.append(f"Row {line}: duplicate student/course/date/session in this CSV")
            else:
                seen.add(key)
                rows.append((*key, status))
    if errors:
        raise ValueError("Please fix the CSV: " + "; ".join(errors[:5]) +
                         (f"; and {len(errors)-5} more" if len(errors)>5 else ""))
    with db() as conn:
        before = conn.total_changes
        conn.executemany("""
            INSERT OR IGNORE INTO attendance_entries
            (roll_no,course,att_date,session,status) VALUES(?,?,?,?,?)
        """, rows)
        inserted = conn.total_changes - before
    clear_caches()
    return inserted, len(rows) - inserted


# ─────────────────────── Calculations ───────────────────────────
def select_students(df, department="All departments", semester="All semesters", search=""):
    selected = df.copy()
    if department != "All departments":
        selected = selected[selected["department"] == department]
    if semester != "All semesters":
        selected = selected[selected["semester"] == int(semester)]
    if search:
        mask = (selected["roll_no"].str.contains(search, case=False, regex=False) |
                selected["name"].str.contains(search, case=False, regex=False))
        selected = selected[mask]
    return selected


def select_entries(df, department="All departments", course="All courses",
                   start=None, end=None):
    selected = df.copy()
    if department != "All departments":
        selected = selected[selected["department"] == department]
    if course != "All courses":
        selected = selected[selected["course"] == course]
    if start is not None:
        selected = selected[selected["att_date"] >= str(start)]
    if end is not None:
        selected = selected[selected["att_date"] <= str(end)]
    return selected


def calculate_summary(students, entries, threshold):
    """No records is not zero attendance; recorded absences do count as zero."""
    labels = {
        "roll_no": "Roll Number", "name": "Student Name",
        "department": "Department", "semester": "Semester",
    }
    base = students.rename(columns=labels).copy()
    if base.empty:
        return pd.DataFrame(columns=[
            "Roll Number", "Student Name", "Department", "Semester",
            "Total Classes", "Classes Attended", "Classes Missed", "Attendance %",
            "Standing", "Classes to Recover", "Safe Absences",
        ])
    if entries.empty:
        counts = pd.DataFrame(columns=["roll_no", "Total Classes", "Classes Attended"])
    else:
        counts = entries.groupby("roll_no", as_index=False).agg(
            **{"Total Classes": ("status", "size"),
               "Classes Attended": ("status", lambda s: int(s.eq("Present").sum()))}
        )
    summary = base.merge(counts, left_on="Roll Number", right_on="roll_no", how="left")
    summary = summary.drop(columns=["roll_no"], errors="ignore")
    for col in ["Total Classes", "Classes Attended"]:
        summary[col] = pd.to_numeric(summary[col], errors="coerce").fillna(0).astype(int)
    summary["Classes Missed"] = summary["Total Classes"] - summary["Classes Attended"]
    summary["Attendance %"] = (
        summary["Classes Attended"].div(summary["Total Classes"].replace(0, float("nan")))
        .mul(100).round(1).fillna(0.0)
    )

    def standing(row):
        if row["Total Classes"] == 0:
            return "No records"
        return "At risk" if 100 * row["Classes Attended"] < threshold * row["Total Classes"] else "On track"

    summary["Standing"] = summary.apply(standing, axis=1)
    summary["Classes to Recover"] = summary.apply(
        lambda row: max(0, math.ceil((threshold * row["Total Classes"] -
                                      100 * row["Classes Attended"]) / (100 - threshold)))
        if row["Total Classes"] else 0,
        axis=1,
    )
    summary["Safe Absences"] = summary.apply(
        lambda row: max(0, (100 * row["Classes Attended"]) // threshold - row["Total Classes"])
        if row["Total Classes"] and row["Standing"] == "On track" else 0,
        axis=1,
    ).astype(int)
    return summary


def daily_trend(entries):
    if entries.empty:
        return pd.DataFrame(columns=["Date", "Attendance %", "Records"])
    chart = entries.groupby("att_date", as_index=False).agg(
        Records=("status", "size"),
        Present=("status", lambda s: int(s.eq("Present").sum())),
    )
    chart["Attendance %"] = (chart["Present"] / chart["Records"] * 100).round(1)
    chart["Date"] = pd.to_datetime(chart["att_date"])
    return chart


def safe_export(df):
    """Prevent spreadsheet formulas from executing in exported user text."""
    output = df.copy()
    for col in output.select_dtypes(include=["object", "string"]).columns:
        output[col] = output[col].map(
            lambda x: "'" + x if isinstance(x, str) and x.lstrip().startswith(("=", "+", "-", "@"))
            else x
        )
    return output


def make_excel(summary, daily):
    """Create a readable two-sheet export with frozen headers and filters."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        safe_export(summary).to_excel(writer, sheet_name="Monthly Summary", index=False)
        safe_export(daily).to_excel(writer, sheet_name="Daily Register", index=False)
        for ws in writer.book.worksheets:
            ws.sheet_view.showGridLines = False
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            ws.row_dimensions[1].height = 28
            for cell in ws[1]:
                cell.fill = PatternFill("solid", fgColor="13264C")
                cell.font = Font(name="Aptos", color="FFFFFF", bold=True, size=11)
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.font = Font(name="Aptos", color="24334F", size=10)
                    cell.alignment = Alignment(vertical="center")
                    if cell.row % 2 == 0:
                        cell.fill = PatternFill("solid", fgColor="EDF3FA")
            for col in ws.columns:
                header = str(col[0].value or "")
                sample = [len(str(c.value or "")) for c in col[:150]]
                width = min(34, max(13, min(34, max(sample, default=0) + 2), len(header) + 2))
                ws.column_dimensions[get_column_letter(col[0].column)].width = width
                if header == "Attendance %":
                    for cell in list(col)[1:]:
                        if isinstance(cell.value, (int, float)):
                            cell.number_format = '0.0"%"'
    output.seek(0)
    return output.getvalue()


def database_backup():
    """Use SQLite's online backup API for a consistent snapshot."""
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "attendance_backup.db"
        with closing(sqlite3.connect(DB_FILE)) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target.read_bytes()


# ─────────────────────────── Design ─────────────────────────────
STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Outfit:wght@500;600;700;800&display=swap');
:root{--gold:#f4c46a;--ice:#eaf1ff;--muted:#9fb0cd;--panel:#121f3c;}
html,body,[class*="css"],.stApp{font-family:'DM Sans',sans-serif;}
[data-testid="stAppViewContainer"]{background:radial-gradient(circle at 87% 0%,#14274b 0%,#09142b 37%,#071126 100%);color:#eaf1ff;}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#0c1933 0%,#08142b 100%);border-right:1px solid #263653;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{color:#c6d3e9;}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:10px 9px;border-radius:10px;margin:2px 0;}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:#1a2e51;}
.block-container{max-width:1480px;padding-top:2.0rem;padding-bottom:5rem;padding-left:2.3rem;padding-right:2.3rem;}
h1,h2,h3,h4{font-family:'Outfit','DM Sans',sans-serif;color:#f2f6ff!important;letter-spacing:-.035em;}
h1{font-weight:700!important;} h3{letter-spacing:-.02em;}
[data-testid="stCaptionContainer"]{color:#9fb0cd!important;}
[data-testid="stVerticalBlockBorderWrapper"] > div{border-color:#2a3955!important;}
[data-testid="stForm"]{border:1px solid #273955;border-radius:16px;background:rgba(17,32,58,.5);}
[data-testid="stDataFrame"]{border:1px solid #293a55;border-radius:11px;overflow:hidden;}
[data-testid="stWidgetLabel"] p{color:#d8e3f5!important;font-weight:600;}
[data-baseweb="select"]>div,[data-baseweb="input"]>div,textarea{background-color:#11233f!important;border-color:#344767!important;}
[data-testid="stFileUploader"]{background:#11213b;border-radius:12px;}
button[kind="primary"]{background:linear-gradient(110deg,#f5c978,#e6a83e)!important;color:#13223b!important;border:0!important;font-weight:800!important;}
button[kind="primary"]:hover{filter:brightness(1.08);}
[data-testid="stAlert"]{border-radius:13px;}
.brand{display:flex;align-items:center;gap:13px;margin:8px 0 24px;padding:14px 4px;}
.brand-mark{height:46px;width:46px;display:flex;align-items:center;justify-content:center;border-radius:13px;background:linear-gradient(140deg,#f7d792,#d9a647);box-shadow:0 5px 22px #d3a34733;color:#0e213d;font-family:'Outfit',sans-serif;font-size:26px;font-weight:800;}
.brand-name{color:#fff;font-family:'Outfit',sans-serif;font-size:18px;font-weight:800;line-height:1.05;}
.brand-sub{color:#9fb0cd;font-size:11px;letter-spacing:.07em;text-transform:uppercase;margin-top:6px;}
.hero{border:1px solid #34466a;position:relative;overflow:hidden;background:linear-gradient(110deg,#182d52,#122442 64%,#172e53);border-radius:20px;padding:27px 32px;margin-bottom:22px;}
.hero:after{content:'';position:absolute;width:290px;height:290px;right:-90px;top:-140px;border:60px solid #f4c46a13;border-radius:50%;pointer-events:none;}
.eyebrow{color:#f4c46a;font-size:11px;letter-spacing:.16em;font-weight:800;text-transform:uppercase;margin-bottom:9px;}
.hero-title{font-family:'Outfit',sans-serif;font-size:clamp(26px,2.6vw,37px);font-weight:800;letter-spacing:-.045em;line-height:1.1;color:#fff;margin-bottom:9px;}
.hero-sub{font-size:13px;color:#c0cde3;max-width:650px;line-height:1.6;}
.hero-pill{display:inline-block;margin-top:16px;padding:7px 11px;border:1px solid #57704c80;border-radius:20px;background:#22443755;color:#9df0bd;font-size:11px;font-weight:700;}
.kpi{background:linear-gradient(145deg,#162844,#111e38);border:1px solid #2b3d60;border-radius:16px;padding:19px 20px;min-height:130px;position:relative;overflow:hidden;margin-bottom:7px;}
.kpi:after{content:'';position:absolute;width:70px;height:70px;border-radius:50%;background:var(--accent);opacity:.08;right:-24px;top:-22px;}
.kpi-label{font-size:11px;color:#a4b5d0;font-weight:700;letter-spacing:.03em;text-transform:uppercase;}
.kpi-value{font-family:'Outfit',sans-serif;font-size:32px;font-weight:800;letter-spacing:-.04em;color:#f4f7fe;margin-top:12px;}
.kpi-detail{font-size:11px;color:#9daec9;margin-top:4px;}
.section-kicker{font-size:11px;font-weight:800;letter-spacing:.13em;color:#f4c46a;text-transform:uppercase;margin-bottom:4px;}
.section-caption{color:#9baec9;font-size:13px;margin:0 0 13px;}
.info-strip{background:#132740;border:1px solid #28415d;border-left:3px solid #f4c46a;padding:13px 16px;border-radius:11px;color:#c4d2e6;font-size:13px;margin:13px 0;}
.side-note{font-size:12px;line-height:1.7;color:#a7b7d0;padding:12px 4px;}
.pill{display:inline-block;padding:5px 10px;border-radius:999px;border:1px solid #3c5271;background:#172b4a;font-size:11px;color:#cbdaf1;margin:3px 5px 3px 0;}
[data-testid="stHorizontalBlock"]{align-items:stretch;}
@media(max-width:750px){.block-container{padding-left:1rem;padding-right:1rem;padding-top:1.4rem;}.hero{padding:24px 18px;}.kpi{min-height:106px;padding:15px;}.kpi-value{font-size:27px;}}
</style>
"""


def hero(eyebrow, title, subtitle, tag=None):
    pill = f'<div class="hero-pill">● {escape(tag)}</div>' if tag else ""
    st.markdown(
        f'<div class="hero"><div class="eyebrow">{escape(eyebrow)}</div>'
        f'<div class="hero-title">{escape(title)}</div>'
        f'<div class="hero-sub">{escape(subtitle)}</div>{pill}</div>',
        unsafe_allow_html=True,
    )


def kpi(label, value, detail, color=BLUE):
    st.markdown(
        f'<div class="kpi" style="--accent:{color}"><div class="kpi-label">'
        f'{escape(str(label))}</div><div class="kpi-value">{escape(str(value))}</div>'
        f'<div class="kpi-detail">{escape(str(detail))}</div></div>',
        unsafe_allow_html=True,
    )


def section(title, hint=""):
    st.subheader(title)
    if hint:
        st.markdown(f'<div class="section-caption">{escape(hint)}</div>', unsafe_allow_html=True)


def chart_style(fig, height=320):
    fig.update_layout(
        height=height, margin=dict(l=15, r=15, t=20, b=22),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans, sans-serif", color="#c8d5ed", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
        xaxis=dict(showgrid=False, zeroline=False, linecolor="#293c57"),
        yaxis=dict(gridcolor="#273853", zeroline=False, linecolor="#293c57"),
        hoverlabel=dict(bgcolor="#172943", font_color="#ffffff"),
    )
    return fig


def table_summary(df, height=390):
    desired = ["Roll Number", "Student Name", "Department", "Semester",
               "Total Classes", "Classes Attended", "Classes Missed", "Attendance %",
               "Standing", "Classes to Recover", "Safe Absences"]
    visible = [c for c in desired if c in df.columns]
    st.dataframe(
        df[visible], use_container_width=True, hide_index=True, height=height,
        column_config={
            "Attendance %": st.column_config.ProgressColumn(
                "Attendance %", min_value=0, max_value=100, format="%.1f%%"),
            "Classes to Recover": st.column_config.NumberColumn(
                "Classes to Recover", help="Consecutive attended classes needed to reach the target"),
            "Safe Absences": st.column_config.NumberColumn(
                "Safe Absences", help="Possible future absences before dropping below target"),
        },
    )


def flash(message, kind="success"):
    st.session_state["flash_message"] = (kind, message)
    st.rerun()


def optional_flash():
    item = st.session_state.pop("flash_message", None)
    if item:
        kind, message = item
        if kind == "error":
            st.error(message)
        elif kind == "warning":
            st.warning(message)
        else:
            st.success(message)


# ─────────────────────────── Pages ──────────────────────────────
def overview_page(threshold):
    hero("Academic operations / Command center", "Attendance intelligence",
         "Live campus overview, student engagement trends and early academic intervention.",
         "Local database connected")
    students, entries = students_data(), entries_data()
    dept_options = ["All departments"] + DEPARTMENTS
    f1, f2, f3 = st.columns([1.1, 1.1, 1.2])
    with f1:
        dept = st.selectbox("Department", dept_options, key="dashboard_department")
    with f2:
        period = st.selectbox("Time period", ["All time", "Last 30 days", "Last 90 days", "This month"],
                              key="dashboard_period")
    with f3:
        filtered_courses = courses_data(True)
        course_list = ["All courses"] + filtered_courses[
            (filtered_courses["department"] == dept) |
            (dept == "All departments") |
            (filtered_courses["department"] == "General")
        ]["course"].tolist()
        selected_course = st.selectbox("Course", course_list, key="dashboard_course")
    start = None
    if period == "Last 30 days":
        start = date.today() - timedelta(days=29)
    elif period == "Last 90 days":
        start = date.today() - timedelta(days=89)
    elif period == "This month":
        start = date.today().replace(day=1)
    cohort = select_students(students, dept)
    selected = select_entries(entries, dept, selected_course, start)
    summary = calculate_summary(cohort, selected, threshold)
    tracked = summary[summary["Total Classes"] > 0]
    risk = summary[summary["Standing"] == "At risk"]
    untracked = summary[summary["Standing"] == "No records"]
    avg = round(tracked["Attendance %"].mean(), 1) if not tracked.empty else None
    unique_classes = (selected[["course", "att_date", "session", "department", "semester"]].drop_duplicates().shape[0]
                      if not selected.empty else 0)

    m1, m2, m3, m4 = st.columns(4, gap="small")
    with m1: kpi("Registered students", len(summary), "Students in selected cohort", BLUE)
    with m2: kpi("Avg. attendance", f"{avg}%" if avg is not None else "—", "Students with recorded classes", TEAL)
    with m3: kpi("Students at risk", len(risk), f"Below {threshold}% minimum", RED)
    with m4: kpi("Class sessions", unique_classes, f"{len(untracked)} students not yet tracked", GOLD)

    if students.empty:
        st.info("No students yet. Open **Students** to register your first class or import a CSV.")
        return
    if selected.empty:
        st.info("No attendance records match these filters. Open **Mark attendance** to record your first lecture.")

    left, right = st.columns([1.5, 1], gap="large")
    with left:
        with st.container(border=True):
            section("Attendance trend", "Daily recorded attendance rate across the selected period")
            trend = daily_trend(selected)
            if trend.empty:
                st.caption("Trend will appear once classes are recorded.")
            else:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=trend["Date"], y=trend["Attendance %"],
                    mode="lines+markers", line=dict(color=TEAL, width=3, shape="spline"),
                    marker=dict(size=6, color=TEAL),
                    fill="tozeroy", fillcolor="rgba(77,212,187,0.10)",
                    name="Attendance", customdata=trend["Records"],
                    hovertemplate="%{x|%d %b %Y}<br>%{y:.1f}% attendance<br>"
                                  "%{customdata} records<extra></extra>",
                ))
                fig.update_yaxes(range=[0, 105], ticksuffix="%")
                st.plotly_chart(chart_style(fig, 315), use_container_width=True,
                                config={"displayModeBar": False})

        with st.container(border=True):
            section("Department performance", "Average student-level attendance by department")
            if tracked.empty:
                st.caption("Department comparison will appear after attendance is recorded.")
            else:
                by_dept = tracked.groupby("Department", as_index=False)["Attendance %"].mean()
                by_dept = by_dept.sort_values("Attendance %", ascending=True)
                fig = px.bar(by_dept, x="Attendance %", y="Department", orientation="h",
                             text="Attendance %", color_discrete_sequence=[BLUE])
                fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside",
                                  marker_line_width=0, marker_cornerradius=5)
                fig.update_xaxes(range=[0, 108], ticksuffix="%")
                st.plotly_chart(chart_style(fig, 300), use_container_width=True,
                                config={"displayModeBar": False})

    with right:
        with st.container(border=True):
            section("Attendance health", "Distribution of students by academic standing")
            distribution = pd.DataFrame({
                "Standing": ["On track", "At risk", "No records"],
                "Students": [int((summary["Standing"] == s).sum())
                             for s in ["On track", "At risk", "No records"]],
            })
            if distribution["Students"].sum() > 0:
                fig = go.Figure(go.Pie(
                    labels=distribution["Standing"], values=distribution["Students"],
                    hole=.76, marker=dict(colors=[TEAL, RED, "#7185a7"]),
                    textinfo="none", sort=False,
                ))
                fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-.08, x=0),
                                  annotations=[dict(text=f"<b>{len(summary)}</b><br>students",
                                                    x=.5, y=.5, font=dict(size=17, color="#eaf1ff"),
                                                    showarrow=False)])
                st.plotly_chart(chart_style(fig, 308), use_container_width=True,
                                config={"displayModeBar": False})
        with st.container(border=True):
            section("Priority alerts", "Students currently below the attendance threshold")
            if risk.empty:
                st.success("No at-risk students in this selection.")
            else:
                shortlist = risk.sort_values("Attendance %").head(8)
                st.dataframe(
                    shortlist[["Student Name", "Roll Number", "Attendance %", "Classes to Recover"]],
                    use_container_width=True, hide_index=True, height=290,
                    column_config={"Attendance %": st.column_config.ProgressColumn(
                        "Attendance", min_value=0, max_value=100, format="%.1f%%")},
                )
                if len(risk) > 8:
                    st.caption(f"+ {len(risk) - 8} more at-risk students. View the full directory below.")

    section("All student attendance", "Live table showing each imported student's attendance and academic standing")
    table_summary(summary, height=440)
    st.caption("No records = no sessions marked. A recorded 0% is correctly flagged as at risk.")


def attendance_page(threshold):
    hero("Faculty workspace / Attendance register", "Mark attendance",
         "Choose a course, lecture and date. Review existing entries or fill the roster in one action.")
    courses = courses_data()
    students = students_data()
    if courses.empty:
        st.warning("No active courses. Add a course on the Courses page first.")
        return
    if students.empty:
        st.warning("No students registered. Add students before recording attendance.")
        return
    a, b, c = st.columns([1.7, 1, 1])
    with a:
        course = st.selectbox("Course", courses["course"].tolist(), key="register_course")
    with b:
        chosen_date = st.date_input("Class date", value=date.today(),
                                    max_value=date.today(), key="register_date")
    with c:
        lecture = st.number_input("Lecture / period", min_value=1, max_value=20,
                                  step=1, value=1, key="register_lecture")
    selected = courses.loc[courses["course"] == course].iloc[0]
    assigned_dept, assigned_sem = selected["department"], int(selected["semester"])
    if assigned_dept == "General":
        dept = st.selectbox("Department for this class", DEPARTMENTS, key="general_roster_dept")
    else:
        dept = assigned_dept
    semester_list = ([assigned_sem] if assigned_sem else
                     sorted(students[students["department"] == dept]["semester"].unique().tolist()))
    if not semester_list:
        st.warning(f"No students found in {dept}.")
        return
    if len(semester_list) > 1:
        selected_sem = st.selectbox("Class semester", semester_list, key="register_semester")
    else:
        selected_sem = semester_list[0]
        st.caption(f"**{dept}** · Semester {selected_sem}")
    roster = select_students(students, dept, selected_sem)
    if roster.empty:
        st.warning("No students match this course and semester. Add students to continue.")
        return

    previous = select_entries(entries_data(), dept, course, chosen_date, chosen_date)
    previous = previous[previous["session"] == int(lecture)]
    status_map = dict(zip(previous["roll_no"], previous["status"]))
    sheet = roster[["roll_no", "name", "semester"]].rename(columns={
        "roll_no": "Roll Number", "name": "Student Name", "semester": "Semester"
    }).copy()
    sheet["Status"] = sheet["Roll Number"].map(lambda roll: status_map.get(roll, "Unmarked"))

    p1, p2, p3 = st.columns(3)
    with p1: kpi("Students in class", len(sheet), f"{dept} · semester {selected_sem}", BLUE)
    with p2: kpi("Already marked", int(sheet["Status"].ne("Unmarked").sum()),
                 "Existing entries for this lecture", TEAL)
    with p3: kpi("Unmarked", int(sheet["Status"].eq("Unmarked").sum()),
                 "Complete the class before saving", GOLD)

    st.markdown('<div class="info-strip">Select <b>Fill all present</b> for quick roll calls, '
                'then change absentees to <b>Absent</b>. Previously saved attendance loads automatically.</div>',
                unsafe_allow_html=True)
    if st.session_state.pop("reset_bulk_after_save", False):
        st.session_state["attendance_bulk"] = "Keep existing / unmarked"
    quick_fill = st.radio("Quick actions", ["Keep existing / unmarked", "Fill all present", "Fill all absent"],
                          horizontal=True, key="attendance_bulk")
    if quick_fill == "Fill all present":
        sheet["Status"] = "Present"
    elif quick_fill == "Fill all absent":
        sheet["Status"] = "Absent"
    editor_key = "register_editor_" + str(abs(hash((
        course, str(chosen_date), int(lecture), dept, int(selected_sem), quick_fill
    ))))
    with st.form("attendance_form"):
        edited = st.data_editor(
            sheet, use_container_width=True, hide_index=True, height=min(620, 100 + len(sheet) * 35),
            key=editor_key, disabled=["Roll Number", "Student Name", "Semester"],
            column_config={
                "Status": st.column_config.SelectboxColumn(
                    "Attendance status", options=["Unmarked", "Present", "Absent"],
                    help="Set Present or Absent for each student", required=True),
                "Semester": st.column_config.NumberColumn("Semester", format="%d"),
            },
        )
        save = st.form_submit_button("Save complete lecture register", type="primary",
                                     use_container_width=True)
    if save:
        if edited["Status"].eq("Unmarked").any():
            st.error(f"Please mark all {int(edited['Status'].eq('Unmarked').sum())} remaining students.")
        else:
            values = list(zip(edited["Roll Number"].tolist(), edited["Status"].tolist()))
            try:
                save_register(course, str(chosen_date), int(lecture), values)
                present = int(edited["Status"].eq("Present").sum())
                st.session_state["reset_bulk_after_save"] = True
                flash(f"Lecture {lecture} saved: {present} present · {len(values) - present} absent.")
            except (sqlite3.Error, ValueError) as exc:
                st.error(f"Could not save attendance: {exc}")
    st.caption("Multiple lectures on the same date are supported by choosing a different period number. "
               "Re-saving a period updates the existing records, rather than duplicating them.")


def students_page(threshold):
    hero("Registrar / Student lifecycle", "Student directory",
         "Manage students, import entire batches and inspect individual attendance histories.")
    students, entries = students_data(), entries_data()
    tab1, tab2, tab3, tab4 = st.tabs(["Directory & profiles", "Add student", "Import students", "Import attendance"])
    with tab1:
        c1, c2, c3 = st.columns([1.3, 1, 1])
        with c1: search = st.text_input("Search by name or roll no.", placeholder="Search students…")
        with c2: dept = st.selectbox("Department", ["All departments"] + DEPARTMENTS,
                                     key="students_department")
        with c3:
            sem_options = ["All semesters"] + [str(s) for s in range(1, 13)]
            sem = st.selectbox("Semester", sem_options, key="students_semester")
        filtered = select_students(students, dept, sem, search)
        st.caption(f"Showing {len(filtered)} of {len(students)} students")
        st.dataframe(filtered.rename(columns={"roll_no": "Roll Number", "name": "Student Name",
                                                "department": "Department", "semester": "Semester"}),
                     hide_index=True, use_container_width=True, height=350)
        if not filtered.empty:
            section("Student profile", "View and edit a student's information and lecture history")
            roll_options = filtered["roll_no"].tolist()
            chosen = st.selectbox("Select student", roll_options,
                                   format_func=lambda r: f"{r} · {students.loc[students.roll_no == r, 'name'].iloc[0]}",
                                   key="student_profile_roll")
            person = students.loc[students["roll_no"] == chosen].iloc[0]
            personal_entries = entries[entries["roll_no"] == chosen]
            profile_summary = calculate_summary(students[students["roll_no"] == chosen],
                                                personal_entries, threshold).iloc[0]
            k1, k2, k3 = st.columns(3)
            with k1: kpi("Overall attendance", f"{profile_summary['Attendance %']:.1f}%"
                         if profile_summary["Total Classes"] else "—", str(profile_summary["Standing"]), TEAL)
            with k2: kpi("Classes recorded", profile_summary["Total Classes"],
                         f"{profile_summary['Classes Attended']} present", BLUE)
            with k3: kpi("Classes to recover", profile_summary["Classes to Recover"],
                         f"Target: {threshold}%", GOLD)
            edit, history = st.columns([1, 1.4], gap="large")
            with edit:
                with st.container(border=True):
                    with st.form(f"edit_student_{chosen}"):
                        new_name = st.text_input("Full name", value=person["name"])
                        new_dept = st.selectbox("Department", DEPARTMENTS,
                                                index=DEPARTMENTS.index(person["department"])
                                                if person["department"] in DEPARTMENTS else 0)
                        new_sem = st.number_input("Semester", min_value=1, max_value=12,
                                                  value=int(person["semester"]))
                        update = st.form_submit_button("Save student changes", type="primary",
                                                        use_container_width=True)
                    if update:
                        try:
                            update_student(chosen, new_name, new_dept, new_sem)
                            flash(f"Updated profile for {chosen}.")
                        except (ValueError, sqlite3.Error) as exc:
                            st.error(str(exc))
                    with st.expander("Danger zone · delete student"):
                        st.warning("This permanently deletes the student and all linked attendance, including legacy entries.")
                        confirm = st.checkbox(f"I understand and want to delete {chosen}", key=f"delete_ok_{chosen}")
                        if st.button("Permanently delete", disabled=not confirm,
                                     key=f"delete_{chosen}", use_container_width=True):
                            try:
                                remove_student(chosen)
                                flash(f"Student {chosen} deleted.")
                            except sqlite3.Error as exc:
                                st.error(f"Could not delete student: {exc}")
            with history:
                with st.container(border=True):
                    section("Recent attendance", "The most recently recorded lectures for this student")
                    if personal_entries.empty:
                        st.info("This student has no attendance entries.")
                    else:
                        history_table = personal_entries[["att_date", "course", "session", "status"]].copy()
                        history_table.columns = ["Date", "Course", "Lecture", "Status"]
                        st.dataframe(history_table.head(25), use_container_width=True,
                                     hide_index=True, height=290)
    with tab2:
        section("Register a new student", "Add a single learner to the campus directory")
        with st.form("add_student_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                roll = st.text_input("Roll number *", placeholder="e.g. MCA26001", max_chars=30)
                name = st.text_input("Student name *", placeholder="Full name")
            with c2:
                dept_new = st.selectbox("Department *", DEPARTMENTS, key="register_dept")
                sem_new = st.number_input("Semester *", min_value=1, max_value=12, value=1)
            submit = st.form_submit_button("Register student", type="primary", use_container_width=True)
        if submit:
            try:
                register_student(roll, name, dept_new, sem_new)
                flash(f"Student {roll.strip().upper()} registered successfully.")
            except sqlite3.IntegrityError:
                st.error("That roll number already exists.")
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))
    with tab3:
        section("Bulk import", "Upload a CSV with roll_no, name, department and semester")
        sample_path = APP_DIR / "sample_students.csv"
        example = sample_path.read_bytes() if sample_path.exists() else (
            "roll_no,name,department,semester\nMCA26001,Example Student,Computer Science,1\n"
        ).encode("utf-8")
        st.download_button("Download 20-student CSV", example, file_name="sample_students.csv",
                           mime="text/csv", use_container_width=False)
        uploaded = st.file_uploader("Choose CSV file", type=["csv"], key="student_csv")
        if uploaded is not None:
            try:
                uploaded.seek(0)
                preview = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
                st.caption(f"{len(preview)} rows detected · preview below")
                st.dataframe(preview.head(12), hide_index=True, use_container_width=True)
                if st.button("Import validated students", type="primary", key="import_csv"):
                    added, skipped = import_students_csv(preview)
                    flash(f"Imported {added} student(s). Skipped {skipped} existing roll number(s).")
            except (ValueError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError, sqlite3.Error) as exc:
                st.error(f"CSV import failed: {exc}")
        st.caption("Imports are validated before insertion. Existing roll numbers are skipped; current records are preserved.")


    with tab4:
        section("Import attendance CSV", "Upload lecture history after importing student records")
        st.caption("Required columns: roll_no, course, att_date, session, status. "
                   "Dates use YYYY-MM-DD; status is Present or Absent. Existing records are not overwritten.")
        example_path = APP_DIR / "sample_attendance.csv"
        if example_path.exists():
            st.download_button("Download sample attendance CSV", example_path.read_bytes(),
                               file_name="sample_attendance.csv", mime="text/csv")
        uploaded_attendance = st.file_uploader("Choose attendance history CSV", type=["csv"],
                                               key="attendance_csv")
        if uploaded_attendance is not None:
            try:
                preview = pd.read_csv(uploaded_attendance, dtype=str, keep_default_na=False)
                st.caption(f"{len(preview)} lecture records detected")
                st.dataframe(preview.head(15), hide_index=True, use_container_width=True)
                if st.button("Import attendance & refresh dashboard", type="primary",
                             key="import_attendance_csv", use_container_width=True):
                    inserted, skipped = import_attendance_csv(preview)
                    flash(f"Imported {inserted} lecture records. Skipped {skipped} duplicates. "
                          "Open Overview to see updated charts and student attendance.")
            except (ValueError, UnicodeError, pd.errors.ParserError,
                    pd.errors.EmptyDataError, sqlite3.Error) as exc:
                st.error(f"Attendance import failed: {exc}")
        st.info("Students must exist in the directory before their attendance can be imported. "
                "Repeated CSV uploads skip duplicates and preserve any already-saved statuses.")


def courses_page():
    hero("Academic setup / Course catalog", "Course management",
         "Organize your academic roster and keep previous attendance even when courses are archived.")
    all_courses = courses_data(include_archived=True)
    tab1, tab2 = st.tabs(["Course catalog", "Add new course"])
    with tab1:
        section("Course catalog", "Active courses are available in the attendance register")
        if all_courses.empty:
            st.info("No courses have been created yet.")
        else:
            visible = all_courses.copy()
            visible["Semester"] = visible["semester"].map(
                lambda n: "All semesters" if n == 0 else str(n))
            visible["Status"] = visible["active"].map({1: "Active", 0: "Archived"})
            st.dataframe(visible[["course", "department", "Semester", "Status"]].rename(
                columns={"course": "Course", "department": "Department"}),
                hide_index=True, use_container_width=True, height=350)
            choice = st.selectbox("Choose course to manage", all_courses["course"].tolist(),
                                  key="manage_course")
            current = all_courses.loc[all_courses["course"] == choice].iloc[0]
            is_active = bool(current["active"])
            st.markdown(f"<span class='pill'>{escape(current['department'])}</span>"
                        f"<span class='pill'>Semester {current['semester'] or 'all'}</span>"
                        f"<span class='pill'>{'Active' if is_active else 'Archived'}</span>",
                        unsafe_allow_html=True)
            action = "Archive course" if is_active else "Reactivate course"
            st.caption("Archiving hides the course from new attendance registration but keeps historical reports.")
            if st.button(action, key="archive_course_btn"):
                set_course_active(choice, not is_active)
                flash(f"{choice} {'archived' if is_active else 'reactivated'}.")
    with tab2:
        section("Create a course", "Courses can be specific to one semester or shared by a department")
        with st.form("add_course_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                code = st.text_input("Course code *", placeholder="e.g. MCA101", max_chars=16)
                title = st.text_input("Course title *", placeholder="e.g. Advanced Databases")
            with c2:
                dept = st.selectbox("Department *", DEPARTMENTS + ["General"])
                sem = st.selectbox("Semester", ["All semesters"] + list(range(1, 13)))
            submit = st.form_submit_button("Create course", type="primary", use_container_width=True)
        if submit:
            try:
                add_course(code, title, dept, 0 if sem == "All semesters" else int(sem))
                flash("Course added successfully.")
            except sqlite3.IntegrityError:
                st.error("A course with that code and title already exists.")
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))
    st.info("Tip: A General course asks you to choose the department when taking attendance.")


def reports_page(threshold):
    hero("Insights / Administrative reporting", "Monthly reports",
         "Export polished student summaries and full day-by-day attendance registers.")
    students, entries = students_data(), entries_data()
    c1, c2, c3, c4 = st.columns([.7, 1, 1.3, 1.6])
    with c1:
        year = st.number_input("Year", min_value=2020, max_value=2100,
                               value=date.today().year, step=1)
    with c2:
        month_names = list(pd.date_range("2026-01-01", periods=12, freq="MS").strftime("%B"))
        month = st.selectbox("Month", list(range(1, 13)), index=date.today().month - 1,
                             format_func=lambda m: month_names[m - 1])
    with c3:
        dept = st.selectbox("Department", ["All departments"] + DEPARTMENTS,
                            key="report_dept")
    with c4:
        all_courses = courses_data(True)
        course_options = ["All courses"] + all_courses[
            (all_courses["department"] == dept) |
            (dept == "All departments") |
            (all_courses["department"] == "General")
        ]["course"].tolist()
        course = st.selectbox("Course", course_options, key="report_course")

    year_month = f"{int(year):04d}-{int(month):02d}"
    cohort = select_students(students, dept)
    selected = entries[entries["att_date"].str.startswith(year_month)].copy()
    selected = select_entries(selected, dept, course)
    summary = calculate_summary(cohort, selected, threshold)
    daily = selected.rename(columns={"att_date": "Date", "course": "Course",
                                     "session": "Lecture", "roll_no": "Roll Number",
                                     "name": "Student Name", "department": "Department",
                                     "semester": "Semester", "status": "Status"})
    daily = daily[["Date", "Course", "Lecture", "Roll Number", "Student Name",
                   "Department", "Semester", "Status"]]
    at_risk = summary[summary["Standing"] == "At risk"]
    count_sessions = selected[["att_date", "course", "session", "department", "semester"]].drop_duplicates().shape[0]
    r1, r2, r3, r4 = st.columns(4)
    with r1: kpi("Students", len(summary), "In the report filter", BLUE)
    with r2: kpi("Lectures logged", count_sessions, "Distinct class sessions", TEAL)
    with r3: kpi("Attendance entries", len(daily), "Individual student records", GOLD)
    with r4: kpi("At-risk students", len(at_risk), f"Below {threshold}%", RED)

    a, b = st.tabs(["Student summary", "Daily register"])
    with a:
        if summary.empty:
            st.info("No students match this report filter.")
        else:
            table_summary(summary, height=440)
    with b:
        if daily.empty:
            st.info("No class entries for this month.")
        else:
            st.dataframe(daily, use_container_width=True, hide_index=True, height=440)

    section("Export your report", "Downloads include the selected month, department, and course filters")
    left, right, third = st.columns(3)
    with left:
        st.download_button(
            "Download summary CSV", safe_export(summary).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"christ_summary_{year_month}.csv", mime="text/csv",
            use_container_width=True,
        )
    with right:
        st.download_button(
            "Download daily CSV", safe_export(daily).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"christ_daily_{year_month}.csv", mime="text/csv",
            use_container_width=True,
        )
    with third:
        st.download_button(
            "Download styled Excel", make_excel(summary, daily),
            file_name=f"christ_attendance_{year_month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True,
        )
    st.caption("Student attendance is calculated from saved lecture entries. "
               "No entries is reported as 'No records' rather than 0%.")


def settings_page(threshold):
    hero("System / Preferences & data integrity", "Settings & backup",
         "Choose an attendance policy and export a safe copy of your complete local database.")
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        with st.container(border=True):
            section("Attendance policy")
            st.markdown(f"Current reporting threshold: **{threshold}%**")
            if st.button("Save threshold as default", use_container_width=True, type="primary"):
                save_threshold(threshold)
                flash(f"Default attendance threshold saved at {threshold}%.")
            st.caption("Adjust the slider in the sidebar. The selected threshold applies instantly to all pages.")
        with st.container(border=True):
            section("System information")
            st.markdown("**Campus:** CHRIST (Deemed to be University), Central Campus, Bengaluru")
            st.markdown("**Storage:** Local SQLite database")
            st.markdown(f"**Students:** {len(students_data())}")
            st.markdown(f"**Attendance entries:** {len(entries_data())}")
    with right:
        with st.container(border=True):
            section("Database backup", "Download the whole database, including student and attendance history")
            st.download_button(
                "Download SQLite backup", data=database_backup(),
                file_name=f"christ_attendance_backup_{date.today().isoformat()}.db",
                mime="application/octet-stream", type="primary", use_container_width=True,
            )
            st.markdown('<div class="info-strip">For local use, the database is saved next to '
                        '<b>app.py</b>. If deploying to ephemeral hosting, use hosted persistent '
                        'storage before relying on the app for official records.</div>',
                        unsafe_allow_html=True)
        with st.container(border=True):
            section("Data model & guidance")
            st.markdown("- One attendance entry per student, course, date and lecture number.\n"
                        "- Re-saving an existing lecture updates that register.\n"
                        "- Course archiving does not delete history.\n"
                        "- Older `attendance` table records are migrated once into Lecture 1.\n"
                        "- This local prototype does not provide user logins or role permissions.")


def main():
    st.set_page_config(page_title="Attendance Pro | CHRIST Central Campus",
                       page_icon="🎓", layout="wide", initial_sidebar_state="expanded")
    initialize_database()
    st.markdown(STYLE, unsafe_allow_html=True)
    if "threshold" not in st.session_state:
        st.session_state["threshold"] = saved_threshold()
    with st.sidebar:
        local_logo = APP_DIR / "christ_logo.png"
        if local_logo.is_file():
            st.image(str(local_logo), width=84)
        st.markdown('''<div class="brand"><div class="brand-mark">C</div><div>
            <div class="brand-name">CHRIST<br>ATTENDANCE</div>
            <div class="brand-sub">Central Campus · Bengaluru</div></div></div>''',
                    unsafe_allow_html=True)
        st.caption("WORKSPACE")
        page = st.radio("Navigation", list(NAVIGATION), label_visibility="collapsed",
                        format_func=lambda name: f"{NAVIGATION[name]}  {name}")
        st.divider()
        st.caption("ACADEMIC POLICY")
        threshold = st.slider("Minimum attendance", min_value=50, max_value=95,
                              step=5, key="threshold", help="Flag students below this value")
        st.markdown(f'<div class="side-note"><b>{threshold}% requirement</b><br>'
                    'Use this target to find students who need additional classes.</div>',
                    unsafe_allow_html=True)
        st.divider()
        st.caption("🎓 CHRIST · Student-built prototype")
        with db() as demo_conn:
            demo_exists = demo_conn.execute(
                "SELECT 1 FROM app_settings WHERE setting_key='demo_seeded'"
            ).fetchone()
        if demo_exists:
            st.caption("🧪 DEMO DATA · 20 fictional students and simulated attendance")
        st.caption(f"{date.today():%A, %d %B %Y}")
    optional_flash()
    if page == "Overview":
        overview_page(threshold)
    elif page == "Mark attendance":
        attendance_page(threshold)
    elif page == "Students":
        students_page(threshold)
    elif page == "Courses":
        courses_page()
    elif page == "Reports":
        reports_page(threshold)
    elif page == "Settings & backup":
        settings_page(threshold)


if __name__ == "__main__":
    main()

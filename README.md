## Ready-to-view demo dashboard

This download already contains **20 fictional student profiles** and **380 simulated attendance records** in `university_attendance.db`. Start the app and open **Overview**: the KPIs, attendance trends, department breakdown, risk alerts, student directory, and report pages are populated immediately. The same records are supplied as `sample_students.csv` and `sample_attendance.csv` for inspection or reuse. All names and attendance are fictional demonstration data.

**Existing database users:** The bundled database is demonstration-only. Do **not** overwrite your actual `university_attendance.db` with it. For an existing installation, upload `sample_students.csv` in **Students → Import students** to add the 20 profiles; that import does not add attendance history. To inspect both sample students and history without replacing your records, run this download in a separate folder.

# CHRIST Smart Attendance Pro

A redesigned Streamlit attendance tracker for CHRIST (Deemed to be University), Central Campus, Bengaluru.

## Run

Requires Python 3.10 or later.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the local Streamlit URL shown in the terminal (normally http://localhost:8501). On Windows you can also double-click `run_windows.bat` after the dependencies have been installed.

## Bring your previous database

If you already use the original app, place your **existing** `university_attendance.db` alongside `app.py` before running this version. The app **preserves the original `attendance` table during migration**, copies its saved records into a new `attendance_entries` table the first time it runs, and treats them as Lecture 1. Your students and old records are retained.

For safety, keep a copy of your original `.db` file before upgrading. Deleting a student from the new app also removes that student's entries from the old table, to honor foreign-key constraints.

## Features

- Polished responsive dark dashboard with contrast-safe charts and UI
- Campus-branded navigation, optional local `christ_logo.png`
- Department and time filtering, trend and risk analytics
- Daily lecture attendance with prefilled existing statuses and quick-fill actions
- Multiple lecture numbers per course per day
- Student add/edit/delete, live search, semester filter, and validated CSV bulk import
- Course creation, archiving and restoration
- Monthly reports with full history, two CSV downloads and styled two-sheet Excel
- Configurable attendance threshold, recovery-class calculations, safe absence estimates
- SQLite backup download and one-time legacy migration

## Optional logo

Place your permitted CHRIST logo image at `christ_logo.png` in the same folder as `app.py`. The sidebar shows it automatically. If not present, a built-in monogram is shown.

## Data and deployment notes

This project stores data in local SQLite. On hosts with **ephemeral storage**, attendance can be lost on redeploy or restart. Use persistent storage or a hosted database for production use. This sample app does not implement teacher/student authentication or access control, so protect the deployed URL and add authorization before use with actual institutional records.

**Attendance definition:** Each saved `Present` or `Absent` entry counts as one student-course-date-lecture record. "No records" is treated distinctly from 0% attendance. Recovery counts assume the student attends all next lectures; safe-absence estimates assume the student is absent for those next lectures and their other attendance stays unchanged.


## Import both CSV files into your existing app

1. Open **Students → Import students** and upload `sample_students.csv` (20 students). Click **Import validated students**.
2. Open **Students → Import attendance** and upload `sample_attendance.csv` (380 records). Click **Import attendance & refresh dashboard**.
3. Open **Overview** and select **All departments**, **All time**, **All courses**. Scroll to **All student attendance** to see everyone.
4. If running the bundled preloaded database, the sample CSVs were already imported. Re-importing skips duplicates.

Both imports validate rows and leave existing records intact. The 20 student profiles and 380 lecture entries are fictional demonstration data, not actual university records.

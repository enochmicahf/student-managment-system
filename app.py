from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "complaints.db"
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024


STATUS_FLOW = ["Submitted", "Under Review", "In Progress", "Resolved", "Closed"]


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student', 'staff', 'admin')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            attachment TEXT,
            status TEXT NOT NULL DEFAULT 'Submitted',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL,
            updater_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            action_taken TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (complaint_id) REFERENCES complaints(id),
            FOREIGN KEY (updater_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comments TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (complaint_id) REFERENCES complaints(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    db.commit()


@app.before_request
def before_request() -> None:
    init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Access denied.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def notify_user(user_id: int, message: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO notifications(user_id, message, created_at) VALUES (?, ?, ?)",
        (user_id, message, now()),
    )
    db.commit()


@app.route("/")
def index():
    return redirect(url_for("dashboard")) if session.get("user_id") else render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "student")

        if not full_name or not email or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users(full_name, email, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                (full_name, email, generate_password_hash(password), role, now()),
            )
            db.commit()
            flash("Account created successfully. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already exists.", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid credentials.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    role = session["role"]

    if role == "student":
        complaints = db.execute(
            "SELECT * FROM complaints WHERE user_id = ? ORDER BY created_at DESC", (uid,)
        ).fetchall()
    else:
        complaints = db.execute(
            """
            SELECT c.*, u.full_name FROM complaints c
            JOIN users u ON u.id = c.user_id
            ORDER BY c.created_at DESC
            """
        ).fetchall()

    notifications = db.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (uid,)
    ).fetchall()

    summary = db.execute(
        "SELECT status, COUNT(*) count FROM complaints GROUP BY status"
    ).fetchall()

    return render_template(
        "dashboard.html",
        complaints=complaints,
        notifications=notifications,
        summary=summary,
        role=role,
    )


@app.route("/complaints/new", methods=["GET", "POST"])
@login_required
@role_required("student")
def submit_complaint():
    if request.method == "POST":
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        file = request.files.get("attachment")
        attachment_path = None

        if file and file.filename:
            safe_name = f"{int(datetime.utcnow().timestamp())}_{secure_filename(file.filename)}"
            saved_path = UPLOAD_FOLDER / safe_name
            file.save(saved_path)
            attachment_path = safe_name

        if not category or not description:
            flash("Category and description are required.", "danger")
            return render_template("new_complaint.html")

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO complaints(user_id, category, description, attachment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session["user_id"], category, description, attachment_path, now(), now()),
        )
        complaint_id = cursor.lastrowid
        db.execute(
            "INSERT INTO updates(complaint_id, updater_id, status, action_taken, created_at) VALUES (?, ?, ?, ?, ?)",
            (complaint_id, session["user_id"], "Submitted", "Complaint submitted by student", now()),
        )

        staff_users = db.execute("SELECT id FROM users WHERE role IN ('staff','admin')").fetchall()
        for user in staff_users:
            db.execute(
                "INSERT INTO notifications(user_id, message, created_at) VALUES (?, ?, ?)",
                (user["id"], f"New complaint #{complaint_id} has been submitted.", now()),
            )

        db.commit()
        flash("Complaint submitted successfully.", "success")
        return redirect(url_for("track_complaint", complaint_id=complaint_id))

    return render_template("new_complaint.html")


@app.route("/complaints/<int:complaint_id>")
@login_required
def track_complaint(complaint_id: int):
    db = get_db()
    complaint = db.execute(
        """
        SELECT c.*, u.full_name, u.id as owner_id FROM complaints c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = ?
        """,
        (complaint_id,),
    ).fetchone()

    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("dashboard"))

    if session["role"] == "student" and complaint["owner_id"] != session["user_id"]:
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    updates = db.execute(
        """
        SELECT u.*, us.full_name FROM updates u
        JOIN users us ON us.id = u.updater_id
        WHERE complaint_id = ?
        ORDER BY created_at ASC
        """,
        (complaint_id,),
    ).fetchall()

    existing_feedback = db.execute(
        "SELECT * FROM feedback WHERE complaint_id = ? AND user_id = ?",
        (complaint_id, session["user_id"]),
    ).fetchone()

    return render_template(
        "track_complaint.html",
        complaint=complaint,
        updates=updates,
        status_flow=STATUS_FLOW,
        existing_feedback=existing_feedback,
    )


@app.route("/complaints/<int:complaint_id>/update", methods=["POST"])
@login_required
@role_required("staff", "admin")
def update_complaint(complaint_id: int):
    status = request.form.get("status", "Submitted")
    action_taken = request.form.get("action_taken", "").strip()
    if status not in STATUS_FLOW or not action_taken:
        flash("Valid status and action taken are required.", "danger")
        return redirect(url_for("track_complaint", complaint_id=complaint_id))

    db = get_db()
    complaint = db.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("dashboard"))

    db.execute(
        "UPDATE complaints SET status = ?, updated_at = ? WHERE id = ?",
        (status, now(), complaint_id),
    )
    db.execute(
        "INSERT INTO updates(complaint_id, updater_id, status, action_taken, created_at) VALUES (?, ?, ?, ?, ?)",
        (complaint_id, session["user_id"], status, action_taken, now()),
    )
    db.commit()

    notify_user(
        complaint["user_id"], f"Complaint #{complaint_id} updated to '{status}'. Action: {action_taken}"
    )

    flash("Complaint updated successfully.", "success")
    return redirect(url_for("track_complaint", complaint_id=complaint_id))


@app.route("/complaints/<int:complaint_id>/feedback", methods=["POST"])
@login_required
@role_required("student")
def submit_feedback(complaint_id: int):
    rating = int(request.form.get("rating", "0"))
    comments = request.form.get("comments", "").strip()

    if rating < 1 or rating > 5:
        flash("Please choose a rating between 1 and 5.", "danger")
        return redirect(url_for("track_complaint", complaint_id=complaint_id))

    db = get_db()
    complaint = db.execute(
        "SELECT * FROM complaints WHERE id = ? AND user_id = ?",
        (complaint_id, session["user_id"]),
    ).fetchone()
    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("dashboard"))

    already_sent = db.execute(
        "SELECT id FROM feedback WHERE complaint_id = ? AND user_id = ?",
        (complaint_id, session["user_id"]),
    ).fetchone()

    if already_sent:
        flash("Feedback already submitted.", "warning")
    else:
        db.execute(
            "INSERT INTO feedback(complaint_id, user_id, rating, comments, created_at) VALUES (?, ?, ?, ?, ?)",
            (complaint_id, session["user_id"], rating, comments, now()),
        )
        db.commit()
        flash("Thank you for your feedback.", "success")

    return redirect(url_for("track_complaint", complaint_id=complaint_id))


@app.route("/reports")
@login_required
@role_required("staff", "admin")
def reports():
    db = get_db()
    by_category = db.execute(
        "SELECT category, COUNT(*) as count FROM complaints GROUP BY category ORDER BY count DESC"
    ).fetchall()
    by_status = db.execute(
        "SELECT status, COUNT(*) as count FROM complaints GROUP BY status"
    ).fetchall()

    response_time = db.execute(
        """
        SELECT ROUND(AVG((julianday(updated_at) - julianday(created_at)) * 24), 2) AS avg_hours
        FROM complaints
        WHERE status IN ('Resolved', 'Closed')
        """
    ).fetchone()

    feedback_summary = db.execute(
        "SELECT ROUND(AVG(rating), 2) as avg_rating, COUNT(*) as total_feedback FROM feedback"
    ).fetchone()

    return render_template(
        "reports.html",
        by_category=by_category,
        by_status=by_status,
        response_time=response_time,
        feedback_summary=feedback_summary,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

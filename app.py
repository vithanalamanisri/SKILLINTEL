# =============================================================================
#  SKILLINTEL BACKEND — app.py
#  SIH26135 · AI-Powered Skill Training Outcome Intelligence
#  Python 3.11+ compatible · No pandas/numpy/sklearn (Windows-safe)
#  Render + Local deployment ready
# =============================================================================

import os
import io
import csv
import json
import math
import random
import secrets
import sqlite3
import datetime as dt
from functools import wraps
from collections import defaultdict, Counter

from flask import (
    Flask, request, jsonify, g, session, send_file, render_template_string
)
from werkzeug.security import generate_password_hash, check_password_hash

# =============================================================================
#  SECTION 0 — APP CONFIG & CONSTANTS
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Render persistent disk support: set DATA_DIR=/data on Render (with a disk mounted)
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "skillintel.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)

# --- Persistent secret key (survives restarts on Render) ---
_secret_file = os.path.join(DATA_DIR, ".secret_key")
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
elif os.path.exists(_secret_file):
    with open(_secret_file, "r") as f:
        app.secret_key = f.read().strip()
else:
    _k = secrets.token_hex(32)
    try:
        with open(_secret_file, "w") as f:
            f.write(_k)
    except Exception:
        pass
    app.secret_key = _k

app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(minutes=45)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
# Set to True only when served over HTTPS (Render gives HTTPS by default)
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15

ROLES = ["super_admin", "admin", "verifier", "employer", "provider", "trainee"]
FOLLOWUP_PERIODS = [30, 90, 180, 365]
NON_PLACEMENT_REASONS = [
    "skill_gap", "low_assessment", "no_local_jobs", "salary_mismatch",
    "lack_experience", "course_job_mismatch", "location_constraint",
    "further_education", "personal_reasons", "other",
]
SECTORS = ["IT", "Healthcare", "Manufacturing", "Retail", "Construction",
           "Hospitality", "Agriculture", "Logistics"]
STATES = ["Maharashtra", "Karnataka", "Delhi", "Tamil Nadu", "Gujarat",
          "Uttar Pradesh", "West Bengal", "Rajasthan", "Kerala", "Telangana"]

# Your super admin credentials
SUPER_ADMIN_USERNAME = "vithanalamanisri@gmail.com"
SUPER_ADMIN_PASSWORD = "vManisri@1512"
SUPER_ADMIN_NAME = "Vithanala Manisri"


# =============================================================================
#  SECTION 1 — DATABASE CONNECTION
# =============================================================================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def qx(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    last = cur.lastrowid
    cur.close()
    return last


# =============================================================================
#  SECTION 2 — SCHEMA
# =============================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    email TEXT UNIQUE,
    phone TEXT,
    role TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    failed_attempts INTEGER DEFAULT 0,
    locked_until TEXT,
    last_login TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS login_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    role TEXT,
    success INTEGER,
    ip TEXT,
    user_agent TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_reset_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    reason TEXT,
    status TEXT DEFAULT 'pending',
    requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    decided_by INTEGER
);

CREATE TABLE IF NOT EXISTS govt_officials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    official_id TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    mobile TEXT,
    department TEXT,
    access_level TEXT DEFAULT 'State',
    region TEXT,
    password_hash TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    expires_at TEXT,
    must_change_password INTEGER DEFAULT 1,
    last_login TEXT,
    login_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    updated_at TEXT,
    status_changed_at TEXT,
    status_changed_by TEXT
);

CREATE TABLE IF NOT EXISTS govt_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT,
    type TEXT,
    actor TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trainees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_code TEXT UNIQUE,
    user_id INTEGER,
    full_name TEXT NOT NULL,
    dob TEXT, gender TEXT,
    phone TEXT, email TEXT,
    category TEXT,
    qualification TEXT, institution TEXT, pass_year TEXT, score TEXT,
    state TEXT, district TEXT,
    preferred_role TEXT, preferred_location TEXT, expected_salary TEXT,
    training_status TEXT DEFAULT 'training',
    completion_date TEXT,
    assessment_score REAL, assessment_date TEXT, assessment_result TEXT,
    skills TEXT, certifications TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    state TEXT, district TEXT,
    contact TEXT, email TEXT,
    verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sector TEXT,
    provider_id INTEGER,
    duration_weeks INTEGER,
    skills_taught TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL,
    program_id INTEGER NOT NULL,
    enrolled_on TEXT,
    completed_on TEXT,
    completion_status TEXT DEFAULT 'ongoing',
    attendance_pct REAL
);

CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    industry TEXT, state TEXT, district TEXT,
    contact TEXT, email TEXT,
    verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS employments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL,
    employer_id INTEGER,
    job_role TEXT,
    employment_type TEXT,
    start_date TEXT,
    monthly_income REAL,
    pre_training_income REAL,
    job_relevance TEXT,
    skills_matched TEXT,
    skills_missing TEXT,
    status TEXT DEFAULT 'pending',
    verified_by INTEGER,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS retention_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employment_id INTEGER NOT NULL,
    days INTEGER NOT NULL,
    retained INTEGER,
    checked_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL,
    period_days INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT DEFAULT 'due',
    submitted_at TEXT,
    verified_at TEXT,
    verified_by INTEGER,
    data TEXT
);

CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    submitted_by INTEGER,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    decided_by INTEGER,
    decided_at TEXT,
    remarks TEXT
);

CREATE TABLE IF NOT EXISTS non_placement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    details TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER,
    employer_id INTEGER,
    provider_id INTEGER,
    rating INTEGER,
    satisfaction INTEGER,
    skills_satisfaction INTEGER,
    text TEXT,
    verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    title TEXT,
    body TEXT,
    read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cms_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,
    title TEXT,
    body TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    actor_username TEXT,
    action TEXT,
    entity TEXT,
    entity_id INTEGER,
    before_json TEXT,
    after_json TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS system_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT,
    message TEXT,
    trace TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backup_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    size_bytes INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db():
    """Idempotent DB bootstrap. Safe to call multiple times."""
    db = sqlite3.connect(DB_PATH, timeout=30)
    try:
        db.executescript(SCHEMA)
        db.commit()
        row = db.execute(
            "SELECT id FROM users WHERE role='super_admin'"
        ).fetchone()
        if not row:
            db.execute(
                "INSERT INTO users (username,password_hash,full_name,email,role) VALUES (?,?,?,?,?)",
                (
                    SUPER_ADMIN_USERNAME,
                    generate_password_hash(SUPER_ADMIN_PASSWORD),
                    SUPER_ADMIN_NAME,
                    SUPER_ADMIN_USERNAME,
                    "super_admin",
                ),
            )
            db.commit()
    finally:
        db.close()


# =============================================================================
#  SECTION 3 — HELPERS
# =============================================================================
def now_iso():
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return q("SELECT * FROM users WHERE id=?", (uid,), one=True)


def audit(action, entity, entity_id, before=None, after=None):
    u = current_user()
    qx(
        "INSERT INTO audit_log (actor_id,actor_username,action,entity,entity_id,before_json,after_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            u["id"] if u else None,
            u["username"] if u else "system",
            action, entity, entity_id,
            json.dumps(before) if before else None,
            json.dumps(after) if after else None,
        ),
    )


def notify(user_id, ntype, title, body=""):
    qx("INSERT INTO notifications (user_id,type,title,body) VALUES (?,?,?,?)",
       (user_id, ntype, title, body))


def notify_admins(ntype, title, body=""):
    for u in q("SELECT id FROM users WHERE role IN ('super_admin','admin')"):
        notify(u["id"], ntype, title, body)


def security_event(message, details=""):
    qx("INSERT INTO system_errors (level,message,trace) VALUES ('security',?,?)",
       (message, details))


# --- Decorators ---
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("uid"):
            return jsonify(error="Authentication required"), 401
        return f(*a, **kw)
    return wrapper


def role_required(*allowed):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return jsonify(error="Authentication required"), 401
            if u["role"] not in allowed and u["role"] != "super_admin":
                return jsonify(error="Forbidden"), 403
            return f(*a, **kw)
        return wrapper
    return deco


def admin_required(f):
    return role_required("admin")(f)


# =============================================================================
#  SECTION 4 — AUTHENTICATION
# =============================================================================
@app.route("/api/auth/register", methods=["POST"])
def api_register():
    d = request.json or {}
    required = ("username", "password", "role")
    if not all(d.get(k) for k in required):
        return jsonify(error="username, password, role required"), 400
    if d["role"] not in ROLES:
        return jsonify(error="invalid role"), 400
    if len(d["password"]) < 8:
        return jsonify(error="Password must be at least 8 chars"), 400
    try:
        uid = qx(
            "INSERT INTO users (username,password_hash,full_name,email,phone,role) "
            "VALUES (?,?,?,?,?,?)",
            (
                d["username"], generate_password_hash(d["password"]),
                d.get("full_name"), d.get("email"), d.get("phone"), d["role"],
            ),
        )
    except sqlite3.IntegrityError as e:
        return jsonify(error=f"duplicate: {e}"), 409
    notify_admins("registration", f"New {d['role']} registered: {d['username']}")
    audit("create", "user", uid)
    return jsonify(id=uid), 201


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """Handles admin + government + trainee/employer/provider logins."""
    d = request.json or {}
    role = d.get("role", "admin")
    ip = request.remote_addr
    ua = request.headers.get("User-Agent", "")[:200]

    # --- ADMIN ---
    if role in ("admin", "super_admin", "verifier") or d.get("username"):
        username = (d.get("username") or d.get("email") or "").strip()
        password = d.get("password") or ""
        if not username or not password:
            return jsonify(error="username and password required"), 400
        u = q("SELECT * FROM users WHERE username=?", (username,), one=True)
        if not u:
            u = q("SELECT * FROM users WHERE email=?", (username,), one=True)
        if not u or not check_password_hash(u["password_hash"], password):
            qx("INSERT INTO login_history (username,role,success,ip,user_agent) VALUES (?,?,0,?,?)",
               (username, role, ip, ua))
            security_event("Failed admin login", f"username={username} ip={ip}")
            return jsonify(error="Invalid credentials"), 401
        if u["locked_until"] and dt.datetime.fromisoformat(u["locked_until"]) > dt.datetime.utcnow():
            return jsonify(error="Account locked. Try later."), 423
        if u["status"] not in ("active",):
            return jsonify(error="Account unavailable"), 403
        qx("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=? WHERE id=?",
           (now_iso(), u["id"]))
        qx("INSERT INTO login_history (user_id,username,role,success,ip,user_agent) VALUES (?,?,?,1,?,?)",
           (u["id"], u["username"], u["role"], ip, ua))
        session.permanent = True
        session["uid"] = u["id"]
        session["role"] = u["role"]
        audit("login", "user", u["id"])
        return jsonify(
            id=u["id"], username=u["username"], role=u["role"],
            full_name=u["full_name"], email=u["email"],
        )

    # --- GOVERNMENT ---
    if role == "government":
        official_id = (d.get("officialId") or "").strip()
        email = (d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        if not (official_id and email and password):
            return jsonify(error="officialId, email and password required"), 400

        o = q("SELECT * FROM govt_officials WHERE official_id=? AND LOWER(email)=?",
              (official_id, email), one=True)
        if not o or not check_password_hash(o["password_hash"], password):
            qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
               (f"Failed login - ID {official_id}", "error", ip))
            security_event("Failed govt login", f"official_id={official_id} ip={ip}")
            return jsonify(error="Invalid Official ID or Email, or wrong password"), 401

        if o["status"] != "active":
            qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
               (f"Blocked - suspended {official_id}", "error", ip))
            return jsonify(error="Your government access has been suspended"), 403

        if o["expires_at"]:
            try:
                if dt.datetime.fromisoformat(o["expires_at"]) < dt.datetime.utcnow():
                    qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
                       (f"Blocked - expired {official_id}", "error", ip))
                    return jsonify(error="Your government access has expired"), 403
            except Exception:
                pass

        qx("UPDATE govt_officials SET last_login=?, login_count=COALESCE(login_count,0)+1 WHERE id=?",
           (now_iso(), o["id"]))
        qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
           (f"Login success - {o['full_name']} ({official_id}, {o['access_level']})", "success", ip))

        session.permanent = True
        session["govt_id"] = o["official_id"]
        session["govt_level"] = o["access_level"]
        return jsonify(
            officialId=o["official_id"], fullName=o["full_name"],
            email=o["email"], accessLevel=o["access_level"],
            department=o["department"], role="government",
        )

    # --- TRAINEE / EMPLOYER / PROVIDER ---
    if role not in ("trainee", "employer", "provider"):
        return jsonify(error="Unknown role"), 400

    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    if not email or not password:
        return jsonify(error="email and password required"), 400

    u = q("SELECT * FROM users WHERE email=?", (email,), one=True)
    if not u or not check_password_hash(u["password_hash"], password):
        security_event("Failed login", f"role={role} email={email} ip={ip}")
        return jsonify(error="Invalid credentials"), 401
    session.permanent = True
    session["uid"] = u["id"]
    session["role"] = role
    session["email"] = email
    return jsonify(id=u["id"], role=role, email=email, full_name=u["full_name"])


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    audit("logout", "user", session["uid"])
    session.clear()
    return jsonify(ok=True)


@app.route("/api/auth/me", methods=["GET"])
@login_required
def api_me():
    u = current_user()
    return jsonify(
        id=u["id"], username=u["username"], role=u["role"],
        full_name=u["full_name"], email=u["email"],
    )


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def api_change_password():
    d = request.json or {}
    old, new = d.get("old"), d.get("new")
    u = current_user()
    if not check_password_hash(u["password_hash"], old):
        return jsonify(error="Old password incorrect"), 400
    if len(new or "") < 8:
        return jsonify(error="New password too short"), 400
    qx("UPDATE users SET password_hash=? WHERE id=?",
       (generate_password_hash(new), u["id"]))
    audit("change_password", "user", u["id"])
    return jsonify(ok=True)


@app.route("/api/auth/forgot", methods=["POST"])
def api_forgot():
    d = request.json or {}
    username = d.get("username")
    u = q("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not u:
        return jsonify(ok=True)
    rid = qx("INSERT INTO password_reset_requests (user_id, reason) VALUES (?,?)",
             (u["id"], d.get("reason", "")))
    notify_admins("password_reset", f"Password reset requested by {username}")
    audit("request_reset", "user", u["id"])
    return jsonify(request_id=rid)


@app.route("/api/auth/sessions", methods=["GET"])
@login_required
def api_my_sessions():
    rows = q("SELECT * FROM login_history WHERE user_id=? ORDER BY at DESC LIMIT 20",
             (session["uid"],))
    return jsonify([dict(r) for r in rows])


# =============================================================================
#  SECTION 5 — ADMIN: PASSWORD RESETS
# =============================================================================
@app.route("/api/admin/password-resets", methods=["GET"])
@admin_required
def api_list_resets():
    rows = q("""SELECT r.*, u.username FROM password_reset_requests r
                JOIN users u ON u.id=r.user_id WHERE r.status='pending'
                ORDER BY r.requested_at DESC""")
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/password-resets/<int:rid>/decide", methods=["POST"])
@admin_required
def api_decide_reset(rid):
    d = request.json or {}
    approve = bool(d.get("approve"))
    r = q("SELECT * FROM password_reset_requests WHERE id=?", (rid,), one=True)
    if not r:
        return jsonify(error="not found"), 404
    if approve:
        temp = secrets.token_urlsafe(8)
        qx("UPDATE users SET password_hash=?, failed_attempts=0, locked_until=NULL WHERE id=?",
           (generate_password_hash(temp), r["user_id"]))
        notify(r["user_id"], "password_reset", "Password reset approved",
               f"Temporary password: {temp}")
    qx("UPDATE password_reset_requests SET status=?, decided_at=?, decided_by=? WHERE id=?",
       ("approved" if approve else "rejected", now_iso(), session["uid"], rid))
    audit("decide_reset", "password_reset", rid, after={"approve": approve})
    return jsonify(ok=True)


# =============================================================================
#  SECTION 6 — GOVERNMENT OFFICIALS (ADMIN CONTROL)
# =============================================================================
def _gen_official_id():
    year = dt.datetime.utcnow().year
    count = q("SELECT COUNT(*) c FROM govt_officials", one=True)["c"] + 1
    return f"GOV{year}{count:04d}"


def _new_password():
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789@#$%&*!"
    return "".join(secrets.choice(alphabet) for _ in range(10))


@app.route("/api/admin/govt-officials", methods=["GET"])
@admin_required
def api_list_govt_officials():
    rows = q("""SELECT id, official_id, full_name, email, mobile, department,
                       access_level, region, status, expires_at,
                       last_login, login_count, created_at, created_by
                FROM govt_officials ORDER BY created_at DESC""")
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/govt-officials", methods=["POST"])
@admin_required
def api_create_govt_official():
    d = request.json or {}
    full_name = (d.get("fullName") or "").strip()
    email = (d.get("email") or "").strip().lower()
    department = (d.get("department") or "").strip()
    if not (full_name and email and department):
        return jsonify(error="fullName, email, department required"), 400
    if q("SELECT id FROM govt_officials WHERE LOWER(email)=?", (email,), one=True):
        return jsonify(error="Email already exists"), 409

    official_id = (d.get("officialId") or _gen_official_id()).strip()
    password = d.get("password") or _new_password()

    qx("""INSERT INTO govt_officials
            (official_id,full_name,email,mobile,department,access_level,region,
             password_hash,status,expires_at,must_change_password,created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
       (
           official_id, full_name, email, d.get("mobile", ""), department,
           d.get("accessLevel", "State"), d.get("region", ""),
           generate_password_hash(password), "active",
           d.get("expiresAt") or None, 1, session.get("role", "admin"),
       ))

    qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
       (f"Created: {full_name} - {official_id} - {d.get('accessLevel','State')} level",
        "success", session.get("role")))
    audit("create", "govt_official", 0, after={"official_id": official_id})
    return jsonify(id=official_id, password=password), 201


@app.route("/api/admin/govt-officials/<int:oid>", methods=["PUT"])
@admin_required
def api_update_govt_official(oid):
    d = request.json or {}
    o = q("SELECT * FROM govt_officials WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(error="not found"), 404
    fields = ["full_name", "email", "mobile", "department", "access_level", "region", "expires_at"]
    sets, args = [], []
    for k in fields:
        if k in d:
            sets.append(f"{k}=?")
            args.append(d[k])
    if d.get("password"):
        sets.append("password_hash=?")
        args.append(generate_password_hash(d["password"]))
        sets.append("must_change_password=?")
        args.append(1)
    sets.append("updated_at=?")
    args.append(now_iso())
    qx(f"UPDATE govt_officials SET {','.join(sets)} WHERE id=?", args + [oid])
    qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
       (f"Updated: {o['full_name']}", "info", session.get("role")))
    audit("update", "govt_official", oid)
    return jsonify(ok=True)


@app.route("/api/admin/govt-officials/<int:oid>/toggle", methods=["POST"])
@admin_required
def api_toggle_govt_official(oid):
    o = q("SELECT * FROM govt_officials WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(error="not found"), 404
    new_status = "suspended" if o["status"] == "active" else "active"
    qx("""UPDATE govt_officials SET status=?, status_changed_at=?, status_changed_by=?
          WHERE id=?""",
       (new_status, now_iso(), session.get("role"), oid))
    msg = ("Reactivated" if new_status == "active" else "Suspended") + f": {o['full_name']}"
    qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
       (msg, "success" if new_status == "active" else "error", session.get("role")))
    audit("toggle", "govt_official", oid, after={"status": new_status})
    return jsonify(status=new_status)


@app.route("/api/admin/govt-officials/<int:oid>", methods=["DELETE"])
@admin_required
def api_delete_govt_official(oid):
    o = q("SELECT * FROM govt_officials WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(error="not found"), 404
    qx("DELETE FROM govt_officials WHERE id=?", (oid,))
    qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
       (f"Deleted: {o['full_name']} ({o['official_id']})", "error", session.get("role")))
    audit("delete", "govt_official", oid)
    return jsonify(ok=True)


@app.route("/api/admin/govt-officials/activity", methods=["GET"])
@admin_required
def api_govt_activity():
    rows = q("SELECT * FROM govt_activity ORDER BY at DESC LIMIT 100")
    return jsonify([dict(r) for r in rows])


# =============================================================================
#  SECTION 7 — SUPER ADMIN DASHBOARD
# =============================================================================
@app.route("/api/admin/dashboard", methods=["GET"])
@admin_required
def api_admin_dashboard():
    total_trainees = q("SELECT COUNT(*) c FROM trainees WHERE archived=0", one=True)["c"]
    total_employers = q("SELECT COUNT(*) c FROM employers", one=True)["c"]
    total_providers = q("SELECT COUNT(*) c FROM providers", one=True)["c"]
    total_programs = q("SELECT COUNT(*) c FROM programs WHERE archived=0", one=True)["c"]
    total_completed = q("SELECT COUNT(*) c FROM trainees WHERE training_status='completed'", one=True)["c"]
    total_employed = q("SELECT COUNT(DISTINCT trainee_id) c FROM employments WHERE status='verified'", one=True)["c"]
    employment_rate = round((total_employed / total_completed * 100), 2) if total_completed else 0

    retention = {}
    for days in FOLLOWUP_PERIODS:
        row = q("""SELECT COUNT(DISTINCT rc.employment_id) c FROM retention_checks rc
                   WHERE rc.days=? AND rc.retained=1""", (days,), one=True)
        retention[str(days)] = row["c"]

    avg_income = q("SELECT AVG(monthly_income) a FROM employments WHERE status='verified'", one=True)["a"] or 0
    pending_verif = q("SELECT COUNT(*) c FROM verifications WHERE status='pending'", one=True)["c"]
    overdue_fu = q("""SELECT COUNT(*) c FROM followups
                      WHERE status IN ('due','pending') AND due_date < ?""",
                   (dt.date.today().isoformat(),), one=True)["c"]
    pending_resets = q("SELECT COUNT(*) c FROM password_reset_requests WHERE status='pending'", one=True)["c"]
    govt_officials = q("SELECT COUNT(*) c FROM govt_officials", one=True)["c"]
    active_govt = q("SELECT COUNT(*) c FROM govt_officials WHERE status='active'", one=True)["c"]
    recent = q("SELECT * FROM audit_log ORDER BY at DESC LIMIT 15")
    alerts = q("SELECT * FROM system_errors ORDER BY at DESC LIMIT 5")

    return jsonify({
        "totals": {
            "trainees": total_trainees, "employers": total_employers,
            "providers": total_providers, "programs": total_programs,
            "completed": total_completed, "employed": total_employed,
            "govtOfficials": govt_officials, "activeGovtOfficials": active_govt,
        },
        "employment_rate": employment_rate,
        "retention": retention,
        "avg_income": round(avg_income, 2),
        "pending_verifications": pending_verif,
        "overdue_followups": overdue_fu,
        "password_reset_requests": pending_resets,
        "recent_activity": [dict(r) for r in recent],
        "alerts": [dict(r) for r in alerts],
    })


# =============================================================================
#  SECTION 8 — USER MANAGEMENT
# =============================================================================
@app.route("/api/admin/users", methods=["GET"])
@admin_required
def api_list_users():
    search = request.args.get("search", "").strip()
    role = request.args.get("role")
    status = request.args.get("status")
    page = int(request.args.get("page", 1))
    per = int(request.args.get("per_page", 25))
    where, args = ["1=1"], []
    if search:
        where.append("(username LIKE ? OR full_name LIKE ? OR email LIKE ?)")
        args += [f"%{search}%"] * 3
    if role:
        where.append("role=?"); args.append(role)
    if status:
        where.append("status=?"); args.append(status)
    sql = f"""SELECT id,username,full_name,email,phone,role,status,last_login,created_at
              FROM users WHERE {' AND '.join(where)}
              ORDER BY created_at DESC LIMIT ? OFFSET ?"""
    rows = q(sql, args + [per, (page - 1) * per])
    total = q(f"SELECT COUNT(*) c FROM users WHERE {' AND '.join(where)}", args, one=True)["c"]
    return jsonify(items=[dict(r) for r in rows], total=total, page=page, per_page=per)


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def api_create_user():
    d = request.json or {}
    if not (d.get("username") and d.get("password") and d.get("role")):
        return jsonify(error="username, password, role required"), 400
    uid = qx("""INSERT INTO users (username,password_hash,full_name,email,phone,role)
                VALUES (?,?,?,?,?,?)""",
             (d["username"], generate_password_hash(d["password"]),
              d.get("full_name"), d.get("email"), d.get("phone"), d["role"]))
    audit("create", "user", uid, after=d)
    return jsonify(id=uid), 201


@app.route("/api/admin/users/<int:uid>", methods=["PUT"])
@admin_required
def api_update_user(uid):
    d = request.json or {}
    before = dict(q("SELECT * FROM users WHERE id=?", (uid,), one=True) or {})
    if not before:
        return jsonify(error="not found"), 404
    fields = ["full_name", "email", "phone", "role", "status"]
    sets = [f"{k}=?" for k in fields if k in d]
    args = [d[k] for k in fields if k in d]
    if not sets:
        return jsonify(error="nothing to update"), 400
    qx(f"UPDATE users SET {','.join(sets)} WHERE id=?", args + [uid])
    after = dict(q("SELECT * FROM users WHERE id=?", (uid,), one=True))
    audit("update", "user", uid, before=before, after=after)
    return jsonify(ok=True)


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@admin_required
def api_archive_user(uid):
    qx("UPDATE users SET status='archived' WHERE id=?", (uid,))
    audit("archive", "user", uid)
    return jsonify(ok=True)


@app.route("/api/admin/users/<int:uid>/block", methods=["POST"])
@admin_required
def api_block_user(uid):
    qx("UPDATE users SET status='blocked' WHERE id=?", (uid,))
    audit("block", "user", uid)
    return jsonify(ok=True)


@app.route("/api/admin/users/<int:uid>/unblock", methods=["POST"])
@admin_required
def api_unblock_user(uid):
    qx("UPDATE users SET status='active', failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
    audit("unblock", "user", uid)
    return jsonify(ok=True)


@app.route("/api/admin/users/export", methods=["GET"])
@admin_required
def api_export_users():
    rows = q("SELECT id,username,full_name,email,phone,role,status,last_login,created_at FROM users")
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow(list(r))
    return send_file(io.BytesIO(buf.getvalue().encode()), mimetype="text/csv",
                     as_attachment=True, download_name="users.csv")


# =============================================================================
#  SECTION 9 — TRAINEES
# =============================================================================
@app.route("/api/trainees", methods=["GET"])
@login_required
def api_list_trainees():
    search = request.args.get("search", "").strip()
    where, args = ["archived=0"], []
    if search:
        where.append("(full_name LIKE ? OR trainee_code LIKE ? OR email LIKE ?)")
        args += [f"%{search}%"] * 3
    rows = q(f"SELECT * FROM trainees WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT 500", args)
    return jsonify([dict(r) for r in rows])


@app.route("/api/trainees", methods=["POST"])
@login_required
def api_create_trainee():
    d = request.json or {}
    if not d.get("full_name"):
        return jsonify(error="full_name required"), 400
    code = d.get("trainee_code") or f"TRN{random.randint(10000, 99999)}"
    tid = qx("""INSERT INTO trainees
        (trainee_code,full_name,dob,gender,phone,email,category,qualification,
         institution,pass_year,score,state,district,preferred_role,
         preferred_location,expected_salary,skills,certifications)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, d["full_name"], d.get("dob"), d.get("gender"),
         d.get("phone"), d.get("email"), d.get("category"),
         d.get("qualification"), d.get("institution"), d.get("pass_year"),
         d.get("score"), d.get("state"), d.get("district"),
         d.get("preferred_role"), d.get("preferred_location"),
         d.get("expected_salary"), d.get("skills"), d.get("certifications")))

    # Auto-schedule follow-ups (30/90/180/365 days)
    try:
        ensure_followup_schedule(tid)
    except Exception as e:
        print(f"[warn] followup scheduling failed: {e}")

    audit("create", "trainee", tid, after=d)
    return jsonify(id=tid, trainee_code=code), 201


@app.route("/api/trainees/<int:tid>/360", methods=["GET"])
@login_required
def api_trainee_360(tid):
    t = q("SELECT * FROM trainees WHERE id=?", (tid,), one=True)
    if not t:
        return jsonify(error="not found"), 404
    t = dict(t)
    t["enrollments"] = [dict(r) for r in q("""
        SELECT e.*, p.name program_name, p.sector
        FROM enrollments e JOIN programs p ON p.id=e.program_id
        WHERE e.trainee_id=?""", (tid,))]
    t["employments"] = [dict(r) for r in q("""
        SELECT em.*, er.name employer_name FROM employments em
        LEFT JOIN employers er ON er.id=em.employer_id
        WHERE em.trainee_id=?""", (tid,))]
    t["followups"] = [dict(r) for r in q("SELECT * FROM followups WHERE trainee_id=? ORDER BY period_days", (tid,))]
    t["non_placement"] = [dict(r) for r in q("SELECT * FROM non_placement WHERE trainee_id=?", (tid,))]
    return jsonify(t)


# =============================================================================
#  SECTION 10 — EMPLOYERS / PROVIDERS / PROGRAMS
# =============================================================================
@app.route("/api/employers", methods=["GET"])
@login_required
def api_list_employers():
    rows = q("SELECT * FROM employers ORDER BY created_at DESC")
    return jsonify([dict(r) for r in rows])


@app.route("/api/employers", methods=["POST"])
@login_required
def api_create_employer():
    d = request.json or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    eid = qx("""INSERT INTO employers (name,industry,state,district,contact,email)
                VALUES (?,?,?,?,?,?)""",
             (d["name"], d.get("industry"), d.get("state"), d.get("district"),
              d.get("contact"), d.get("email")))
    audit("create", "employer", eid, after=d)
    return jsonify(id=eid), 201


@app.route("/api/employers/<int:eid>/verify", methods=["POST"])
@role_required("admin", "verifier")
def api_verify_employer(eid):
    qx("UPDATE employers SET verified=1 WHERE id=?", (eid,))
    audit("verify", "employer", eid)
    return jsonify(ok=True)


@app.route("/api/providers", methods=["GET"])
@login_required
def api_list_providers():
    rows = q("SELECT * FROM providers ORDER BY name")
    return jsonify([dict(r) for r in rows])


@app.route("/api/providers", methods=["POST"])
@admin_required
def api_create_provider():
    d = request.json or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    pid = qx("""INSERT INTO providers (name,state,district,contact,email)
                VALUES (?,?,?,?,?)""",
             (d["name"], d.get("state"), d.get("district"),
              d.get("contact"), d.get("email")))
    audit("create", "provider", pid, after=d)
    return jsonify(id=pid), 201


@app.route("/api/providers/<int:pid>/verify", methods=["POST"])
@role_required("admin", "verifier")
def api_verify_provider(pid):
    qx("UPDATE providers SET verified=1 WHERE id=?", (pid,))
    audit("verify", "provider", pid)
    return jsonify(ok=True)


@app.route("/api/programs", methods=["GET"])
@login_required
def api_list_programs():
    rows = q("""SELECT p.*, pr.name provider_name FROM programs p
                LEFT JOIN providers pr ON pr.id=p.provider_id
                WHERE p.archived=0 ORDER BY p.created_at DESC""")
    out = []
    for r in rows:
        d = dict(r)
        d["enrolled"] = q("SELECT COUNT(*) c FROM enrollments WHERE program_id=?", (r["id"],), one=True)["c"]
        out.append(d)
    return jsonify(out)


@app.route("/api/programs", methods=["POST"])
@admin_required
def api_create_program():
    d = request.json or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    pid = qx("""INSERT INTO programs (name,sector,provider_id,duration_weeks,skills_taught)
                VALUES (?,?,?,?,?)""",
             (d["name"], d.get("sector"), d.get("provider_id"),
              d.get("duration_weeks"), d.get("skills_taught")))
    audit("create", "program", pid, after=d)
    return jsonify(id=pid), 201


# =============================================================================
#  SECTION 11 — FOLLOW-UPS
# =============================================================================
def _schedule_followups(trainee_id, completion_date_str):
    base = dt.date.fromisoformat(completion_date_str)
    for days in FOLLOWUP_PERIODS:
        due = (base + dt.timedelta(days=days)).isoformat()
        qx("""INSERT INTO followups (trainee_id,period_days,due_date,status)
              VALUES (?,?,?, 'due')""", (trainee_id, days, due))


def ensure_followup_schedule(trainee_id, completion_date=None):
    """
    Ensure a trainee has 30/90/180/365 day follow-ups scheduled.
    Called after registration or completion.
    """
    base = None
    if completion_date:
        try:
            base = dt.date.fromisoformat(completion_date)
        except Exception:
            base = None
    if not base:
        base = dt.date.today()

    for days in FOLLOWUP_PERIODS:
        due = (base + dt.timedelta(days=days)).isoformat()
        exists = q("""SELECT id FROM followups
                      WHERE trainee_id=? AND period_days=?""",
                   (trainee_id, days), one=True)
        if not exists:
            qx("""INSERT INTO followups (trainee_id,period_days,due_date,status)
                  VALUES (?,?,?, 'due')""",
               (trainee_id, days, due))


@app.route("/api/followups", methods=["GET"])
@login_required
def api_list_followups():
    status = request.args.get("status")
    where, args = ["1=1"], []
    if status:
        where.append("f.status=?"); args.append(status)
    rows = q(f"""SELECT f.*, t.full_name trainee_name, t.trainee_code
                 FROM followups f JOIN trainees t ON t.id=f.trainee_id
                 WHERE {' AND '.join(where)}
                 ORDER BY f.due_date ASC""", args)
    return jsonify([dict(r) for r in rows])


@app.route("/api/followups/overdue", methods=["GET"])
@login_required
def api_overdue_followups():
    rows = q("""SELECT f.*, t.full_name trainee_name FROM followups f
                JOIN trainees t ON t.id=f.trainee_id
                WHERE f.status IN ('due','pending') AND f.due_date < ?""",
             (dt.date.today().isoformat(),))
    return jsonify(count=len(rows), items=[dict(r) for r in rows])


@app.route("/api/followups/auto-flag", methods=["POST"])
@admin_required
def api_autoflag_overdue():
    n = qx("""UPDATE followups SET status='overdue'
              WHERE status IN ('due','pending') AND due_date < ?""",
           (dt.date.today().isoformat(),))
    audit("autoflag", "followup", 0, after={"count": n})
    return jsonify(updated=n)


@app.route("/api/followups/<int:fid>/submit", methods=["POST"])
@login_required
def api_submit_followup(fid):
    """Trainee submits their follow-up data."""
    d = request.json or {}
    f = q("SELECT * FROM followups WHERE id=?", (fid,), one=True)
    if not f:
        return jsonify(error="followup not found"), 404

    status = d.get("status") or "completed"
    submitted_at = d.get("submitted_at") or now_iso()
    data_json = d.get("data") if isinstance(d.get("data"), str) else json.dumps(d.get("data") or {})

    qx("""UPDATE followups SET status=?, submitted_at=?, data=?
          WHERE id=?""",
       (status, submitted_at, data_json, fid))
    audit("submit", "followup", fid, after={"status": status})
    return jsonify(ok=True)


# =============================================================================
#  SECTION 12 — VERIFICATIONS
# =============================================================================
@app.route("/api/verifications", methods=["GET"])
@role_required("admin", "verifier")
def api_list_verifications():
    status = request.args.get("status", "pending")
    rows = q("""SELECT v.*, u.username submitted_by_name FROM verifications v
                LEFT JOIN users u ON u.id=v.submitted_by
                WHERE v.status=? ORDER BY v.submitted_at DESC""", (status,))
    return jsonify([dict(r) for r in rows])


@app.route("/api/verifications/<int:vid>/decide", methods=["POST"])
@role_required("admin", "verifier")
def api_decide_verification(vid):
    d = request.json or {}
    action = d.get("action")
    if action not in ("approve", "reject", "request_correction"):
        return jsonify(error="invalid action"), 400
    status = {"approve": "approved", "reject": "rejected",
              "request_correction": "correction"}[action]
    v = q("SELECT * FROM verifications WHERE id=?", (vid,), one=True)
    if not v:
        return jsonify(error="not found"), 404
    qx("""UPDATE verifications SET status=?, decided_by=?, decided_at=?, remarks=?
          WHERE id=?""",
       (status, session["uid"], now_iso(), d.get("remarks"), vid))
    if action == "approve":
        if v["entity_type"] == "employment":
            qx("UPDATE employments SET status='verified', verified_by=?, verified_at=? WHERE id=?",
               (session["uid"], now_iso(), v["entity_id"]))
        elif v["entity_type"] == "employer":
            qx("UPDATE employers SET verified=1 WHERE id=?", (v["entity_id"],))
    audit("decide", "verification", vid, after={"action": action})
    return jsonify(ok=True)


# =============================================================================
#  SECTION 12B — EMPLOYMENTS (save employment records from trainee dashboard)
# =============================================================================
@app.route("/api/employments", methods=["GET"])
@login_required
def api_list_employments():
    """List employments (optionally filtered by trainee)."""
    trainee_id = request.args.get("trainee_id")
    where, args = ["1=1"], []
    if trainee_id:
        where.append("em.trainee_id=?")
        args.append(trainee_id)
    rows = q(f"""SELECT em.*, er.name employer_name, t.full_name trainee_name
                 FROM employments em
                 LEFT JOIN employers er ON er.id=em.employer_id
                 LEFT JOIN trainees t ON t.id=em.trainee_id
                 WHERE {' AND '.join(where)}
                 ORDER BY em.start_date DESC, em.id DESC""", args)
    return jsonify([dict(r) for r in rows])


@app.route("/api/employments", methods=["POST"])
@login_required
def api_create_employment():
    """
    Save/update an employment record from the trainee dashboard.
    If a record already exists for the trainee, update it.
    Otherwise, insert a new one.
    """
    d = request.json or {}
    trainee_id = d.get("trainee_id")
    if not trainee_id:
        return jsonify(error="trainee_id required"), 400

    # Verify trainee exists
    t = q("SELECT id FROM trainees WHERE id=?", (trainee_id,), one=True)
    if not t:
        return jsonify(error="Trainee not found"), 404

    # Optional: auto-link to an employer if the name matches an existing employer
    employer_id = d.get("employer_id")
    employer_name = (d.get("employer_name") or "").strip()
    if not employer_id and employer_name:
        existing_emp = q("SELECT id FROM employers WHERE LOWER(name)=LOWER(?)",
                         (employer_name,), one=True)
        if existing_emp:
            employer_id = existing_emp["id"]

    # Check if there is already an employment row for this trainee
    existing = q("SELECT id FROM employments WHERE trainee_id=? ORDER BY id DESC LIMIT 1",
                 (trainee_id,), one=True)

    job_role = d.get("job_role") or None
    employment_type = d.get("employment_type") or "full"
    start_date = d.get("start_date") or None
    monthly_income = float(d.get("monthly_income") or 0)
    pre_training_income = float(d.get("pre_training_income") or 0)
    job_relevance = d.get("job_relevance") or "medium"
    skills_matched = d.get("skills_matched") or ""
    skills_missing = d.get("skills_missing") or ""

    if existing:
        # Update
        qx("""UPDATE employments
              SET employer_id=?, job_role=?, employment_type=?, start_date=?,
                  monthly_income=?, pre_training_income=?, job_relevance=?,
                  skills_matched=?, skills_missing=?
              WHERE id=?""",
           (employer_id, job_role, employment_type, start_date,
            monthly_income, pre_training_income, job_relevance,
            skills_matched, skills_missing, existing["id"]))
        emp_id = existing["id"]
        audit("update", "employment", emp_id, after=d)
    else:
        # Insert
        emp_id = qx("""INSERT INTO employments
            (trainee_id,employer_id,job_role,employment_type,start_date,
             monthly_income,pre_training_income,job_relevance,
             skills_matched,skills_missing,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (trainee_id, employer_id, job_role, employment_type, start_date,
             monthly_income, pre_training_income, job_relevance,
             skills_matched, skills_missing, "pending"))
        audit("create", "employment", emp_id, after=d)

    # Automatically create a verification request for admins
    try:
        existing_verif = q("""SELECT id FROM verifications
                              WHERE entity_type='employment' AND entity_id=?
                              AND status='pending'""",
                           (emp_id,), one=True)
        if not existing_verif:
            qx("""INSERT INTO verifications
                  (entity_type,entity_id,status,submitted_by,remarks)
                  VALUES ('employment',?,'pending',?,?)""",
               (emp_id, session.get("uid"), "Submitted from trainee dashboard"))
    except Exception:
        pass

    return jsonify(id=emp_id, ok=True), 201


@app.route("/api/employments/<int:eid>", methods=["GET"])
@login_required
def api_get_employment(eid):
    emp = q("""SELECT em.*, er.name employer_name FROM employments em
               LEFT JOIN employers er ON er.id=em.employer_id
               WHERE em.id=?""", (eid,), one=True)
    if not emp:
        return jsonify(error="not found"), 404
    return jsonify(dict(emp))


@app.route("/api/employments/<int:eid>/verify", methods=["POST"])
@role_required("admin", "verifier", "employer")
def api_verify_employment(eid):
    """Employer / admin verifies a trainee's employment record."""
    emp = q("SELECT * FROM employments WHERE id=?", (eid,), one=True)
    if not emp:
        return jsonify(error="not found"), 404
    qx("""UPDATE employments SET status='verified', verified_by=?, verified_at=?
          WHERE id=?""",
       (session.get("uid"), now_iso(), eid))
    # Mark verification request as approved
    qx("""UPDATE verifications SET status='approved', decided_by=?, decided_at=?
          WHERE entity_type='employment' AND entity_id=? AND status='pending'""",
       (session.get("uid"), now_iso(), eid))
    # Schedule retention checks based on start date
    if emp["start_date"]:
        try:
            start = dt.date.fromisoformat(emp["start_date"])
            for days in FOLLOWUP_PERIODS:
                check_date = (start + dt.timedelta(days=days)).isoformat()
                exists = q("""SELECT id FROM retention_checks
                              WHERE employment_id=? AND days=?""",
                           (eid, days), one=True)
                if not exists:
                    qx("""INSERT INTO retention_checks
                          (employment_id,days,retained,checked_at)
                          VALUES (?,?,?,?)""",
                       (eid, days, None, check_date))
        except Exception:
            pass
    audit("verify", "employment", eid)
    return jsonify(ok=True)


# =============================================================================
#  SECTION 12C — TRAINEE SELF REGISTRATION (auth-based)
# =============================================================================
@app.route("/api/trainees/register", methods=["POST"])
def api_register_trainee_full():
    """
    Register a new trainee AND create their user account, AND schedule followups.
    Called by trainee-register.html if using the combined flow.
    """
    d = request.json or {}
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    full_name = (d.get("full_name") or "").strip()

    if not (email and password and full_name):
        return jsonify(error="email, password, full_name required"), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters"), 400

    # 1. Create user account (if not exists)
    uid = None
    existing_user = q("SELECT id FROM users WHERE email=?", (email,), one=True)
    if existing_user:
        uid = existing_user["id"]
    else:
        try:
            uid = qx("""INSERT INTO users
                (username,password_hash,full_name,email,phone,role)
                VALUES (?,?,?,?,?,?)""",
                (email, generate_password_hash(password),
                 full_name, email, d.get("phone"), "trainee"))
        except sqlite3.IntegrityError:
            uid = q("SELECT id FROM users WHERE email=?", (email,), one=True)["id"]

    # 2. Create trainee record (if not exists)
    existing_t = q("SELECT id FROM trainees WHERE email=?", (email,), one=True)
    if existing_t:
        trainee_id = existing_t["id"]
    else:
        code = d.get("trainee_code") or f"TRN{random.randint(10000, 99999)}"
        trainee_id = qx("""INSERT INTO trainees
            (trainee_code,user_id,full_name,dob,gender,phone,email,
             category,qualification,state,district,preferred_role,skills)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, uid, full_name, d.get("dob"), d.get("gender"),
             d.get("phone"), email, d.get("category"),
             d.get("qualification"), d.get("state"), d.get("district"),
             d.get("preferred_role"), d.get("skills")))

    # 3. Schedule follow-ups (30/90/180/365 days from today)
    ensure_followup_schedule(trainee_id)

    audit("register", "trainee", trainee_id)
    return jsonify(
        id=trainee_id, user_id=uid,
        trainee_code=q("SELECT trainee_code FROM trainees WHERE id=?",
                       (trainee_id,), one=True)["trainee_code"],
    ), 201


# =============================================================================
#  SECTION 12D — GOVERNMENT ANALYTICS (aggregated real data)
# =============================================================================
@app.route("/api/govt/analytics", methods=["GET"])
@login_required
def api_govt_analytics():
    """Aggregated analytics for government dashboard."""
    total_trainees = q("SELECT COUNT(*) c FROM trainees WHERE archived=0", one=True)["c"]
    completed = q("SELECT COUNT(*) c FROM trainees WHERE training_status='completed' AND archived=0",
                  one=True)["c"]
    employed = q("""SELECT COUNT(DISTINCT trainee_id) c FROM employments
                    WHERE status='verified'""", one=True)["c"]
    avg_income = q("""SELECT AVG(monthly_income) a FROM employments
                      WHERE status='verified'""", one=True)["a"] or 0
    avg_previous = q("""SELECT AVG(pre_training_income) a FROM employments
                        WHERE status='verified' AND pre_training_income > 0""",
                     one=True)["a"] or 0

    # By state
    by_state = q("""SELECT state, COUNT(*) c FROM trainees
                    WHERE state IS NOT NULL AND archived=0
                    GROUP BY state ORDER BY c DESC LIMIT 15""")

    # By sector (from employments)
    by_sector = q("""SELECT t.preferred_role AS sector, COUNT(*) c
                     FROM employments em
                     JOIN trainees t ON t.id=em.trainee_id
                     WHERE em.status='verified' AND t.preferred_role IS NOT NULL
                     GROUP BY t.preferred_role ORDER BY c DESC LIMIT 10""")

    # Employment rate
    rate = round(employed / completed * 100, 2) if completed else 0

    # Income growth
    growth = round(avg_income - avg_previous, 2) if avg_income and avg_previous else 0
    growth_pct = round(growth / avg_previous * 100, 1) if avg_previous else 0

    # Non-placement breakdown
    non_placement = q("""SELECT reason, COUNT(*) c FROM non_placement
                         GROUP BY reason ORDER BY c DESC""")

    return jsonify({
        "totals": {
            "trainees": total_trainees,
            "completed": completed,
            "employed": employed,
            "employment_rate": rate,
        },
        "income": {
            "avg_current": round(avg_income, 2),
            "avg_previous": round(avg_previous, 2),
            "growth": growth,
            "growth_pct": growth_pct,
        },
        "by_state": [dict(r) for r in by_state],
        "by_sector": [dict(r) for r in by_sector],
        "non_placement": [dict(r) for r in non_placement],
    })


@app.route("/api/govt/trainee/<int:tid>/full", methods=["GET"])
@login_required
def api_govt_trainee_full(tid):
    """
    Full 360° trainee profile for government 360° view page.
    Includes: personal, training, employment, followups, retention.
    """
    t = q("SELECT * FROM trainees WHERE id=?", (tid,), one=True)
    if not t:
        return jsonify(error="not found"), 404

    result = dict(t)

    # Enrollments
    result["enrollments"] = [dict(r) for r in q("""
        SELECT e.*, p.name program_name, p.sector
        FROM enrollments e
        LEFT JOIN programs p ON p.id=e.program_id
        WHERE e.trainee_id=?""", (tid,))]

    # Employments + employer names
    result["employments"] = [dict(r) for r in q("""
        SELECT em.*, er.name employer_name, er.industry
        FROM employments em
        LEFT JOIN employers er ON er.id=em.employer_id
        WHERE em.trainee_id=?
        ORDER BY em.start_date DESC""", (tid,))]

    # Retention checks for the latest employment
    if result["employments"]:
        latest_emp_id = result["employments"][0]["id"]
        result["retention"] = [dict(r) for r in q("""
            SELECT * FROM retention_checks
            WHERE employment_id=?
            ORDER BY days""", (latest_emp_id,))]
    else:
        result["retention"] = []

    # Follow-ups
    result["followups"] = [dict(r) for r in q("""
        SELECT * FROM followups WHERE trainee_id=?
        ORDER BY period_days""", (tid,))]

    # Non-placement records
    result["non_placement"] = [dict(r) for r in q("""
        SELECT * FROM non_placement WHERE trainee_id=?""", (tid,))]

    # Feedback
    result["feedback"] = [dict(r) for r in q("""
        SELECT * FROM feedback WHERE trainee_id=?""", (tid,))]

    return jsonify(result)


# =============================================================================
#  SECTION 12E — ADMIN AUTO-SEED ROUTES (for bulk seeding)
# =============================================================================
@app.route("/api/admin/seed-complete", methods=["POST"])
@admin_required
def api_admin_seed_complete():
    """
    Idempotent seed that creates trainees + followups + employments + verifications.
    Safe to call repeatedly.
    """
    # Create providers
    provider_ids = []
    for i, pname in enumerate(["Skill India Centre", "NSDC Partner", "DataEdge Academy"]):
        existing = q("SELECT id FROM providers WHERE name=?", (pname,), one=True)
        if existing:
            provider_ids.append(existing["id"])
        else:
            provider_ids.append(qx(
                "INSERT INTO providers (name,state,district,verified) VALUES (?,?,?,1)",
                (pname, random.choice(STATES), f"District{i}")))

    # Employers
    emp_ids = []
    for ename in ["TechCorp", "InfoSys", "Wipro Ltd"]:
        existing = q("SELECT id FROM employers WHERE name=?", (ename,), one=True)
        if existing:
            emp_ids.append(existing["id"])
        else:
            emp_ids.append(qx(
                "INSERT INTO employers (name,industry,state,verified) VALUES (?,?,?,1)",
                (ename, random.choice(SECTORS), random.choice(STATES))))

    # Programs
    program_ids = []
    for i in range(4):
        pname = f"Program {chr(65 + i)}"
        existing = q("SELECT id FROM programs WHERE name=?", (pname,), one=True)
        if existing:
            program_ids.append(existing["id"])
        else:
            program_ids.append(qx(
                """INSERT INTO programs (name,sector,provider_id,duration_weeks,skills_taught)
                   VALUES (?,?,?,?,?)""",
                (pname, random.choice(SECTORS), random.choice(provider_ids),
                 random.choice([8, 12, 16]), "Python, SQL, Communication")))

    # Trainees + followups + employments
    created = 0
    for i in range(30):
        email = f"seed.trainee{i}@example.com"
        if q("SELECT id FROM trainees WHERE email=?", (email,), one=True):
            continue
        tid = qx("""INSERT INTO trainees
            (trainee_code,full_name,gender,phone,email,category,qualification,
             state,district,training_status,completion_date,assessment_score,
             assessment_result,skills)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"TRN{20000+i}", f"Seed Trainee {i+1}",
             random.choice(["Male", "Female"]),
             f"+9199{random.randint(1000000, 9999999)}",
             email, "General", "B.Sc.",
             random.choice(STATES), f"District{i % 5}",
             "completed",
             (dt.date.today() - dt.timedelta(days=random.randint(60, 400))).isoformat(),
             round(random.gauss(70, 12), 1),
             "Pass", "Python, SQL"))
        # Enrollment
        qx("""INSERT INTO enrollments (trainee_id,program_id,enrolled_on,
              completion_status,attendance_pct)
              VALUES (?,?,?,?,?)""",
           (tid, random.choice(program_ids),
            (dt.date.today() - dt.timedelta(days=200)).isoformat(),
            "completed", round(random.uniform(70, 100), 1)))
        # Followups
        ensure_followup_schedule(tid,
            (dt.date.today() - dt.timedelta(days=random.randint(60, 400))).isoformat())
        # Employment
        if random.random() < 0.75:
            emp_id = qx("""INSERT INTO employments
                (trainee_id,employer_id,job_role,employment_type,start_date,
                 monthly_income,pre_training_income,job_relevance,status,
                 verified_by,verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, random.choice(emp_ids),
                 random.choice(["Data Analyst", "Engineer", "Associate"]),
                 random.choice(["full", "part", "self"]),
                 (dt.date.today() - dt.timedelta(days=random.randint(10, 200))).isoformat(),
                 float(random.randint(15000, 45000)),
                 float(random.randint(6000, 15000)),
                 random.choice(["high", "medium"]),
                 "verified", 1, now_iso()))
            for d in FOLLOWUP_PERIODS:
                if random.random() < 0.7:
                    qx("""INSERT INTO retention_checks (employment_id,days,retained,checked_at)
                          VALUES (?,?,?,?)""",
                       (emp_id, d, 1 if random.random() < 0.8 else 0, now_iso()))
        created += 1

    return jsonify(ok=True, created=created)


# =============================================================================
#  SECTION 13 — AI INSIGHTS (pure Python, no pandas/sklearn)
# =============================================================================
def _mean(values):
    return sum(values) / len(values) if values else 0


def _std(values):
    if len(values) < 2:
        return 0
    m = _mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(var)


@app.route("/api/insights/global", methods=["GET"])
@role_required("admin", "verifier")
def api_global_insights():
    """Pure-Python AI insights engine. No pandas/numpy/sklearn needed."""
    out = []

    # 1. Program performance
    programs = q("SELECT id, name FROM programs WHERE archived=0")
    for prog in programs:
        total = q("""SELECT COUNT(DISTINCT e.trainee_id) c FROM enrollments e
                     WHERE e.program_id=?""", (prog["id"],), one=True)["c"] or 0
        employed = q("""SELECT COUNT(DISTINCT em.trainee_id) c FROM employments em
                        JOIN enrollments e ON e.trainee_id=em.trainee_id
                        WHERE e.program_id=? AND em.status='verified'""",
                     (prog["id"],), one=True)["c"] or 0
        rate = round(employed / total * 100, 1) if total else 0
        if rate < 40 and total >= 5:
            out.append({
                "type": "low_employment_program",
                "severity": "HIGH",
                "problem": f"Low employment in {prog['name']}",
                "evidence": [f"{total} trainees", f"{employed} employed", f"{rate}% employment"],
                "recommendation": "Review curriculum and strengthen industry-aligned training.",
            })

    # 2. State-wise employment
    states = q("""SELECT DISTINCT state FROM trainees WHERE state IS NOT NULL AND archived=0""")
    for s in states:
        st = s["state"]
        if not st:
            continue
        total = q("SELECT COUNT(*) c FROM trainees WHERE state=? AND archived=0",
                  (st,), one=True)["c"] or 0
        employed = q("""SELECT COUNT(DISTINCT em.trainee_id) c FROM employments em
                        JOIN trainees t ON t.id=em.trainee_id
                        WHERE t.state=? AND em.status='verified'""",
                     (st,), one=True)["c"] or 0
        rate = round(employed / total * 100, 1) if total else 0
        if rate < 30 and total >= 10:
            out.append({
                "type": "regional_gap",
                "severity": "MEDIUM",
                "problem": f"Low employment in {st}",
                "evidence": [f"{total} trainees", f"{employed} employed", f"{rate}%"],
                "recommendation": "Focus local employer partnerships.",
            })

    # 3. Income anomaly detection (z-score, pure Python)
    incomes = [r["monthly_income"] for r in q(
        "SELECT monthly_income FROM employments WHERE monthly_income IS NOT NULL AND status='verified'"
    )]
    if len(incomes) > 5:
        mu, sigma = _mean(incomes), _std(incomes)
        if sigma > 0:
            for v in incomes:
                z = (v - mu) / sigma
                if abs(z) > 3:
                    out.append({
                        "type": "income_anomaly",
                        "severity": "LOW",
                        "problem": f"Suspicious income value {v}",
                        "evidence": [f"Z-score = {round(z, 2)}", f"Mean = {round(mu, 0)}"],
                        "recommendation": "Verify employment data for this record.",
                    })

    # 4. Assessment score distribution (clustering-style, pure Python)
    scores = [r["assessment_score"] for r in q(
        "SELECT assessment_score FROM trainees WHERE assessment_score IS NOT NULL AND archived=0"
    )]
    if len(scores) >= 6:
        low_count = sum(1 for s in scores if s < 50)
        mid_count = sum(1 for s in scores if 50 <= s < 75)
        high_count = sum(1 for s in scores if s >= 75)
        low_avg = round(_mean([s for s in scores if s < 50]), 1) if low_count else 0
        out.append({
            "type": "assessment_clusters",
            "severity": "INFO",
            "problem": "Trainees clustered by assessment score",
            "evidence": [
                f"Low (<50): {low_count}",
                f"Mid (50-75): {mid_count}",
                f"High (>=75): {high_count}",
                f"Low-group avg: {low_avg}",
            ],
            "recommendation": "Prioritize remedial training for the low-score cluster.",
        })

    # 5. Income trend (simple linear regression, pure Python)
    rows = q("""SELECT id, monthly_income FROM employments
                WHERE monthly_income IS NOT NULL AND status='verified' ORDER BY id""")
    if len(rows) >= 6:
        n = len(rows)
        xs = list(range(n))
        ys = [r["monthly_income"] for r in rows]
        mx, my = _mean(xs), _mean(ys)
        num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
        den = sum((x - mx) ** 2 for x in xs)
        if den:
            slope = num / den
            if slope < 0:
                out.append({
                    "type": "income_trend",
                    "severity": "MEDIUM",
                    "problem": "Declining income trend detected",
                    "evidence": [f"Slope = {round(slope, 2)} per record"],
                    "recommendation": "Investigate sector-wide salary compression.",
                })

    # 6. Non-placement dominant reason
    np_rows = q("SELECT reason, COUNT(*) c FROM non_placement GROUP BY reason ORDER BY c DESC LIMIT 1")
    if np_rows:
        top = np_rows[0]
        total_np = q("SELECT COUNT(*) c FROM non_placement", one=True)["c"] or 1
        pct = round(top["c"] / total_np * 100, 1)
        if pct > 30:
            out.append({
                "type": "non_placement_pattern",
                "severity": "HIGH",
                "problem": f"Major non-placement reason: {top['reason']}",
                "evidence": [f"{top['c']} trainees", f"{pct}% of all non-placements"],
                "recommendation": "Target intervention on this specific cause.",
            })

    return jsonify(count=len(out), insights=out)


# =============================================================================
#  SECTION 14 — GOVERNMENT ANALYTICS
# =============================================================================
@app.route("/api/analytics/summary", methods=["GET"])
@role_required("admin", "verifier")
def api_analytics_summary():
    total = q("SELECT COUNT(*) c FROM trainees WHERE archived=0", one=True)["c"]
    employed = q("SELECT COUNT(DISTINCT trainee_id) c FROM employments WHERE status='verified'", one=True)["c"]
    self_emp = q("""SELECT COUNT(DISTINCT trainee_id) c FROM employments
                    WHERE status='verified' AND employment_type='self'""", one=True)["c"]
    unemployed = max(total - employed, 0)
    return jsonify({
        "total": total, "employed": employed,
        "self_employed": self_emp, "unemployed": unemployed,
        "employment_rate": round(employed / total * 100, 2) if total else 0,
    })


@app.route("/api/analytics/retention", methods=["GET"])
@role_required("admin", "verifier")
def api_analytics_retention():
    out = {}
    for days in FOLLOWUP_PERIODS:
        total = q("SELECT COUNT(*) c FROM retention_checks WHERE days=?", (days,), one=True)["c"]
        kept = q("SELECT COUNT(*) c FROM retention_checks WHERE days=? AND retained=1", (days,), one=True)["c"]
        out[f"{days}d"] = {
            "total": total, "retained": kept,
            "rate": round(kept / total * 100, 2) if total else 0,
        }
    return jsonify(out)


@app.route("/api/analytics/income", methods=["GET"])
@role_required("admin", "verifier")
def api_analytics_income():
    overall = q("SELECT AVG(monthly_income) a FROM employments WHERE status='verified'", one=True)["a"] or 0
    growth = q("""SELECT AVG(monthly_income - pre_training_income) g
                  FROM employments WHERE status='verified' AND pre_training_income IS NOT NULL""",
               one=True)["g"] or 0
    return jsonify({
        "avg_starting_income": round(overall, 2),
        "avg_income_growth": round(growth, 2),
    })


@app.route("/api/analytics/relevance", methods=["GET"])
@role_required("admin", "verifier")
def api_analytics_relevance():
    rows = q("""SELECT job_relevance, COUNT(*) c FROM employments
                WHERE status='verified' GROUP BY job_relevance""")
    return jsonify({r["job_relevance"] or "unknown": r["c"] for r in rows})


# =============================================================================
#  SECTION 15 — NON-PLACEMENT
# =============================================================================
@app.route("/api/nonplacement", methods=["GET"])
@login_required
def api_nonplacement_overview():
    rows = q("SELECT reason, COUNT(*) c FROM non_placement GROUP BY reason ORDER BY c DESC")
    total = sum(r["c"] for r in rows) or 1
    data = [{"reason": r["reason"], "count": r["c"],
             "percentage": round(r["c"] / total * 100, 1)} for r in rows]
    return jsonify(breakdown=data)


@app.route("/api/nonplacement", methods=["POST"])
@login_required
def api_record_nonplacement():
    d = request.json or {}
    if not d.get("trainee_id") or d.get("reason") not in NON_PLACEMENT_REASONS:
        return jsonify(error="trainee_id and valid reason required"), 400
    nid = qx("INSERT INTO non_placement (trainee_id,reason,details) VALUES (?,?,?)",
             (d["trainee_id"], d["reason"], d.get("details")))
    audit("create", "non_placement", nid, after=d)
    return jsonify(id=nid), 201


# =============================================================================
#  SECTION 16 — GLOBAL SEARCH
# =============================================================================
@app.route("/api/search", methods=["GET"])
@login_required
def api_global_search():
    term = request.args.get("q", "").strip()
    if not term:
        return jsonify(results=[])
    like = f"%{term}%"
    out = []
    for row in q("SELECT id,full_name name,'trainee' kind FROM trainees WHERE full_name LIKE ? LIMIT 10", (like,)):
        out.append(dict(row))
    for row in q("SELECT id,name,'employer' kind FROM employers WHERE name LIKE ? LIMIT 10", (like,)):
        out.append(dict(row))
    for row in q("SELECT id,name,'provider' kind FROM providers WHERE name LIKE ? LIMIT 10", (like,)):
        out.append(dict(row))
    for row in q("SELECT id,name,'program' kind FROM programs WHERE name LIKE ? LIMIT 10", (like,)):
        out.append(dict(row))
    for row in q("SELECT id,official_id,'govt_official' kind FROM govt_officials WHERE full_name LIKE ? OR official_id LIKE ? LIMIT 10",
                 (like, like)):
        out.append(dict(row))
    return jsonify(results=out)


# =============================================================================
#  SECTION 17 — NOTIFICATIONS / CMS
# =============================================================================
@app.route("/api/notifications", methods=["GET"])
@login_required
def api_list_notifications():
    rows = q("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
             (session["uid"],))
    return jsonify([dict(r) for r in rows])


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def api_read_all():
    qx("UPDATE notifications SET read=1 WHERE user_id=?", (session["uid"],))
    return jsonify(ok=True)


@app.route("/api/notifications/broadcast", methods=["POST"])
@admin_required
def api_broadcast():
    d = request.json or {}
    title = d.get("title", "Announcement")
    body = d.get("body", "")
    for u in q("SELECT id FROM users"):
        notify(u["id"], "broadcast", title, body)
    audit("broadcast", "notification", 0, after={"title": title})
    return jsonify(ok=True)


@app.route("/api/cms/<key>", methods=["GET"])
def api_get_cms(key):
    row = q("SELECT * FROM cms_content WHERE key=?", (key,), one=True)
    return jsonify(dict(row) if row else {})


@app.route("/api/cms/<key>", methods=["PUT"])
@admin_required
def api_upsert_cms(key):
    d = request.json or {}
    exists = q("SELECT id FROM cms_content WHERE key=?", (key,), one=True)
    if exists:
        qx("UPDATE cms_content SET title=?, body=?, updated_at=? WHERE key=?",
           (d.get("title"), d.get("body"), now_iso(), key))
    else:
        qx("INSERT INTO cms_content (key,title,body) VALUES (?,?,?)",
           (key, d.get("title"), d.get("body")))
    audit("upsert", "cms", 0, after={"key": key})
    return jsonify(ok=True)


# =============================================================================
#  SECTION 18 — AUDIT + DATA QUALITY + SECURITY
# =============================================================================
@app.route("/api/audit", methods=["GET"])
@admin_required
def api_audit_list():
    rows = q("SELECT * FROM audit_log ORDER BY at DESC LIMIT 200")
    return jsonify([dict(r) for r in rows])


@app.route("/api/data-quality", methods=["GET"])
@admin_required
def api_data_quality():
    dup_trainees = q("""SELECT full_name, phone, COUNT(*) c FROM trainees
                        WHERE archived=0 AND phone IS NOT NULL
                        GROUP BY full_name, phone HAVING c > 1""")
    missing = {
        "no_phone": q("SELECT COUNT(*) c FROM trainees WHERE phone IS NULL OR phone=''", one=True)["c"],
        "no_email": q("SELECT COUNT(*) c FROM trainees WHERE email IS NULL OR email=''", one=True)["c"],
        "no_state": q("SELECT COUNT(*) c FROM trainees WHERE state IS NULL OR state=''", one=True)["c"],
    }
    conflicting = q("""SELECT DISTINCT np.trainee_id FROM non_placement np
                       JOIN employments em ON em.trainee_id=np.trainee_id
                       WHERE em.status='verified'""")
    unverified = q("SELECT COUNT(*) c FROM employers WHERE verified=0", one=True)["c"]
    return jsonify({
        "duplicate_trainees": [dict(r) for r in dup_trainees],
        "missing_fields": missing,
        "conflicting_outcomes": [dict(r) for r in conflicting],
        "unverified_employers": unverified,
    })


@app.route("/api/security/overview", methods=["GET"])
@admin_required
def api_security_overview():
    failed = q("SELECT COUNT(*) c FROM login_history WHERE success=0", one=True)["c"]
    recent_failed = q("""SELECT username, ip, at FROM login_history
                         WHERE success=0 ORDER BY at DESC LIMIT 20""")
    return jsonify({
        "failed_logins": failed,
        "recent_failures": [dict(r) for r in recent_failed],
    })


# =============================================================================
#  SECTION 19 — BACKUP / SETTINGS / HEALTH
# =============================================================================
@app.route("/api/backup/create", methods=["POST"])
@admin_required
def api_backup_create():
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"skillintel_{ts}.db"
    fpath = os.path.join(BACKUP_DIR, fname)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(fpath)
    with dst:
        src.backup(dst)
    dst.close(); src.close()
    size = os.path.getsize(fpath)
    bid = qx("INSERT INTO backup_history (filename,size_bytes) VALUES (?,?)", (fname, size))
    audit("backup", "database", bid, after={"filename": fname})
    return jsonify(id=bid, filename=fname, size=size)


@app.route("/api/backup/history", methods=["GET"])
@admin_required
def api_backup_history():
    rows = q("SELECT * FROM backup_history ORDER BY created_at DESC")
    return jsonify([dict(r) for r in rows])


@app.route("/api/settings", methods=["GET"])
@admin_required
def api_get_settings():
    rows = q("SELECT * FROM system_settings")
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/settings", methods=["PUT"])
@admin_required
def api_put_settings():
    d = request.json or {}
    for k, v in d.items():
        exists = q("SELECT key FROM system_settings WHERE key=?", (k,), one=True)
        if exists:
            qx("UPDATE system_settings SET value=? WHERE key=?", (str(v), k))
        else:
            qx("INSERT INTO system_settings (key,value) VALUES (?,?)", (k, str(v)))
    audit("update", "settings", 0, after=d)
    return jsonify(ok=True)


@app.route("/api/system/health", methods=["GET"])
def api_health():
    checks = {
        "backend": "online",
        "authentication": "working",
        "ai_engine": "pure-python",
        "data_dir": DATA_DIR,
        "db_path": DB_PATH,
    }
    try:
        q("SELECT 1", one=True)
        checks["database"] = "connected"
    except Exception as e:
        checks["database"] = f"error: {e}"
    try:
        errs = q("SELECT COUNT(*) c FROM system_errors", one=True)["c"]
        checks["error_count"] = errs
    except Exception:
        checks["error_count"] = "n/a"
    return jsonify(status="ok", checks=checks)


# =============================================================================
#  SECTION 20 — DEMO SEED (safe defaults)
# =============================================================================
@app.route("/api/demo/seed", methods=["POST"])
@admin_required
def api_demo_seed():
    random.seed(42)

    # Providers
    provider_ids = []
    for i, pname in enumerate(["Skill India Training Centre", "DataEdge Academy",
                               "TechSkill Institute", "NSDC Partner Institute"]):
        existing = q("SELECT id FROM providers WHERE name=?", (pname,), one=True)
        if existing:
            provider_ids.append(existing["id"])
        else:
            pid = qx("INSERT INTO providers (name,state,district,verified) VALUES (?,?,?,1)",
                     (pname, random.choice(STATES), f"District{i}"))
            provider_ids.append(pid)

    # Employers
    emp_ids = []
    for ename in ["ABC Technologies", "TCS", "Infosys Ltd.", "Wipro", "HCL"]:
        existing = q("SELECT id FROM employers WHERE name=?", (ename,), one=True)
        if existing:
            emp_ids.append(existing["id"])
        else:
            eid = qx("INSERT INTO employers (name,industry,state,verified) VALUES (?,?,?,1)",
                     (ename, random.choice(SECTORS), random.choice(STATES)))
            emp_ids.append(eid)

    # Programs
    program_ids = []
    for i in range(6):
        pname = f"Program {chr(65 + i)}"
        existing = q("SELECT id FROM programs WHERE name=?", (pname,), one=True)
        if existing:
            program_ids.append(existing["id"])
        else:
            pid = qx("""INSERT INTO programs (name,sector,provider_id,duration_weeks,skills_taught)
                        VALUES (?,?,?,?,?)""",
                     (pname, random.choice(SECTORS), random.choice(provider_ids),
                      random.choice([8, 12, 16, 24]), "Python, SQL, Excel"))
            program_ids.append(pid)

    # Trainees + enrollments + employments
    for i in range(60):
        email = f"trainee{i}@example.com"
        if q("SELECT id FROM trainees WHERE email=?", (email,), one=True):
            continue
        name = f"Trainee {i+1:03d}"
        st = random.choice(STATES)
        completed = random.random() < 0.8
        score = round(random.gauss(65, 15), 1)
        completion = (dt.date.today() - dt.timedelta(days=random.randint(10, 700))).isoformat() if completed else None
        tid = qx("""INSERT INTO trainees
            (trainee_code,full_name,gender,phone,email,category,qualification,state,district,
             training_status,completion_date,assessment_score,assessment_result,skills)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"TRN{10000+i}", name,
             random.choice(["Male", "Female", "Other"]),
             f"+9199{random.randint(1000000, 9999999)}",
             email, random.choice(["General", "OBC", "SC", "ST"]),
             "B.Sc.", st, f"District{i % 10}",
             "completed" if completed else "training",
             completion,
             score, "Pass" if score >= 40 else "Fail", "Python, SQL"))
        prog = random.choice(program_ids)
        comp_status = "completed" if completed else random.choice(["ongoing", "dropped"])
        qx("""INSERT INTO enrollments (trainee_id,program_id,enrolled_on,completion_status,attendance_pct)
              VALUES (?,?,?,?,?)""",
           (tid, prog, (dt.date.today() - dt.timedelta(days=500)).isoformat(),
            comp_status, round(random.uniform(50, 100), 1)))

        if completed and random.random() < 0.7:
            emp_id = qx("""INSERT INTO employments
                (trainee_id,employer_id,job_role,employment_type,start_date,
                 monthly_income,pre_training_income,job_relevance,status,
                 verified_by,verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, random.choice(emp_ids),
                 random.choice(["Data Analyst", "ML Engineer", "Associate", "Technician"]),
                 random.choice(["full", "part", "contract", "self"]),
                 (dt.date.today() - dt.timedelta(days=random.randint(10, 500))).isoformat(),
                 float(random.randint(12000, 45000)),
                 float(random.randint(6000, 15000)),
                 random.choice(["high", "medium", "low"]),
                 "verified" if random.random() < 0.8 else "pending",
                 1, now_iso()))
            for d in FOLLOWUP_PERIODS:
                if random.random() < 0.7:
                    qx("""INSERT INTO retention_checks (employment_id,days,retained,checked_at)
                          VALUES (?,?,?,?)""",
                       (emp_id, d, 1 if random.random() < 0.8 else 0, now_iso()))
        else:
            if completed and random.random() < 0.6:
                qx("INSERT INTO non_placement (trainee_id,reason,details) VALUES (?,?,?)",
                   (tid, random.choice(NON_PLACEMENT_REASONS), "seeded"))

        if completed and completion:
            _schedule_followups(tid, completion)

    # Seed a government official for demo
    if not q("SELECT id FROM govt_officials LIMIT 1", one=True):
        qx("""INSERT INTO govt_officials
              (official_id,full_name,email,department,access_level,password_hash,created_by)
              VALUES (?,?,?,?,?,?,?)""",
           (_gen_official_id(), "Demo Official", "official@gov.in",
            "Ministry of Skill Development", "State",
            generate_password_hash("Govt@123"), session.get("role", "super_admin")))

    audit("seed", "demo", 0)
    return jsonify(ok=True, message="Demo data seeded")


# =============================================================================
#  SECTION 21 — ROOT / STATIC / ERROR HANDLERS
# =============================================================================
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


# --- Serve the actual website (frontend folder) ---
@app.route("/", methods=["GET"])
def serve_root():
    """Serve frontend/index.html as the homepage."""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return send_file(index_path)
    # Fallback: API status page if no frontend found
    return render_template_string("""
    <html><head><title>SKILLINTEL Backend</title></head>
    <body style="font-family:system-ui;padding:2rem;background:#f1f5f9;">
      <h1 style="color:#2563eb;">SKILLINTEL backend is running</h1>
      <p>Base API: <code>/api/...</code></p>
      <p>Health: <a href="/api/system/health">/api/system/health</a></p>
      <p style="color:#64748b;">Frontend not found at <code>%FRONTEND%</code></p>
    </body></html>
    """.replace("%FRONTEND%", FRONTEND_DIR))


@app.route("/<path:path>", methods=["GET"])
def serve_frontend(path):
    """
    Serve static files from the frontend folder.
    Handles:
      /css/style.css            → frontend/css/style.css
      /js/api.js                → frontend/js/api.js
      /auth/login.html          → frontend/auth/login.html
      /admin/admin.html         → frontend/admin/admin.html
      /login                    → frontend/login.html (auto adds .html)
      /dashboard                → frontend/dashboard.html
      /admin                    → frontend/admin/index.html or admin.html
    """
    # Never intercept API routes (safety net — they're already matched above)
    if path.startswith("api/") or path.startswith("static/"):
        return jsonify(error="not found"), 404

    # 1. Try exact file: /css/style.css → frontend/css/style.css
    exact = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(exact):
        return send_file(exact)

    # 2. Try with .html: /login → frontend/login.html
    html_path = os.path.join(FRONTEND_DIR, path + ".html")
    if os.path.isfile(html_path):
        return send_file(html_path)

    # 3. Try path as folder with index.html: /admin → frontend/admin/index.html
    index_path = os.path.join(FRONTEND_DIR, path, "index.html")
    if os.path.isfile(index_path):
        return send_file(index_path)

    # 4. Try path as folder with <last-segment>.html: /admin → frontend/admin/admin.html
    last_seg = path.split("/")[-1]
    if last_seg:
        alt_html = os.path.join(FRONTEND_DIR, path, last_seg + ".html")
        if os.path.isfile(alt_html):
            return send_file(alt_html)

    # 5. Nothing found
    return jsonify(error="not found", path=path), 404


# =============================================================================
#  SECTION 22 — ENTRY POINT (Render + Local compatible)
# =============================================================================
# Ensure DB is initialised for BOTH gunicorn (production) and direct run
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"

    print("=" * 70)
    print(" SKILLINTEL backend ready")
    print(f" DB: {DB_PATH}")
    print(f" Super admin: {SUPER_ADMIN_USERNAME} / {SUPER_ADMIN_PASSWORD}")
    print(f" Running on port {port}  (debug={debug_mode})")
    print("=" * 70)

    # use_reloader=False avoids double init_db on local reloads
    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)
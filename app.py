# =============================================================================
#  SKILLINTEL BACKEND — app.py
#  SIH26135 · AI-Powered Skill Training Outcome Intelligence
#  Python 3.11+ compatible · No pandas/numpy/sklearn (Windows-safe)
#  PostgreSQL-backed (Render) + SQLite fallback (local)
# =============================================================================

import os
import io
import re
import csv
import json
import math
import random
import secrets
import sqlite3
import datetime as dt
from functools import wraps
from collections import defaultdict, Counter

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    pdfplumber = None
    PDFPLUMBER_AVAILABLE = False

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    PSYCOPG2_AVAILABLE = False

from flask import (
    Flask, request, jsonify, g, session, send_file, render_template_string
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# =============================================================================
#  SECTION 0 — APP CONFIG & CONSTANTS
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL and PSYCOPG2_AVAILABLE)

DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "skillintel.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)
try:
    from flask_cors import CORS
    CORS(app, supports_credentials=True)
except ImportError:
    pass  # CORS optional

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "skillintel-sih26135-FIXED-secret-key-do-not-change-abc123xyz987"
)

app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(days=30)
app.config["SESSION_COOKIE_PATH"] = "/"
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
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

SUPER_ADMIN_USERNAME = "vithanalamanisri@gmail.com"
SUPER_ADMIN_PASSWORD = "vManisri@1512"
SUPER_ADMIN_NAME = "Vithanala Manisri"


# =============================================================================
#  SECTION 1 — DATABASE CONNECTION
# =============================================================================
def get_db():
    if "db" not in g:
        if USE_POSTGRES:
            conn = psycopg2.connect(DATABASE_URL, sslmode="require")
            conn.autocommit = False
            g.db = conn
        else:
            conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def _normalize_sql(sql):
    if USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


def q(sql, args=(), one=False):
    db = get_db()
    sql_norm = _normalize_sql(sql)
    if USE_POSTGRES:
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = db.cursor()
    cur.execute(sql_norm, args)
    rows = cur.fetchall()
    cur.close()
    rows = [dict(r) for r in rows]
    if one:
        return rows[0] if rows else None
    return rows


def qx(sql, args=()):
    db = get_db()
    sql_norm = _normalize_sql(sql)
    is_insert = sql_norm.strip().upper().startswith("INSERT")

    if USE_POSTGRES and is_insert and "RETURNING" not in sql_norm.upper():
        sql_norm = sql_norm.rstrip().rstrip(";").rstrip() + " RETURNING id"

    cur = db.cursor()
    cur.execute(sql_norm, args)
    result = None
    try:
        if is_insert:
            if USE_POSTGRES and cur.description:
                row = cur.fetchone()
                if row:
                    result = row[0]
            else:
                result = cur.lastrowid
        else:
            result = cur.rowcount
    except Exception:
        result = None
    db.commit()
    cur.close()
    return result


def exec_script(sql):
    db = get_db()
    cur = db.cursor()
    if USE_POSTGRES:
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            try:
                cur.execute(stmt)
            except Exception as e:
                msg = str(e).lower()
                if "already exists" not in msg:
                    print(f"[schema] Warning: {e}")
    else:
        cur.executescript(sql)
    db.commit()
    cur.close()


# =============================================================================
#  SECTION 2 — SCHEMA
# =============================================================================
def build_schema():
    if USE_POSTGRES:
        return """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
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
    id SERIAL PRIMARY KEY,
    user_id INTEGER, username TEXT, role TEXT, success INTEGER,
    ip TEXT, user_agent TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS password_reset_requests (
    id SERIAL PRIMARY KEY,
    user_id INTEGER, reason TEXT, status TEXT DEFAULT 'pending',
    requested_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT, decided_by INTEGER
);
CREATE TABLE IF NOT EXISTS govt_officials (
    id SERIAL PRIMARY KEY,
    official_id TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL, mobile TEXT, department TEXT,
    access_level TEXT DEFAULT 'State', region TEXT,
    password_hash TEXT NOT NULL, status TEXT DEFAULT 'active',
    expires_at TEXT, must_change_password INTEGER DEFAULT 1,
    last_login TEXT, login_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, created_by TEXT,
    updated_at TEXT, status_changed_at TEXT, status_changed_by TEXT
);
CREATE TABLE IF NOT EXISTS govt_activity (
    id SERIAL PRIMARY KEY,
    message TEXT, type TEXT, actor TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trainees (
    id SERIAL PRIMARY KEY,
    trainee_code TEXT UNIQUE, user_id INTEGER, full_name TEXT NOT NULL,
    dob TEXT, gender TEXT, phone TEXT, email TEXT, category TEXT,
    qualification TEXT, institution TEXT, pass_year TEXT, score TEXT,
    state TEXT, district TEXT, preferred_role TEXT, preferred_location TEXT,
    expected_salary TEXT, training_status TEXT DEFAULT 'training',
    completion_date TEXT, assessment_score REAL, assessment_date TEXT,
    assessment_result TEXT, skills TEXT, certifications TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS providers (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL, state TEXT, district TEXT,
    contact TEXT, email TEXT, verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS programs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL, sector TEXT, provider_id INTEGER,
    duration_weeks INTEGER, skills_taught TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    trainee_id INTEGER NOT NULL, program_id INTEGER NOT NULL,
    enrolled_on TEXT, completed_on TEXT,
    completion_status TEXT DEFAULT 'ongoing', attendance_pct REAL
);
CREATE TABLE IF NOT EXISTS employers (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL, industry TEXT, state TEXT, district TEXT,
    contact TEXT, email TEXT, verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS employments (
    id SERIAL PRIMARY KEY,
    trainee_id INTEGER NOT NULL, employer_id INTEGER,
    job_role TEXT, employment_type TEXT, start_date TEXT,
    monthly_income REAL, pre_training_income REAL,
    job_relevance TEXT, skills_matched TEXT, skills_missing TEXT,
    status TEXT DEFAULT 'pending', verified_by INTEGER, verified_at TEXT
);
CREATE TABLE IF NOT EXISTS retention_checks (
    id SERIAL PRIMARY KEY,
    employment_id INTEGER NOT NULL, days INTEGER NOT NULL,
    retained INTEGER, checked_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS followups (
    id SERIAL PRIMARY KEY,
    trainee_id INTEGER NOT NULL, period_days INTEGER NOT NULL,
    due_date TEXT NOT NULL, status TEXT DEFAULT 'due',
    submitted_at TEXT, verified_at TEXT, verified_by INTEGER, data TEXT
);
CREATE TABLE IF NOT EXISTS verifications (
    id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending', submitted_by INTEGER,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    decided_by INTEGER, decided_at TEXT, remarks TEXT
);
CREATE TABLE IF NOT EXISTS non_placement (
    id SERIAL PRIMARY KEY,
    trainee_id INTEGER NOT NULL, reason TEXT NOT NULL, details TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    trainee_id INTEGER, employer_id INTEGER, provider_id INTEGER,
    rating INTEGER, satisfaction INTEGER, skills_satisfaction INTEGER,
    text TEXT, verified INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER, type TEXT, title TEXT, body TEXT,
    read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cms_content (
    id SERIAL PRIMARY KEY,
    key TEXT UNIQUE, title TEXT, body TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    actor_id INTEGER, actor_username TEXT, action TEXT,
    entity TEXT, entity_id INTEGER, before_json TEXT, after_json TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS system_errors (
    id SERIAL PRIMARY KEY,
    level TEXT, message TEXT, trace TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS backup_history (
    id SERIAL PRIMARY KEY,
    filename TEXT, size_bytes INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
    else:
        return """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    full_name TEXT, email TEXT UNIQUE, phone TEXT, role TEXT NOT NULL,
    status TEXT DEFAULT 'active', failed_attempts INTEGER DEFAULT 0,
    locked_until TEXT, last_login TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS login_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, username TEXT, role TEXT, success INTEGER,
    ip TEXT, user_agent TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS password_reset_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, reason TEXT, status TEXT DEFAULT 'pending',
    requested_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT, decided_by INTEGER
);
CREATE TABLE IF NOT EXISTS govt_officials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    official_id TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL, mobile TEXT, department TEXT,
    access_level TEXT DEFAULT 'State', region TEXT,
    password_hash TEXT NOT NULL, status TEXT DEFAULT 'active',
    expires_at TEXT, must_change_password INTEGER DEFAULT 1,
    last_login TEXT, login_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, created_by TEXT,
    updated_at TEXT, status_changed_at TEXT, status_changed_by TEXT
);
CREATE TABLE IF NOT EXISTS govt_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT, type TEXT, actor TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trainees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_code TEXT UNIQUE, user_id INTEGER, full_name TEXT NOT NULL,
    dob TEXT, gender TEXT, phone TEXT, email TEXT, category TEXT,
    qualification TEXT, institution TEXT, pass_year TEXT, score TEXT,
    state TEXT, district TEXT, preferred_role TEXT, preferred_location TEXT,
    expected_salary TEXT, training_status TEXT DEFAULT 'training',
    completion_date TEXT, assessment_score REAL, assessment_date TEXT,
    assessment_result TEXT, skills TEXT, certifications TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL, state TEXT, district TEXT,
    contact TEXT, email TEXT, verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, sector TEXT, provider_id INTEGER,
    duration_weeks INTEGER, skills_taught TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL, program_id INTEGER NOT NULL,
    enrolled_on TEXT, completed_on TEXT,
    completion_status TEXT DEFAULT 'ongoing', attendance_pct REAL
);
CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL, industry TEXT, state TEXT, district TEXT,
    contact TEXT, email TEXT, verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS employments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL, employer_id INTEGER,
    job_role TEXT, employment_type TEXT, start_date TEXT,
    monthly_income REAL, pre_training_income REAL,
    job_relevance TEXT, skills_matched TEXT, skills_missing TEXT,
    status TEXT DEFAULT 'pending', verified_by INTEGER, verified_at TEXT
);
CREATE TABLE IF NOT EXISTS retention_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employment_id INTEGER NOT NULL, days INTEGER NOT NULL,
    retained INTEGER, checked_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL, period_days INTEGER NOT NULL,
    due_date TEXT NOT NULL, status TEXT DEFAULT 'due',
    submitted_at TEXT, verified_at TEXT, verified_by INTEGER, data TEXT
);
CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending', submitted_by INTEGER,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    decided_by INTEGER, decided_at TEXT, remarks TEXT
);
CREATE TABLE IF NOT EXISTS non_placement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER NOT NULL, reason TEXT NOT NULL, details TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trainee_id INTEGER, employer_id INTEGER, provider_id INTEGER,
    rating INTEGER, satisfaction INTEGER, skills_satisfaction INTEGER,
    text TEXT, verified INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, type TEXT, title TEXT, body TEXT,
    read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cms_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE, title TEXT, body TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER, actor_username TEXT, action TEXT,
    entity TEXT, entity_id INTEGER, before_json TEXT, after_json TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS system_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT, message TEXT, trace TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS backup_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT, size_bytes INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db():
    with app.app_context():
        print("=" * 70)
        print(f"[init_db] Using: {'PostgreSQL' if USE_POSTGRES else 'SQLite'}")
        if not USE_POSTGRES:
            print(f"[init_db] DB path: {DB_PATH}")
        exec_script(build_schema())
        try:
            existing = q("SELECT id FROM users WHERE role='super_admin'", one=True)
            if not existing:
                qx(
                    "INSERT INTO users (username,password_hash,full_name,email,role) VALUES (?,?,?,?,?)",
                    (
                        SUPER_ADMIN_USERNAME,
                        generate_password_hash(SUPER_ADMIN_PASSWORD),
                        SUPER_ADMIN_NAME,
                        SUPER_ADMIN_USERNAME,
                        "super_admin",
                    ),
                )
                print(f"[init_db] Super admin created: {SUPER_ADMIN_USERNAME}")
            else:
                print(f"[init_db] Super admin exists")
        except Exception as e:
            print(f"[init_db] Super admin check failed: {e}")
        try:
            users = q("SELECT COUNT(*) c FROM users", one=True)["c"]
            trainees = q("SELECT COUNT(*) c FROM trainees", one=True)["c"]
            print(f"[init_db] Users: {users} | Trainees: {trainees}")
        except Exception as e:
            print(f"[init_db] Count error: {e}")
        print("=" * 70)


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
    try:
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
    except Exception as e:
        print(f"[audit] failed: {e}")


def notify(user_id, ntype, title, body=""):
    try:
        qx("INSERT INTO notifications (user_id,type,title,body) VALUES (?,?,?,?)",
           (user_id, ntype, title, body))
    except Exception:
        pass


def notify_admins(ntype, title, body=""):
    try:
        for u in q("SELECT id FROM users WHERE role IN ('super_admin','admin')"):
            notify(u["id"], ntype, title, body)
    except Exception:
        pass


def security_event(message, details=""):
    try:
        qx("INSERT INTO system_errors (level,message,trace) VALUES ('security',?,?)",
           (message, details))
    except Exception:
        pass


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("uid") and not session.get("govt_id"):
            return jsonify(error="Authentication required"), 401
        return f(*a, **kw)
    return wrapper


def role_required(*allowed):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            u = current_user()
            govt = session.get("govt_id")
            if not u and not govt:
                return jsonify(error="Authentication required"), 401
            if u:
                if u["role"] not in allowed and u["role"] != "super_admin":
                    return jsonify(error="Forbidden"), 403
            else:
                if not any(r in allowed for r in ("admin", "verifier")):
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
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            return jsonify(error=f"duplicate: {e}"), 409
        return jsonify(error=f"register failed: {e}"), 500
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

    # --- ADMIN / SUPER ADMIN / VERIFIER ---
    if role in ("admin", "super_admin", "verifier"):
        username = (d.get("username") or d.get("email") or "").strip()
        password = d.get("password") or ""
        if not username or not password:
            return jsonify(error="username and password required"), 400
        u = q("SELECT * FROM users WHERE username=?", (username,), one=True)
        if not u:
            u = q("SELECT * FROM users WHERE email=?", (username,), one=True)
        if not u or not check_password_hash(u["password_hash"], password):
            try:
                qx("INSERT INTO login_history (username,role,success,ip,user_agent) VALUES (?,?,0,?,?)",
                   (username, role, ip, ua))
            except Exception:
                pass
            security_event("Failed admin login", f"username={username} ip={ip}")
            return jsonify(error="Invalid credentials"), 401
        if u["locked_until"]:
            try:
                if dt.datetime.fromisoformat(u["locked_until"]) > dt.datetime.utcnow():
                    return jsonify(error="Account locked. Try later."), 423
            except Exception:
                pass
        if u["status"] not in ("active",):
            return jsonify(error="Account unavailable"), 403
        qx("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=? WHERE id=?",
           (now_iso(), u["id"]))
        try:
            qx("INSERT INTO login_history (user_id,username,role,success,ip,user_agent) VALUES (?,?,?,1,?,?)",
               (u["id"], u["username"], u["role"], ip, ua))
        except Exception:
            pass
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
            try:
                qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
                   (f"Failed login - ID {official_id}", "error", ip))
            except Exception:
                pass
            security_event("Failed govt login", f"official_id={official_id} ip={ip}")
            return jsonify(error="Invalid Official ID or Email, or wrong password"), 401

        if o["status"] != "active":
            return jsonify(error="Your government access has been suspended"), 403

        if o["expires_at"]:
            try:
                if dt.datetime.fromisoformat(o["expires_at"]) < dt.datetime.utcnow():
                    return jsonify(error="Your government access has expired"), 403
            except Exception:
                pass

        qx("UPDATE govt_officials SET last_login=?, login_count=COALESCE(login_count,0)+1 WHERE id=?",
           (now_iso(), o["id"]))
        try:
            qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
               (f"Login success - {o['full_name']} ({official_id}, {o['access_level']})", "success", ip))
        except Exception:
            pass

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

    email = (d.get("email") or d.get("username") or "").strip().lower()
    password = d.get("password") or ""
    if not email or not password:
        return jsonify(error="email and password required"), 400

    u = q("SELECT * FROM users WHERE email=?", (email,), one=True)
    if not u:
        u = q("SELECT * FROM users WHERE username=?", (email,), one=True)
    if not u or not check_password_hash(u["password_hash"], password):
        security_event("Failed login", f"role={role} email={email} ip={ip}")
        return jsonify(error="Invalid credentials"), 401
    session.permanent = True
    session["uid"] = u["id"]
    session["role"] = role
    session["email"] = email
    return jsonify(id=u["id"], role=role, email=email, full_name=u["full_name"])


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.route("/api/auth/me", methods=["GET"])
@login_required
def api_me():
    u = current_user()
    if u:
        return jsonify(
            id=u["id"], username=u["username"], role=u["role"],
            full_name=u["full_name"], email=u["email"],
        )
    return jsonify(
        role="government",
        officialId=session.get("govt_id"),
        accessLevel=session.get("govt_level"),
    )


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def api_change_password():
    d = request.json or {}
    old, new = d.get("old"), d.get("new")
    u = current_user()
    if not u:
        return jsonify(error="Only user accounts can change password here"), 400
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
#  SECTION 6 — GOVERNMENT OFFICIALS
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

    try:
        qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
           (f"Created: {full_name} - {official_id} - {d.get('accessLevel','State')} level",
            "success", session.get("role")))
    except Exception:
        pass
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
    try:
        qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
           (f"Updated: {o['full_name']}", "info", session.get("role")))
    except Exception:
        pass
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
    try:
        qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
           (msg, "success" if new_status == "active" else "error", session.get("role")))
    except Exception:
        pass
    audit("toggle", "govt_official", oid, after={"status": new_status})
    return jsonify(status=new_status)


@app.route("/api/admin/govt-officials/<int:oid>", methods=["DELETE"])
@admin_required
def api_delete_govt_official(oid):
    o = q("SELECT * FROM govt_officials WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(error="not found"), 404
    qx("DELETE FROM govt_officials WHERE id=?", (oid,))
    try:
        qx("INSERT INTO govt_activity (message,type,actor) VALUES (?,?,?)",
           (f"Deleted: {o['full_name']} ({o['official_id']})", "error", session.get("role")))
    except Exception:
        pass
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
        retention[str(days)] = row["c"] if row else 0

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
            w.writerow(list(r.values()))
    return send_file(io.BytesIO(buf.getvalue().encode()), mimetype="text/csv",
                     as_attachment=True, download_name="users.csv")


# =============================================================================
#  SECTION 9 — TRAINEES
# =============================================================================
@app.route("/api/trainees", methods=["GET"])
def api_list_trainees():
    search = request.args.get("search", "").strip()
    where, args = ["archived=0"], []
    if search:
        where.append("(full_name LIKE ? OR trainee_code LIKE ? OR email LIKE ?)")
        args += [f"%{search}%"] * 3
    rows = q(f"SELECT * FROM trainees WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT 500", args)
    return jsonify([dict(r) for r in rows])


@app.route("/api/trainees", methods=["POST"])
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

    try:
        ensure_followup_schedule(tid)
    except Exception as e:
        print(f"[warn] followup scheduling failed: {e}")

    audit("create", "trainee", tid, after=d)
    return jsonify(id=tid, trainee_code=code), 201


@app.route("/api/trainees/<int:tid>/360", methods=["GET"])
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
def api_list_employers():
    rows = q("SELECT * FROM employers ORDER BY created_at DESC")
    return jsonify([dict(r) for r in rows])


@app.route("/api/employers", methods=["POST"])
def api_create_employer():
    d = request.json or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    try:
        eid = qx("""INSERT INTO employers (name,industry,state,district,contact,email)
                    VALUES (?,?,?,?,?,?)""",
                 (d["name"], d.get("industry"), d.get("state"), d.get("district"),
                  d.get("contact"), d.get("email")))
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            existing = q("SELECT id FROM employers WHERE LOWER(name)=LOWER(?)", (d["name"],), one=True)
            return jsonify(id=existing["id"] if existing else None, ok=True), 200
        return jsonify(error=str(e)), 500
    audit("create", "employer", eid, after=d)
    return jsonify(id=eid), 201


@app.route("/api/employers/<int:eid>/verify", methods=["POST"])
@role_required("admin", "verifier")
def api_verify_employer(eid):
    qx("UPDATE employers SET verified=1 WHERE id=?", (eid,))
    audit("verify", "employer", eid)
    return jsonify(ok=True)


@app.route("/api/providers", methods=["GET"])
def api_list_providers():
    rows = q("SELECT * FROM providers ORDER BY name")
    return jsonify([dict(r) for r in rows])


@app.route("/api/providers", methods=["POST"])
def api_create_provider():
    d = request.json or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    try:
        pid = qx("""INSERT INTO providers (name,state,district,contact,email)
                    VALUES (?,?,?,?,?)""",
                 (d["name"], d.get("state"), d.get("district"),
                  d.get("contact"), d.get("email")))
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            existing = q("SELECT id FROM providers WHERE LOWER(name)=LOWER(?)", (d["name"],), one=True)
            return jsonify(id=existing["id"] if existing else None, ok=True), 200
        return jsonify(error=str(e)), 500
    audit("create", "provider", pid, after=d)
    return jsonify(id=pid), 201


@app.route("/api/providers/<int:pid>/verify", methods=["POST"])
@role_required("admin", "verifier")
def api_verify_provider(pid):
    qx("UPDATE providers SET verified=1 WHERE id=?", (pid,))
    audit("verify", "provider", pid)
    return jsonify(ok=True)


@app.route("/api/programs", methods=["GET"])
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
            try:
                qx("""INSERT INTO followups (trainee_id,period_days,due_date,status)
                      VALUES (?,?,?, 'due')""",
                   (trainee_id, days, due))
            except Exception as e:
                print(f"[followup] insert failed: {e}")


@app.route("/api/followups", methods=["GET"])
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
def api_submit_followup(fid):
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
#  SECTION 12B — EMPLOYMENTS
# =============================================================================
@app.route("/api/employments", methods=["GET"])
def api_list_employments():
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
def api_create_employment():
    d = request.json or {}
    trainee_id = d.get("trainee_id")
    if not trainee_id:
        return jsonify(error="trainee_id required"), 400

    t = q("SELECT id FROM trainees WHERE id=?", (trainee_id,), one=True)
    if not t:
        return jsonify(error="Trainee not found"), 404

    employer_id = d.get("employer_id")
    employer_name = (d.get("employer_name") or "").strip()
    if not employer_id and employer_name:
        existing_emp = q("SELECT id FROM employers WHERE LOWER(name)=LOWER(?)",
                         (employer_name,), one=True)
        if existing_emp:
            employer_id = existing_emp["id"]

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
        emp_id = qx("""INSERT INTO employments
            (trainee_id,employer_id,job_role,employment_type,start_date,
             monthly_income,pre_training_income,job_relevance,
             skills_matched,skills_missing,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (trainee_id, employer_id, job_role, employment_type, start_date,
             monthly_income, pre_training_income, job_relevance,
             skills_matched, skills_missing, "pending"))
        audit("create", "employment", emp_id, after=d)

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
    emp = q("SELECT * FROM employments WHERE id=?", (eid,), one=True)
    if not emp:
        return jsonify(error="not found"), 404
    qx("""UPDATE employments SET status='verified', verified_by=?, verified_at=?
          WHERE id=?""",
       (session.get("uid"), now_iso(), eid))
    qx("""UPDATE verifications SET status='approved', decided_by=?, decided_at=?
          WHERE entity_type='employment' AND entity_id=? AND status='pending'""",
       (session.get("uid"), now_iso(), eid))
    if emp.get("start_date"):
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
#  SECTION 12C — TRAINEE SELF REGISTRATION
# =============================================================================
@app.route("/api/trainees/register", methods=["POST"])
def api_register_trainee_full():
    d = request.json or {}
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    full_name = (d.get("full_name") or "").strip()

    if not (email and password and full_name):
        return jsonify(error="email, password, full_name required"), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters"), 400

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
        except Exception:
            row = q("SELECT id FROM users WHERE email=?", (email,), one=True)
            uid = row["id"] if row else None

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

    try:
        ensure_followup_schedule(trainee_id)
    except Exception as e:
        print(f"[warn] followup schedule failed: {e}")

    audit("register", "trainee", trainee_id)
    code_final = q("SELECT trainee_code FROM trainees WHERE id=?", (trainee_id,), one=True)["trainee_code"]
    return jsonify(id=trainee_id, user_id=uid, trainee_code=code_final), 201


# =============================================================================
#  SECTION 12D — GOVERNMENT ANALYTICS
# =============================================================================
@app.route("/api/govt/analytics", methods=["GET"])
def api_govt_analytics():
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

    by_state = q("""SELECT state, COUNT(*) c FROM trainees
                    WHERE state IS NOT NULL AND archived=0
                    GROUP BY state ORDER BY c DESC LIMIT 15""")

    by_sector = q("""SELECT t.preferred_role AS sector, COUNT(*) c
                     FROM employments em
                     JOIN trainees t ON t.id=em.trainee_id
                     WHERE em.status='verified' AND t.preferred_role IS NOT NULL
                     GROUP BY t.preferred_role ORDER BY c DESC LIMIT 10""")

    rate = round(employed / completed * 100, 2) if completed else 0
    growth = round(avg_income - avg_previous, 2) if avg_income and avg_previous else 0
    growth_pct = round(growth / avg_previous * 100, 1) if avg_previous else 0

    non_placement = q("""SELECT reason, COUNT(*) c FROM non_placement
                         GROUP BY reason ORDER BY c DESC""")

    return jsonify({
        "totals": {
            "trainees": total_trainees, "completed": completed,
            "employed": employed, "employment_rate": rate,
        },
        "income": {
            "avg_current": round(avg_income, 2), "avg_previous": round(avg_previous, 2),
            "growth": growth, "growth_pct": growth_pct,
        },
        "by_state": [dict(r) for r in by_state],
        "by_sector": [dict(r) for r in by_sector],
        "non_placement": [dict(r) for r in non_placement],
    })


@app.route("/api/govt/trainee/<int:tid>/full", methods=["GET"])
def api_govt_trainee_full(tid):
    t = q("SELECT * FROM trainees WHERE id=?", (tid,), one=True)
    if not t:
        return jsonify(error="not found"), 404
    result = dict(t)
    result["enrollments"] = [dict(r) for r in q("""
        SELECT e.*, p.name program_name, p.sector
        FROM enrollments e LEFT JOIN programs p ON p.id=e.program_id
        WHERE e.trainee_id=?""", (tid,))]
    result["employments"] = [dict(r) for r in q("""
        SELECT em.*, er.name employer_name, er.industry
        FROM employments em LEFT JOIN employers er ON er.id=em.employer_id
        WHERE em.trainee_id=? ORDER BY em.start_date DESC""", (tid,))]
    if result["employments"]:
        latest_emp_id = result["employments"][0]["id"]
        result["retention"] = [dict(r) for r in q("""
            SELECT * FROM retention_checks WHERE employment_id=? ORDER BY days""",
            (latest_emp_id,))]
    else:
        result["retention"] = []
    result["followups"] = [dict(r) for r in q("""
        SELECT * FROM followups WHERE trainee_id=? ORDER BY period_days""", (tid,))]
    result["non_placement"] = [dict(r) for r in q("""
        SELECT * FROM non_placement WHERE trainee_id=?""", (tid,))]
    result["feedback"] = [dict(r) for r in q("""
        SELECT * FROM feedback WHERE trainee_id=?""", (tid,))]
    return jsonify(result)


# =============================================================================
#  SECTION 12E — ADMIN SEED
# =============================================================================
@app.route("/api/admin/seed-complete", methods=["POST"])
@admin_required
def api_admin_seed_complete():
    provider_ids = []
    for i, pname in enumerate(["Skill India Centre", "NSDC Partner", "DataEdge Academy"]):
        existing = q("SELECT id FROM providers WHERE name=?", (pname,), one=True)
        if existing:
            provider_ids.append(existing["id"])
        else:
            provider_ids.append(qx(
                "INSERT INTO providers (name,state,district,verified) VALUES (?,?,?,1)",
                (pname, random.choice(STATES), f"District{i}")))

    emp_ids = []
    for ename in ["TechCorp", "InfoSys", "Wipro Ltd"]:
        existing = q("SELECT id FROM employers WHERE name=?", (ename,), one=True)
        if existing:
            emp_ids.append(existing["id"])
        else:
            emp_ids.append(qx(
                "INSERT INTO employers (name,industry,state,verified) VALUES (?,?,?,1)",
                (ename, random.choice(SECTORS), random.choice(STATES))))

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
        qx("""INSERT INTO enrollments (trainee_id,program_id,enrolled_on,
              completion_status,attendance_pct)
              VALUES (?,?,?,?,?)""",
           (tid, random.choice(program_ids),
            (dt.date.today() - dt.timedelta(days=200)).isoformat(),
            "completed", round(random.uniform(70, 100), 1)))
        ensure_followup_schedule(tid,
            (dt.date.today() - dt.timedelta(days=random.randint(60, 400))).isoformat())
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
#  SECTION 13 — AI INSIGHTS
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
    out = []

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
                "type": "low_employment_program", "severity": "HIGH",
                "problem": f"Low employment in {prog['name']}",
                "evidence": [f"{total} trainees", f"{employed} employed", f"{rate}% employment"],
                "recommendation": "Review curriculum and strengthen industry-aligned training.",
            })

    states = q("""SELECT DISTINCT state FROM trainees WHERE state IS NOT NULL AND archived=0""")
    for s in states:
        st = s["state"]
        if not st:
            continue
        total = q("SELECT COUNT(*) c FROM trainees WHERE state=? AND archived=0", (st,), one=True)["c"] or 0
        employed = q("""SELECT COUNT(DISTINCT em.trainee_id) c FROM employments em
                        JOIN trainees t ON t.id=em.trainee_id
                        WHERE t.state=? AND em.status='verified'""",
                     (st,), one=True)["c"] or 0
        rate = round(employed / total * 100, 1) if total else 0
        if rate < 30 and total >= 10:
            out.append({
                "type": "regional_gap", "severity": "MEDIUM",
                "problem": f"Low employment in {st}",
                "evidence": [f"{total} trainees", f"{employed} employed", f"{rate}%"],
                "recommendation": "Focus local employer partnerships.",
            })

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
                        "type": "income_anomaly", "severity": "LOW",
                        "problem": f"Suspicious income value {v}",
                        "evidence": [f"Z-score = {round(z, 2)}", f"Mean = {round(mu, 0)}"],
                        "recommendation": "Verify employment data for this record.",
                    })

    scores = [r["assessment_score"] for r in q(
        "SELECT assessment_score FROM trainees WHERE assessment_score IS NOT NULL AND archived=0"
    )]
    if len(scores) >= 6:
        low_count = sum(1 for s in scores if s < 50)
        mid_count = sum(1 for s in scores if 50 <= s < 75)
        high_count = sum(1 for s in scores if s >= 75)
        low_avg = round(_mean([s for s in scores if s < 50]), 1) if low_count else 0
        out.append({
            "type": "assessment_clusters", "severity": "INFO",
            "problem": "Trainees clustered by assessment score",
            "evidence": [f"Low (<50): {low_count}", f"Mid (50-75): {mid_count}",
                         f"High (>=75): {high_count}", f"Low-group avg: {low_avg}"],
            "recommendation": "Prioritize remedial training for the low-score cluster.",
        })

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
                    "type": "income_trend", "severity": "MEDIUM",
                    "problem": "Declining income trend detected",
                    "evidence": [f"Slope = {round(slope, 2)} per record"],
                    "recommendation": "Investigate sector-wide salary compression.",
                })

    np_rows = q("SELECT reason, COUNT(*) c FROM non_placement GROUP BY reason ORDER BY c DESC LIMIT 1")
    if np_rows:
        top = np_rows[0]
        total_np = q("SELECT COUNT(*) c FROM non_placement", one=True)["c"] or 1
        pct = round(top["c"] / total_np * 100, 1)
        if pct > 30:
            out.append({
                "type": "non_placement_pattern", "severity": "HIGH",
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
def api_nonplacement_overview():
    rows = q("SELECT reason, COUNT(*) c FROM non_placement GROUP BY reason ORDER BY c DESC")
    total = sum(r["c"] for r in rows) or 1
    data = [{"reason": r["reason"], "count": r["c"],
             "percentage": round(r["c"] / total * 100, 1)} for r in rows]
    return jsonify(breakdown=data)


@app.route("/api/nonplacement", methods=["POST"])
def api_record_nonplacement():
    d = request.json or {}
    if not d.get("trainee_id") or d.get("reason") not in NON_PLACEMENT_REASONS:
        return jsonify(error="trainee_id and valid reason required"), 400
    nid = qx("INSERT INTO non_placement (trainee_id,reason,details) VALUES (?,?,?)",
             (d["trainee_id"], d["reason"], d.get("details")))
    audit("create", "non_placement", nid, after=d)
    return jsonify(id=nid), 201


# =============================================================================
#  SECTION 15B — DEMO SEED: NON-PLACEMENT
# =============================================================================
@app.route("/api/demo/seed-nonplacement", methods=["POST"])
@admin_required
def api_seed_nonplacement():
    """Create non_placement records for trainees who have NO verified employment."""
    random.seed(99)
    count = 0
    trainees = q("SELECT id, state FROM trainees WHERE archived=0")
    for t in trainees:
        has_emp = q("""SELECT id FROM employments
                       WHERE trainee_id=? AND status='verified' LIMIT 1""",
                    (t["id"],), one=True)
        if has_emp:
            continue
        already = q("SELECT id FROM non_placement WHERE trainee_id=? LIMIT 1",
                    (t["id"],), one=True)
        if already:
            continue
        reason = random.choice(NON_PLACEMENT_REASONS)
        qx("INSERT INTO non_placement (trainee_id,reason,details) VALUES (?,?,?)",
           (t["id"], reason, "seeded"))
        count += 1
    return jsonify(ok=True, seeded=count)


# =============================================================================
#  SECTION 15C — DEMO SEED: PROGRAMS + ENROLLMENTS
# =============================================================================
@app.route("/api/demo/seed-programs", methods=["POST"])
@admin_required
def api_seed_programs():
    """Ensure there are programs + enrollments + employers so program comparison works."""
    random.seed(7)

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

    emp_ids = []
    for ename in ["ABC Technologies", "TCS", "Infosys Ltd.", "Wipro", "HCL"]:
        existing = q("SELECT id FROM employers WHERE name=?", (ename,), one=True)
        if existing:
            emp_ids.append(existing["id"])
        else:
            eid = qx("INSERT INTO employers (name,industry,state,verified) VALUES (?,?,?,1)",
                     (ename, random.choice(SECTORS), random.choice(STATES)))
            emp_ids.append(eid)

    # Enroll every trainee who isn't enrolled
    enrolled_count = 0
    for t in q("SELECT id FROM trainees WHERE archived=0 LIMIT 200"):
        exists = q("SELECT id FROM enrollments WHERE trainee_id=? LIMIT 1", (t["id"],), one=True)
        if exists:
            continue
        qx("""INSERT INTO enrollments
              (trainee_id,program_id,enrolled_on,completion_status,attendance_pct)
              VALUES (?,?,?,?,?)""",
           (t["id"], random.choice(program_ids),
            (dt.date.today() - dt.timedelta(days=random.randint(60, 400))).isoformat(),
            "completed", round(random.uniform(60, 100), 1)))
        enrolled_count += 1

    return jsonify(ok=True, programs=len(program_ids), enrolled=enrolled_count)


# =============================================================================
#  SECTION 15D — DEMO SEED: EVERYTHING
# =============================================================================
@app.route("/api/demo/seed-all", methods=["POST"])
@admin_required
def api_seed_all():
    """Seed demo data for every dashboard at once."""
    from flask import Response
    results = {}
    with app.test_request_context():
        try:
            random.seed(99)
            np_count = 0
            for t in q("SELECT id FROM trainees WHERE archived=0"):
                has_emp = q("""SELECT id FROM employments
                               WHERE trainee_id=? AND status='verified' LIMIT 1""",
                            (t["id"],), one=True)
                if has_emp:
                    continue
                already = q("SELECT id FROM non_placement WHERE trainee_id=? LIMIT 1",
                            (t["id"],), one=True)
                if already:
                    continue
                qx("INSERT INTO non_placement (trainee_id,reason,details) VALUES (?,?,?)",
                   (t["id"], random.choice(NON_PLACEMENT_REASONS), "seeded"))
                np_count += 1
            results["non_placement"] = np_count
        except Exception as e:
            results["non_placement_error"] = str(e)

        try:
            random.seed(7)
            provider_ids = []
            for i, pname in enumerate(["Skill India Training Centre", "DataEdge Academy",
                                       "TechSkill Institute", "NSDC Partner Institute"]):
                existing = q("SELECT id FROM providers WHERE name=?", (pname,), one=True)
                if existing:
                    provider_ids.append(existing["id"])
                else:
                    provider_ids.append(qx(
                        "INSERT INTO providers (name,state,district,verified) VALUES (?,?,?,1)",
                        (pname, random.choice(STATES), f"District{i}")))

            program_ids = []
            for i in range(6):
                pname = f"Program {chr(65 + i)}"
                existing = q("SELECT id FROM programs WHERE name=?", (pname,), one=True)
                if existing:
                    program_ids.append(existing["id"])
                else:
                    program_ids.append(qx(
                        """INSERT INTO programs (name,sector,provider_id,duration_weeks,skills_taught)
                           VALUES (?,?,?,?,?)""",
                        (pname, random.choice(SECTORS), random.choice(provider_ids),
                         random.choice([8, 12, 16, 24]), "Python, SQL, Excel")))

            enrolled = 0
            for t in q("SELECT id FROM trainees WHERE archived=0 LIMIT 200"):
                exists = q("SELECT id FROM enrollments WHERE trainee_id=? LIMIT 1",
                           (t["id"],), one=True)
                if exists:
                    continue
                qx("""INSERT INTO enrollments
                      (trainee_id,program_id,enrolled_on,completion_status,attendance_pct)
                      VALUES (?,?,?,?,?)""",
                   (t["id"], random.choice(program_ids),
                    (dt.date.today() - dt.timedelta(days=random.randint(60, 400))).isoformat(),
                    "completed", round(random.uniform(60, 100), 1)))
                enrolled += 1
            results["programs_created"] = len(program_ids)
            results["enrollments_created"] = enrolled
        except Exception as e:
            results["programs_error"] = str(e)

    return jsonify(ok=True, results=results)


# =============================================================================
#  SECTION 16 — GLOBAL SEARCH
# =============================================================================
@app.route("/api/search", methods=["GET"])
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
                        GROUP BY full_name, phone HAVING COUNT(*) > 1""")
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
    if USE_POSTGRES:
        return jsonify(error="Backups not available on PostgreSQL. Use Render's database backups."), 400
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
        "database_type": "postgres" if USE_POSTGRES else "sqlite",
        "database_url_set": bool(DATABASE_URL),
        "ai_engine": "pure-python",
    }
    if not USE_POSTGRES:
        checks["db_path"] = DB_PATH
        checks["data_dir"] = DATA_DIR
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


@app.route("/api/admin/check-db", methods=["GET"])
def api_check_db():
    try:
        return jsonify({
            "db_type": "postgres" if USE_POSTGRES else "sqlite",
            "counts": {
                "users": q("SELECT COUNT(*) c FROM users", one=True)["c"],
                "trainees": q("SELECT COUNT(*) c FROM trainees", one=True)["c"],
                "employers": q("SELECT COUNT(*) c FROM employers", one=True)["c"],
                "providers": q("SELECT COUNT(*) c FROM providers", one=True)["c"],
                "govt_officials": q("SELECT COUNT(*) c FROM govt_officials", one=True)["c"],
                "followups": q("SELECT COUNT(*) c FROM followups", one=True)["c"],
                "employments": q("SELECT COUNT(*) c FROM employments", one=True)["c"],
                "non_placement": q("SELECT COUNT(*) c FROM non_placement", one=True)["c"],
                "programs": q("SELECT COUNT(*) c FROM programs", one=True)["c"],
                "enrollments": q("SELECT COUNT(*) c FROM enrollments", one=True)["c"],
            },
        })
    except Exception as e:
        return jsonify(error=str(e)), 500


# =============================================================================
#  SECTION 20 — DEMO SEED (full)
# =============================================================================
@app.route("/api/demo/seed", methods=["POST"])
@admin_required
def api_demo_seed():
    random.seed(42)

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

    emp_ids = []
    for ename in ["ABC Technologies", "TCS", "Infosys Ltd.", "Wipro", "HCL"]:
        existing = q("SELECT id FROM employers WHERE name=?", (ename,), one=True)
        if existing:
            emp_ids.append(existing["id"])
        else:
            eid = qx("INSERT INTO employers (name,industry,state,verified) VALUES (?,?,?,1)",
                     (ename, random.choice(SECTORS), random.choice(STATES)))
            emp_ids.append(eid)

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
#  SECTION 21 — RESUME INTELLIGENCE (ATS ENGINE)
#  Full ATS engine ported from ATS PROJECT.
#  Exposes:
#     GET  /api/resume/branches   → branch/role/skills map
#     POST /api/resume/analyze    → resume PDF → full ATS analysis
# =============================================================================
RESUME_UPLOAD_FOLDER = os.path.join(DATA_DIR, "resume_uploads")
os.makedirs(RESUME_UPLOAD_FOLDER, exist_ok=True)


# --- Full ATS BRANCH_DATA (12 branches × 10 roles × 15 skills) ---
BRANCH_DATA = {
    "Computer Science (CSE)": {
        "Full Stack Developer": ["react", "node.js", "mongodb", "javascript", "html", "css", "git", "rest api", "graphql", "next.js", "typescript", "express", "redux", "webpack", "docker"],
        "Backend Engineer": ["java", "spring boot", "sql", "docker", "python", "microservices", "redis", "postgresql", "fastapi", "golang", "flask", "django", "rabbitmq", "kafka", "orm"],
        "Cloud Architect": ["aws", "azure", "docker", "kubernetes", "terraform", "linux", "cloud computing", "s3", "ec2", "lambda", "iam", "vpc", "cloudformation", "eks", "ansible"],
        "Cybersecurity Analyst": ["wireshark", "metasploit", "penetration testing", "firewalls", "cryptography", "linux", "siem", "soc", "nmap", "vulnerability assessment", "burp suite", "owasp", "kali", "encryption", "ids/ips"],
        "Android Developer": ["kotlin", "java", "android studio", "xml", "mvvm", "retrofit", "firebase", "jetpack compose", "gradle", "coroutine", "dagger hilt", "rxjava", "sqlite", "material design", "activity lifecycle"],
        "Blockchain Engineer": ["solidity", "ethereum", "smart contracts", "web3", "rust", "cryptography", "hyperledger", "truffle", "ganache", "ipfs", "tokenomics", "defi", "consensus algorithms", "dapps", "polkadot"],
        "QA Automation": ["selenium", "junit", "test automation", "jira", "manual testing", "cucumber", "testng", "playwright", "postman", "cypress", "appium", "ci/cd", "regression testing", "loadrunner", "api testing"],
        "DevOps Engineer": ["jenkins", "ansible", "monitoring", "linux", "terraform", "bash", "prometheus", "grafana", "gitops", "cicd", "shell scripting", "helm", "argo cd", "nagios", "cloudwatch"],
        "UI/UX Designer": ["figma", "adobe xd", "wireframing", "prototyping", "user research", "sketch", "invision", "design system", "zeplin", "user journeys", "usability testing", "typography", "color theory", "interaction design", "accessibility"],
        "Software Architect": ["design patterns", "microservices", "system design", "scalability", "ddd", "clean architecture", "kafka", "soa", "monolith", "event-driven", "caching", "load balancing", "solid principles", "abstraction", "high availability"]
    },
    "AI & Machine Learning (AIML)": {
        "ML Engineer": ["python", "pytorch", "tensorflow", "scikit-learn", "numpy", "neural networks", "keras", "pandas", "huggingface", "transformers", "xgboost", "random forest", "gradient descent", "model deployment", "opencv"],
        "Data Scientist": ["pandas", "statistics", "sql", "tableau", "data visualization", "data mining", "r language", "power bi", "seaborn", "matplotlib", "probability", "jupyter", "hypothesis testing", "bigquery", "cleaning"],
        "NLP Engineer": ["transformers", "bert", "nltk", "spacy", "large language models", "prompt engineering", "gpt", "tokenization", "llm", "word2vec", "sentiment analysis", "langchain", "sequence labeling", "rag", "fasttext"],
        "Computer Vision Pro": ["opencv", "cnn", "yolo", "image processing", "pytorch", "deep learning", "segmentation", "resnet", "object detection", "gan", "mediapipe", "tensorflow lite", "image augmentation", "face recognition", "spatial AI"],
        "MLOps Engineer": ["mlflow", "kubeflow", "docker", "dvc", "bentoml", "fastapi", "cicd", "zenml", "monitoring", "model registry", "wandb", "feast", "data versioning", "pipelines", "serving"],
        "Data Engineer": ["spark", "hadoop", "etl", "data lake", "redshift", "airflow", "sql", "snowflake", "bigquery", "hive", "pyspark", "kafka", "dbt", "talend", "data modeling"],
        "Research Scientist": ["algorithms", "mathematics", "calculus", "linear algebra", "optimization", "publishing", "latex", "conference", "r&d", "peer review", "experiment design", "stochastic processes", "simulation", "bayesian", "theorems"],
        "Speech AI Specialist": ["asr", "tts", "signal processing", "audio analysis", "librosa", "transformers", "stt", "wav2vec", "speech-to-text", "acoustic modeling", "vocoders", "mfcc", "mel-spectrogram", "phonetics", "deepgram"],
        "Bioinformatics": ["genomics", "biopython", "molecular modeling", "ncbi", "r language", "matlab", "proteomics", "dna sequencing", "rna-seq", "phylogenetics", "alignment", "blast", "drug discovery", "protein folding", "cytoscape"],
        "AI Consultant": ["ai ethics", "strategy", "risk assessment", "llm", "fintech", "compliance", "policy", "business alignment", "roi", "governance", "explainable AI", "digital transformation", "stakeholder", "feasibility", "implementation"]
    },
    "Electronics (ECE)": {
        "VLSI Designer": ["verilog", "system verilog", "fpga", "cadence", "cmos", "digital electronics", "rtl", "asic", "vivado", "synopsys", "genus", "innovus", "sta", "physical design", "tcl"],
        "Embedded Developer": ["embedded c", "microcontrollers", "arm", "rtos", "i2c", "spi", "stm32", "can bus", "uart", "freeRTOS", "esp32", "bare metal", "debugging", "jtag", "logic analyzer"],
        "RF Engineer": ["antennas", "wireless", "dsp", "fourier transform", "modulation", "vna", "microwaves", "ads", "impedance", "smith chart", "lte", "5g", "spectrum analysis", "link budget", "hfss"],
        "Circuit Designer": ["pcb design", "altium", "analog circuits", "spice", "proteus", "kicad", "orcad", "layout", "multisim", "analog to digital", "op-amps", "signal integrity", "bom", "prototyping", "surface mount"],
        "IoT Architect": ["arduino", "raspberry pi", "mqtt", "node-red", "sensors", "lorawan", "zigbee", "esp32", "cloud connectivity", "coap", "edge computing", "ble", "iot gateway", "api", "dashboard"],
        "Signal Processing Eng": ["dsp", "matlab", "filter design", "wavelets", "fft", "signal modeling", "z-transform", "noise reduction", "adaptive filtering", "image compression", "sampling", "nyquist", "stft", "kalman filter", "spectral analysis"],
        "Firmware Engineer": ["linux kernel", "drivers", "assembly", "c++", "debugging", "jtag", "bare metal", "bootloader", "hal", "interrupts", "dma", "pci-e", "embedded linux", "yocto", "toolchain"],
        "Control Systems Eng": ["matlab", "simulink", "pid", "industrial automation", "plc programming", "feedback loops", "root locus", "stability", "state space", "lqr", "nyquist plot", "transfer function", "bode plot", "servos", "nonlinear control"],
        "Hardware QA": ["oscilloscope", "spectrum analyzer", "multimeter", "testing", "calibration", "logic analyzer", "emi", "bench testing", "validation", "thermal testing", "compliance", "iso 9001", "failure analysis", "documentation", "reliability"],
        "Telecom Manager": ["voip", "gsm", "lte", "5g", "spectrum management", "routing", "switching", "fiber optics", "sip", "sdn", "nfv", "oss/bss", "microwave links", "satellite", "network planning"]
    },
    "Electrical (EEE)": {
        "Power Systems Eng": ["power systems", "plc", "scada", "matlab", "relays", "switchgear", "etap", "load flow", "poweer quality", "smart grid", "high voltage", "transmission", "distribution", "fault analysis", "protection"],
        "EV Engineer": ["bms", "battery management", "electric motors", "powertrain", "can bus", "inverter", "thermal management", "charging infrastructure", "hevs", "regenerative braking", "li-ion", "motor control", "simulation", "dcdc converter", "automotive standards"],
        "Renewable Energy Pro": ["solar design", "wind turbines", "grid integration", "photovoltaics", "pvsyst", "energy storage", "hydroelectric", "biomass", "sustainability", "mppt", "inverters", "feasibility study", "clean tech", "epc", "homer"],
        "Automation Engineer": ["hmi", "dcs", "instrumentation", "sensors", "fieldbus", "plc", "ladder logic", "factorytalk", "tiaportal", "modbus", "profibus", "servo systems", "vfd", "commissioning", "control panels"],
        "Smart Grid Architect": ["microgrid", "distributed generation", "storage", "inverters", "demand response", "ami", "smart meter", "synchrophasor", "cybersecurity", "interoperability", "standards", "energy management", "peak shaving", "v2g", "renewables"],
        "Maintenance Eng": ["predictive maintenance", "tpm", "switchboard", "wiring", "diagnostics", "troubleshooting", "earthing", "preventive", "rcm", "root cause", "cmms", "safety protocols", "relays", "testing", "installation"],
        "Energy Auditor": ["energy efficiency", "iso 50001", "carbon", "lighting control", "hvac", "bems", "utility billing", "reporting", "ashrae", "benchmarking", "retrofitting", "cogeneration", "demand side", "payback analysis", "compliance"],
        "Protection Engineer": ["relays", "switchgear", "fault analysis", "etap", "coordination", "breaker", "current transformer", "voltage transformer", "differential protection", "distance protection", "arc flash", "selectivity", "settings", "commissioning", "standards"],
        "Lighting Designer": ["dialux", "relux", "photometrics", "led tech", "control systems", "luminaire", "ies", "glare evaluation", "daylighting", "specification", "renderings", "lux levels", "color rendering", "emergency lighting", "energy code"],
        "Instrumentation Eng": ["sensors", "transducers", "calibration", "analog digital", "labview", "data acquisition", "daq", "p&id", "loop tuning", "measurement", "signal conditioning", "fieldbus", "hart", "plc", "valves"]
    },
    "Mechanical (ME)": {
        "CAD Designer": ["solidworks", "catia", "nx cad", "autocad", "g-code", "3d modeling", "rendering", "drafting", "ptc creo", "geometric dimensioning", "tolerance analysis", "pdm", "surface modeling", "assembly", "prototyping"],
        "FEA Analyst": ["ansys", "hypermesh", "finite element analysis", "nastran", "simulation", "structural analysis", "boundary conditions", "meshing", "linear", "nonlinear", "fatigue", "vibration", "stress analysis", "thermal simulation", "abaqus"],
        "Thermal Engineer": ["thermodynamics", "heat transfer", "cfd", "hvac", "fluid mechanics", "refrigeration", "cooling systems", "conduction", "convection", "radiation", "heat exchangers", "simulation", "boiling", "condensation", "energy balance"],
        "Manufacturing Eng": ["cnc", "cam", "lean manufacturing", "six sigma", "kaizen", "qa", "jit", "kanban", "process optimization", "value stream", "quality control", "iso 9001", "tooling", "assembly line", "operations"],
        "Robotics Engineer": ["ros", "kinematics", "actuators", "control systems", "sensors", "path planning", "pathfinding", "inverse kinematics", "path planning", "vision systems", "end effectors", "simulation", "mechatronics", "uav", "automation"],
        "Automotive Engineer": ["engine design", "suspension", "aerodynamics", "hybrid systems", "chassis", "braking system", "powertrain", "vehicle dynamics", "hmi", "safety", "nvh", "materials", "crash testing", "diagnostics", "manufacturing"],
        "Maintenance Mgr": ["tpm", "reliability", "pumps", "compressors", "lubrication", "valves", "pdm", "cmms", "pumps", "bearings", "gears", "alignment", "condition monitoring", "safety", "scheduling"],
        "Aerospace Eng": ["propulsion", "avionics", "structural analysis", "turbines", "materials", "aerodynamics", "mach number", "lift", "drag", "flight mechanics", "composites", "wind tunnel", "orbital", "simulation", "standards"],
        "Plant Engineer": ["boilers", "utility systems", "safety management", "pumps", "inventory", "facilities", "osha", "pumps", "piping", "maintenance", "operations", "hvac", "power generation", "contractor management", "shutdown planning"],
        "HVAC Designer": ["psychrometry", "duct design", "chillers", "ventilation", "hvac", "cooling load", "vrf", "ahu", "vav", "controls", "refrigeration", "ashrae", "energy modeling", "piping", "commissioning"]
    },
    "AI & Data Science (AIDS)": {
        "Data Scientist": ["python", "sql", "predictive modeling", "statistics", "seaborn", "pandas", "linear regression", "clustering", "time series", "r", "tableau", "exploratory data", "hypothesis testing", "machine learning", "feature engineering"],
        "Data Lake Architect": ["spark", "hadoop", "snowflake", "databricks", "aws glue", "athena", "delta lake", "parquet", "storage", "cloud", "etl", "governance", "security", "pipelines", "scalability"],
        "BI Developer": ["power bi", "looker", "dashboards", "etl", "excel vba", "data modeling", "tableau", "dax", "sql", "reporting", "data warehouse", "kpis", "analysis", "data integration", "ssrs"],
        "Big Data Engineer": ["hive", "pyspark", "bigquery", "nosql", "impala", "pipelines", "kafka", "flink", "storm", "hbase", "cloud", "scrapping", "data ingestion", "optimization", "scalability"],
        "Cloud Data Analyst": ["s3", "redshift", "quicksight", "data warehouse", "kinesis", "cloud storage", "elt", "athena", "glue", "fargate", "cloudwatch", "optimization", "security", "reporting", "cost management"],
        "Information Security": ["data privacy", "gdpr", "compliance", "encryption", "anonymization", "soc2", "access control", "auditing", "risk assessment", "hipaa", "nist", "dlp", "threat modeling", "incident response", "policies"],
        "Quantitative Analyst": ["financial modeling", "r", "risk analysis", "monte carlo", "trading", "time series", "statistics", "matlab", "stochastic", "calculus", "pricing", "derivatives", "portfolio", "excel", "sql"],
        "Data Steward": ["governance", "metadata", "cataloging", "lineage", "quality rules", "mdm", "dama", "standards", "compliance", "security", "dictionary", "lifecycle", "policies", "master data", "curation"],
        "Market Analyst": ["google analytics", "crm", "segmentation", "churn prediction", "trends", "behavior", "excel", "sql", "tableau", "reporting", "forecasting", "surveys", "ab testing", "consumer insight", "kpis"],
        "AI Solutions Arch": ["llm", "integration", "api", "prompt engineering", "agentic workflows", "rag", "langchain", "vector db", "cloud", "scalability", "openai", "deployment", "mlops", "transformers", "strategy"]
    },
    "Civil Engineering": {
        "Structural Engineer": ["staad pro", "etabs", "revit", "concrete design", "steel structures", "load calculation", "eurocodes", "is codes", "foundation", "seismic", "dynamics", "bridge", "high rise", "optimization", "analysis"],
        "Site Engineer": ["construction management", "billing", "surveying", "estimation", "m-book", "execution", "quality control", "safety", "surveying", "concrete", "steel", "reports", "labor management", "planning", "site supervision"],
        "BIM Coordinator": ["revit", "navisworks", "bim 360", "3d modeling", "point cloud", "clash detection", "4d simulation", "vDC", "ifc", "standards", "collaboration", "architecture", "mep", "coordination", "interoperability"],
        "Geotechnical Engineer": ["soil mechanics", "foundation design", "plaxis", "seismology", "slope stability", "drilling", "retaining walls", "tunnels", "dams", "landslides", "exploration", "geo-environmental", "ground improvement", "rock mechanics", "laboratory testing"],
        "Quantity Surveyor": ["cost estimation", "tendering", "contracts", "autocad", "valuation", "boq", "rate analysis", "billing", "budgeting", "variation", "claims", "measurement", "procurement", "vba", "subcontracting"],
        "Transportation Engineer": ["traffic engineering", "gis", "pavement design", "vissim", "mx road", "highway design", "alignment", "infrastructure", "simulation", "public transport", "safety", "modeling", "its", "rail", "airport"],
        "Environmental Engineer": ["water treatment", "waste management", "eia", "hydrology", "air quality", "sewage", "sustainability", "remediation", "compliance", "hse", "scrubbers", "renewable", "audit", "carbon footprint", "policy"],
        "Hydraulics Engineer": ["hec-ras", "epanet", "irrigation", "dams", "fluid dynamics", "stormwater", "open channel", "flood mapping", "drainage", "river engineering", "sediment transport", "pumps", "hydropower", "coastal", "software"],
        "Urban Planner": ["gis", "public policy", "sustainable design", "cad", "zoning", "master plan", "transport planning", "housing", "regeneration", "community", "environment", "economic development", "legislation", "land use", "stakeholder"],
        "Civil Project Manager": ["primavera", "ms project", "budgeting", "wbs", "scheduling", "pmp", "cpm", "pert", "risk", "contracts", "site", "labor", "cost control", "reporting", "stakeholder"]
    },
    "Mechatronics": {
        "Robotics Engineer": ["ros", "path planning", "kinematics", "lidar", "opencv", "c++", "trajectory", "kalman filter", "simulink", "automation", "actuators", "sensors", "embedded", "slamm", "simulation"],
        "Automation Lead": ["plc programming", "tia portal", "hmi", "scada", "motion control", "servo motors", "industrial automation", "fieldbus", "sensors", "integration", "commissioning", "safety", "network", "vfd", "troubleshooting"],
        "Embedded Systems": ["stm32", "rtos", "can open", "motor control", "encoders", "uart", "microchip", "embedded c", "i2c", "spi", "bare metal", "firmware", "iot", "testing", "pcb"],
        "System Integrator": ["sensors", "actuators", "plc", "vision systems", "industrial iot", "modbus", "profibus", "ethercat", "control panel", "testing", "wiring", "design", "specification", "hmi", "automation"],
        "Control Engineer": ["matlab", "pid", "kalman filter", "feedback loops", "stability", "state space", "lqr", "simulink", "dynamics", "optimization", "servo", "signal", "system", "identification", "modelling"],
        "UAV Developer": ["drones", "pixhawk", "mavlink", "autopilot", "telemetry", "flight control", "ardupilot", "gimbal", "vision", "gps", "propulsion", "aerodynamics", "ground station", "simulation", "testing"],
        "Machine Vision Eng": ["opencv", "image segmentation", "halcon", "industrial cameras", "lighting", "object recognition", "blob detection", "inspection", "ocr", "sorting", "algorithm", "python", "optics", "ai", "industrial"],
        "PLC Programmer": ["ladder logic", "scl", "allen bradley", "beckhoff", "ethercat", "function block", "twincat", "codesys", "automation", "hmi", "troubleshooting", "safety", "panels", "programming", "maintenance"],
        "Test Engineer": ["labview", "data acquisition", "hili", "sil", "validation", "instrumentation", "simulation", "v&v", "software", "hardware", "daq", "reporting", "testing", "systems", "standards"],
        "Product Designer": ["fusion 360", "3d printing", "prototyping", "mechanism design", "electronics", "catia", "ergonomics", "materials", "manufacturing", "sketching", "styling", "solidworks", "user interface", "ux", "rd"]
    },
    "Biotechnology": {
        "Bioinformatics": ["genomics", "sequencing", "biopython", "r language", "molecular modeling", "ncbi", "alignment", "blast", "proteomics", "phylo", "perl", "linux", "bioconductor", "crispr", "data mining"],
        "Lab Technician": ["pcr", "hplc", "cell culture", "gmp", "glp", "biosafety", "pipetting", "centrifuge", "titration", "spectroscopy", "buffer preparation", "gel electrophoresis", "incubation", "autoclave", "sop"],
        "R&D Scientist": ["assay development", "immunology", "protein purification", "cloning", "crispr", "elisa", "western blot", "drug discovery", "synthetic biology", "in-vitro", "spectrophotometry", "molecular biology", "rnaseq", "flow cytometry", "biochemistry"],
        "Clinical Analyst": ["ctms", "data management", "protocol", "regulations", "safety", "trial metrics", "ich-gcp", "clinical trials", "biostatistics", "sas", "data cleaning", "patient recruitment", "pharmacovigilance", "e-crf", "fda"],
        "Bioprocess Eng": ["fermentation", "bioreactor", "scale up", "downstream", "purification", "mass transfer", "upstream", "bioprocessing", "sterilization", "atps", "chromatography", "validation", "doe", "process control", "hplc"],
        "Quality Assurance": ["iso 13485", "gmp", "audit", "validation", "sop", "compliance", "corrective action", "documentation", "quality control", "risk management", "iso 9001", "regulatory affairs", "inspections", "capa", "qc lab"],
        "Molecular Biologist": ["dna", "rna", "electrophoresis", "sequencing", "genetics", "cloning", "transfection", "pcr", "crispr", "mutation analysis", "recombinant dna", "vector design", "genotyping", "microscopy", "rna-seq"],
        "Medical Writer": ["scientific writing", "submission", "data summary", "journal", "abstract", "manuscript", "ama style", "regulatory documents", "clinical study report", "investigator brochure", "medline", "pubmed", "peer review", "editing", "lit review"],
        "Regulatory Affairs": ["fda", "ema", "ce marking", "submission", "compliance", "strategy", "investigational new drug", "mhra", "safety reporting", "clinical trial application", "labeling", "regulatory CMC", "gmp audit", "orphan drug", "post-market"],
        "Genetics Counselor": ["ancestry", "disease marker", "data analysis", "ethics", "counseling", "inheritance", "pedigree", "prenatal", "cancer genetics", "variant interpretation", "syndromes", "chromosomal", "psychosocial", "patient advocacy", "genomics"]
    },
    "Cybersecurity": {
        "Pentester": ["kali linux", "metasploit", "wireshark", "burp suite", "ethical hacking", "owasp", "red team", "nmap", "vulnerability scan", "shellcode", "sql injection", "xss", "exploitation", "scripting", "enumeration"],
        "Security Architect": ["firewalls", "zero trust", "iam", "iso 27001", "encryption", "siem", "proxies", "hsm", "security policy", "vpn", "pki", "casb", "infrastructure", "risk mitigation", "threat modeling"],
        "SOC Analyst": ["incident response", "log analysis", "splunk", "threat hunting", "detection", "soar", "edr", "incident management", "siem", "ids/ips", "packet capture", "forensics", "mfa", "alerts", "tcp/ip"],
        "Compliance Officer": ["nist", "hipaa", "soc2", "audit", "risk management", "policy", "grc", "gdpr", "sox", "pci-dss", "security controls", "governance", "remediation", "standards", "itgc"],
        "Cloud Security": ["aws guardduty", "azure sentinel", "casb", "serverless security", "iam", "cspm", "cwpp", "cloud security posture", "lambda security", "tenant", "s3 security", "cloudwatch", "containers", "eks", "fargate"],
        "Malware Analyst": ["reverse engineering", "sandbox", "static analysis", "dynamic analysis", "assembly", "ghidra", "ida pro", "ollydbg", "peid", "binary analysis", "obfuscation", "shellcode", "disassembler", "threat intel", "rootkits"],
        "Forensic Expert": ["encase", "ftk", "chain of custody", "recovery", "investigation", "memory dump", "autopsy", "digital evidence", "imaging", "forensic extraction", "cyber crime", "metadata", "volatility", "pcap", "write blocker"],
        "Network Security": ["vpn", "ips", "ids", "firewalls", "proxy", "wireshark", "ipsec", "radius", "routing", "dnssec", "subnetting", "vlans", "dmz", "bgp", "encryption"],
        "App Security": ["sast", "dast", "code review", "secure coding", "api security", "fuzzing", "dependency check", "burp suite", "owasp top 10", "fortify", "checkmarx", "security testing", "mitigation", "devsecops", "ci/cd"],
        "Threat Intel": ["osint", "dark web", "attribution", "iocs", "threat modeling", "stix", "taxii", "misp", "feed", "indicators", "adversary", "campaign", "tactics", "mitre att&ck", "analysis"]
    }
}


def get_seo_analysis(text):
    cliche_dict = {
        "Fillers": ["hardworking", "passionate", "team player", "motivated", "self-starter"],
        "Vague": ["results-oriented", "dynamic", "professional", "experienced"],
        "Passive": ["responsible for", "assisted with", "helped in"]
    }
    text_low = text.lower()
    found_cliches = [word for cat in cliche_dict.values() for word in cat if word in text_low]

    words = re.findall(r'\b[A-Za-z]{7,}\b', text)
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    top_themes = [t.capitalize() for t in sorted(freq, key=freq.get, reverse=True)[:6]]

    return {"themes": top_themes, "cliches": found_cliches}


def analyze_resume_ats(text, branch, job_role):
    """
    Resume → ATS analysis (v2 — discriminating scoring).
    Score is NOT clamped to a narrow band; it reflects real variance
    across skill match, keyword density, section structure, and length.
    """
    text_lower = text.lower()
    required_skills = BRANCH_DATA.get(branch, {}).get(job_role, [])

    # ---------- 1. Skills detection (with normalization) ----------
    detected_skills = []
    missing_skills = []
    for skill in required_skills:
        s = skill.lower().strip()
        if s in text_lower:
            detected_skills.append(skill)
        else:
            # partial / variant matching
            alts = [s.replace(" ", ""), s.replace(".", ""), s.replace("-", " ")]
            if any(a and a in text_lower for a in alts):
                detected_skills.append(skill)
            else:
                missing_skills.append(skill)

    total_required = len(required_skills) if required_skills else 1
    skill_match_pct = (len(detected_skills) / total_required) * 100

    # ---------- 2. Resume length signal ----------
    words = re.findall(r"\w+", text_lower)
    word_count = len(words)
    # Ideal resume: 250–700 words. Penalize too short or too long.
    if word_count < 150:
        length_score = 40 + (word_count / 150) * 40  # 40–80
    elif 150 <= word_count <= 700:
        length_score = 100
    elif 700 < word_count <= 1000:
        length_score = 90
    else:
        length_score = max(60, 90 - (word_count - 1000) / 50)

    # ---------- 3. Keyword density ----------
    # Count occurrences of the required skills across the resume
    total_keyword_hits = 0
    for skill in required_skills:
        total_keyword_hits += text_lower.count(skill.lower())
    # Ideal: keyword density between 1.5% and 5% of resume
    if word_count > 0:
        density = round((total_keyword_hits / word_count) * 100, 2)
    else:
        density = 0.0
    if 1.5 <= density <= 5:
        density_score = 100
    elif density < 1.5:
        density_score = max(20, (density / 1.5) * 100)
    else:  # > 5 (keyword stuffing)
        density_score = max(50, 100 - (density - 5) * 8)

    # ---------- 4. Section structure detection ----------
    sections = {
        "contact": "@" in text or "phone" in text_lower or "+91" in text,
        "education": "education" in text_lower or "b.tech" in text_lower or "bsc" in text_lower or "b.e" in text_lower,
        "skills": "skills" in text_lower or "technical skills" in text_lower,
        "experience": "experience" in text_lower or "internship" in text_lower or "work history" in text_lower,
        "projects": "project" in text_lower,
        "summary": "summary" in text_lower or "objective" in text_lower or "profile" in text_lower,
        "achievements": any(ch.isdigit() for ch in text),  # has numbers
        "certifications": "certificat" in text_lower or "course" in text_lower,
    }
    section_score = (sum(1 for v in sections.values() if v) / len(sections)) * 100

    # ---------- 5. Action verbs quality ----------
    action_verbs = ["developed", "designed", "built", "implemented", "created",
                    "led", "managed", "improved", "increased", "reduced",
                    "delivered", "achieved", "engineered", "optimized", "launched"]
    verb_hits = sum(1 for v in action_verbs if v in text_lower)
    verb_score = min(100, (verb_hits / 6) * 100)

    # ---------- 6. Quantified achievements ----------
    # Count number-like patterns (e.g., "20%", "3 projects", "100 users")
    quantified = len(re.findall(r'\d+\s*(%|percent|projects?|users?|clients?|months?|years?|k|m)', text_lower))
    quant_score = min(100, (quantified / 4) * 100)

    # ---------- 7. Weighted final score ----------
    # Weights chosen so each factor materially affects the score
    weights = {
        "skills":      0.35,   # biggest signal
        "structure":   0.20,
        "density":     0.10,
        "length":      0.05,
        "verbs":       0.15,
        "quantified":  0.15,
    }
    weighted = (
        skill_match_pct * weights["skills"] +
        section_score   * weights["structure"] +
        density_score   * weights["density"] +
        length_score    * weights["length"] +
        verb_score      * weights["verbs"] +
        quant_score     * weights["quantified"]
    )

    # Small bonus for balanced high-quality resumes
    if skill_match_pct >= 80 and section_score >= 85 and verb_score >= 60:
        weighted += 3

    # Clamp to a wide, realistic range — NOT the narrow 35–95 band.
    # Most real resumes land somewhere between 25 and 98.
    score = int(round(max(15, min(98, weighted))))

    # ---------- 8. Rating ----------
    if score >= 85:
        rating = "Excellent"
    elif score >= 70:
        rating = "Strong"
    elif score >= 55:
        rating = "Good"
    elif score >= 40:
        rating = "Fair"
    else:
        rating = "Needs Improvement"

    # ---------- 9. SEO themes / clichés ----------
    seo_themes = [s.title() for s in detected_skills[:8]]
    cliche_library = ["hardworking", "passionate", "team player", "quick learner",
                      "self-starter", "results-oriented", "dynamic", "motivated",
                      "go-getter", "detail-oriented", "responsible for", "helped in"]
    seo_cliches = [c for c in cliche_library if c in text_lower]

    # ---------- 10. 13-point checklist ----------
    checklist = [
        {"label": "Contact Information Present",  "status": sections["contact"]},
        {"label": "Education Section",             "status": sections["education"]},
        {"label": "Skills Section",                "status": sections["skills"]},
        {"label": "Experience / Internship",       "status": sections["experience"]},
        {"label": "Projects Section",              "status": sections["projects"]},
        {"label": "Professional Summary",          "status": sections["summary"]},
        {"label": "Action Verbs Used",             "status": verb_hits >= 3},
        {"label": "Achievements Quantified",       "status": quantified >= 2},
        {"label": "Certifications / Courses",      "status": sections["certifications"]},
        {"label": "Good Resume Length",            "status": 200 <= word_count <= 900},
        {"label": "No Keyword Stuffing",           "status": density <= 6},
        {"label": "Relevant Keywords Present",     "status": skill_match_pct >= 50},
        {"label": "ATS-Friendly Format",           "status": True},
    ]

    return {
        "score": score,
        "rating": rating,
        "density": density,
        "word_count": word_count,
        "seo_themes": seo_themes,
        "seo_cliches": seo_cliches,
        "detected": detected_skills,
        "missing": missing_skills,
        "checklist": checklist,
        "metrics": {
            "skill_match": round(skill_match_pct, 1),
            "structure":   round(section_score, 1),
            "density":     round(density_score, 1),
            "length":      round(length_score, 1),
            "verbs":       round(verb_score, 1),
            "quantified":  round(quant_score, 1),
        }
    }


@app.route("/api/resume/branches", methods=["GET"])
def api_resume_branches():
    """Return the branch → role → skills map for the frontend dropdowns."""
    return jsonify({"branches": BRANCH_DATA})


@app.route("/api/resume/analyze", methods=["POST"])
def api_resume_analyze():
    """Accepts resume PDF + branch + job_role → returns full ATS analysis."""
    if not PDFPLUMBER_AVAILABLE:
        return jsonify(error="Server missing pdfplumber. Run: pip install pdfplumber"), 500

    branch = request.form.get("branch")
    role = request.form.get("job_role")
    resume = request.files.get("resume")

    if not branch or not role or not resume:
        return jsonify({"error": "Missing branch, job_role, or resume file."}), 400

    if not resume.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF resumes are accepted."}), 400

    filepath = os.path.join(RESUME_UPLOAD_FOLDER, secure_filename(resume.filename))
    resume.save(filepath)

    try:
        with pdfplumber.open(filepath) as pdf:
            text = ""
            for page in pdf.pages:
                if page.extract_text():
                    text += page.extract_text()
    except Exception as e:
        return jsonify({"error": f"Could not read PDF: {str(e)}"}), 500

    result = analyze_resume_ats(text, branch, role)

    # ---- Dynamic suggestions (same logic as ATS project) ----
    suggestions = []
    if result['missing']:
        suggestions.append(f"Consider adding these missing skills: {', '.join(result['missing'][:5])}.")
    if result['density'] < 2:
        suggestions.append("Increase the use of relevant keywords naturally in experience and projects.")
    if result['seo_cliches']:
        suggestions.append(f"Avoid generic terms like: {', '.join(result['seo_cliches'])}.")
    if result['score'] < 60:
        suggestions.append("Improve ATS compatibility by adding role-specific keywords and relevant projects.")
    if "project" not in text.lower():
        suggestions.append("Add detailed projects with outcomes and your contributions.")
    if "summary" not in text.lower():
        suggestions.append("Include a concise professional summary highlighting your skills and achievements.")
    suggestions.extend([
        "Use action verbs like Designed, Developed, Implemented.",
        "Quantify achievements using numbers and percentages.",
        "Ensure consistent formatting and spacing.",
        "Keep resume length to 1–2 pages.",
        "Use ATS-friendly headings like Skills, Experience, Education.",
        "Save resume in PDF format with a simple layout."
    ])
    result["suggestions"] = suggestions

    return jsonify(result)


# =============================================================================
#  SECTION 22 — FRONTEND SERVING (FIXED — no more 405 on /api/*)
# =============================================================================
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


@app.route("/", methods=["GET"])
def serve_root():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return send_file(index_path)
    return render_template_string("""
    <html><head><title>SKILLINTEL Backend</title></head>
    <body style="font-family:system-ui;padding:2rem;background:#f1f5f9;">
      <h1 style="color:#2563eb;">SKILLINTEL backend is running</h1>
      <p>Base API: <code>/api/...</code></p>
      <p>Health: <a href="/api/system/health">/api/system/health</a></p>
      <p>DB check: <a href="/api/admin/check-db">/api/admin/check-db</a></p>
      <p>Resume branches: <a href="/api/resume/branches">/api/resume/branches</a></p>
    </body></html>
    """)


@app.route("/<path:path>", methods=["GET"])
def serve_frontend(path):
    # CRITICAL FIX:
    # This catch-all MUST NEVER intercept /api/* or /static/* requests.
    # If it does, POST /api/resume/analyze lands here with only GET allowed → 405.
    # Returning JSON 404 keeps API routes clean.
    if path == "api" or path.startswith("api/"):
        return jsonify(error="API endpoint not found", path="/" + path), 404
    if path == "static" or path.startswith("static/"):
        return jsonify(error="Static asset not found", path="/" + path), 404

    exact = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(exact):
        return send_file(exact)

    html_path = os.path.join(FRONTEND_DIR, path + ".html")
    if os.path.isfile(html_path):
        return send_file(html_path)

    index_path = os.path.join(FRONTEND_DIR, path, "index.html")
    if os.path.isfile(index_path):
        return send_file(index_path)

    last_seg = path.split("/")[-1]
    if last_seg:
        alt_html = os.path.join(FRONTEND_DIR, path, last_seg + ".html")
        if os.path.isfile(alt_html):
            return send_file(alt_html)

    return jsonify(error="not found", path=path), 404


# =============================================================================
#  SECTION 22B — API METHOD GUARD (prevents 405 leaking from catch-all)
# =============================================================================
@app.errorhandler(405)
def method_not_allowed(e):
    """Return JSON for API 405s so the frontend sees a clean error message."""
    try:
        path = request.path or ""
    except Exception:
        path = ""
    if path.startswith("/api/"):
        return jsonify(
            error="Method Not Allowed",
            path=path,
            method=request.method,
            hint="This URL exists but does not accept this HTTP method. "
                 "For resume analysis use POST /api/resume/analyze with multipart form-data."
        ), 405
    return e


# =============================================================================
#  SECTION 23 — ENTRY POINT
# =============================================================================
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"

    print("=" * 70)
    print(" SKILLINTEL backend ready")
    print(f" Database: {'PostgreSQL' if USE_POSTGRES else 'SQLite'}")
    if not USE_POSTGRES:
        print(f" DB path: {DB_PATH}")
    print(f" Super admin: {SUPER_ADMIN_USERNAME} / {SUPER_ADMIN_PASSWORD}")
    print(f" Running on port {port}")
    print("=" * 70)

    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)
import os
import sqlite3
from pathlib import Path
from datetime import datetime
import json

DB_PATH = Path(__file__).parent.parent / "data" / "pce.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            total INTEGER, compliant INTEGER, warned INTEGER,
            forced_change INTEGER, disabled INTEGER, errors INTEGER,
            whatif INTEGER DEFAULT 0, server TEXT
        );
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            sam TEXT, upn TEXT, display_name TEXT, email TEXT,
            action TEXT, days_left INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users_snapshot (
            sam TEXT PRIMARY KEY,
            upn TEXT, display_name TEXT, email TEXT,
            password_last_set TEXT, password_expiry TEXT,
            days_until_expiry INTEGER, is_locked INTEGER DEFAULT 0,
            is_disabled INTEGER DEFAULT 0,
            must_change_at_logon INTEGER DEFAULT 0,
            status_reason TEXT,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS action_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sam TEXT NOT NULL, action TEXT NOT NULL,
            requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
            processed INTEGER DEFAULT 0, result TEXT
        );
        CREATE TABLE IF NOT EXISTS email_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            sam TEXT, upn TEXT, display_name TEXT, email TEXT,
            action TEXT, template_key TEXT, days_left INTEGER,
            cycle_key TEXT,
            password_last_set TEXT,
            password_expiry TEXT,
            subject TEXT, status TEXT, provider_status_code INTEGER,
            error TEXT, attempt_no INTEGER DEFAULT 1,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS live_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            level TEXT DEFAULT 'info',
            message TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS app_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_actions_run ON actions(run_id);
        CREATE INDEX IF NOT EXISTS idx_actions_sam ON actions(sam);
        CREATE INDEX IF NOT EXISTS idx_users_expiry ON users_snapshot(days_until_expiry);
        CREATE INDEX IF NOT EXISTS idx_email_deliveries_sam ON email_deliveries(sam);
        CREATE INDEX IF NOT EXISTS idx_email_deliveries_run ON email_deliveries(run_id);
        CREATE INDEX IF NOT EXISTS idx_email_deliveries_run_sam_action
            ON email_deliveries(run_id, sam, action);
        CREATE INDEX IF NOT EXISTS idx_live_logs_created_at ON live_logs(created_at);
        """)
        cols = {row["name"] for row in c.execute("PRAGMA table_info(users_snapshot)").fetchall()}
        if "is_disabled" not in cols:
            c.execute("ALTER TABLE users_snapshot ADD COLUMN is_disabled INTEGER DEFAULT 0")
        if "must_change_at_logon" not in cols:
            c.execute("ALTER TABLE users_snapshot ADD COLUMN must_change_at_logon INTEGER DEFAULT 0")
        if "status_reason" not in cols:
            c.execute("ALTER TABLE users_snapshot ADD COLUMN status_reason TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_disabled ON users_snapshot(is_disabled)")
        email_cols = {row["name"] for row in c.execute("PRAGMA table_info(email_deliveries)").fetchall()}
        if "cycle_key" not in email_cols:
            c.execute("ALTER TABLE email_deliveries ADD COLUMN cycle_key TEXT")
        if "password_last_set" not in email_cols:
            c.execute("ALTER TABLE email_deliveries ADD COLUMN password_last_set TEXT")
        if "password_expiry" not in email_cols:
            c.execute("ALTER TABLE email_deliveries ADD COLUMN password_expiry TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_email_deliveries_cycle ON email_deliveries(sam, template_key, cycle_key)")

def save_run(payload):
    with get_conn() as c:
        s = payload.get("stats", {})
        c.execute("""INSERT OR REPLACE INTO runs
            (run_id, started_at, finished_at, total, compliant, warned,
             forced_change, disabled, errors, whatif, server)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            payload.get("run_id"), payload.get("started_at"), payload.get("finished_at"),
            s.get("Total", 0), s.get("Compliant", 0), s.get("Warned", 0),
            s.get("ForcedChange", 0), s.get("Disabled", 0), s.get("Errors", 0),
            int(payload.get("whatif", False)), payload.get("server", "")
        ))
        c.execute("DELETE FROM users_snapshot")
        for a in payload.get("actions", []):
            c.execute("""INSERT INTO actions
                (run_id, sam, upn, display_name, email, action, days_left)
                VALUES (?,?,?,?,?,?,?)""", (
                payload.get("run_id"), a.get("User"), a.get("Upn"),
                a.get("DisplayName"), a.get("Email"),
                a.get("Action"), a.get("DaysLeft", 0)
            ))
        for u in payload.get("users_snapshot", []):
            c.execute("""INSERT OR REPLACE INTO users_snapshot
                (sam, upn, display_name, email, password_last_set,
                 password_expiry, days_until_expiry, is_locked, is_disabled,
                 must_change_at_logon, status_reason, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""", (
                u.get("SamAccountName"), u.get("UserPrincipalName"),
                u.get("DisplayName"), u.get("Email"),
                u.get("PasswordLastSet"), u.get("PasswordExpiryDate"),
                u.get("DaysUntilExpiry", 0), int(u.get("IsLocked", False)),
                int(u.get("IsDisabled", False)),
                int(u.get("MustChangeAtLogon", False)),
                u.get("StatusReason"),
            ))

def seed_if_empty():
    if os.getenv("PCE_SEED_DEMO", "false").lower() not in ("1", "true", "yes", "on"):
        return
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        if n > 0:
            return
        now = datetime.utcnow().isoformat()
        c.execute("""INSERT INTO runs (run_id, started_at, finished_at, total, compliant, warned,
                     forced_change, disabled, errors, whatif, server)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  ("DEMO-001", now, now, 150, 120, 18, 8, 3, 1, 0, "DEMO"))
        demo_users = [
            ("jdoe","jdoe@example.com","John Doe","jdoe@example.com","2026-04-15","2026-07-14",7,0),
            ("asmith","asmith@example.com","Alice Smith","asmith@example.com","2026-04-08","2026-07-07",0,0),
            ("btan","btan@example.com","Bob Tan","btan@example.com","2026-04-03","2026-07-02",-5,1),
            ("clee","clee@example.com","Cindy Lee","clee@example.com","2026-05-01","2026-07-30",23,0),
            ("dwang","dwang@example.com","David Wang","dwang@example.com","2026-06-01","2026-08-30",54,0),
        ]
        for u in demo_users:
            c.execute("""INSERT INTO users_snapshot
                (sam,upn,display_name,email,password_last_set,password_expiry,days_until_expiry,is_locked)
                VALUES (?,?,?,?,?,?,?,?)""", u)
        for a in [("jdoe","Warned",7),("asmith","ForcedChange",0),("btan","Disabled",-5)]:
            c.execute("""INSERT INTO actions (run_id,sam,upn,display_name,email,action,days_left)
                VALUES (?,?,?,?,?,?,?)""",
                ("DEMO-001", a[0], f"{a[0]}@example.com", a[0].upper(),
                 f"{a[0]}@example.com", a[1], a[2]))


def append_live_log(event_type, message, level="info", run_id=None):
    with get_conn() as c:
        c.execute(
            """INSERT INTO live_logs (event_type, level, message, run_id)
               VALUES (?, ?, ?, ?)""",
            (event_type, level, message, run_id),
        )


def recent_live_logs(limit=300):
    with get_conn() as c:
        rows = c.execute(
            """SELECT * FROM live_logs ORDER BY id DESC LIMIT ?""",
            (max(1, min(int(limit), 10000)),),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_live_logs():
    with get_conn() as c:
        count = c.execute("SELECT COUNT(*) FROM live_logs").fetchone()[0]
        c.execute("DELETE FROM live_logs")
    return int(count or 0)


def set_app_state(state_key, value):
    serialized = json.dumps(value) if not isinstance(value, str) else value
    with get_conn() as c:
        c.execute(
            """INSERT INTO app_state (state_key, state_value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(state_key) DO UPDATE SET
                 state_value = excluded.state_value,
                 updated_at = CURRENT_TIMESTAMP""",
            (state_key, serialized),
        )


def get_app_state(state_key, default=None):
    with get_conn() as c:
        row = c.execute(
            "SELECT state_value, updated_at FROM app_state WHERE state_key = ?",
            (state_key,),
        ).fetchone()
    if not row:
        return default
    raw = row["state_value"]
    try:
        value = json.loads(raw)
    except Exception:
        value = raw
    if isinstance(value, dict) and "updated_at" not in value:
        value["updated_at"] = row["updated_at"]
    return value


def log_email_delivery(
    run_id,
    sam,
    upn,
    display_name,
    email,
    action,
    template_key,
    days_left,
    subject,
    status,
    cycle_key=None,
    password_last_set=None,
    password_expiry=None,
    provider_status_code=None,
    error=None,
):
    with get_conn() as c:
        prior = c.execute(
            "SELECT COUNT(*) FROM email_deliveries WHERE sam = ? AND template_key = ?",
            (sam, template_key),
        ).fetchone()[0]
        attempt_no = int(prior or 0) + 1
        c.execute(
            """INSERT INTO email_deliveries
               (run_id, sam, upn, display_name, email, action, template_key, days_left,
                cycle_key, password_last_set, password_expiry,
                subject, status, provider_status_code, error, attempt_no)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                sam,
                upn,
                display_name,
                email,
                action,
                template_key,
                days_left,
                cycle_key,
                password_last_set,
                password_expiry,
                subject,
                status,
                provider_status_code,
                error,
                attempt_no,
            ),
        )
    return attempt_no


def build_email_cycle_key(password_last_set=None, password_expiry=None, sam=None):
    left = str(password_last_set or "").strip()
    right = str(password_expiry or "").strip()
    if left or right:
        return f"pls={left}|pexp={right}"
    identity = str(sam or "").strip().lower()
    return f"legacy={identity}" if identity else ""


def has_sent_template_today(sam, template_key, actions=None):
    actions = tuple(actions or ())
    with get_conn() as c:
        if actions:
            placeholders = ",".join("?" for _ in actions)
            row = c.execute(
                f"""SELECT COUNT(*) FROM email_deliveries
                    WHERE sam = ?
                      AND template_key = ?
                      AND status = 'sent'
                      AND action IN ({placeholders})
                      AND date(sent_at, 'localtime') = date('now', 'localtime')""",
                (sam, template_key, *actions),
            ).fetchone()
        else:
            row = c.execute(
                """SELECT COUNT(*) FROM email_deliveries
                   WHERE sam = ?
                     AND template_key = ?
                     AND status = 'sent'
                     AND date(sent_at, 'localtime') = date('now', 'localtime')""",
                (sam, template_key),
            ).fetchone()
    return int(row[0] or 0) > 0


def has_sent_template_for_cycle(sam, template_key, cycle_key, actions=None):
    if not cycle_key:
        return False
    actions = tuple(actions or ())
    with get_conn() as c:
        if actions:
            placeholders = ",".join("?" for _ in actions)
            row = c.execute(
                f"""SELECT COUNT(*) FROM email_deliveries
                    WHERE sam = ?
                      AND template_key = ?
                      AND cycle_key = ?
                      AND status = 'sent'
                      AND action IN ({placeholders})""",
                (sam, template_key, cycle_key, *actions),
            ).fetchone()
        else:
            row = c.execute(
                """SELECT COUNT(*) FROM email_deliveries
                   WHERE sam = ?
                     AND template_key = ?
                     AND cycle_key = ?
                     AND status = 'sent'""",
                (sam, template_key, cycle_key),
            ).fetchone()
    return int(row[0] or 0) > 0

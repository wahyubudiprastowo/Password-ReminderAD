import os
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import StreamingResponse
from ..db import build_email_cycle_key, get_conn, log_email_delivery
from ..email_templates import get_inline_email_attachments
from ..runner import (
    ActiveDirectoryClient,
    EntraClient,
    build_password_notification_email,
    ceil_days_between,
    derive_password_last_set,
    load_config,
    parse_datetime,
)
import io, csv

router = APIRouter()
VALID_SORTS = {"display_name", "upn", "password_last_set", "password_expiry", "days_until_expiry", "is_disabled"}
CONFIG_PATH = os.getenv("PCE_CONFIG_PATH", "/app/config/config.json")


USER_SELECT = """
SELECT u.*,
       ed.action AS last_email_action,
       ed.template_key AS last_email_template,
       ed.status AS last_email_status,
       ed.attempt_no AS last_email_attempt,
       ed.sent_at AS last_email_sent_at,
       ed.error AS last_email_error,
       ed_auto.action AS last_auto_email_action,
       ed_auto.template_key AS last_auto_email_template,
       ed_auto.status AS last_auto_email_status,
       ed_auto.attempt_no AS last_auto_email_attempt,
       ed_auto.sent_at AS last_auto_email_sent_at,
       ed_auto.error AS last_auto_email_error
FROM users_snapshot u
LEFT JOIN (
    SELECT e1.*
    FROM email_deliveries e1
    INNER JOIN (
        SELECT sam, MAX(id) AS max_id
        FROM email_deliveries
        GROUP BY sam
    ) latest ON latest.sam = e1.sam AND latest.max_id = e1.id
) ed ON ed.sam = u.sam
LEFT JOIN (
    SELECT e1.*
    FROM email_deliveries e1
    INNER JOIN (
        SELECT sam, MAX(id) AS max_id
        FROM email_deliveries
        WHERE action IN ('Warned', 'ForcedChange')
        GROUP BY sam
    ) latest_auto ON latest_auto.sam = e1.sam AND latest_auto.max_id = e1.id
) ed_auto ON ed_auto.sam = u.sam
"""

def _build_where(q, status, alias=""):
    prefix = f"{alias}." if alias else ""
    where, params = ["1=1"], []
    if q:
        where.append(f"({prefix}sam LIKE ? OR {prefix}upn LIKE ? OR {prefix}display_name LIKE ? OR {prefix}email LIKE ?)")
        like = f"%{q}%"
        params += [like] * 4
    if status == "expiring":
        where.append(f"{prefix}days_until_expiry BETWEEN 0 AND 7 AND {prefix}is_locked = 0 AND {prefix}is_disabled = 0")
    elif status == "expired":
        where.append(f"{prefix}days_until_expiry < 0 AND {prefix}is_locked = 0 AND {prefix}is_disabled = 0")
    elif status == "locked":
        where.append(f"{prefix}is_locked = 1 AND {prefix}is_disabled = 0")
    elif status == "disabled":
        where.append(f"{prefix}is_disabled = 1")
    elif status == "compliant":
        where.append(f"{prefix}days_until_expiry > 7 AND {prefix}is_locked = 0 AND {prefix}is_disabled = 0")
    return " AND ".join(where), params


def _load_user_snapshot(c, sam: str):
    return c.execute("SELECT * FROM users_snapshot WHERE sam = ?", (sam,)).fetchone()


def _normalize_user_row(row: sqlite3.Row | dict, config: dict | None = None) -> dict:
    user = dict(row)
    if user.get("password_last_set"):
        return user

    expiry = parse_datetime(user.get("password_expiry"))
    if expiry is None:
        return user

    config = config or load_config(CONFIG_PATH)
    max_age = int((config.get("Policy") or {}).get("MaxPasswordAgeDays") or 0)
    derived = derive_password_last_set(expiry, max_age)
    if derived is None:
        return user

    user["password_last_set"] = derived.isoformat()
    user["password_last_set_estimated"] = True
    if not user.get("status_reason"):
        user["status_reason"] = "password_last_set_derived_from_expiry"
    return user


def _record_user_action(run_id: str, sam: str, user: dict, action: str, days_left):
    with get_conn() as c:
        c.execute(
            "INSERT INTO actions (run_id, sam, upn, display_name, email, action, days_left) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, sam, user.get("upn"), user.get("display_name"), user.get("email"), action, days_left if days_left is not None else 0),
        )


def _refresh_snapshot_after_action(ad: ActiveDirectoryClient, sam: str, action: str):
    user = ad._find_user(sam)
    with get_conn() as c:
        if not user:
            return None
        c.execute(
            """INSERT OR REPLACE INTO users_snapshot
               (sam, upn, display_name, email, password_last_set, password_expiry,
                days_until_expiry, is_locked, is_disabled, must_change_at_logon,
                status_reason, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                sam,
                user.get("upn"),
                user.get("display_name"),
                user.get("email"),
                user.get("password_last_set"),
                user.get("password_expiry"),
                user.get("days_until_expiry"),
                1 if user.get("is_locked") else 0,
                1 if user.get("is_disabled") else 0,
                1 if user.get("must_change_at_logon") else 0,
                user.get("status_reason"),
            ),
        )
    return user


def _send_manual_notify(sam: str) -> dict:
    config = load_config(CONFIG_PATH)
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress")
    if not config.get("M365", {}).get("Enabled"):
        raise HTTPException(400, "M365 mail is not enabled")
    if not sender:
        raise HTTPException(400, "Missing ReminderSender/FromAddress in config")

    with get_conn() as c:
        row = _load_user_snapshot(c, sam)
        if not row:
            raise HTTPException(404, "User not found")
        user = dict(row)

    email = user.get("email") or user.get("upn")
    if not email:
        raise HTTPException(400, "User has no email address")

    days_left = user.get("days_until_expiry")
    grace_mode = days_left is not None and int(days_left) < 0
    grace_end = None
    if grace_mode:
        policy = config.get("Policy", {})
        grace_days = int(policy.get("TemporaryExpiredGraceDays") or 0)
        grace_start = parse_datetime(policy.get("TemporaryExpiredGraceStart"))
        if grace_days > 0 and grace_start is not None:
            grace_end = grace_start + timedelta(days=grace_days)
            days_left = ceil_days_between(grace_end, datetime.now(timezone.utc))

    body, subject, template_key = build_password_notification_email(
        display_name=user.get("display_name") or sam,
        upn=user.get("upn") or email,
        days_left=days_left,
        grace_end=grace_end,
        grace_mode=grace_mode,
    )
    cycle_key = build_email_cycle_key(
        password_last_set=user.get("password_last_set"),
        password_expiry=user.get("password_expiry"),
        sam=sam,
    )

    try:
        result = EntraClient(config).send_mail(
            sender=sender,
            to_recipients=[email],
            subject=subject,
            html_body=body,
            cc_recipients=[],
            attachments=get_inline_email_attachments(),
        )
        attempt_no = log_email_delivery(
            run_id="MANUAL-NOTIFY",
            sam=sam,
            upn=user.get("upn"),
            display_name=user.get("display_name"),
            email=email,
            action="ManualNotify",
            template_key=template_key,
            days_left=days_left,
            cycle_key=cycle_key,
            password_last_set=user.get("password_last_set"),
            password_expiry=user.get("password_expiry"),
            subject=subject,
            status="sent",
            provider_status_code=result.get("status_code"),
            error=None,
        )
        _record_user_action("MANUAL-NOTIFY", sam, user, "ManualNotify", days_left)
        return {"status": "sent", "attempt_no": attempt_no, "email": email, "subject": subject, "template_key": template_key}
    except Exception as exc:
        attempt_no = log_email_delivery(
            run_id="MANUAL-NOTIFY",
            sam=sam,
            upn=user.get("upn"),
            display_name=user.get("display_name"),
            email=email,
            action="ManualNotify",
            template_key=template_key,
            days_left=days_left,
            cycle_key=cycle_key,
            password_last_set=user.get("password_last_set"),
            password_expiry=user.get("password_expiry"),
            subject=subject,
            status="failed",
            provider_status_code=getattr(getattr(exc, "response", None), "status_code", None),
            error=str(exc),
        )
        raise HTTPException(502, f"Notify failed on attempt #{attempt_no}: {exc}") from exc


def _run_manual_action(sam: str, action: str) -> dict:
    config = load_config(CONFIG_PATH)
    ad = ActiveDirectoryClient(config)
    with get_conn() as c:
        row = _load_user_snapshot(c, sam)
    snapshot_user = dict(row) if row else {"upn": None, "display_name": sam, "email": None, "days_until_expiry": None}

    try:
        if action == "force":
            result = ad.force_change_at_next_logon(sam)
            action_name = "ForcedChange"
            run_id = "MANUAL-FORCE"
        elif action == "disable":
            result = ad.disable_user(sam)
            action_name = "Disabled"
            run_id = "MANUAL-DISABLE"
        elif action == "enable":
            result = ad.enable_user(sam)
            action_name = "Enabled"
            run_id = "MANUAL-ENABLE"
        elif action == "unlock":
            result = ad.unlock_user(sam)
            action_name = "Unlocked"
            run_id = "MANUAL-UNLOCK"
        else:
            raise HTTPException(400, "Unsupported action")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Action failed: {exc}") from exc

    _record_user_action(run_id, sam, snapshot_user | result, action_name, snapshot_user.get("days_until_expiry"))
    refresh_warning = None
    try:
        refreshed = _refresh_snapshot_after_action(ad, sam, action_name)
    except Exception as exc:
        refreshed = None
        refresh_warning = f"Action berhasil, tetapi refresh snapshot gagal: {exc}"
    return {
        "status": "completed",
        "action": action_name,
        "sam": sam,
        "user": refreshed or result,
        "refresh_warning": refresh_warning,
    }

@router.get("")
async def list_users(q: str = "", status: str = "all", page: int = 1, size: int = 50,
                     sort_by: str = "days_until_expiry", sort_dir: str = "asc"):
    if sort_by not in VALID_SORTS: sort_by = "days_until_expiry"
    if sort_dir not in ("asc", "desc"): sort_dir = "asc"
    size = max(1, min(500, size)); page = max(1, page)
    offset = (page - 1) * size
    where_sql, params = _build_where(q, status, alias="u")
    with get_conn() as c:
        total_where_sql, total_params = _build_where(q, status)
        total = c.execute(f"SELECT COUNT(*) FROM users_snapshot WHERE {total_where_sql}", total_params).fetchone()[0]
        rows = c.execute(f"{USER_SELECT} WHERE {where_sql} "
                         f"ORDER BY u.{sort_by} {sort_dir.upper()} LIMIT ? OFFSET ?",
                         params + [size, offset]).fetchall()
    config = load_config(CONFIG_PATH)
    return {"users": [_normalize_user_row(r, config) for r in rows], "total": total, "page": page, "size": size}

@router.get("/export")
async def export_csv(q: str = "", status: str = "all"):
    where_sql, params = _build_where(q, status)
    with get_conn() as c:
        rows = c.execute(f"""SELECT sam,upn,display_name,email,password_last_set,password_expiry,
                             days_until_expiry,is_locked,is_disabled FROM users_snapshot WHERE {where_sql}
                             ORDER BY days_until_expiry ASC""", params).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["SAM", "UPN", "DisplayName", "Email", "PasswordLastSet",
                "PasswordExpiry", "DaysUntilExpiry", "IsLocked", "IsDisabled"])
    config = load_config(CONFIG_PATH)
    for r in rows:
        user = _normalize_user_row(r, config)
        w.writerow([user["sam"], user["upn"], user["display_name"], user["email"],
                    user["password_last_set"], user["password_expiry"],
                    user["days_until_expiry"], "Yes" if user["is_locked"] else "No",
                    "Yes" if user["is_disabled"] else "No"])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pce-users.csv"})

@router.get("/{sam}")
async def get_user(sam: str):
    with get_conn() as c:
        row = c.execute(f"{USER_SELECT} WHERE u.sam = ?", (sam,)).fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    return _normalize_user_row(row)

@router.get("/{sam}/history")
async def user_history(sam: str, limit: int = 50):
    with get_conn() as c:
        rows = c.execute("SELECT * FROM actions WHERE sam = ? ORDER BY id DESC LIMIT ?",
                         (sam, limit)).fetchall()
    return [dict(r) for r in rows]


@router.get("/{sam}/email-history")
async def user_email_history(sam: str, limit: int = 50):
    with get_conn() as c:
        rows = c.execute(
            """SELECT * FROM email_deliveries
               WHERE sam = ?
               ORDER BY id DESC
               LIMIT ?""",
            (sam, limit),
        ).fetchall()
    return [dict(r) for r in rows]

@router.post("/action")
async def single_action(payload: dict = Body(...)):
    sam = payload.get("sam"); action = payload.get("action")
    if not sam or action not in ("notify", "force", "disable", "enable", "unlock"):
        raise HTTPException(400, "Invalid payload")
    if action == "notify":
        return _send_manual_notify(sam)
    return _run_manual_action(sam, action)

@router.post("/bulk-action")
async def bulk_action(payload: dict = Body(...)):
    sams = payload.get("sams", []); action = payload.get("action")
    if not sams or action not in ("notify", "force", "disable", "enable", "unlock"):
        raise HTTPException(400, "Invalid payload")
    if action == "notify":
        results = []
        sent = 0
        failed = 0
        for sam in sams:
            try:
                result = _send_manual_notify(sam)
                sent += 1
                results.append({"sam": sam, **result})
            except HTTPException as exc:
                failed += 1
                results.append({"sam": sam, "status": "failed", "error": exc.detail})
        return {"status": "completed", "affected": len(sams), "sent": sent, "failed": failed, "results": results}
    results = []
    success = 0
    failed = 0
    for sam in sams:
        try:
            result = _run_manual_action(sam, action)
            success += 1
            results.append({"sam": sam, **result})
        except HTTPException as exc:
            failed += 1
            results.append({"sam": sam, "status": "failed", "error": exc.detail})
    warnings = [item for item in results if item.get("refresh_warning")]
    return {
        "status": "completed",
        "affected": len(sams),
        "success": success,
        "failed": failed,
        "warnings": len(warnings),
        "results": results,
    }


@router.post("/{sam}/set-password")
async def set_password(sam: str, payload: dict = Body(...)):
    new_password = (payload.get("new_password") or "").strip()
    must_change = bool(payload.get("must_change", True))
    unlock_user = bool(payload.get("unlock_user", False))
    if len(new_password) < 8:
        raise HTTPException(400, "Password minimum 8 characters")

    try:
        config = load_config(CONFIG_PATH)
        ad = ActiveDirectoryClient(config)
        result = ad.admin_set_password(
            sam=sam,
            new_password=new_password,
            must_change_at_next_logon=must_change,
            unlock_if_locked=unlock_user,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(500, f"Database error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Password reset failed: {exc}") from exc

    with get_conn() as c:
        c.execute(
            "INSERT INTO actions (run_id, sam, upn, display_name, email, action, days_left) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "MANUAL-RESET",
                sam,
                result.get("upn"),
                result.get("display_name"),
                result.get("email"),
                "PasswordReset",
                0,
            ),
        )
        if unlock_user and result.get("unlocked"):
            c.execute(
                "INSERT INTO actions (run_id, sam, upn, display_name, email, action, days_left) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "MANUAL-RESET",
                    sam,
                    result.get("upn"),
                    result.get("display_name"),
                    result.get("email"),
                    "Unlocked",
                    0,
                ),
            )

    refresh_warning = None
    refreshed = None
    try:
        refreshed = _refresh_snapshot_after_action(ad, sam, "PasswordReset")
    except Exception as exc:
        refresh_warning = f"Password reset succeeded, but snapshot refresh failed: {exc}"

    if refreshed:
        result["user"] = refreshed
    if refresh_warning:
        result["refresh_warning"] = refresh_warning
    return result

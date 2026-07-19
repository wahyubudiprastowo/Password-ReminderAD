from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from ..db import get_conn

router = APIRouter()


def _scheduler_info() -> dict:
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime.now(tz)
    next_run = now.replace(minute=5, second=0, microsecond=0)
    if now.minute >= 5:
        next_run = next_run + timedelta(hours=1)
    return {
        "timezone": "Asia/Jakarta",
        "cron": "5 * * * *",
        "frequency_label": "Every hour at minute 05",
        "next_run_at": next_run.isoformat(),
        "threshold_note": "The 7/3/1-day reminder thresholds are evaluated on every scheduler run. The same template is sent only once per password cycle, with extra same-day duplicate protection.",
    }

@router.get("/kpi")
async def kpi():
    with get_conn() as c:
        latest = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        total = c.execute("SELECT COUNT(*) FROM users_snapshot").fetchone()[0]
        exp7 = c.execute(
            "SELECT COUNT(*) FROM users_snapshot WHERE days_until_expiry BETWEEN 0 AND 7 AND is_locked = 0 AND is_disabled = 0"
        ).fetchone()[0]
        expd = c.execute(
            "SELECT COUNT(*) FROM users_snapshot WHERE days_until_expiry < 0 AND is_locked = 0 AND is_disabled = 0"
        ).fetchone()[0]
        lck = c.execute("SELECT COUNT(*) FROM users_snapshot WHERE is_locked = 1 AND is_disabled = 0").fetchone()[0]
        dis = c.execute("SELECT COUNT(*) FROM users_snapshot WHERE is_disabled = 1").fetchone()[0]
        compliant = c.execute(
            "SELECT COUNT(*) FROM users_snapshot WHERE days_until_expiry > 7 AND is_locked = 0 AND is_disabled = 0"
        ).fetchone()[0]
    return {
        "latest_run": dict(latest) if latest else None,
        "total_users": total,
        "expiring_7d": exp7,
        "expired": expd,
        "locked": lck,
        "disabled": dis,
        "compliant": compliant,
        "schedule": _scheduler_info(),
    }

@router.get("/trend")
async def trend(days: int = 30):
    with get_conn() as c:
        rows = c.execute(f"""SELECT started_at, warned, forced_change, disabled, errors
                             FROM runs WHERE started_at >= datetime('now','-{int(days)} days')
                             ORDER BY started_at ASC""").fetchall()
    return [dict(r) for r in rows]

@router.get("/distribution")
async def distribution():
    buckets = {"expired": 0, "0-7": 0, "8-30": 0, "31-60": 0, "61-90": 0, ">90": 0}
    with get_conn() as c:
        rows = c.execute("SELECT days_until_expiry, is_disabled FROM users_snapshot").fetchall()
    for r in rows:
        if r["is_disabled"]:
            continue
        d = r["days_until_expiry"]
        if d is None:
            continue
        if d < 0: buckets["expired"] += 1
        elif d <= 7: buckets["0-7"] += 1
        elif d <= 30: buckets["8-30"] += 1
        elif d <= 60: buckets["31-60"] += 1
        elif d <= 90: buckets["61-90"] += 1
        else: buckets[">90"] += 1
    return buckets

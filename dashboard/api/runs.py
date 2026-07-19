from fastapi import APIRouter, HTTPException
from ..db import get_conn

router = APIRouter()

@router.get("")
async def list_runs(limit: int = 30):
    with get_conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

@router.get("/{run_id}")
async def run_detail(run_id: str):
    with get_conn() as c:
        run = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        acts = c.execute("SELECT * FROM actions WHERE run_id=?", (run_id,)).fetchall()
    if not run:
        raise HTTPException(404, "Run not found")
    return {"run": dict(run), "actions": [dict(a) for a in acts]}

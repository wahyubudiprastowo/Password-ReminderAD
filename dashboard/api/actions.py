from fastapi import APIRouter
from ..db import get_conn

router = APIRouter()

def _base_query(where_clause: str = "", limit_clause: str = "LIMIT ? OFFSET ?") -> str:
    return f"""SELECT a.*,
                      (
                          SELECT status FROM email_deliveries ed
                          WHERE ed.run_id = a.run_id
                            AND ed.sam = a.sam
                            AND ed.action = a.action
                          ORDER BY ed.id DESC LIMIT 1
                      ) AS email_status,
                      (
                          SELECT attempt_no FROM email_deliveries ed
                          WHERE ed.run_id = a.run_id
                            AND ed.sam = a.sam
                            AND ed.action = a.action
                          ORDER BY ed.id DESC LIMIT 1
                      ) AS email_attempt,
                      (
                          SELECT template_key FROM email_deliveries ed
                          WHERE ed.run_id = a.run_id
                            AND ed.sam = a.sam
                            AND ed.action = a.action
                          ORDER BY ed.id DESC LIMIT 1
                      ) AS email_template
               FROM actions a
               {where_clause}
               ORDER BY a.id DESC {limit_clause}"""


@router.get("")
async def list_actions(run_id: str = None, limit: int = 100, page: int | None = None, size: int | None = None):
    if page is not None or size is not None:
        size = max(1, min(int(size or 100), 1000))
        page = max(1, int(page or 1))
        offset = (page - 1) * size
        with get_conn() as c:
            if run_id:
                total = c.execute("SELECT COUNT(*) FROM actions WHERE run_id=?", (run_id,)).fetchone()[0]
                rows = c.execute(_base_query("WHERE a.run_id=?"), (run_id, size, offset)).fetchall()
            else:
                total = c.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
                rows = c.execute(_base_query(), (size, offset)).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "size": size,
        }

    limit = max(1, min(int(limit), 1000))
    with get_conn() as c:
        if run_id:
            rows = c.execute(_base_query("WHERE a.run_id=?", "LIMIT ?"), (run_id, limit)).fetchall()
        else:
            rows = c.execute(_base_query(limit_clause="LIMIT ?"), (limit,)).fetchall()
    return [dict(r) for r in rows]

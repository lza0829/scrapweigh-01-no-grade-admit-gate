from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.db import db, rows_to_list
from app.db import TICKET_STATUSES
from app.services.common import (
    GRADE_MISSING_TICKET_WHERE,
    TICKET_SELECT,
)

router = APIRouter(tags=["磅条"])


@router.get("/磅条")
def list_tickets(状态: str | None = None, 缺品级: int | None = None):
    where = []
    params: list = []
    if 状态 is not None:
        if 状态 not in TICKET_STATUSES:
            raise HTTPException(400, "状态无效")
        where.append("t.status=?")
        params.append(状态)
    if 缺品级 == 1:
        # 缺品级桶：写侧同一谓词 GRADE_MISSING_TICKET_WHERE 重放
        where.append(GRADE_MISSING_TICKET_WHERE)
    elif 缺品级 == 0:
        where.append("t.grade_id IS NOT NULL")
    elif 缺品级 is not None:
        raise HTTPException(400, "缺品级参数仅支持 0/1")
    sql = TICKET_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.id DESC LIMIT 200"
    with db() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())

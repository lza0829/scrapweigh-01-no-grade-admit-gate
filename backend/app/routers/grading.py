from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import write_db
from app.services.common import (
    REASON_GRADE_INVALID,
    REASON_TICKET_STATE,
    assert_shift_writable,
    audit,
    is_pending_grade,
    reject,
    require_ticket,
    ticket_view,
)

router = APIRouter(tags=["定级"])


class GradeLockIn(BaseModel):
    品级编号: int


@router.post("/定级/{ticket_id}")
def lock_grade(ticket_id: int, body: GradeLockIn):
    with write_db() as conn:
        ticket = require_ticket(conn, ticket_id)
        assert_shift_writable(conn, ticket["shift_id"])
        # 入口资格与读侧待定级桶同一谓词（is_pending_grade ⇔ status='待定级'）
        if not is_pending_grade(ticket):
            raise reject(REASON_TICKET_STATE, "仅待定级桶内磅单可定级")
        grade = conn.execute("SELECT * FROM grades WHERE id=?", (body.品级编号,)).fetchone()
        if not grade or grade["unit_price"] <= 0:
            raise reject(REASON_GRADE_INVALID, "品级不存在或单价无效")
        cur = conn.execute(
            """
            UPDATE tickets
            SET status='待结价', grade_id=?, locked_price=?
            WHERE id=? AND status='待定级'
            """,
            (grade["id"], grade["unit_price"], ticket_id),
        )
        if cur.rowcount != 1:
            raise reject(REASON_TICKET_STATE, "定级未生效，磅单已不在待定级桶")
        # 定级后缺品级事实被消除（grade_id 落值），磅册/总览/审计同刷
        audit(conn, "磅条", ticket_id, "定级锁价",
              f"{grade['category']}{grade['grade_name']}@{grade['unit_price']}")
        return ticket_view(conn, ticket_id)

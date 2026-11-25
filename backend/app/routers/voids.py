from __future__ import annotations
from fastapi import APIRouter
from app.db import write_db
from app.services.common import (
    REASON_TICKET_STATE,
    SCALE_HELD_WHERE,
    assert_shift_writable,
    audit,
    now_str,
    release_scale,
    reject,
    require_ticket,
    ticket_view,
)

router = APIRouter(tags=["销单"])


@router.post("/销单/{ticket_id}")
def void_ticket(ticket_id: int):
    with write_db() as conn:
        ticket = require_ticket(conn, ticket_id)
        assert_shift_writable(conn, ticket["shift_id"])
        if ticket["status"] not in ("待定级", "待结价"):
            raise reject(REASON_TICKET_STATE, "仅待定级或待结价可销单")
        cur = conn.execute(
            f"""
            UPDATE tickets
            SET status='已销', voided_at=?
            WHERE id=? AND {SCALE_HELD_WHERE}
            """,
            (now_str(), ticket_id),
        )
        if cur.rowcount != 1:
            raise reject(REASON_TICKET_STATE, "销单未生效，磅单已不在制")
        release_scale(conn, ticket["scale_id"])
        audit(conn, "磅条", ticket_id, "销单", ticket["serial"])
        return ticket_view(conn, ticket_id)

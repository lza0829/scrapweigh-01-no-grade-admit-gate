from __future__ import annotations
from fastapi import APIRouter
from app.db import write_db
from app.services.common import (
    PRICEABLE_WHERE,
    REASON_NOT_PRICEABLE,
    assert_shift_writable,
    audit,
    is_priceable,
    now_str,
    reject,
    release_scale,
    require_ticket,
    ticket_view,
)

router = APIRouter(tags=["结价"])


@router.post("/结价/{ticket_id}")
def settle(ticket_id: int):
    with write_db() as conn:
        ticket = require_ticket(conn, ticket_id)
        assert_shift_writable(conn, ticket["shift_id"])
        # 可结价口径由 common.is_priceable 统一，缺品级/未锁价一律不放行，
        # 结算入口不得把待定级（含缺品级）磅单假装成可计价。
        if not is_priceable(ticket):
            raise reject(REASON_NOT_PRICEABLE, "磅单未定级或未锁价，不能结价")
        amount = round(float(ticket["net_kg"]) * float(ticket["locked_price"]), 2)
        # 条件更新重放同一写侧谓词，防止并发下状态被改后仍结价
        cur = conn.execute(
            f"""
            UPDATE tickets
            SET status='已结价', amount=?, settled_at=?
            WHERE id=? AND {PRICEABLE_WHERE}
            """,
            (amount, now_str(), ticket_id),
        )
        if cur.rowcount != 1:
            raise reject(REASON_NOT_PRICEABLE, "结价未生效，磅单当前不可计价")
        release_scale(conn, ticket["scale_id"])
        audit(conn, "磅条", ticket_id, "结算关账", str(amount))
        return ticket_view(conn, ticket_id)

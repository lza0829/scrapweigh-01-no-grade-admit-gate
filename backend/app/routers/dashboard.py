from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list, row_to_dict
from app.services.common import (
    GRADE_MISSING_WHERE,
    PENDING_GRADE_WHERE,
    PRICEABLE_WHERE,
    SCALE_HELD_WHERE,
)

router = APIRouter(tags=["总览"])

TICKET_STATUS = ("待定级", "待结价", "已结价", "已销")


@router.get("/总览")
def overview():
    with db() as conn:
        shift = conn.execute(
            """
            SELECT id AS 编号, business_day AS 营业日, name AS 班次名,
                   status AS 状态, stamped_at AS 落印时间
            FROM shifts ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        counts = {}
        for name in TICKET_STATUS:
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE status=?", (name,)
            ).fetchone()["c"]

        # 以下三个口径全部重放 common 中的写侧同一谓词，读侧不得另立 SQL 分叉：
        # 待定级格 = PENDING_GRADE_WHERE；缺品级 = GRADE_MISSING_WHERE；
        # 可结价 = PRICEABLE_WHERE。失败过磅因无磅单行，天然不计入任何一格。
        待定级数 = conn.execute(
            f"SELECT COUNT(*) AS c FROM tickets WHERE {PENDING_GRADE_WHERE}"
        ).fetchone()["c"]
        缺品级数 = conn.execute(
            f"SELECT COUNT(*) AS c FROM tickets WHERE {PENDING_GRADE_WHERE} AND {GRADE_MISSING_WHERE}"
        ).fetchone()["c"]
        已带品级待级数 = 待定级数 - 缺品级数
        可结价数 = conn.execute(
            f"SELECT COUNT(*) AS c FROM tickets WHERE {PRICEABLE_WHERE}"
        ).fetchone()["c"]
        占秤数 = conn.execute(
            f"SELECT COUNT(*) AS c FROM tickets WHERE {SCALE_HELD_WHERE}"
        ).fetchone()["c"]

        scales = rows_to_list(conn.execute(
            """
            SELECT sc.id AS 编号, sc.code AS 地磅, sc.status AS 状态,
                   st.name AS 回收点, st.status AS 回收点状态
            FROM scales sc JOIN sites st ON st.id = sc.site_id
            ORDER BY st.id, sc.id
            """
        ).fetchall())
        return {
            "班次": row_to_dict(shift),
            "分布": counts,
            "口径": {
                "待定级": 待定级数,
                "其中缺品级": 缺品级数,
                "其中已带品级": 已带品级待级数,
                "可结价": 可结价数,
                "在制占秤磅单": 占秤数,
            },
            "地磅": scales,
        }

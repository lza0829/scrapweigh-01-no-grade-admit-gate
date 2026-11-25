from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException

# ─────────────────────────────────────────────────────────────────────────────
# 全局唯一口径（架构 B：告警仍入站待定级）
# 写入侧与读取侧必须重放同一谓词，禁止各写各的 SQL，禁止只改按钮文案。
# ─────────────────────────────────────────────────────────────────────────────

# 待定级桶成员资格：过磅 INSERT 即落桶；定级锁价/销单移出。
# 过磅在册列表、定级入口桶、总览待定级格、班次落印闸门共用此片段。
PENDING_GRADE_WHERE = "status = '待定级'"
PENDING_GRADE_TICKET_WHERE = "t.status = '待定级'"

# 缺品级事实：桶内允许缺品级入站，但该事实必须在回执、磅册、审计可核，不得静默抹掉。
GRADE_MISSING_WHERE = "grade_id IS NULL"
GRADE_MISSING_TICKET_WHERE = "t.grade_id IS NULL"

# 可结价口径：结价入口与结算写口共用，缺品级/未锁价一律不得假装可计价。
PRICEABLE_WHERE = "status = '待结价' AND grade_id IS NOT NULL AND locked_price IS NOT NULL"

# 仍占用地磅的在制磅单（结价/销单后据此释放秤位）
SCALE_HELD_WHERE = "status IN ('待定级','待结价')"

# 过磅拒收的稳定拒因码（写入审计 note，亦作为 API 拒因字段）
REASON_SITE_CLOSED = "SITE_NOT_OPEN"
REASON_SITE_MISMATCH = "SITE_SCALE_MISMATCH"
REASON_SCALE_NOT_FOUND = "SCALE_NOT_FOUND"
REASON_SCALE_OCCUPIED = "SCALE_OCCUPIED"
REASON_SCALE_BROKEN = "SCALE_BROKEN"
REASON_WEIGHT_INVALID = "WEIGHT_INVALID"
REASON_GRADE_INVALID = "GRADE_INVALID"
REASON_SHIFT_NONE = "SHIFT_NONE"
# 定级/结价/销单链路的稳定拒因码
REASON_TICKET_NOT_FOUND = "TICKET_NOT_FOUND"
REASON_TICKET_STATE = "TICKET_STATE"
REASON_NOT_PRICEABLE = "NOT_PRICEABLE"
REASON_SHIFT_LOCKED = "SHIFT_LOCKED"


def is_pending_grade(ticket) -> bool:
    """行级谓词：该磅单是否在待定级桶内（与 PENDING_GRADE_WHERE 同义）。"""
    return ticket["status"] == "待定级"


def is_grade_missing(ticket) -> bool:
    """行级谓词：该磅单是否缺品级（与 GRADE_MISSING_WHERE 同义）。"""
    return ticket["grade_id"] is None


def is_priceable(ticket) -> bool:
    """行级谓词：该磅单是否可结价（与 PRICEABLE_WHERE 同义）。"""
    return (
        ticket["status"] == "待结价"
        and ticket["grade_id"] is not None
        and ticket["locked_price"] is not None
    )


def reject(reason: str, detail: str) -> HTTPException:
    """带稳定拒因码的 400，机读拒因字段与人读说明同时返回。"""
    return HTTPException(400, {"拒因": reason, "说明": detail})


TICKET_SELECT = """
SELECT t.id AS 编号,
       t.serial AS 磅条号,
       t.site_id AS 回收点编号,
       st.name AS 回收点,
       t.scale_id AS 地磅编号,
       sc.code AS 地磅,
       sc.status AS 地磅状态,
       t.shift_id AS 班次编号,
       sh.business_day AS 营业日,
       sh.name AS 班次,
       sh.status AS 班次状态,
       t.status AS 状态,
       CASE WHEN t.status = '待定级' THEN 1 ELSE 0 END AS 待定级桶,
       CASE WHEN t.grade_id IS NULL THEN 1 ELSE 0 END AS 缺品级,
       CASE WHEN t.status = '待结价' AND t.grade_id IS NOT NULL AND t.locked_price IS NOT NULL
            THEN 1 ELSE 0 END AS 可结价,
       t.gross_kg AS 毛重,
       t.tare_kg AS 皮重,
       t.deduct_kg AS 扣杂,
       t.net_kg AS 净重,
       t.grade_id AS 品级编号,
       g.category AS 品类,
       g.grade_name AS 品级,
       t.locked_price AS 锁价,
       t.amount AS 金额,
       t.note AS 备注,
       t.created_at AS 入站时间,
       t.settled_at AS 结价时间,
       t.voided_at AS 销单时间
FROM tickets t
JOIN sites st ON st.id = t.site_id
JOIN scales sc ON sc.id = t.scale_id
JOIN shifts sh ON sh.id = t.shift_id
LEFT JOIN grades g ON g.id = t.grade_id
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def audit(conn, kind: str, ref_id: int | None, action: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        (kind, ref_id, action, now_str(), note),
    )


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def current_shift(conn):
    row = conn.execute(
        "SELECT * FROM shifts WHERE status='未落印' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise reject(REASON_SHIFT_NONE, "无未落印班次")
    return row


def assert_shift_writable(conn, shift_id: int) -> None:
    row = conn.execute("SELECT status FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if not row:
        raise reject(REASON_SHIFT_LOCKED, "班次不存在")
    if row["status"] != "未落印":
        raise reject(REASON_SHIFT_LOCKED, "班次已落印，禁止改磅条")


def next_serial(conn) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"磅{day}"
    row = conn.execute(
        "SELECT serial FROM tickets WHERE serial LIKE ? ORDER BY serial DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = 1
    if row and len(row["serial"]) >= 4:
        try:
            n = int(row["serial"][-4:]) + 1
        except ValueError:
            n = 1
    return f"{prefix}{n:04d}"


def ticket_view(conn, ticket_id: int) -> dict | None:
    row = conn.execute(TICKET_SELECT + " WHERE t.id=?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def require_ticket(conn, ticket_id: int):
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, {"拒因": REASON_TICKET_NOT_FOUND, "说明": "磅条不存在"})
    return row


def occupy_scale(conn, scale_id: int) -> None:
    """条件更新占秤：仅空闲可破。并发双入站时后到者 rowcount=0 被拒，绝不双破空闲。

    必须在 BEGIN IMMEDIATE 写事务内调用，由写事务保证检查与更新原子。
    """
    cur = conn.execute(
        "UPDATE scales SET status='占用' WHERE id=? AND status='空闲'",
        (scale_id,),
    )
    if cur.rowcount == 1:
        return
    row = conn.execute("SELECT status FROM scales WHERE id=?", (scale_id,)).fetchone()
    if not row:
        raise reject(REASON_SCALE_NOT_FOUND, "地磅不存在")
    if row["status"] == "故障":
        raise reject(REASON_SCALE_BROKEN, "地磅故障，不能过磅")
    if row["status"] == "占用":
        raise reject(REASON_SCALE_OCCUPIED, "地磅占用中")
    raise reject(REASON_SCALE_OCCUPIED, "地磅不可占用")


def release_scale(conn, scale_id: int) -> None:
    """该秤位上已无在制磅单时才回空闲；在制口径与占秤口径同源（SCALE_HELD_WHERE）。"""
    still = conn.execute(
        f"SELECT COUNT(*) AS c FROM tickets WHERE scale_id=? AND {SCALE_HELD_WHERE}",
        (scale_id,),
    ).fetchone()["c"]
    if still == 0:
        conn.execute(
            "UPDATE scales SET status='空闲' WHERE id=? AND status='占用'",
            (scale_id,),
        )

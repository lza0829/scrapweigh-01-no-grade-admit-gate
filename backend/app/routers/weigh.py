from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, write_db, rows_to_list
from app.services.common import (
    REASON_GRADE_INVALID,
    REASON_SCALE_NOT_FOUND,
    REASON_SHIFT_NONE,
    REASON_SITE_CLOSED,
    REASON_SITE_MISMATCH,
    REASON_WEIGHT_INVALID,
    SCALE_HELD_WHERE,
    TICKET_SELECT,
    audit,
    get_setting,
    next_serial,
    now_str,
    occupy_scale,
    reject,
    ticket_view,
)

router = APIRouter(tags=["过磅"])

# 缺品级入站的告警文案（架构 B：告警仍入站，事实必须可核、不得静默抹掉）
MISSING_GRADE_WARNING = "未选定品级，已按待定级入站；须在定级口定级后方可结价"


class WeighIn(BaseModel):
    地磅编号: int
    回收点编号: int | None = None
    品级编号: int | None = None
    毛重: float = Field(gt=0)
    皮重: float = Field(ge=0)
    扣杂: float | None = None
    备注: str = ""


def check_admittable(conn, body: WeighIn) -> dict:
    """过磅准入闸（写侧谓词）。

    架构 B 下品级缺失不是硬拒因（落待定级桶 + 告警）；
    其余硬条件不过即抛带稳定拒因码的 reject，整笔不落磅单、不占秤。
    读路径「能否进待定级桶」由 common.PENDING_GRADE_* 同一口径重放。
    """
    shift = conn.execute(
        "SELECT * FROM shifts WHERE status='未落印' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not shift:
        raise reject(REASON_SHIFT_NONE, "无未落印班次，不能过磅")

    scale = conn.execute("SELECT * FROM scales WHERE id=?", (body.地磅编号,)).fetchone()
    if not scale:
        raise reject(REASON_SCALE_NOT_FOUND, "地磅不存在")

    site = conn.execute("SELECT * FROM sites WHERE id=?", (scale["site_id"],)).fetchone()
    if not site:
        raise reject(REASON_SITE_CLOSED, "回收点不存在")
    if body.回收点编号 is not None and body.回收点编号 != site["id"]:
        raise reject(REASON_SITE_MISMATCH, "地磅不属于该回收点")
    if site["status"] != "营业中":
        raise reject(REASON_SITE_CLOSED, f"回收点{site['status']}，不能过磅")

    if body.皮重 >= body.毛重:
        raise reject(REASON_WEIGHT_INVALID, "皮重须小于毛重")
    try:
        cap = float(get_setting(conn, "毛重软上限", "40000"))
    except ValueError:
        cap = 40000.0
    if body.毛重 > cap:
        raise reject(REASON_WEIGHT_INVALID, "毛重超过软上限")
    if body.扣杂 is None:
        try:
            deduct = float(get_setting(conn, "扣杂默认", "0"))
        except ValueError:
            deduct = 0.0
    else:
        deduct = body.扣杂
    if deduct < 0:
        raise reject(REASON_WEIGHT_INVALID, "扣杂不能为负")
    net = round(body.毛重 - body.皮重 - deduct, 3)
    if net <= 0:
        raise reject(REASON_WEIGHT_INVALID, "净重须大于零")

    grade = None
    if body.品级编号 is not None:
        grade = conn.execute("SELECT * FROM grades WHERE id=?", (body.品级编号,)).fetchone()
        if not grade or grade["unit_price"] <= 0:
            raise reject(REASON_GRADE_INVALID, "品级不存在或单价无效")

    return {"shift": shift, "site": site, "scale": scale, "grade": grade,
            "deduct": deduct, "net": net}


def _log_reject(exc: HTTPException) -> None:
    """拒收在独立短事务留审计；主事务已整体回滚，失败过磅不产生任何磅单/秤位变化。"""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    reason = detail.get("拒因", "REJECTED")
    note = detail.get("说明", str(exc.detail))
    with db() as audit_conn:
        audit(audit_conn, "过磅", None, "过磅拒收", f"{reason}|{note}")


@router.get("/过磅")
def list_open_weighs():
    """在制磅单（待定级/待结价），口径与占秤释放同源 SCALE_HELD_WHERE。"""
    with db() as conn:
        return rows_to_list(conn.execute(
            TICKET_SELECT + f" WHERE t.{SCALE_HELD_WHERE} ORDER BY t.id DESC LIMIT 100"
        ).fetchall())


@router.post("/过磅")
def admit(body: WeighIn):
    try:
        # 单一写事务：入站与占秤同成败，任一步失败整体回到过磅前快照
        with write_db() as conn:
            gate = check_admittable(conn, body)
            scale, site, shift = gate["scale"], gate["site"], gate["shift"]
            grade, deduct, net = gate["grade"], gate["deduct"], gate["net"]

            # 条件 UPDATE 占秤；BEGIN IMMEDIATE 下并发后到者在此被拒，不双破空闲
            occupy_scale(conn, scale["id"])

            serial = next_serial(conn)
            conn.execute(
                """
                INSERT INTO tickets(
                  serial, site_id, scale_id, shift_id, status,
                  gross_kg, tare_kg, deduct_kg, net_kg, grade_id, note, created_at
                ) VALUES (?, ?, ?, ?, '待定级', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    serial, site["id"], scale["id"], shift["id"],
                    body.毛重, body.皮重, deduct, net,
                    grade["id"] if grade else None,
                    body.备注, now_str(),
                ),
            )
            ticket_id = conn.execute(
                "SELECT id FROM tickets WHERE serial=?", (serial,)
            ).fetchone()["id"]

            missing = grade is None
            audit(
                conn, "磅条", ticket_id, "过磅入站",
                f"{serial}|{'缺品级告警' if missing else '已带品级'}",
            )
            view = ticket_view(conn, ticket_id)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and "拒因" in exc.detail:
            _log_reject(exc)
        raise

    # 回执显式携带缺品级事实，不静默抹掉；读侧可由 common 谓词重放同一结论
    view["告警"] = [MISSING_GRADE_WARNING] if view["缺品级"] else []
    view["拒因"] = None
    return view

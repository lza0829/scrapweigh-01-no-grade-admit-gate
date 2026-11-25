"""缺品级入站闸 + 全链联动对照测试（架构 B：告警仍入站待定级）。

对照场景：
  1. 缺品级点过磅 → 落磅单/占秤/告警/审计/总览/磅册口径一致
  2. 定级前后结算分叉（缺品级不可结价，定级后可结价）
  3. 同秤并发双入站 → 不双破空闲，后到干净拒绝且无脏行
  4. 半截失败 → 整笔回到过磅前快照
  5. 总览与磅单册对读、班次边界、销单释放、谓词重放
"""
from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from app.db import write_db
from app.routers import weigh as weigh_mod
from app.services.common import (
    GRADE_MISSING_WHERE,
    PENDING_GRADE_WHERE,
    PRICEABLE_WHERE,
    SCALE_HELD_WHERE,
    is_pending_grade,
    is_priceable,
)


def payload(scale_id: int, site_id: int = 1, grade_id=None, **over):
    body = {"地磅编号": scale_id, "回收点编号": site_id,
            "毛重": 1200.0, "皮重": 800.0, "扣杂": 10.0}
    if grade_id is not None:
        body["品级编号"] = grade_id
    body.update(over)
    return body


def admit(client, **kw):
    scale_id = kw.pop("scale_id", 1)
    return client.post("/api/过磅", json=payload(scale_id, **kw))


def scale_status(raw, scale_id: int) -> str:
    return raw.execute("SELECT status FROM scales WHERE id=?", (scale_id,)).fetchone()[0]


def ticket_count(raw, where: str = "1=1", params=()) -> int:
    return raw.execute(f"SELECT COUNT(*) FROM tickets WHERE {where}", params).fetchone()[0]


def audit_actions(raw, action: str):
    return raw.execute(
        "SELECT note FROM audit_events WHERE action=? ORDER BY id", (action,)
    ).fetchall()


# ── 场景 1：缺品级点过磅，全链同刷、口径一致 ──────────────────────────────────

def test_missing_grade_admitted_with_visible_warning(client, raw_conn):
    r = admit(client)
    assert r.status_code == 200, r.text
    view = r.json()
    ticket_id = view["编号"]

    # 回执：落磅单、进待定级桶、缺品级事实显式可核、带告警，且未静默抹掉
    assert view["状态"] == "待定级"
    assert view["待定级桶"] == 1
    assert view["缺品级"] == 1
    assert view["品级编号"] is None
    assert view["可结价"] == 0
    assert view["拒因"] is None
    assert any("待定级" in w for w in view["告警"])

    # 真实落库：磅单存在且 grade_id 为 NULL；秤位已占用（入站与占秤同成败）
    row = raw_conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    assert row is not None and row["status"] == "待定级" and row["grade_id"] is None
    assert scale_status(raw_conn, 1) == "占用"

    # 审计：入站流水标注缺品级事实
    note = audit_actions(raw_conn, "过磅入站")[-1][0]
    assert "缺品级告警" in note

    # 磅单册：缺品级过滤（读侧重放写侧谓词）能捞到同一行
    listed = client.get("/api/磅条?缺品级=1").json()
    assert any(x["编号"] == ticket_id and x["缺品级"] == 1 for x in listed)
    listed_all = client.get("/api/磅条?状态=待定级").json()
    assert any(x["编号"] == ticket_id for x in listed_all)

    # 过磅在制列表（待定级桶读路径）含该行
    open_rows = client.get("/api/过磅").json()
    assert any(x["编号"] == ticket_id for x in open_rows)

    # 总览：待定级格计数与磅册行数对读一致，缺品级被暴露，不把失败过磅计成待定级
    ov = client.get("/api/总览").json()
    assert ov["口径"]["待定级"] == ov["分布"]["待定级"]
    assert ov["口径"]["其中缺品级"] >= 1
    assert ticket_count(raw_conn, PENDING_GRADE_WHERE) == ov["口径"]["待定级"]
    assert ticket_count(raw_conn, f"{PENDING_GRADE_WHERE} AND {GRADE_MISSING_WHERE}") == \
        ov["口径"]["其中缺品级"]


def test_with_grade_admitted_without_warning(client, raw_conn):
    r = admit(client, grade_id=1)
    assert r.status_code == 200
    view = r.json()
    # 带品级入站仍落待定级桶（定级是独立动作），但无缺品级告警
    assert view["状态"] == "待定级" and view["缺品级"] == 0 and view["品级编号"] == 1
    assert view["告警"] == []
    assert scale_status(raw_conn, 1) == "占用"
    # 已带品级的不在缺品级桶里（种子缺品级单可能仍在，只核对本单）
    assert all(x["编号"] != view["编号"] for x in client.get("/api/磅条?缺品级=1").json())


# ── 场景 2：定级前后结算分叉，缺品级不得假装可计价 ────────────────────────────

def test_missing_grade_cannot_settle_before_grading(client, raw_conn):
    t = admit(client).json()["编号"]
    # 结价口直接拒绝缺品级磅单，带稳定拒因码
    r = client.post(f"/api/结价/{t}")
    assert r.status_code == 400
    assert r.json()["detail"]["拒因"] == "NOT_PRICEABLE"
    # 状态与秤位未被写脏
    assert raw_conn.execute("SELECT status FROM tickets WHERE id=?", (t,)).fetchone()[0] == "待定级"
    assert scale_status(raw_conn, 1) == "占用"

    # 定级后才可结价：定级 → 待结价/可结价 → 结价 → 已结价并释放秤
    g = client.post(f"/api/定级/{t}", json={"品级编号": 2})
    assert g.status_code == 200
    gv = g.json()
    assert gv["状态"] == "待结价" and gv["缺品级"] == 0 and gv["可结价"] == 1
    # 定级后缺品级事实被消除：磅册缺品级桶不再含它，审计可追溯定级
    assert all(x["编号"] != t for x in client.get("/api/磅条?缺品级=1").json())

    s = client.post(f"/api/结价/{t}")
    assert s.status_code == 200
    sv = s.json()
    assert sv["状态"] == "已结价" and sv["金额"] == round(sv["净重"] * sv["锁价"], 2)
    assert scale_status(raw_conn, 1) == "空闲"
    ov = client.get("/api/总览").json()
    assert ov["口径"]["可结价"] == ticket_count(raw_conn, PRICEABLE_WHERE)
    assert ov["分布"]["已结价"] >= 1


def test_grade_entry_only_accepts_pending_bucket(client):
    t = admit(client).json()["编号"]
    client.post(f"/api/定级/{t}", json={"品级编号": 1})
    # 已离开待定级桶，重复定级被拒（入口谓词与桶一致）
    r = client.post(f"/api/定级/{t}", json={"品级编号": 2})
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "TICKET_STATE"


# ── 场景 3：同秤并发双入站，不双破空闲，后到无脏行 ────────────────────────────

def test_concurrent_double_occupy_single_winner(client, raw_conn):
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        # 直调路由函数，写事务 BEGIN IMMEDIATE 在 SQLite 层串行化
        try:
            from app.routers.weigh import WeighIn
            weigh_mod.admit(WeighIn(**payload(1)))
            results.append(("ok", None))
        except HTTPException as e:
            results.append(("reject", e.detail))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    oks = [r for r in results if r[0] == "ok"]
    rejects = [r for r in results if r[0] == "reject"]
    assert len(oks) == 1 and len(rejects) == 1
    assert rejects[0][1]["拒因"] == "SCALE_OCCUPIED"

    # 空闲只被破一次：秤位占用、只有一张在制磅单，无第二张脏行
    assert scale_status(raw_conn, 1) == "占用"
    held = ticket_count(raw_conn, "scale_id=1 AND " + SCALE_HELD_WHERE, ())
    assert held == 1
    # 后到者的拒收留了审计，但没有任何磅单行
    assert any("SCALE_OCCUPIED" in n[0] for n in audit_actions(raw_conn, "过磅拒收"))
    assert ticket_count(raw_conn, "scale_id=1") == 1


# ── 场景 4：半截失败整笔回滚到过磅前快照 ─────────────────────────────────────

def test_half_failure_rolls_back_everything(client, raw_conn, monkeypatch):
    before_scale = scale_status(raw_conn, 1)
    before_tickets = ticket_count(raw_conn)
    before_missing = ticket_count(raw_conn, GRADE_MISSING_WHERE)
    before_audit = raw_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    real_audit = weigh_mod.audit

    def boom(*a, **k):
        raise RuntimeError("injected audit failure")

    # 让占秤与插单成功后的审计步骤炸掉，模拟半截失败
    monkeypatch.setattr(weigh_mod, "audit", boom)
    with pytest.raises(RuntimeError):
        weigh_mod.admit(weigh_mod.WeighIn(**payload(1)))
    monkeypatch.setattr(weigh_mod, "audit", real_audit)

    # 秤位、磅单、审计全部回到过磅前快照，无无品级可计价脏行
    assert scale_status(raw_conn, 1) == before_scale == "空闲"
    assert ticket_count(raw_conn) == before_tickets
    assert raw_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == before_audit
    assert ticket_count(raw_conn, GRADE_MISSING_WHERE) == before_missing


def test_hard_rejects_leave_no_dirty_write_and_log_reason(client, raw_conn):
    base_tickets = ticket_count(raw_conn)
    base_scale = scale_status(raw_conn, 1)

    # 故障秤
    r = admit(client, scale_id=3)
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "SCALE_BROKEN"
    # 停收回收点的秤
    r = client.post("/api/过磅", json=payload(4, site_id=2))
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "SITE_NOT_OPEN"
    # 回收点与地磅不匹配
    r = client.post("/api/过磅", json=payload(1, site_id=2))
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "SITE_SCALE_MISMATCH"
    # 重量非法
    r = client.post("/api/过磅", json=payload(1, 毛重=10, 皮重=20))
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "WEIGHT_INVALID"
    # 品级不存在
    r = client.post("/api/过磅", json=payload(1, grade_id=999))
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "GRADE_INVALID"

    # 无未落印班次：清掉在制磅单后落印，再过磅
    for t in client.get("/api/磅条?状态=待定级").json():
        client.post(f"/api/销单/{t['编号']}")
    for t in client.get("/api/磅条?状态=待结价").json():
        client.post(f"/api/销单/{t['编号']}")
    sid = client.get("/api/班次").json()[0]["编号"]
    assert client.post(f"/api/班次/{sid}/落印").status_code == 200
    r = admit(client)
    assert r.status_code == 400 and r.json()["detail"]["拒因"] == "SHIFT_NONE"

    # 全部硬拒：无磅单、秤位没变、每条拒收都有审计拒因
    assert ticket_count(raw_conn) == base_tickets
    assert scale_status(raw_conn, 1) == base_scale
    reasons = [n[0].split("|")[0] for n in audit_actions(raw_conn, "过磅拒收")]
    for code in ("SCALE_BROKEN", "SITE_NOT_OPEN", "SITE_SCALE_MISMATCH",
                 "WEIGHT_INVALID", "GRADE_INVALID", "SHIFT_NONE"):
        assert code in reasons


# ── 场景 5：班次边界、销单释放、总览与磅册对读、谓词重放 ─────────────────────

def test_shift_stamp_blocked_while_pending_or_missing_grade(client):
    # 先清掉种子在制磅单，避免它挡住班次边界
    for st in ("待定级", "待结价"):
        for t in client.get(f"/api/磅条?状态={st}").json():
            client.post(f"/api/销单/{t['编号']}")
    t = admit(client).json()["编号"]  # 缺品级在制
    sid = client.get("/api/班次").json()[0]["编号"]
    r = client.post(f"/api/班次/{sid}/落印")
    assert r.status_code == 400 and "未结磅条" in r.json()["detail"]
    # 定级并结价清掉在制后才允许落印
    client.post(f"/api/定级/{t}", json={"品级编号": 1})
    client.post(f"/api/结价/{t}")
    assert client.post(f"/api/班次/{sid}/落印").status_code == 200


def test_void_releases_scale_and_keeps_counts_consistent(client, raw_conn):
    t = admit(client).json()["编号"]
    assert scale_status(raw_conn, 1) == "占用"
    assert client.post(f"/api/销单/{t}").status_code == 200
    assert scale_status(raw_conn, 1) == "空闲"
    row = raw_conn.execute("SELECT status, grade_id FROM tickets WHERE id=?", (t,)).fetchone()
    assert row["status"] == "已销"  # 销单不抹缺品级事实，只是移出在制桶
    ov = client.get("/api/总览").json()
    assert ov["分布"]["已销"] == ticket_count(raw_conn, "status='已销'")
    assert ov["口径"]["在制占秤磅单"] == ticket_count(raw_conn, SCALE_HELD_WHERE)


def test_overview_matches_ticket_book_and_read_predicate_replay(client, raw_conn):
    a = admit(client).json()["编号"]                 # 缺品级
    # 甲磅已被 a 占用，第二张放到新建的空闲秤位并带品级
    ns = client.post("/api/地磅", json={"回收点编号": 1, "地磅": "丁磅"})
    new_scale = ns.json()["编号"]
    b = client.post("/api/过磅", json=payload(new_scale, grade_id=1)).json()["编号"]

    ov = client.get("/api/总览").json()
    book_pending = client.get("/api/磅条?状态=待定级").json()
    book_missing = client.get("/api/磅条?缺品级=1").json()

    # 总览待定级格 == 磅册待定级行数 == 写侧谓词直查
    assert ov["口径"]["待定级"] == len(book_pending) == ticket_count(raw_conn, PENDING_GRADE_WHERE)
    assert ov["口径"]["其中缺品级"] == len(book_missing) == \
        ticket_count(raw_conn, f"{PENDING_GRADE_WHERE} AND {GRADE_MISSING_WHERE}")
    assert ov["口径"]["其中已带品级"] + ov["口径"]["其中缺品级"] == ov["口径"]["待定级"]

    # 行级谓词与 SQL 谓词同义（读路径可由写入侧同一谓词重放）
    for tid, expect_pending, expect_price in ((a, True, False), (b, True, False)):
        row = raw_conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        assert is_pending_grade(row) is expect_pending
        assert is_priceable(row) is expect_price


def test_seed_scale_held_by_seed_ticket_not_released_by_other_scale(client, raw_conn):
    # 种子乙磅(2)被一张待定级磅单占用；甲磅(1)走完整结价后只释放甲磅，乙磅仍占用
    t = admit(client).json()["编号"]
    client.post(f"/api/定级/{t}", json={"品级编号": 1})
    client.post(f"/api/结价/{t}")
    assert scale_status(raw_conn, 1) == "空闲"
    assert scale_status(raw_conn, 2) == "占用"


def test_write_transaction_is_immediate_and_serialized():
    # 写事务拿的是 IMMEDIATE 锁：两个并发写事务不会出现双写交错
    barrier = threading.Barrier(2)
    order = []
    with write_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('kx','ky')")

    def worker(name):
        barrier.wait()
        with write_db() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES (?,?)", (f"k{name}", "1"))
            order.append(name)

    threads = [threading.Thread(target=worker, args=(i,)) for i in ("x", "y")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(order) == ["x", "y"]
    with write_db() as conn:
        rows = conn.execute("SELECT key FROM settings WHERE key IN ('kx','ky')").fetchall()
        assert {r[0] for r in rows} == {"kx", "ky"}

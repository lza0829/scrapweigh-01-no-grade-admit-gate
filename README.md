# 旧物磅条（Scrapweigh）

骨架：过磅入站 → 品级锁价 → 结算关账 → 班次落印

- 前端 http://127.0.0.1:6700
- API http://127.0.0.1:11700
- 数据库环境变量 `SCRAPWEIGH_DB`（容器内默认 `/data/scrapweigh.db`）

```bash
docker compose up -d --build
```

## 缺品级入站闸：全局唯一口径（架构 B：告警仍入站待定级）

全局只贯彻一种口径，禁止混用：

- 过磅时**品级可选**。未选定品级不硬拒，磅单正常落库并进入「待定级」桶，
  回执与磅单册、审计流水均显式保留「缺品级」事实，不得静默抹掉。
- 缺品级磅单在**定级口**补齐品级后才进入「待结价」；结价口对缺品级/未锁价一律拒绝
  （拒因 `NOT_PRICEABLE`），结算入口不得假装其可计价。
- 失败过磅（地磅故障/回收点停收/重量非法/无班次/秤位占用等）**整笔不落磅单、不占秤**，
  只在审计留带稳定拒因码的「过磅拒收」流水；总览与磅单册不把失败过磅计成待定级。

### 写入侧与读取侧同一谓词（可重放）

口径常量集中在 `backend/app/services/common.py`，所有写口与读口重放同一片段：

| 口径 | SQL 片段常量 | 行级谓词 | 用在哪 |
| --- | --- | --- | --- |
| 待定级桶 | `PENDING_GRADE_WHERE` / `..._TICKET_WHERE` | `is_pending_grade` | 过磅在册、定级入口、总览待定级格、磅册 |
| 缺品级事实 | `GRADE_MISSING_WHERE` / `..._TICKET_WHERE` | `is_grade_missing` | 回执告警、磅册「仅缺品级」过滤、总览缺品级格 |
| 可结价 | `PRICEABLE_WHERE` | `is_priceable` | 结价写口条件更新、总览可结价格、结价页按钮 |
| 在制占秤 | `SCALE_HELD_WHERE` | — | 占秤释放、过磅在册、班次落印闸门 |

磅单视图 `TICKET_SELECT` 直接输出 `待定级桶`/`缺品级`/`可结价` 三个派生标志，
前端各页面按标志渲染，不再各自推断。

### 事务与并发

- 所有写口走 `app.db.write_db()`：`BEGIN IMMEDIATE` 立即取 RESERVED 锁 +
  `busy_timeout=30000`，写事务由 SQLite 串行化；纯读走 `db()` 不拿写锁。
- 占秤用条件更新 `UPDATE scales SET status='占用' WHERE id=? AND status='空闲'`：
  同一秤位并发双入站时后到者 `rowcount=0`，以 `SCALE_OCCUPIED` 干净拒绝，空闲只破一次。
- 入站与占秤在同一事务内同成败，任一步抛错整体回滚到过磅前快照，不留半截脏行
  （无品级却可计价的脏行不可能出现）。

### 拒收拒因码

`SITE_NOT_OPEN` / `SITE_SCALE_MISMATCH` / `SCALE_NOT_FOUND` / `SCALE_OCCUPIED` /
`SCALE_BROKEN` / `WEIGHT_INVALID` / `GRADE_INVALID` / `SHIFT_NONE` /
`TICKET_STATE` / `NOT_PRICEABLE` / `SHIFT_LOCKED` / `TICKET_NOT_FOUND`。
API 返回 `{detail: {拒因, 说明}}`，并写入审计「过磅拒收」流水。

## 测试

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
```

对照场景覆盖：缺品级点过磅全链同刷、定级前后结算分叉、同秤并发双占仅一胜者、
半截失败整笔回滚、总览与磅单册对读、班次边界、销单释放、读谓词由写侧同一谓词重放。

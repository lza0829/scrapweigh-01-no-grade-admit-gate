import os
import sys
import tempfile
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 任何 app 模块导入之前固定测试库路径（config 在导入时读环境变量）
_TMP = Path(tempfile.mkdtemp(prefix="swtest_"))
os.environ["SCRAPWEIGH_DB"] = str(_TMP / "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402

TABLES = ("audit_events", "tickets", "shifts", "settings", "scales", "grades", "sites")


def reset_db() -> None:
    conn = connect()
    try:
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()
    finally:
        conn.close()
    seed_if_empty()


@pytest.fixture()
def client():
    reset_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def raw_conn():
    """直连数据库，用于绕过 API 核对真实落库状态（脏写检查）。"""
    conn = connect()
    yield conn
    conn.close()

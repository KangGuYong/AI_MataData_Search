import psycopg
import pytest

from sqlmcp.db import biz_conn_readonly


def _db_available() -> bool:
    try:
        with biz_conn_readonly():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="업무 DB에 연결할 수 없음")


def test_쓰기는_거부된다():
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with biz_conn_readonly() as conn, conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE t_readonly_probe (a int)")


def test_읽기는_동작한다():
    with biz_conn_readonly() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1

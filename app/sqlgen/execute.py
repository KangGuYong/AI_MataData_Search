"""LLM이 생성한 SQL을 검증(EXPLAIN)하고 READ ONLY 트랜잭션에서 실행한다.

방어선 ③: 반드시 biz_conn_readonly()를 통해서만 실행한다.
"""

from app.config import settings
from app.db import biz_conn_readonly


def explain(sql: str) -> str | None:
    """실행 전 문법·컬럼 존재를 검증한다. 통과하면 None, 실패하면 오류 문자열."""
    try:
        with biz_conn_readonly() as conn, conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
        return None
    except Exception as e:  # noqa: BLE001
        return str(e).strip()


def run(sql: str) -> tuple[list[str], list[tuple]]:
    """READ ONLY 트랜잭션에서 실행하고 항상 롤백한다."""
    with biz_conn_readonly() as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(settings.sql_max_limit)
    return columns, rows

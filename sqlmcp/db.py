"""업무 DB 커넥션. 이 프로세스만 BIZ_DSN 을 안다."""

import re
from contextlib import contextmanager

import psycopg

from sqlmcp.config import settings


@contextmanager
def biz_conn_readonly():
    """항상 READ ONLY 트랜잭션이며 커밋하지 않는다."""
    # app 쪽 _cursor_kw()/SQL_ECHO 커서 플러밍은 의도적으로 가져오지 않는다.
    with psycopg.connect(settings.biz_dsn, autocommit=False) as conn:
        conn.read_only = True
        try:
            with conn.cursor() as cur:
                # SET 은 바인드 파라미터를 지원하지 않는다. 값은 설정값(int)이라 안전하다.
                cur.execute(f"SET LOCAL statement_timeout = '{int(settings.sql_timeout_sec)}s'")
                cur.execute("SET LOCAL transaction_read_only = on")
            yield conn
        finally:
            conn.rollback()


def mask_dsn(dsn: str) -> str:
    """로그 출력용. 비밀번호를 가린다."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", dsn)

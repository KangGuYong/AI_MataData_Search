import re
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings


def mask_dsn(dsn: str) -> str:
    """로그 출력용. 비밀번호를 가린다."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", dsn)


@contextmanager
def meta_conn():
    """메타데이터 DB 커넥션. 쓰기 가능."""
    with psycopg.connect(settings.meta_dsn, autocommit=False) as conn:
        try:
            register_vector(conn)
        except Exception:  # noqa: BLE001
            # vector 확장 설치 전(init-db 실행 시점)에는 등록이 실패한다.
            # 벡터 값은 항상 문자열 + ::vector 캐스팅으로 넘기므로 없어도 동작한다.
            conn.rollback()
        yield conn


@contextmanager
def biz_conn_readonly():
    """업무 DB 커넥션. 항상 READ ONLY 트랜잭션이며 커밋하지 않는다."""
    with psycopg.connect(settings.biz_dsn, autocommit=False) as conn:
        conn.read_only = True
        try:
            with conn.cursor() as cur:
                # SET은 바인드 파라미터를 지원하지 않는다. 값은 설정값(int)이라 안전하다.
                cur.execute(f"SET LOCAL statement_timeout = '{int(settings.sql_timeout_sec)}s'")
                cur.execute("SET LOCAL transaction_read_only = on")
            yield conn
        finally:
            conn.rollback()


@contextmanager
def biz_conn_collect():
    """수집용 업무 DB 커넥션. 프로파일링은 시간이 걸릴 수 있어 타임아웃을 길게 잡는다."""
    with psycopg.connect(settings.biz_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '120s'")
        yield conn


def dsn_user(dsn: str) -> str:
    m = re.search(r"://([^:/@]+):", dsn)
    return m.group(1) if m else "?"

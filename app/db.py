import logging
import re
import sys
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings

log = logging.getLogger("app.sql")


def _init_echo_logger() -> None:
    """SQL_ECHO=true일 때 실행 터미널(stderr)로 바로 출력되게 한다.

    uvicorn / streamlit / pytest 어디서 띄우든 루트 로거 설정에 의존하지
    않도록 이 로거에만 핸들러를 직접 붙이고 전파를 끊는다.
    """
    if not settings.sql_echo or log.handlers:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s [SQL] %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
    log.propagate = False


_init_echo_logger()


def _clip(v: object) -> str:
    t = str(v)
    n = settings.sql_echo_maxlen
    return t if len(t) <= n else t[:n] + f"...(+{len(t) - n} chars)"


class EchoCursor(psycopg.Cursor):
    """보내는 SQL과 돌아온 행을 stderr로 찍는 디버그용 커서."""

    def execute(self, query, params=None, **kw):  # noqa: ANN001, ANN003
        sql_text = query if isinstance(query, str) else query.as_string(self)
        log.debug("--> %s", _clip(" ".join(sql_text.split())))
        if params:
            # 임베딩 벡터처럼 긴 파라미터가 있으므로 개별로 자른다.
            log.debug("    params=%s", [_clip(p) for p in params]
                      if isinstance(params, (list, tuple)) else _clip(params))
        return super().execute(query, params, **kw)

    def _echo_rows(self, rows: list) -> None:
        cols = [d.name for d in self.description] if self.description else []
        log.debug("<-- %d rows %s", len(rows), cols)
        for r in rows[: settings.sql_echo_rows]:
            log.debug("    %s", _clip(r))
        if settings.sql_echo_rows and len(rows) > settings.sql_echo_rows:
            log.debug("    ...(+%d more rows)", len(rows) - settings.sql_echo_rows)

    def fetchone(self):
        row = super().fetchone()
        self._echo_rows([row] if row is not None else [])
        return row

    def fetchmany(self, size=None):  # noqa: ANN001
        rows = super().fetchmany(size)
        self._echo_rows(rows)
        return rows

    def fetchall(self):
        rows = super().fetchall()
        self._echo_rows(rows)
        return rows


def _cursor_kw() -> dict:
    return {"cursor_factory": EchoCursor} if settings.sql_echo else {}


def mask_dsn(dsn: str) -> str:
    """로그 출력용. 비밀번호를 가린다."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", dsn)


@contextmanager
def meta_conn():
    """메타데이터 DB 커넥션. 쓰기 가능."""
    with psycopg.connect(settings.meta_dsn, autocommit=False, **_cursor_kw()) as conn:
        try:
            register_vector(conn)
        except Exception:  # noqa: BLE001
            # vector 확장 설치 전(init-db 실행 시점)에는 등록이 실패한다.
            # 벡터 값은 항상 문자열 + ::vector 캐스팅으로 넘기므로 없어도 동작한다.
            conn.rollback()
        yield conn


@contextmanager
def biz_conn_collect():
    """수집용 업무 DB 커넥션. 프로파일링은 시간이 걸릴 수 있어 타임아웃을 길게 잡는다."""
    with psycopg.connect(settings.biz_dsn, autocommit=True, **_cursor_kw()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{int(settings.collect_timeout_sec)}s'")
        yield conn


def dsn_user(dsn: str) -> str:
    m = re.search(r"://([^:/@]+):", dsn)
    return m.group(1) if m else "?"

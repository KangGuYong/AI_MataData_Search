"""테스트 공용 헬퍼.

업무 DB 연결 가능 여부는 여러 테스트 모듈이 물어보므로 한 번만 확인하고
결과를 재사용한다. 수집 시점에 커넥션을 여러 번 여는 것을 막기 위해서다.
"""

from functools import cache

import pytest


@cache
def db_available() -> bool:
    # import를 함수 안에 둔다. sqlmcp.db 를 모듈 최상단에서 끌어오면
    # sqlmcp.config 의 Settings()가 수집 시점에 실행되어, .env.mcp 가 없는
    # 환경에서는 DB와 무관한 test_guard.py 조차 수집되지 못한다.
    try:
        from sqlmcp.db import biz_conn_readonly

        with biz_conn_readonly():
            return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not db_available(), reason="업무 DB에 연결할 수 없음")

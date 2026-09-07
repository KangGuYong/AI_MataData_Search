"""run_query 도구의 본체.

MCP·HTTP 와 무관한 순수 오케스트레이션이라 직접 호출로 테스트한다.
순서: guard 검증 -> LIMIT 주입 -> EXPLAIN -> 실행.
어느 단계에서 멈췄는지를 error_stage 로 알려준다. 호출자(백엔드)는 이 값으로
LLM 재생성 여부를 판단한다.
"""

from sqlmcp import execute, guard
from sqlmcp.config import settings


def _fail(stage: str, error: str, sql: str | None) -> dict:
    return {
        "ok": False,
        "sql": sql,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "error": error,
        "error_stage": stage,
    }


def run_query(sql: str) -> dict:
    verdict = guard.validate(sql)
    if not verdict.ok:
        return _fail("guard", verdict.reason or "안전 검증 거부", None)

    safe_sql = guard.inject_limit(
        verdict.sql,
        default_limit=settings.sql_row_limit,
        max_limit=settings.sql_max_limit,
    )

    err = execute.explain(safe_sql)
    if err:
        return _fail("explain", err, safe_sql)

    try:
        columns, rows = execute.run(safe_sql)
    except Exception as e:  # noqa: BLE001
        return _fail("execute", str(e).strip(), safe_sql)

    return {
        "ok": True,
        "sql": safe_sql,
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "error": None,
        "error_stage": None,
    }

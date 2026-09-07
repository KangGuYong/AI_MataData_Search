"""MCP 서버 호출의 sync 경계.

MCP Python SDK 클라이언트는 async 라서 여기서 asyncio.run() 으로 감싼다.
api.py 의 ask_endpoint 는 def(sync) 라 FastAPI 가 스레드풀에서 실행하며,
그 스레드에는 실행 중인 이벤트 루프가 없으므로 안전하다.

연결 실패·타임아웃·401 은 예외로 올리지 않고 error_stage="transport" 로 바꾼다.
pipeline.ask() 가 LLM 실패를 AskResult.error 로 바꿔 eval 루프를 살려두는 것과
같은 이유다.
"""

import asyncio
import json
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings
from app.models import QueryResult


def _to_result(payload: dict) -> QueryResult:
    return QueryResult(
        ok=bool(payload.get("ok")),
        sql=payload.get("sql"),
        columns=list(payload.get("columns") or []),
        rows=[list(r) for r in (payload.get("rows") or [])],
        row_count=int(payload.get("row_count") or 0),
        error=payload.get("error"),
        error_stage=payload.get("error_stage"),
    )


async def _call(sql: str) -> QueryResult:
    headers = {"Authorization": f"Bearer {settings.mcp_auth_token}"}
    timeout = timedelta(seconds=settings.mcp_timeout_sec)
    async with streamablehttp_client(
        settings.mcp_url, headers=headers, timeout=timeout
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("run_query", {"sql": sql})
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    return _to_result(json.loads(text))


def run_query(sql: str) -> QueryResult:
    """MCP 서버에 SQL 실행을 위임한다. 예외를 던지지 않는다."""
    try:
        return asyncio.run(_call(sql))
    except Exception as e:  # noqa: BLE001
        return QueryResult(
            ok=False,
            error=f"MCP 서버 호출 실패: {type(e).__name__}: {e}",
            error_stage="transport",
        )

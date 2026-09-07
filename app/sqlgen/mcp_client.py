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


def _parse_tool_result(result) -> QueryResult:
    """MCP call_tool() 결과를 QueryResult로 바꾼다.

    _call()에서 분리해둔 이유는 실제 서버 없이(가짜 result 객체로) 단위
    테스트할 수 있게 하기 위함이다.
    """
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    if result.isError:
        # FastMCP는 도구가 예외를 던지면 isError=True + 평문(JSON 아님) 텍스트를
        # 돌려준다. 그대로 json.loads에 넘기면 JSONDecodeError가 나서 서버가
        # 실제로 뭐라 했는지 묻혀버리므로 여기서 먼저 걸러 원문을 그대로 담는다.
        raise RuntimeError(f"MCP 도구 오류: {text}")
    if not text:
        raise RuntimeError("MCP 응답이 비어 있다 (content 없음)")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        snippet = text[:200]
        raise RuntimeError(f"MCP 응답이 JSON 형식이 아니다: {e} / 응답 일부: {snippet!r}") from e
    return _to_result(payload)


async def _call(sql: str) -> QueryResult:
    headers = {"Authorization": f"Bearer {settings.mcp_auth_token}"}
    timeout = timedelta(seconds=settings.mcp_timeout_sec)
    async with streamablehttp_client(
        settings.mcp_url, headers=headers, timeout=timeout
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("run_query", {"sql": sql})
    return _parse_tool_result(result)


def run_query(sql: str) -> QueryResult:
    """MCP 서버에 SQL 실행을 위임한다. 예외를 던지지 않는다.

    연결 실패(서버 다운/타임아웃), 도구 오류(isError=True), 응답 파싱 실패
    (JSON 아님/형식 불일치) 는 원인이 전혀 다르지만 error_stage는 모두
    "transport" 하나로 묶는다. 호출자(pipeline.ask 등)는 이 세 경우를
    구분해 다르게 동작하지 않고 - LLM에게 SQL을 다시 만들어보라고 시키는
    건 guard/explain/execute 단계 실패뿐이다 - 그저 실패로 취급하기 때문
    이다. 대신 무슨 일이 있었는지는 error 메시지 문구로 구분 가능하게
    (_parse_tool_result가 던지는 RuntimeError의 문구를 그대로 포함해)
    남긴다.
    """
    try:
        return asyncio.run(_call(sql))
    except Exception as e:  # noqa: BLE001
        return QueryResult(
            ok=False,
            error=f"MCP 서버 호출 실패: {type(e).__name__}: {e}",
            error_stage="transport",
        )

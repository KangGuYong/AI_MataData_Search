"""MCP 서버 진입점.

Streamable HTTP 로 run_query 도구 하나만 노출한다. 127.0.0.1 에만 바인딩하고
Bearer 토큰을 검사한다 — 같은 호스트의 다른 프로세스도 막기 위해서다.
"""

import hmac
import json

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from sqlmcp import query
from sqlmcp.config import settings

mcp = FastMCP("sqlmcp", host=settings.mcp_host, port=settings.mcp_port)


@mcp.tool()
def run_query(sql: str) -> str:
    """조회 SQL 을 검증하고 READ ONLY 로 실행한다. 결과를 JSON 문자열로 돌려준다.

    date/Decimal/UUID 등 JSON 이 모르는 타입은 문자열로 떨어진다.
    숫자·불리언·null 은 네이티브 타입으로 남는다.
    """
    return json.dumps(query.run_query(sql), default=str, ensure_ascii=False)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        # 타이밍 공격을 막기 위해 상수 시간 비교를 쓴다.
        if not header.startswith(prefix) or not hmac.compare_digest(
            header[len(prefix):], self._token
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app


def main() -> None:
    uvicorn.run(build_app(), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()

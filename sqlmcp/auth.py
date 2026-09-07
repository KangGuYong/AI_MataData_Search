"""Bearer 인증 미들웨어.

설정(sqlmcp.config)과 DB 의존 모듈을 거치지 않도록 sqlmcp.server 에서 분리했다 —
설정·DB 의존 없이 미들웨어만 단독으로 테스트할 수 있도록 하기 위해서다.
"""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        try:
            # 타이밍 공격을 막기 위해 상수 시간 비교를 쓴다.
            ok = header.startswith(prefix) and hmac.compare_digest(
                header[len(prefix):], self._token
            )
        except TypeError:
            # 비ASCII 문자열은 compare_digest 가 TypeError 를 던진다.
            # 실제 토큰은 secrets.token_urlsafe 로 생성되어 항상 ASCII 이므로
            # 이 경로는 인증 우회가 아니라 500 대신 깔끔한 401 을 돌려주기 위한 방어다.
            ok = False
        if not ok:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

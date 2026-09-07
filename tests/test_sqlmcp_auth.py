from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sqlmcp.auth import BearerAuthMiddleware


def _client(token: str) -> TestClient:
    app = Starlette(routes=[Route("/mcp", lambda r: PlainTextResponse("ok"))])
    app.add_middleware(BearerAuthMiddleware, token=token)
    return TestClient(app)


def test_토큰이_맞으면_통과한다():
    r = _client("s3cret").get("/mcp", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_토큰이_틀리면_401이다():
    r = _client("s3cret").get("/mcp", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_헤더가_없으면_401이다():
    r = _client("s3cret").get("/mcp")
    assert r.status_code == 401


def test_토큰이_접두사면_401이다():
    r = _client("s3cret").get("/mcp", headers={"Authorization": "Bearer s3cre"})
    assert r.status_code == 401


def test_토큰이_비어있으면_401이다():
    r = _client("s3cret").get("/mcp", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_비ASCII_토큰은_500이_아니라_401이다():
    # httpx 는 str 헤더 값을 ascii 로만 인코딩하므로, 실제 ASGI 계층처럼
    # UTF-8 바이트를 그대로 보내 latin-1 로 디코딩되는 상황을 재현한다.
    r = _client("s3cret").get(
        "/mcp", headers={"Authorization": "Bearer 토큰".encode("utf-8")}
    )
    assert r.status_code == 401

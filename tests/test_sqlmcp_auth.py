from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sqlmcp.server import BearerAuthMiddleware


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

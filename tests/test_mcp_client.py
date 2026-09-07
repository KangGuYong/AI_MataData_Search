from dataclasses import dataclass, field

from app.sqlgen import mcp_client


@dataclass
class _FakeContent:
    text: str


@dataclass
class _FakeResult:
    isError: bool = False
    content: list = field(default_factory=list)


def test_서버가_없으면_transport_오류로_바뀐다(monkeypatch):
    # 9번 포트는 discard. 아무도 듣지 않는다.
    monkeypatch.setattr(mcp_client.settings, "mcp_url", "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(mcp_client.settings, "mcp_timeout_sec", 2)

    r = mcp_client.run_query("SELECT 1")

    assert r.ok is False
    assert r.error_stage == "transport"
    assert r.error
    assert r.rows == []


def test_응답_JSON을_QueryResult로_바꾼다():
    payload = {
        "ok": True, "sql": "SELECT 1 LIMIT 100", "columns": ["a"],
        "rows": [[1]], "row_count": 1, "error": None, "error_stage": None,
    }
    r = mcp_client._to_result(payload)
    assert r.ok is True
    assert r.columns == ["a"]
    assert r.rows == [[1]]
    assert r.row_count == 1


def test_도구가_오류를_던지면_서버_메시지가_보이는_transport_오류가_된다():
    result = _FakeResult(isError=True, content=[_FakeContent(text="권한이 없습니다")])

    try:
        mcp_client._parse_tool_result(result)
        assert False, "예외가 나야 한다"
    except RuntimeError as e:
        assert "권한이 없습니다" in str(e)


def test_isError_결과가_run_query를_통하면_transport로_분류된다(monkeypatch):
    async def fake_call(sql):
        return mcp_client._parse_tool_result(
            _FakeResult(isError=True, content=[_FakeContent(text="권한이 없습니다")])
        )

    monkeypatch.setattr(mcp_client, "_call", fake_call)

    r = mcp_client.run_query("SELECT 1")

    assert r.ok is False
    assert r.error_stage == "transport"
    assert "권한이 없습니다" in r.error


def test_응답이_JSON이_아니면_디코드_문제라고_알려준다(monkeypatch):
    async def fake_call(sql):
        return mcp_client._parse_tool_result(
            _FakeResult(isError=False, content=[_FakeContent(text="<html>이건 JSON이 아니다</html>")])
        )

    monkeypatch.setattr(mcp_client, "_call", fake_call)

    r = mcp_client.run_query("SELECT 1")

    assert r.ok is False
    assert r.error_stage == "transport"
    assert "JSON" in r.error
    assert "이건 JSON이 아니다" in r.error
    assert "연결" not in r.error and "서버에 연결" not in r.error


def test_응답_content가_비어있으면_명확한_메시지를_남긴다(monkeypatch):
    async def fake_call(sql):
        return mcp_client._parse_tool_result(_FakeResult(isError=False, content=[]))

    monkeypatch.setattr(mcp_client, "_call", fake_call)

    r = mcp_client.run_query("SELECT 1")

    assert r.ok is False
    assert r.error_stage == "transport"
    assert "비어" in r.error

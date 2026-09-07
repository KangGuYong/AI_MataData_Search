"""재생성 분기 검증. LLM·DB·MCP 서버 모두 불필요하다."""

import pytest

from app import pipeline
from app.models import QueryResult


class _FakeLLM:
    pass


@pytest.fixture
def stub(monkeypatch):
    """검색·LLM 을 고정하고 재생성 호출을 기록한다."""
    calls: list[str] = []
    generated: list[str] = []

    monkeypatch.setattr(
        pipeline, "retrieve", lambda q: ("컨텍스트", [1], ["biz.customer"], {})
    )
    monkeypatch.setattr(pipeline, "get_llm_client", lambda: _FakeLLM())
    monkeypatch.setattr(pipeline.generate, "generate", lambda llm, ctx: "SELECT 1")
    monkeypatch.setattr(pipeline.generate, "looks_like_sql", lambda s: True)

    def _regenerate(llm, ctx, sql, err):
        generated.append(err)
        return "SELECT 2"

    monkeypatch.setattr(pipeline.generate, "regenerate", _regenerate)
    return calls, generated


def test_성공하면_한_번만_호출한다(monkeypatch, stub):
    calls, generated = stub
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql)
        or QueryResult(ok=True, sql=sql, columns=["a"], rows=[[1]], row_count=1),
    )

    r = pipeline.ask("질문")

    assert r.error is None
    assert r.rows == [[1]]
    assert calls == ["SELECT 1"]
    assert generated == []


def test_explain_실패는_한_번_재생성한다(monkeypatch, stub):
    calls, generated = stub
    responses = [
        QueryResult(ok=False, sql="SELECT 1 LIMIT 100", error="컬럼 없음",
                    error_stage="explain"),
        QueryResult(ok=True, sql="SELECT 2 LIMIT 100", columns=["a"],
                    rows=[[2]], row_count=1),
    ]
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql) or responses.pop(0),
    )

    r = pipeline.ask("질문")

    assert r.error is None
    assert r.rows == [[2]]
    assert calls == ["SELECT 1", "SELECT 2"]
    assert generated == ["컬럼 없음"]


def test_explain이_두_번_실패하면_포기한다(monkeypatch, stub):
    calls, generated = stub
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql)
        or QueryResult(ok=False, sql=sql, error="컬럼 없음", error_stage="explain"),
    )

    r = pipeline.ask("질문")

    assert "컬럼 없음" in r.error
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["guard", "execute", "transport"])
def test_explain_외의_실패는_재생성하지_않는다(monkeypatch, stub, stage):
    calls, generated = stub
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql)
        or QueryResult(ok=False, sql=sql, error="사유", error_stage=stage),
    )

    r = pipeline.ask("질문")

    assert r.error
    assert len(calls) == 1
    assert generated == []

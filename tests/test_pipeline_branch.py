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

    assert r.error.startswith("SQL 검증 실패")
    assert "재시도 포함 2회" in r.error
    assert "컬럼 없음" in r.error
    assert len(calls) == 2


def test_explain_실패_후_재시도가_guard로_거부되면_guard_문구를_유지한다(monkeypatch, stub):
    """두 리뷰어가 지적한 회귀: 재시도 실패 사유가 EXPLAIN이 아니어도
    문구는 실제 실패 단계(guard)를 반영해야 한다."""
    calls, generated = stub
    responses = [
        QueryResult(ok=False, sql="SELECT 1", error="컬럼 없음", error_stage="explain"),
        QueryResult(ok=False, sql="SELECT 2", error="위험한 구문", error_stage="guard"),
    ]
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql) or responses.pop(0),
    )

    r = pipeline.ask("질문")

    assert r.error.startswith("안전 검증 거부")
    assert "재시도 포함 2회" in r.error
    assert len(calls) == 2
    assert len(generated) == 1


def test_재시도_중_regenerate가_실패하면_LLM_호출_실패를_반환한다(monkeypatch, stub):
    calls, generated = stub
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql)
        or QueryResult(ok=False, sql=sql, error="컬럼 없음", error_stage="explain"),
    )

    def _regenerate_실패(llm, ctx, sql, err):
        raise RuntimeError("연결 끊김")

    monkeypatch.setattr(pipeline.generate, "regenerate", _regenerate_실패)

    r = pipeline.ask("질문")

    assert "LLM 호출 실패" in r.error
    assert "llm_error" in r.trace
    assert len(calls) == 1


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


def test_PROMPT_ECHO가_꺼져있으면_프롬프트를_남기지_않는다(monkeypatch, stub):
    calls, _ = stub
    monkeypatch.setattr(pipeline.settings, "prompt_echo", False)
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql)
        or QueryResult(ok=True, sql=sql, columns=["a"], rows=[[1]], row_count=1),
    )

    r = pipeline.ask("질문")

    assert not [k for k in r.trace if k.startswith("prompt_")]


def test_PROMPT_ECHO가_켜지면_보낸_프롬프트를_그대로_남긴다(monkeypatch, stub):
    calls, _ = stub
    monkeypatch.setattr(pipeline.settings, "prompt_echo", True)
    monkeypatch.setattr(
        pipeline.mcp_client, "run_query",
        lambda sql: calls.append(sql)
        or QueryResult(ok=True, sql=sql, columns=["a"], rows=[[1]], row_count=1),
    )

    r = pipeline.ask("질문")

    # stub의 retrieve가 컨텍스트로 "컨텍스트"를 돌려준다. user 메시지는 그것 자체다.
    assert r.trace["prompt_1"] == pipeline.generate.prompt_for("컨텍스트")
    assert r.trace["prompt_1"]["user"] == "컨텍스트"
    assert "PostgreSQL" in r.trace["prompt_1"]["system"]
    assert "prompt_explain" not in r.trace


def test_재생성하면_2차_프롬프트도_남는다(monkeypatch, stub):
    calls, _ = stub
    monkeypatch.setattr(pipeline.settings, "prompt_echo", True)
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

    user = r.trace["prompt_explain"]["user"]
    # 재생성 프롬프트에는 컨텍스트·실패한 SQL·오류가 모두 들어가야 한다.
    assert "컨텍스트" in user
    assert "SELECT 1 LIMIT 100" in user
    assert "컬럼 없음" in user

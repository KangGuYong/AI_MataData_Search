from decimal import Decimal

from fastapi.testclient import TestClient

from app import api
from app.models import AskResult


def test_NULL은_JSON_null로_나간다(monkeypatch):
    result = AskResult(question="질문")
    result.columns = ["a", "b"]
    result.rows = [[None, 3]]
    monkeypatch.setattr(api, "run_ask", lambda q: result)

    r = TestClient(api.api).post("/ask", json={"question": "질문"})

    assert r.status_code == 200
    assert r.json()["rows"] == [[None, "3"]]


def test_모든_컬럼이_NULL인_행도_null_리스트로_나간다(monkeypatch):
    # 값 하나만 NULL인 경우 외에, 행 전체가 NULL(예: LEFT JOIN 미매칭)일 때도
    # 문자열 "None"이 섞여 들어오지 않는지 확인한다.
    result = AskResult(question="질문")
    result.columns = ["a", "b", "c"]
    result.rows = [[None, None, None]]
    monkeypatch.setattr(api, "run_ask", lambda q: result)

    r = TestClient(api.api).post("/ask", json={"question": "질문"})

    assert r.status_code == 200
    assert r.json()["rows"] == [[None, None, None]]


def test_Decimal값은_문자열로_변환된다(monkeypatch):
    # date/Decimal 등 JSON으로 직접 못 실어보내는 타입이 지금처럼 str()로
    # 문자열화되는지, NULL 처리 변경이 이 경로를 깨지 않았는지 확인한다.
    result = AskResult(question="질문")
    result.columns = ["amount"]
    result.rows = [[Decimal("12.50")]]
    monkeypatch.setattr(api, "run_ask", lambda q: result)

    r = TestClient(api.api).post("/ask", json={"question": "질문"})

    assert r.status_code == 200
    assert r.json()["rows"] == [["12.50"]]

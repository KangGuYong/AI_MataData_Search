from app.sqlgen import mcp_client


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

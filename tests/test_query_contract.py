from sqlmcp.query import run_query
from tests.conftest import needs_db


def test_guard_거부는_stage가_guard다():
    r = run_query("DROP TABLE biz.customer")
    assert r["ok"] is False
    assert r["error_stage"] == "guard"
    assert r["sql"] is None
    assert r["error"]


def test_guard_거부시_행은_비어있다():
    r = run_query("SELECT 1; SELECT 2")
    assert r["error_stage"] == "guard"
    assert r["columns"] == []
    assert r["rows"] == []
    assert r["row_count"] == 0


@needs_db
def test_존재하지_않는_컬럼은_stage가_explain이다():
    r = run_query("SELECT no_such_column FROM biz.customer")
    assert r["ok"] is False
    assert r["error_stage"] == "explain"
    # 재생성 프롬프트에 넣어야 하므로 LIMIT 주입까지 끝난 SQL 이 담긴다.
    assert r["sql"] is not None
    assert "LIMIT" in r["sql"].upper()


@needs_db
def test_정상_조회는_행을_돌려준다():
    r = run_query("SELECT 1 AS a")
    assert r["ok"] is True
    assert r["error"] is None
    assert r["error_stage"] is None
    assert r["columns"] == ["a"]
    assert r["rows"] == [[1]]
    assert r["row_count"] == 1


@needs_db
def test_LIMIT이_없으면_주입된다():
    r = run_query("SELECT 1 AS a")
    assert "LIMIT 100" in r["sql"].upper()


@needs_db
def test_행은_리스트의_리스트다():
    r = run_query("SELECT 1 AS a, 'x' AS b")
    assert isinstance(r["rows"], list)
    assert isinstance(r["rows"][0], list)

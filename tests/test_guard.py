import pytest

from app.sqlgen.guard import inject_limit, validate


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM biz.customer",
        "SELECT count(*) FROM biz.orders WHERE order_date >= '2025-01-01'",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "SELECT a FROM t1 UNION ALL SELECT b FROM t2",
    ],
)
def test_정상_select는_통과한다(sql):
    assert validate(sql).ok is True


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE biz.customer",
        "DELETE FROM biz.orders",
        "UPDATE biz.customer SET region = 'x'",
        "INSERT INTO biz.customer (region) VALUES ('x')",
        "TRUNCATE biz.orders",
        "CREATE TABLE t (a int)",
        "ALTER TABLE biz.orders ADD COLUMN x int",
        "GRANT SELECT ON biz.orders TO public",
    ],
)
def test_ddl_dml은_거부된다(sql):
    assert validate(sql).ok is False


def test_다중_statement는_거부된다():
    r = validate("SELECT 1; DELETE FROM biz.orders")
    assert r.ok is False
    assert "단일" in r.reason


def test_주석으로_숨긴_dml도_거부된다():
    assert validate("SELECT 1 /* x */; /**/ DROP TABLE biz.orders").ok is False


def test_시스템_카탈로그_접근은_거부된다():
    assert validate("SELECT * FROM pg_catalog.pg_user").ok is False
    assert validate("SELECT * FROM information_schema.tables").ok is False
    assert validate("SELECT * FROM pg_shadow").ok is False


def test_빈_sql은_거부된다():
    assert validate("").ok is False
    assert validate("   ").ok is False


def test_파싱_불가능한_문자열은_거부된다():
    assert validate("이것은 SQL이 아닙니다 @@@").ok is False


def test_dblink으로_다른_커넥션에서_쓰기를_시도하면_거부된다():
    # dblink는 READ ONLY 트랜잭션(방어선 ③)과 무관한 별도 커넥션을 열어
    # 임의 SQL(DROP TABLE 포함)을 실행할 수 있어 SELECT로 위장한 우회 경로다.
    assert validate("SELECT dblink('dbname=x', 'DROP TABLE biz.customer')").ok is False


def test_pg_sleep으로_dos를_시도하면_거부된다():
    assert validate("SELECT pg_sleep(10)").ok is False


def test_limit이_없으면_기본값을_넣는다():
    out = inject_limit("SELECT * FROM biz.customer", default_limit=100, max_limit=1000)
    assert "LIMIT 100" in out.upper()


def test_큰_limit은_클램프된다():
    out = inject_limit("SELECT * FROM biz.customer LIMIT 5000",
                       default_limit=100, max_limit=1000)
    assert "LIMIT 1000" in out.upper()
    assert "5000" not in out


def test_작은_limit은_유지된다():
    out = inject_limit("SELECT * FROM biz.customer LIMIT 5",
                       default_limit=100, max_limit=1000)
    assert "LIMIT 5" in out.upper()


def test_서브쿼리의_limit은_건드리지_않는다():
    out = inject_limit(
        "SELECT * FROM (SELECT * FROM biz.orders LIMIT 3) s",
        default_limit=100, max_limit=1000,
    )
    assert "LIMIT 3" in out.upper()
    assert "LIMIT 100" in out.upper()

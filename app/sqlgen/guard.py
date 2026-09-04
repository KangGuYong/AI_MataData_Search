"""LLM이 생성한 SQL을 실행 전에 검사/정제하는 순수 함수 모듈.

이 모듈은 방어선 ①(AST 검증)과 ②(LIMIT 주입)을 담당한다. 정규식 블랙리스트는
주석(/* */), 대소문자, 유니코드, 문자열 리터럴 안의 키워드, 다중 statement로
우회 가능하므로 사용하지 않는다. sqlglot으로 파싱한 AST의 노드 타입만 신뢰한다.

순수 함수만 존재해야 한다: DB 접근, 네트워크 호출, settings import 금지.
"""

import sqlglot
from sqlglot import exp

from app.models import GuardResult

# SELECT/WITH 트리 안에 이런 노드가 하나라도 있으면 무조건 거부한다.
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Grant,
    exp.Copy,
    exp.Merge,
    exp.Command,
    exp.Into,  # SELECT ... INTO newtable / CREATE TABLE AS 형태의 쓰기 위장
)

# 최상위 statement로 허용하는 타입.
ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)

FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_temp", "pg_toast"}

# 문법상으로는 순수 SELECT지만 실제로는 쓰기/DoS/파일접근을 일으키는 함수들.
# dblink 계열은 별도 커넥션으로 임의 SQL을 실행해 방어선 ③(READ ONLY 트랜잭션)을
# 통째로 우회하므로 특히 위험하다.
FORBIDDEN_FUNCTIONS = {
    "dblink",
    "dblink_exec",
    "dblink_connect",
    "dblink_connect_u",
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_ls_logdir",
    "pg_ls_waldir",
    "lo_import",
    "lo_export",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
}


def validate(sql: str) -> GuardResult:
    """LLM이 생성한 SQL을 AST로 검사한다.

    단일 SELECT/WITH statement만 허용하고, DDL/DML/시스템 카탈로그 접근은
    모두 거부한다. 실패 시 사유를 GuardResult.reason에 담아 반환한다.
    """
    text = (sql or "").strip()
    if not text:
        return GuardResult(False, None, "빈 SQL")

    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except Exception as e:  # noqa: BLE001
        return GuardResult(False, None, f"파싱 실패: {e}")

    if len(statements) != 1:
        return GuardResult(False, None, f"단일 statement가 아님 ({len(statements)}개)")

    root = statements[0]
    if not isinstance(root, ALLOWED_ROOTS):
        return GuardResult(False, None, f"SELECT/WITH 가 아님: {type(root).__name__}")

    for node in root.walk():
        # sqlglot 버전에 따라 walk()가 (node, parent, key) 튜플을 낼 수도 있어 방어적으로 처리한다.
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, FORBIDDEN_NODES):
            return GuardResult(False, None, f"금지된 구문: {type(n).__name__}")
        if isinstance(n, (exp.Anonymous, exp.Func)):
            fname = (getattr(n, "name", "") or "").lower()
            if fname in FORBIDDEN_FUNCTIONS:
                return GuardResult(False, None, f"금지된 함수 호출: {fname}")

    for table in root.find_all(exp.Table):
        db = (table.text("db") or "").lower()
        name = (table.name or "").lower()
        if db in FORBIDDEN_SCHEMAS or name.startswith("pg_"):
            return GuardResult(False, None, f"시스템 카탈로그 접근: {db}.{name}".strip("."))

    return GuardResult(True, root.sql(dialect="postgres"), None)


def inject_limit(sql: str, *, default_limit: int, max_limit: int) -> str:
    """최상위 SELECT에만 LIMIT을 보장한다. 서브쿼리의 LIMIT은 건드리지 않는다."""
    root = sqlglot.parse_one(sql, read="postgres")
    limit = root.args.get("limit")
    if limit is None:
        root.set("limit", exp.Limit(expression=exp.Literal.number(default_limit)))
    else:
        try:
            current = int(limit.expression.name)
        except (AttributeError, ValueError):
            current = max_limit + 1
        if current > max_limit:
            limit.set("expression", exp.Literal.number(max_limit))
    return root.sql(dialect="postgres")

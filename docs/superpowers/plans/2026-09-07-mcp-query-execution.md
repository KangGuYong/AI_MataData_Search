# AI 생성 조회 SQL의 MCP 서버 분리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM이 생성한 조회 SQL의 검증·실행을 `sqlmcp` MCP 서버 프로세스로 옮기고, 백엔드(FastAPI)만 그 서버를 호출하게 만든다.

**Architecture:** 새 최상위 패키지 `sqlmcp/`가 `BIZ_DSN`·AST 검증·LIMIT 주입·READ ONLY 실행을 독점한다. MCP Streamable HTTP로 `run_query` 도구 하나를 `127.0.0.1`에 노출하고 Bearer 토큰으로 보호한다. `app/pipeline.py`는 SQL 실행부를 `mcp_client.run_query()` 한 번의 호출로 대체하고, Streamlit·CLI는 FastAPI `/ask`를 거치게 바꿔 MCP 토큰 보유 프로세스를 uvicorn 하나로 줄인다.

**Tech Stack:** Python 3.11, `mcp` (Python SDK, FastMCP + streamable-http), Starlette 미들웨어, uvicorn, psycopg 3, sqlglot, pytest, httpx, Typer, Streamlit

**설계 문서:** `docs/superpowers/specs/2026-09-07-mcp-query-execution-design.md`

---

## 파일 구조

**신규**

| 파일 | 책임 |
|---|---|
| `sqlmcp/__init__.py` | 패키지 마커 |
| `sqlmcp/config.py` | `.env.mcp` 전용 Settings. `app/config.py`와 아무것도 공유하지 않음 |
| `sqlmcp/guard.py` | `app/sqlgen/guard.py`를 그대로 이동. 순수 함수 |
| `sqlmcp/db.py` | `biz_conn_readonly()` — READ ONLY + statement_timeout + 항상 rollback |
| `sqlmcp/execute.py` | `explain(sql)` / `run(sql)` — DB 왕복 2종 |
| `sqlmcp/query.py` | `run_query(sql) -> dict` — guard→inject→explain→run 오케스트레이션. HTTP·MCP 무관 |
| `sqlmcp/server.py` | FastMCP 도구 등록, Bearer 미들웨어, uvicorn 기동 |
| `app/sqlgen/mcp_client.py` | sync 경계. `run_query(sql) -> QueryResult` |
| `scripts/run.ps1` | sqlmcp → uvicorn → streamlit 순차 기동/종료 |
| `.env.mcp.example` | 서버 설정 예시 |
| `tests/test_query_contract.py` | `sqlmcp.query.run_query`의 결과 검증 |
| `tests/test_pipeline_branch.py` | `pipeline.ask`의 재생성 분기 검증 (LLM·DB 불필요) |

**수정**

| 파일 | 변경 |
|---|---|
| `app/db.py` | `biz_conn_readonly()` 제거 |
| `app/config.py` | `sql_row_limit`/`sql_max_limit`/`sql_timeout_sec` 제거, `api_url`/`mcp_url`/`mcp_auth_token`/`mcp_timeout_sec` 추가 |
| `app/models.py` | `QueryResult` 추가, `GuardResult` 제거, `AskResult.rows` 타입 `list[list]` |
| `app/pipeline.py` | SQL 실행부를 `mcp_client.run_query()`로 교체 |
| `app/ui.py` | `pipeline` 직접 import 제거 → `POST {API_URL}/ask` |
| `app/cli.py` | `ask`·`eval` 기본 경로를 API 경유로, `doctor`에 MCP 점검 추가 |
| `pyproject.toml` | `mcp>=1.2` 추가, 패키지 탐색에 `sqlmcp*` 추가 |
| `.gitignore` | `!.env.mcp.example` 추가 |
| `.env.example` | MCP 설정 추가, SQL 실행 3종 제거 |
| `README.md` / `docs/ARCHITECTURE.md` | 실행 절차와 구조 갱신 |

**삭제**: `app/sqlgen/guard.py`, `app/sqlgen/execute.py` (이동)

**커밋마다 저장소는 동작 가능한 상태를 유지한다.** Task 2~3에서 `app/pipeline.py`가 일시적으로 `sqlmcp.guard`/`sqlmcp.execute`를 직접 import하며, Task 7에서 그 import가 사라진다.

---

## Task 1: sqlmcp 패키지 뼈대와 설정

**Files:**
- Create: `sqlmcp/__init__.py`, `sqlmcp/config.py`, `.env.mcp.example`
- Test: `tests/test_sqlmcp_config.py`
- Modify: `pyproject.toml`, `.gitignore`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sqlmcp_config.py`:

```python
import pytest

from sqlmcp.config import Settings


def test_필수값이_있으면_기본값이_채워진다(tmp_path):
    env = tmp_path / ".env.mcp"
    env.write_text(
        "BIZ_DSN=postgresql://u:p@127.0.0.1:5432/db\nMCP_AUTH_TOKEN=t\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=str(env))
    assert s.biz_dsn == "postgresql://u:p@127.0.0.1:5432/db"
    assert s.mcp_auth_token == "t"
    assert s.mcp_host == "127.0.0.1"
    assert s.mcp_port == 8100
    assert s.sql_row_limit == 100
    assert s.sql_max_limit == 1000
    assert s.sql_timeout_sec == 10


def test_토큰이_비면_거부한다(tmp_path):
    env = tmp_path / ".env.mcp"
    env.write_text("BIZ_DSN=postgresql://u:p@h/db\nMCP_AUTH_TOKEN=\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Settings(_env_file=str(env))
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_sqlmcp_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sqlmcp'`

- [ ] **Step 3: 패키지와 설정 구현**

`sqlmcp/__init__.py`: 빈 파일.

`sqlmcp/config.py`:

```python
"""MCP 서버 전용 설정.

app/config.py 와 아무것도 공유하지 않는다. 이 프로세스만 BIZ_DSN 을 안다.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env.mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    biz_dsn: str
    biz_schema: str = "biz"

    sql_row_limit: int = 100
    sql_max_limit: int = 1000
    sql_timeout_sec: int = Field(default=10, gt=0)

    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100
    # 빈 토큰은 인증을 통째로 무력화하므로 기동 자체를 막는다.
    mcp_auth_token: str = Field(min_length=1)


settings = Settings()
```

`.env.mcp.example`:

```
# MCP 서버 전용 설정. 이 파일의 실물(.env.mcp)은 커밋하지 않는다.
BIZ_DSN=postgresql://USER:PASSWORD@192.168.0.140:5432/ggydb
BIZ_SCHEMA=biz

SQL_ROW_LIMIT=100
SQL_MAX_LIMIT=1000
SQL_TIMEOUT_SEC=10

MCP_HOST=127.0.0.1
MCP_PORT=8100
# 앱 .env 의 MCP_AUTH_TOKEN 과 같은 값이어야 한다.
MCP_AUTH_TOKEN=change-me
```

`pyproject.toml` — `dependencies` 마지막 항목 뒤에 추가:

```toml
    "streamlit==1.40.0",
    "mcp>=1.2",
]
```

패키지 탐색 대상도 넓힌다:

```toml
[tool.setuptools.packages.find]
include = ["app*", "sqlmcp*"]
```

`.gitignore` — `!.env.example` 바로 아래 줄에 추가:

```
!.env.mcp.example
```

- [ ] **Step 4: 설치와 실환경 설정 파일 생성**

Run: `pip install -e .`
Expected: `Successfully installed ... mcp-<version>`

`sqlmcp/config.py`는 모듈 최상단에서 `settings = Settings()`를 실행하므로 `.env.mcp` 실물이 없으면 import 자체가 실패한다. 먼저 만든다:

Run: `cp .env.mcp.example .env.mcp`

`.env.mcp`를 열어 `BIZ_DSN`을 `.env`의 값과 동일하게 채우고, `MCP_AUTH_TOKEN`을 임의의 문자열(예: `python -c "import secrets; print(secrets.token_urlsafe(24))"` 결과)로 바꾼다.

Run: `python -m pytest tests/test_sqlmcp_config.py -v`
Expected: 2 passed

- [ ] **Step 5: SDK API 확인**

Run: `python -c "import mcp; from mcp.server.fastmcp import FastMCP; from mcp.client.streamable_http import streamablehttp_client; print(mcp.__file__)"`
Expected: 오류 없이 경로 출력

실패하면 설치된 `mcp` 버전에서 모듈 경로가 다르다는 뜻이다. `pip show mcp`로 버전을 확인하고 Task 5·6의 import 경로를 그 버전에 맞춰 조정한다. 계획의 나머지 구조는 그대로 유효하다.

- [ ] **Step 6: 커밋**

```bash
git add sqlmcp/__init__.py sqlmcp/config.py .env.mcp.example .gitignore pyproject.toml tests/test_sqlmcp_config.py
git commit -m "feat: sqlmcp 패키지 뼈대와 전용 설정을 추가한다"
```

---

## Task 2: guard 모듈을 sqlmcp로 이동

**Files:**
- Move: `app/sqlgen/guard.py` → `sqlmcp/guard.py`
- Modify: `tests/test_guard.py:3`, `app/pipeline.py:21`, `app/models.py`

- [ ] **Step 1: 테스트 import를 먼저 바꿔 실패시킨다**

`tests/test_guard.py:3`을 교체:

```python
from sqlmcp.guard import inject_limit, validate
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sqlmcp.guard'`

- [ ] **Step 3: 파일 이동과 의존성 절단**

```bash
git mv app/sqlgen/guard.py sqlmcp/guard.py
```

`sqlmcp`가 `app`을 import하면 독립 배포 단위라는 전제가 깨진다. `sqlmcp/guard.py`에서 `from app.models import GuardResult`를 지우고 import 블록을 아래로 교체한다:

```python
from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None
    reason: str | None
```

`app/models.py`에서 `GuardResult` 클래스 정의를 삭제한다 — 앱은 더 이상 쓰지 않는다.

`app/pipeline.py:21`을 교체한다. Task 7에서 다시 사라질 과도기 import다:

```python
from app.sqlgen import execute, generate
from sqlmcp import guard
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest -v`
Expected: 전부 pass

Run: `python -c "import app.pipeline; import app.cli"`
Expected: 오류 없음

- [ ] **Step 5: 커밋**

```bash
git add -A sqlmcp app/sqlgen app/models.py app/pipeline.py tests/test_guard.py
git commit -m "refactor: guard 모듈을 sqlmcp 패키지로 옮긴다"
```

---

## Task 3: 업무 DB 커넥션과 실행 함수를 sqlmcp로 이동

**Files:**
- Create: `sqlmcp/db.py`, `sqlmcp/execute.py`
- Delete: `app/sqlgen/execute.py`
- Test: `tests/test_sqlmcp_db.py`
- Modify: `app/db.py`, `app/cli.py`, `app/pipeline.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sqlmcp_db.py`:

```python
import psycopg
import pytest

from sqlmcp.db import biz_conn_readonly


def _db_available() -> bool:
    try:
        with biz_conn_readonly():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="업무 DB에 연결할 수 없음")


def test_쓰기는_거부된다():
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with biz_conn_readonly() as conn, conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE t_readonly_probe (a int)")


def test_읽기는_동작한다():
    with biz_conn_readonly() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_sqlmcp_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sqlmcp.db'`

- [ ] **Step 3: sqlmcp 쪽 구현**

`sqlmcp/db.py`. `app/db.py`의 `biz_conn_readonly()`를 옮기되 `sqlmcp.config`를 쓴다. SQL_ECHO 디버그 커서는 앱 쪽 기능이므로 가져오지 않는다:

```python
"""업무 DB 커넥션. 이 프로세스만 BIZ_DSN 을 안다."""

import re
from contextlib import contextmanager

import psycopg

from sqlmcp.config import settings


@contextmanager
def biz_conn_readonly():
    """항상 READ ONLY 트랜잭션이며 커밋하지 않는다."""
    with psycopg.connect(settings.biz_dsn, autocommit=False) as conn:
        conn.read_only = True
        try:
            with conn.cursor() as cur:
                # SET 은 바인드 파라미터를 지원하지 않는다. 값은 설정값(int)이라 안전하다.
                cur.execute(f"SET LOCAL statement_timeout = '{int(settings.sql_timeout_sec)}s'")
                cur.execute("SET LOCAL transaction_read_only = on")
            yield conn
        finally:
            conn.rollback()


def mask_dsn(dsn: str) -> str:
    """로그 출력용. 비밀번호를 가린다."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", dsn)
```

`sqlmcp/execute.py` — `app/sqlgen/execute.py`의 내용을 옮긴다:

```python
"""검증된 SQL 을 EXPLAIN 으로 선검사하고 READ ONLY 트랜잭션에서 실행한다.

방어선 ③: 반드시 biz_conn_readonly() 를 통해서만 실행한다.
"""

from sqlmcp.config import settings
from sqlmcp.db import biz_conn_readonly


def explain(sql: str) -> str | None:
    """문법·컬럼 존재를 검증한다. 통과하면 None, 실패하면 오류 문자열."""
    try:
        with biz_conn_readonly() as conn, conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
        return None
    except Exception as e:  # noqa: BLE001
        return str(e).strip()


def run(sql: str) -> tuple[list[str], list[tuple]]:
    """READ ONLY 트랜잭션에서 실행하고 항상 롤백한다."""
    with biz_conn_readonly() as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(settings.sql_max_limit)
    return columns, rows
```

```bash
git rm app/sqlgen/execute.py
```

- [ ] **Step 4: 앱 쪽 정리**

`app/db.py` — `biz_conn_readonly()` 함수 전체(데코레이터 포함)를 삭제한다. `meta_conn`, `biz_conn_collect`, `mask_dsn`, `dsn_user`, EchoCursor 관련은 모두 남긴다.

`app/pipeline.py`의 import를 과도기 형태로 맞춘다:

```python
from app.sqlgen import generate
from sqlmcp import execute, guard
```

`app/cli.py:8` — `biz_conn_readonly`를 제거한다:

```python
from app.db import dsn_user, mask_dsn, meta_conn
```

`app/cli.py`의 `doctor()` 안 biz 점검 블록을 `biz_conn_collect`로 바꾼다. CLI는 수집을 위해 여전히 `BIZ_DSN`을 갖고 있고, 이 점검의 목적은 "CLI가 업무 DB에 닿는가"이기 때문이다. 기존 `try: with biz_conn_readonly() ...` 블록을 통째로 교체한다:

```python
    from app.db import biz_conn_collect

    try:
        with biz_conn_collect() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            user, db = cur.fetchone()
        console.print(f"[green]OK[/] biz DB 연결 (수집용). user={user} db={db}")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] biz DB 연결 실패: {e}")
        ok = False
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest -v`
Expected: 전부 pass (업무 DB가 없으면 `tests/test_sqlmcp_db.py`는 skip)

Run: `python -m app.cli doctor`
Expected: meta / biz(수집용) / Ollama 점검이 모두 출력된다

Run: `python -m app.cli ask "서울 고객의 2025년 판매 실적"`
Expected: 이전과 동일하게 SQL 생성·실행 (아직 MCP 경유 아님)

- [ ] **Step 6: 커밋**

```bash
git add -A sqlmcp app/db.py app/cli.py app/pipeline.py app/sqlgen tests/test_sqlmcp_db.py
git commit -m "refactor: 업무 DB 커넥션과 SQL 실행을 sqlmcp로 옮긴다"
```

---

## Task 4: run_query 오케스트레이션

**Files:**
- Create: `sqlmcp/query.py`
- Test: `tests/test_query_contract.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_query_contract.py`:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_query_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sqlmcp.query'`

- [ ] **Step 3: 구현**

`sqlmcp/query.py`:

```python
"""run_query 도구의 본체.

MCP·HTTP 와 무관한 순수 오케스트레이션이라 직접 호출로 테스트한다.
순서: guard 검증 -> LIMIT 주입 -> EXPLAIN -> 실행.
어느 단계에서 멈췄는지를 error_stage 로 알려준다. 호출자(백엔드)는 이 값으로
LLM 재생성 여부를 판단한다.
"""

from sqlmcp import execute, guard
from sqlmcp.config import settings


def _fail(stage: str, error: str, sql: str | None) -> dict:
    return {
        "ok": False,
        "sql": sql,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "error": error,
        "error_stage": stage,
    }


def run_query(sql: str) -> dict:
    verdict = guard.validate(sql)
    if not verdict.ok:
        return _fail("guard", verdict.reason or "안전 검증 거부", None)

    safe_sql = guard.inject_limit(
        verdict.sql,
        default_limit=settings.sql_row_limit,
        max_limit=settings.sql_max_limit,
    )

    err = execute.explain(safe_sql)
    if err:
        return _fail("explain", err, safe_sql)

    try:
        columns, rows = execute.run(safe_sql)
    except Exception as e:  # noqa: BLE001
        return _fail("execute", str(e).strip(), safe_sql)

    return {
        "ok": True,
        "sql": safe_sql,
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "error": None,
        "error_stage": None,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_query_contract.py -v`
Expected: 6 passed (업무 DB가 없으면 2 passed, 4 skipped)

- [ ] **Step 5: 커밋**

```bash
git add sqlmcp/query.py tests/test_query_contract.py
git commit -m "feat: run_query 오케스트레이션과 계약 테스트를 추가한다"
```

---

## Task 5: MCP 서버 (FastMCP + Bearer 인증)

**Files:**
- Create: `sqlmcp/server.py`
- Test: `tests/test_sqlmcp_auth.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sqlmcp_auth.py`. ASGI 앱 전체를 띄우지 않고 미들웨어만 검증한다:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_sqlmcp_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sqlmcp.server'`

- [ ] **Step 3: 구현**

`sqlmcp/server.py`:

```python
"""MCP 서버 진입점.

Streamable HTTP 로 run_query 도구 하나만 노출한다. 127.0.0.1 에만 바인딩하고
Bearer 토큰을 검사한다 — 같은 호스트의 다른 프로세스도 막기 위해서다.
"""

import hmac
import json

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from sqlmcp import query
from sqlmcp.config import settings

mcp = FastMCP("sqlmcp", host=settings.mcp_host, port=settings.mcp_port)


@mcp.tool()
def run_query(sql: str) -> str:
    """조회 SQL 을 검증하고 READ ONLY 로 실행한다. 결과를 JSON 문자열로 돌려준다.

    date/Decimal/UUID 등 JSON 이 모르는 타입은 문자열로 떨어진다.
    숫자·불리언·null 은 네이티브 타입으로 남는다.
    """
    return json.dumps(query.run_query(sql), default=str, ensure_ascii=False)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        # 타이밍 공격을 막기 위해 상수 시간 비교를 쓴다.
        if not header.startswith(prefix) or not hmac.compare_digest(
            header[len(prefix):], self._token
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app


def main() -> None:
    uvicorn.run(build_app(), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
```

설치된 SDK 버전에 `mcp.streamable_http_app()`이 없으면 `python -c "from mcp.server.fastmcp import FastMCP; print([m for m in dir(FastMCP) if 'app' in m])"`로 동등한 ASGI 앱 팩토리 이름을 찾아 그 이름으로 교체한다. 도구 정의와 미들웨어는 그대로 쓴다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_sqlmcp_auth.py -v`
Expected: 3 passed

- [ ] **Step 5: 서버 수동 기동 확인**

한 터미널에서:

Run: `python -m sqlmcp.server`
Expected: `Uvicorn running on http://127.0.0.1:8100`

다른 터미널에서:

Run: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8100/mcp`
Expected: `401`

확인 후 서버를 Ctrl+C로 종료한다.

- [ ] **Step 6: 커밋**

```bash
git add sqlmcp/server.py tests/test_sqlmcp_auth.py
git commit -m "feat: Bearer 인증이 붙은 MCP 서버를 추가한다"
```

---

## Task 6: 백엔드 MCP 클라이언트

**Files:**
- Create: `app/sqlgen/mcp_client.py`
- Test: `tests/test_mcp_client.py`
- Modify: `app/models.py`, `app/config.py`, `.env.example`, `.env`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_mcp_client.py`. 서버 없이 오류 변환과 응답 매핑만 검증한다:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_mcp_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'mcp_client' from 'app.sqlgen'`

- [ ] **Step 3: 설정과 모델 추가**

`app/config.py` — `# --- SQL 실행 ---` 블록에서 `sql_row_limit`, `sql_max_limit`, `sql_timeout_sec` 세 줄을 삭제한다 (`collect_timeout_sec`는 수집이 계속 쓰므로 남긴다). 같은 블록에 추가:

```python
    # 백엔드 API. Streamlit / CLI 가 이 주소로 질문을 보낸다.
    api_url: str = "http://127.0.0.1:8000"

    # SQL 실행 MCP 서버. uvicorn 프로세스만 사용한다.
    mcp_url: str = "http://127.0.0.1:8100/mcp"
    mcp_auth_token: str = ""
    # 서버의 statement_timeout(.env.mcp 의 SQL_TIMEOUT_SEC)이 먼저 터져야
    # 원인을 알 수 있으므로 반드시 그보다 크게 잡는다.
    mcp_timeout_sec: int = Field(default=30, gt=0)
```

`app/models.py` — `AskResult` 정의 위에 추가한다:

```python
@dataclass(frozen=True)
class QueryResult:
    """MCP run_query 응답.

    error_stage: guard | explain | execute | transport | None
    """
    ok: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    error: str | None = None
    error_stage: str | None = None
```

`AskResult`의 rows 타입도 바꾼다:

```python
    rows: list[list] = field(default_factory=list)
```

`.env.example` — `# --- SQL 실행 ---` 블록에서 `SQL_ROW_LIMIT`, `SQL_MAX_LIMIT`, `SQL_TIMEOUT_SEC` 세 줄을 지우고(`COLLECT_TIMEOUT_SEC`는 유지) 새 블록을 추가한다:

```
# --- 백엔드 / MCP ---
# Streamlit·CLI 가 질문을 보낼 백엔드 주소
API_URL=http://127.0.0.1:8000
# SQL 실행 MCP 서버. uvicorn 프로세스만 사용한다.
MCP_URL=http://127.0.0.1:8100/mcp
# .env.mcp 의 MCP_AUTH_TOKEN 과 같은 값이어야 한다.
MCP_AUTH_TOKEN=change-me
# .env.mcp 의 SQL_TIMEOUT_SEC 보다 커야 한다.
MCP_TIMEOUT_SEC=30
```

`.env` 실물에도 같은 4개 항목을 추가하고, `MCP_AUTH_TOKEN`을 Task 1에서 `.env.mcp`에 넣은 값과 동일하게 맞춘다.

- [ ] **Step 4: 클라이언트 구현**

`app/sqlgen/mcp_client.py`:

```python
"""MCP 서버 호출의 sync 경계.

MCP Python SDK 클라이언트는 async 라서 여기서 asyncio.run() 으로 감싼다.
api.py 의 ask_endpoint 는 def(sync) 라 FastAPI 가 스레드풀에서 실행하며,
그 스레드에는 실행 중인 이벤트 루프가 없으므로 안전하다.

연결 실패·타임아웃·401 은 예외로 올리지 않고 error_stage="transport" 로 바꾼다.
pipeline.ask() 가 LLM 실패를 AskResult.error 로 바꿔 eval 루프를 살려두는 것과
같은 이유다.
"""

import asyncio
import json
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings
from app.models import QueryResult


def _to_result(payload: dict) -> QueryResult:
    return QueryResult(
        ok=bool(payload.get("ok")),
        sql=payload.get("sql"),
        columns=list(payload.get("columns") or []),
        rows=[list(r) for r in (payload.get("rows") or [])],
        row_count=int(payload.get("row_count") or 0),
        error=payload.get("error"),
        error_stage=payload.get("error_stage"),
    )


async def _call(sql: str) -> QueryResult:
    headers = {"Authorization": f"Bearer {settings.mcp_auth_token}"}
    timeout = timedelta(seconds=settings.mcp_timeout_sec)
    async with streamablehttp_client(
        settings.mcp_url, headers=headers, timeout=timeout
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("run_query", {"sql": sql})
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    return _to_result(json.loads(text))


def run_query(sql: str) -> QueryResult:
    """MCP 서버에 SQL 실행을 위임한다. 예외를 던지지 않는다."""
    try:
        return asyncio.run(_call(sql))
    except Exception as e:  # noqa: BLE001
        return QueryResult(
            ok=False,
            error=f"MCP 서버 호출 실패: {type(e).__name__}: {e}",
            error_stage="transport",
        )
```

- [ ] **Step 5: 테스트와 실제 왕복 확인**

Run: `python -m pytest tests/test_mcp_client.py -v`
Expected: 2 passed

한 터미널에서 `python -m sqlmcp.server`를 띄운 뒤:

Run: `python -c "from app.sqlgen import mcp_client; print(mcp_client.run_query('SELECT 1 AS a'))"`
Expected: `QueryResult(ok=True, sql='SELECT 1 AS a LIMIT 100', columns=['a'], rows=[[1]], row_count=1, error=None, error_stage=None)`

토큰이 틀리면 transport로 떨어지는지도 본다:

Run: `python -c "from app.sqlgen import mcp_client as m; m.settings.mcp_auth_token='wrong'; print(m.run_query('SELECT 1').error_stage)"`
Expected: `transport`

- [ ] **Step 6: 커밋**

```bash
git add app/sqlgen/mcp_client.py app/models.py app/config.py .env.example tests/test_mcp_client.py
git commit -m "feat: 백엔드용 MCP 클라이언트와 설정을 추가한다"
```

---

## Task 7: pipeline.ask 를 MCP 경유로 교체

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline_branch.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pipeline_branch.py`:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_pipeline_branch.py -v`
Expected: FAIL — `AttributeError: module 'app.pipeline' has no attribute 'mcp_client'`

- [ ] **Step 3: 모듈 docstring 과 import 정리**

`app/pipeline.py` 상단 docstring의 재시도 정책에서 EXPLAIN 항목을 교체하고 한 줄 추가한다:

```
- EXPLAIN 실패(MCP error_stage="explain")는 오류 메시지를 붙여 1회만 재생성한다.
- guard 거부 / 실행 실패 / MCP 통신 실패는 재생성하지 않는다.
```

import 블록의 마지막 줄을 교체한다. `settings`는 `retrieve()`가 계속 쓰므로 `from app.config import settings`는 남긴다:

```python
from app.sqlgen import generate, mcp_client
```

(Task 2·3에서 넣었던 `from sqlmcp import execute, guard` 줄을 삭제한다. 이제 `app`은 `sqlmcp`를 import하지 않는다.)

- [ ] **Step 4: 실행부 교체**

`ask()`에서 `verdict = guard.validate(sql)`부터 함수 끝(`return result`)까지를 통째로 아래로 바꾼다:

```python
    result.sql = sql

    res = mcp_client.run_query(sql)
    trace["error_stage"] = res.error_stage
    if res.sql:
        result.sql = res.sql

    # EXPLAIN 실패만 재생성한다. guard 거부를 다시 넣으면 모델이 게이트를
    # 통과하는 변형을 찾도록 유도할 뿐이고, 실행 실패와 통신 실패는
    # 재생성으로 나아지지 않는다.
    if not res.ok and res.error_stage == "explain":
        trace["explain_error_1"] = res.error
        try:
            retry = generate.regenerate(llm, text, result.sql, res.error)
        except Exception as e:  # noqa: BLE001
            trace["llm_error"] = f"{type(e).__name__}: {e}"
            result.error = f"재생성 중 LLM 호출 실패: {type(e).__name__}"
            return result

        result.sql = retry
        res = mcp_client.run_query(retry)
        trace["error_stage"] = res.error_stage
        if res.sql:
            result.sql = res.sql
        if not res.ok:
            trace["explain_error_2"] = res.error
            result.error = f"SQL 검증 실패(재시도 포함 2회): {res.error}"
            return result

    if not res.ok:
        labels = {
            "guard": "안전 검증 거부",
            "execute": "실행 실패",
            "transport": "SQL 실행 서버 연결 실패",
        }
        result.error = f"{labels.get(res.error_stage, '실패')}: {res.error}"
        return result

    result.columns = res.columns
    result.rows = res.rows
    return result
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_pipeline_branch.py -v`
Expected: 6 passed

Run: `python -m pytest -v`
Expected: 전부 pass

Run: `grep -n "sqlmcp" app/pipeline.py`
Expected: 출력 없음 (exit 1)

- [ ] **Step 6: 실제 왕복 확인**

한 터미널에서 `python -m sqlmcp.server` 기동 후:

Run: `python -m app.cli ask "서울 고객의 2025년 판매 실적"`
Expected: 선정 테이블·SQL·결과표가 이전과 동일하게 출력된다

서버를 내리고 다시:

Run: `python -m app.cli ask "서울 고객의 2025년 판매 실적"`
Expected: `SQL 실행 서버 연결 실패: ...` 가 빨간 글씨로 출력되고 exit 1. 트레이스백은 뜨지 않는다

- [ ] **Step 7: 커밋**

```bash
git add app/pipeline.py tests/test_pipeline_branch.py
git commit -m "feat: SQL 실행을 MCP 서버 호출로 대체한다"
```

---

## Task 8: Streamlit UI 를 API 경유로 전환

**Files:**
- Modify: `app/ui.py`

- [ ] **Step 1: 구현**

`app/ui.py` 전체를 아래로 교체한다. `pipeline`을 import하지 않는 것이 이 태스크의 요점이다 — Streamlit 프로세스는 `MCP_AUTH_TOKEN`을 쓰지 않는다:

```python
import httpx
import streamlit as st

from app.config import settings

st.set_page_config(page_title="AI 메타데이터 검색", layout="wide")
st.title("AI 메타데이터 검색")

question = st.text_input("질문", placeholder="서울 고객의 2025년 판매 실적")

if st.button("질의", type="primary") and question:
    with st.spinner("검색 및 SQL 생성 중... (LLM 응답까지 최대 1분(최대 5분) 정도 걸릴 수 있습니다)"):
        try:
            resp = httpx.post(
                f"{settings.api_url}/ask",
                json={"question": question},
                timeout=settings.llm_timeout_sec + 60,
            )
            resp.raise_for_status()
            r = resp.json()
        except Exception as e:  # noqa: BLE001
            st.error(f"백엔드 호출 실패: {type(e).__name__}: {e}")
            st.stop()

    st.subheader("선정 테이블")
    st.write(r["tables"] or "(없음)")

    if r["sql"]:
        st.subheader("생성된 SQL")
        st.code(r["sql"], language="sql")

    if r["error"]:
        st.error(r["error"])
    elif r["rows"]:
        st.subheader(f"결과 ({len(r['rows'])}행)")
        st.dataframe([dict(zip(r["columns"], row)) for row in r["rows"]])

    with st.expander("검색 점수 / 히트"):
        st.json(r["trace"])
    with st.expander("LLM 컨텍스트"):
        st.text(r["context"])
```

`api.py:22`가 이미 `[list(map(str, row)) for row in r.rows]`로 값을 문자열화해 내보내므로 `st.dataframe`에 `map(str, ...)`을 다시 씌우지 않는다.

- [ ] **Step 2: 직접 import 가 사라졌는지 확인**

Run: `grep -n "pipeline" app/ui.py`
Expected: 출력 없음 (exit 1)

- [ ] **Step 3: 화면 확인**

세 터미널에서 순서대로 `python -m sqlmcp.server`, `uvicorn app.api:api --port 8000`, `streamlit run app/ui.py`를 띄운다.

브라우저에서 "서울 고객의 2025년 판매 실적"을 질의.
Expected: 선정 테이블·SQL·결과표·trace·컨텍스트가 이전과 동일하게 표시된다

uvicorn만 내리고 다시 질의.
Expected: `백엔드 호출 실패: ConnectError: ...` 가 표시되고 앱이 죽지 않는다

- [ ] **Step 4: 커밋**

```bash
git add app/ui.py
git commit -m "refactor: Streamlit UI 를 백엔드 API 경유로 바꾼다"
```

---

## Task 9: CLI 를 API 경유로 전환하고 doctor 에 MCP 점검 추가

**Files:**
- Modify: `app/cli.py`

- [ ] **Step 1: 공통 헬퍼 추가와 ask 교체**

`app/cli.py`의 `ask` 명령 정의 바로 위에 헬퍼를 넣는다:

```python
def _ask_api(question: str) -> dict:
    """백엔드 /ask 를 호출한다. CLI 는 MCP 토큰을 갖지 않는다."""
    import httpx

    r = httpx.post(
        f"{settings.api_url}/ask",
        json={"question": question},
        timeout=settings.llm_timeout_sec + 60,
    )
    r.raise_for_status()
    return r.json()
```

`ask` 명령 전체를 교체한다:

```python
@app_cli.command()
def ask(question: str, show_context: bool = typer.Option(False, "--show-context")) -> None:
    """질문에 대해 SQL을 생성하고 실행한다. 백엔드 API를 경유한다."""
    from rich.table import Table

    try:
        r = _ask_api(question)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]백엔드 호출 실패[/] {type(e).__name__}: {e}")
        console.print(f"  {settings.api_url} 에 uvicorn 이 떠 있는지 확인하십시오.")
        raise typer.Exit(1)

    console.print(f"[bold]선정 테이블[/] {r['tables']}")
    console.print(f"[bold]점수[/] {r['trace'].get('scores')}")
    if show_context:
        console.print(r["context"])
    if r["sql"]:
        console.print(f"[bold]SQL[/]\n{r['sql']}")
    if r["error"]:
        console.print(f"[red]{r['error']}[/]")
        raise typer.Exit(1)

    if r["columns"]:
        tbl = Table(*r["columns"])
        for row in r["rows"][:20]:
            tbl.add_row(*["" if v is None else str(v) for v in row])
        console.print(tbl)
    console.print(f"{len(r['rows'])}행")
```

- [ ] **Step 2: eval 교체**

`eval_cmd` 안의 `from app.pipeline import ask as run_ask` 줄을 삭제한다 (`from app.pipeline import retrieve`는 남긴다). `--retrieval-only`는 메타 DB만 쓰므로 지금처럼 `retrieve()`를 직접 호출한다. `else:` 분기만 교체한다:

```python
        else:
            try:
                r = _ask_api(case["question"])
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]백엔드 호출 실패[/] {type(e).__name__}: {e}")
                raise typer.Exit(1)
            actual = {n.split(".")[-1] for n in r["tables"]}
            # 무관 질문은 SQL을 만들지 않는 것이 정답이다.
            if expected:
                ok = r["error"] is None and r["sql"] is not None
            else:
                ok = r["sql"] is None
            sql_ok += int(ok)
            sql_mark = "O" if ok else "X"
            note = (r["error"] or "")[:40]
```

- [ ] **Step 3: doctor 에 MCP 점검 추가**

`doctor()` 상단 DSN 출력 아래에 한 줄 덧붙인다:

```python
    console.print(f"[bold]MCP_URL [/] {settings.mcp_url}")
```

biz 점검과 Ollama 점검 사이에 MCP 도달 확인을 넣는다. 인증 없이 호출해 401이 오면 서버가 살아 있고 인증도 켜져 있다는 뜻이다:

```python
    import httpx

    try:
        r = httpx.get(settings.mcp_url, timeout=5)
        if r.status_code == 401:
            console.print(f"[green]OK[/] MCP 서버 응답 (인증 활성). {settings.mcp_url}")
        else:
            console.print(
                f"[red]FAIL[/] MCP 서버가 인증 없이 {r.status_code} 를 반환했습니다. "
                "MCP_AUTH_TOKEN 설정을 확인하십시오."
            )
            ok = False
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] MCP 서버 연결 실패: {e}")
        ok = False
```

`doctor()` 뒷부분의 Ollama 점검이 이미 `import httpx`를 하고 있다면 중복 import를 지운다.

- [ ] **Step 4: 확인**

`python -m sqlmcp.server` 와 `uvicorn app.api:api --port 8000` 을 띄운 상태에서:

Run: `python -m app.cli doctor`
Expected: meta / biz(수집용) / MCP / Ollama 점검이 모두 `OK`

Run: `python -m app.cli ask "서울 고객의 2025년 판매 실적"`
Expected: 선정 테이블·SQL·결과표 출력

Run: `python -m app.cli eval --retrieval-only`
Expected: 8행 표 + `평균 Recall 0.833   평균 Precision 0.688` (백엔드가 없어도 동작)

Run: `python -m app.cli eval`
Expected: 8행 표 + `SQL 성공 7/8`

Run: `grep -n "from app.pipeline import ask" app/cli.py`
Expected: 출력 없음 (exit 1)

- [ ] **Step 5: 커밋**

```bash
git add app/cli.py
git commit -m "refactor: CLI ask/eval 을 API 경유로 바꾸고 doctor 에 MCP 점검을 추가한다"
```

---

## Task 10: 기동 스크립트와 문서

**Files:**
- Create: `scripts/run.ps1`
- Modify: `README.md`, `docs/ARCHITECTURE.md`

- [ ] **Step 1: 기동 스크립트 작성**

`scripts/run.ps1`:

```powershell
# sqlmcp -> uvicorn -> (선택) streamlit 순으로 띄우고, 종료 시 함께 내린다.
# 사용법: scripts\run.ps1 api   또는   scripts\run.ps1 ui
param([ValidateSet("api", "ui")][string]$Mode = "ui")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Wait-For([string]$url, [string]$name, [int[]]$okCodes) {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $code = (Invoke-WebRequest -Uri $url -TimeoutSec 2 -SkipHttpErrorCheck).StatusCode
            if ($okCodes -contains $code) { Write-Host "OK $name ($code)"; return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "$name 기동 실패: $url"
}

$procs = @()
try {
    $procs += Start-Process python -ArgumentList "-m", "sqlmcp.server" -PassThru -NoNewWindow
    # 인증이 켜져 있으므로 토큰 없는 요청에 401 이 오는 것이 정상이다.
    Wait-For "http://127.0.0.1:8100/mcp" "sqlmcp" @(401)

    $procs += Start-Process python -ArgumentList "-m", "uvicorn", "app.api:api", "--port", "8000" -PassThru -NoNewWindow
    Wait-For "http://127.0.0.1:8000/docs" "uvicorn" @(200)

    if ($Mode -eq "ui") {
        python -m streamlit run app/ui.py
    } else {
        Write-Host "API 준비 완료. Ctrl+C 로 종료합니다."
        Wait-Process -Id $procs[-1].Id
    }
} finally {
    foreach ($p in $procs) {
        if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
}
```

- [ ] **Step 2: 스크립트 확인**

Run: `powershell -ExecutionPolicy Bypass -File scripts/run.ps1 api`
Expected: `OK sqlmcp (401)` → `OK uvicorn (200)` → `API 준비 완료.`

Ctrl+C로 종료한 뒤 남은 프로세스가 없는지 확인한다:

Run: `powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object ProcessId,CommandLine"`
Expected: `sqlmcp.server` 나 `uvicorn app.api:api` 를 실행 중인 프로세스가 없다

- [ ] **Step 3: README 갱신**

`README.md`의 "FastAPI로 띄우기"와 "Streamlit UI" 두 블록을 아래 하나로 교체한다:

````markdown
## 실행

SQL 실행은 별도 MCP 서버(`sqlmcp`) 프로세스가 담당한다. `.env.mcp`(예시: `.env.mcp.example`)에
`BIZ_DSN`과 `MCP_AUTH_TOKEN`을 채우고, `.env`의 `MCP_AUTH_TOKEN`을 같은 값으로 맞춘다.

```powershell
scripts\run.ps1 ui    # sqlmcp + uvicorn + streamlit
scripts\run.ps1 api   # sqlmcp + uvicorn
```

개별 기동:

```bash
python -m sqlmcp.server           # SQL 실행 MCP 서버 (127.0.0.1:8100)
uvicorn app.api:api --port 8000   # 백엔드. MCP를 호출하는 유일한 프로세스
streamlit run app/ui.py           # UI. 백엔드 API만 호출한다
```
````

"사용법" 블록의 CLI 예시에 주석을 보강한다:

```
python -m app.cli ask "서울 고객의 2025년 판매 실적"   # 백엔드 API가 떠 있어야 한다
python -m app.cli eval                                # 백엔드 API 필요
python -m app.cli eval --retrieval-only               # 검색만 평가. 백엔드 불필요
```

- [ ] **Step 4: ARCHITECTURE.md 갱신**

`docs/ARCHITECTURE.md`의 다음 네 곳을 고친다.

(1) 모듈 표에서 `db.py` 행을 바꾸고 행을 하나 추가한다:

```
| `db.py` | 메타 DB 커넥션 + 수집용 업무 DB 커넥션 |
| `sqlgen/mcp_client.py` | MCP 서버 호출 (sync 경계) |
```

(2) 표 아래에 문단을 추가한다:

```markdown
`sqlmcp/` 는 별도 프로세스로 도는 MCP 서버다. 업무 DB 자격증명(`BIZ_DSN`)과 안전 검증
(AST 검증 · LIMIT 주입 · READ ONLY 실행)을 독점하며 `run_query` 도구 하나를 노출한다.
이 서버를 호출하는 것은 uvicorn(FastAPI) 프로세스뿐이고, Streamlit·CLI 는 백엔드 API 를
거친다.
```

(3) "## 1. DB 접근" 절의 `biz_conn_readonly()` 항목을 교체한다:

```markdown
- `sqlmcp/db.py` `biz_conn_readonly()` — 업무 DB, 항상 READ ONLY 트랜잭션 +
  `statement_timeout`(`.env.mcp` 의 `SQL_TIMEOUT_SEC`). 커밋 없이 항상 롤백.
  **MCP 서버 프로세스에만 존재한다.**
```

(4) "## 4. SQL 생성 및 실행" 절의 3~5번 항목을 교체한다:

```markdown
3. `pipeline.ask()` 가 생성된 SQL 을 `sqlgen/mcp_client.run_query()` 로 MCP 서버에 넘긴다.
4. 서버(`sqlmcp/query.py`)가 `guard.validate` → `guard.inject_limit` → `EXPLAIN` → 실행
   순으로 처리하고 `{ok, sql, columns, rows, row_count, error, error_stage}` 를 돌려준다.
5. `error_stage == "explain"` 일 때만 오류 메시지를 붙여 1회 재생성한다.
   `guard` / `execute` / `transport` 는 재생성 없이 종료한다.
```

"## 5. API / UI" 절의 `ui.py` 설명에서 "API를 거치지 않고 `pipeline`을 직접 import해서"를
"백엔드 `/ask` 를 호출해서"로 고친다.

- [ ] **Step 5: 전체 검증**

Run: `python -m pytest -v`
Expected: 전부 pass

`scripts\run.ps1 api` 로 두 서버를 띄운 뒤:

Run: `python -m app.cli eval`
Expected: 분리 전과 동일한 `평균 Recall 0.833   평균 Precision 0.688   SQL 성공 7/8`

Run: `grep -rn "BIZ_DSN\|biz_conn" app/api.py app/pipeline.py app/ui.py`
Expected: 출력 없음 (exit 1) — 질문 처리 경로에 업무 DB 접근이 남아 있지 않다

- [ ] **Step 6: 커밋**

```bash
git add scripts/run.ps1 README.md docs/ARCHITECTURE.md
git commit -m "docs: MCP 서버 분리에 맞춰 기동 스크립트와 문서를 갱신한다"
```

---

## 완료 기준 (설계 문서 10절)

- [ ] `tests/questions.yaml` 8문항 `eval` 결과가 분리 전과 동일하다 — Recall 0.833 / Precision 0.688 / SQL 7/8 (Task 10 Step 5)
- [ ] uvicorn 프로세스에서 업무 DB로 나가는 psycopg 커넥션이 0건이다 (Task 10 Step 5의 grep)
- [ ] `MCP_AUTH_TOKEN` 없이 `/mcp`를 호출하면 401 (Task 5 Step 5)
- [ ] `sqlmcp` 서버를 내린 상태에서 질문하면 `AskResult.error`가 transport 오류를 담고 프로세스가 죽지 않는다 (Task 7 Step 6)

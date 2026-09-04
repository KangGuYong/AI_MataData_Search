# AI 메타데이터 검색 Text-to-SQL 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한국어 자연어 질문을 받아 PostgreSQL 메타데이터에서 관련 테이블을 검색하고, LLM으로 SQL을 생성해 안전하게 실행한 뒤 결과를 반환하는 PoC를 만든다.

**Architecture:** `meta` 스키마에 테이블/컬럼/값/관계 메타데이터를 적재하고, 질문에 대해 벡터·키워드(pg_trgm)·값 3경로를 병렬 검색한 뒤 가중 RRF로 테이블 단위 점수를 합산한다. 선정된 테이블 사이의 조인 경로를 BFS로 채워 LLM 컨텍스트를 만들고, 생성된 SQL은 sqlglot AST 검사·LIMIT 주입·READ ONLY 트랜잭션을 거쳐 실행한다. `fusion`/`guard`/`graph`/`tokenize` 네 모듈은 순수 함수로 만들어 DB·LLM 없이 단위 테스트한다.

**Tech Stack:** Python 3.11+, psycopg 3, pgvector, pg_trgm, sqlglot, httpx, pydantic-settings, typer, pytest, FastAPI, Streamlit, Ollama(`gemma4:26b-a4b-it-q4_K_M`), 임베딩 `bge-m3:latest`(1024차원, 향후 `nlpai-lab/KURE-v1`으로 교체 가능)

**Spec:** [2026-09-03-ai-metadata-search-design.md](../specs/2026-09-03-ai-metadata-search-design.md)

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `pyproject.toml` | 의존성·패키지 정의 |
| `.env.example` / `.env` | 설정. `.env`는 gitignore |
| `app/config.py` | `.env` 로드, 모든 튜닝 상수의 단일 출처 |
| `app/models.py` | 모듈 간 주고받는 데이터 클래스 (`SearchHit`, `TableScore`, `Edge`, `JoinPath`, `GuardResult`, `AskResult`) |
| `app/db.py` | meta / biz 커넥션 2종, pgvector 등록 |
| `sql/01_extensions.sql` | `vector`, `pg_trgm`, 스키마 생성 |
| `sql/02_meta_schema.sql` | meta 7개 테이블 + 인덱스 |
| `sql/03_biz_fixture.sql` | biz 테스트 테이블 4개 + 더미 데이터 |
| `sql/04_readonly_role.sql` | 읽기전용 롤 (작성만, 적용 보류) |
| `app/embedding/base.py` | `EmbeddingClient` 프로토콜 + 팩토리 |
| `app/embedding/ollama_client.py` | Ollama `/api/embed` |
| `app/embedding/sstf_client.py` | sentence-transformers 폴백 |
| `app/llm/base.py` | `LLMClient` 프로토콜 + 팩토리 |
| `app/llm/ollama_client.py` | Ollama `/api/chat` |
| `app/collect/introspect.py` | 테이블·컬럼·FK 추출 → meta 적재 |
| `app/collect/profile.py` | 카디널리티·대표값·통계 수집 |
| `app/collect/enrich.py` | LLM으로 업무명/설명/동의어 생성 |
| `app/collect/search_text.py` | `search_text` 조립 |
| `app/collect/embed.py` | 테이블/컬럼/값 임베딩 배치 |
| `app/search/tokenize.py` | 한국어 단순 토크나이저 (순수) |
| `app/search/vector.py` | 경로1 — 벡터 검색 |
| `app/search/keyword.py` | 경로2 — pg_trgm 검색 |
| `app/search/value.py` | 경로3 — 값 검색 |
| `app/search/fusion.py` | 가중 RRF 융합 (순수) |
| `app/search/graph.py` | BFS 조인 경로 (순수) + DB 로더 |
| `app/search/context.py` | LLM 컨텍스트 문자열 조립 |
| `app/sqlgen/prompt.py` | 프롬프트 템플릿 |
| `app/sqlgen/generate.py` | LLM 호출 → SQL 추출 |
| `app/sqlgen/guard.py` | SELECT-only 검증 + LIMIT 주입 (순수) |
| `app/sqlgen/execute.py` | EXPLAIN → 실행 → 1회 재생성 |
| `app/pipeline.py` | `ask(question) -> AskResult` |
| `app/cli.py` | typer CLI |
| `app/api.py` | FastAPI |
| `app/ui.py` | Streamlit |
| `tests/questions.yaml` | 8문항 평가 세트 |
| `tests/test_*.py` | 단위 테스트 |

---

## Task 1: 프로젝트 스캐폴딩과 설정

**Files:**
- Create: `pyproject.toml`, `.env.example`, `app/__init__.py`, `app/config.py`, `app/models.py`, `tests/__init__.py`
- Create: `.env` (gitignore 대상, 커밋하지 않음)

- [ ] **Step 1: `pyproject.toml` 작성**

```toml
[project]
name = "ai-metadata-search"
version = "0.1.0"
description = "AI 메타데이터 검색 기반 Text-to-SQL PoC"
requires-python = ">=3.11"
dependencies = [
    "psycopg[binary]>=3.2",
    "pgvector>=0.3.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "httpx>=0.27",
    "sqlglot>=25.24",
    "typer>=0.15",
    "rich>=13.9",
    "pyyaml>=6.0",
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "streamlit>=1.40",
]

[project.optional-dependencies]
sstf = ["sentence-transformers>=3.3"]
dev = ["pytest>=8.3"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: `.env.example` 작성**

```bash
# --- DB ---
META_DSN=postgresql://USER:PASSWORD@192.168.0.140:5432/ggydb
BIZ_DSN=postgresql://USER:PASSWORD@192.168.0.140:5432/ggydb
BIZ_SCHEMA=biz
META_SCHEMA=meta

# --- LLM ---
OLLAMA_BASE_URL=http://192.168.0.169:11434
LLM_MODEL=gemma4:26b-a4b-it-q4_K_M
LLM_TIMEOUT_SEC=60

# --- Embedding ---
EMBED_PROVIDER=ollama
EMBED_MODEL=bge-m3:latest
EMBED_DIM=1024
EMBED_BATCH=16

# --- 수집 ---
VALUE_DISTINCT_MAX=50
SAMPLE_VALUE_COUNT=5

# --- 검색 튜닝 ---
RRF_K=60
W_VALUE=3.0
W_VECTOR_COL=1.0
W_VECTOR_TBL=1.0
W_KEYWORD=0.7
TOP_TABLES=5
SCORE_CUTOFF_RATIO=0.2
MAX_HITS_PER_TABLE=3
MAX_CONTEXT_TABLES=8
JOIN_MAX_DEPTH=3
TRGM_MIN_SIMILARITY=0.7

# --- SQL 실행 ---
SQL_ROW_LIMIT=100
SQL_MAX_LIMIT=1000
SQL_TIMEOUT_SEC=10
COLLECT_TIMEOUT_SEC=120
```

- [ ] **Step 3: 실제 `.env` 생성 (커밋 금지)**

`.env.example`을 복사한 뒤 `META_DSN`, `BIZ_DSN`의 `USER:PASSWORD`를 실제 값(`itos_dev` / 실제 비밀번호)으로 채운다.

```bash
cp .env.example .env
```

- [ ] **Step 4: `app/config.py` 작성**

```python
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 상대경로 ".env"는 cwd 기준으로 해석된다. streamlit/uvicorn을 다른
# 디렉토리에서 실행하면 설정이 조용히 무시되므로 절대경로로 고정한다.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # DB
    meta_dsn: str
    biz_dsn: str
    biz_schema: str = "biz"
    meta_schema: str = "meta"

    # LLM
    ollama_base_url: str = "http://192.168.0.169:11434"
    llm_model: str = "gemma4:26b-a4b-it-q4_K_M"
    llm_timeout_sec: int = 60

    # Embedding
    embed_provider: str = "ollama"          # ollama | sentence_transformers
    embed_model: str = "bge-m3:latest"
    embed_dim: int = 1024
    embed_batch: int = 16

    # 수집
    value_distinct_max: int = 50
    sample_value_count: int = 5

    # 검색 튜닝
    rrf_k: int = 60
    w_value: float = 3.0
    w_vector_col: float = 1.0
    w_vector_tbl: float = 1.0
    w_keyword: float = 0.7
    top_tables: int = 5
    score_cutoff_ratio: float = 0.2
    max_hits_per_table: int = 3
    max_context_tables: int = 8
    join_max_depth: int = 3
    trgm_min_similarity: float = 0.7

    # SQL 실행
    sql_row_limit: int = 100
    sql_max_limit: int = 1000
    # statement_timeout = 0 은 "무제한"이므로 반드시 양수여야 한다.
    sql_timeout_sec: int = Field(default=10, gt=0)
    collect_timeout_sec: int = Field(default=120, gt=0)

    @property
    def weights(self) -> dict[str, float]:
        return {
            "value": self.w_value,
            "v_col": self.w_vector_col,
            "v_tbl": self.w_vector_tbl,
            "keyword": self.w_keyword,
        }


settings = Settings()
```

- [ ] **Step 5: `app/models.py` 작성**

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchHit:
    """검색 경로 하나가 만들어낸 히트 1건."""
    source: str                 # 'v_col' | 'v_tbl' | 'keyword' | 'value'
    table_id: int
    column_id: int | None
    rank: int                   # 1-based
    raw_score: float
    detail: str = ""            # trace용. 예: "region='서울'"


@dataclass(frozen=True)
class TableScore:
    table_id: int
    score: float
    hits: tuple[SearchHit, ...]


@dataclass(frozen=True)
class Edge:
    """무방향 조인 간선 1개."""
    from_table_id: int
    from_column: str
    to_table_id: int
    to_column: str


@dataclass(frozen=True)
class JoinPath:
    tables: tuple[int, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None
    reason: str | None


@dataclass
class AskResult:
    question: str
    table_ids: list[int] = field(default_factory=list)
    table_names: list[str] = field(default_factory=list)
    context: str = ""
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None
    trace: dict = field(default_factory=dict)
```

- [ ] **Step 6: 빈 패키지 파일 생성**

```bash
mkdir -p app/embedding app/llm app/collect app/search app/sqlgen sql tests
for d in app app/embedding app/llm app/collect app/search app/sqlgen tests; do touch $d/__init__.py; done
```

- [ ] **Step 7: 설치 확인**

Run: `pip install -e ".[dev]"`
Expected: 설치 성공, 오류 없음

Run: `python -c "from app.config import settings; print(settings.embed_dim, settings.weights)"`
Expected: `1024 {'value': 3.0, 'v_col': 1.0, 'v_tbl': 1.0, 'keyword': 0.7}`

- [ ] **Step 8: 커밋**

```bash
git add pyproject.toml .env.example app tests
git commit -m "feat: 프로젝트 스캐폴딩, 설정 및 공용 데이터 모델"
```

---

## Task 2: DB 연결과 `cli doctor`

**Files:**
- Create: `app/db.py`, `app/cli.py`

- [ ] **Step 1: `app/db.py` 작성**

```python
import re
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings


def mask_dsn(dsn: str) -> str:
    """로그 출력용. 비밀번호를 가린다."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", dsn)


@contextmanager
def meta_conn():
    """메타데이터 DB 커넥션. 쓰기 가능."""
    with psycopg.connect(settings.meta_dsn, autocommit=False) as conn:
        try:
            register_vector(conn)
        except Exception:  # noqa: BLE001
            # vector 확장 설치 전(init-db 실행 시점)에는 등록이 실패한다.
            # 벡터 값은 항상 문자열 + ::vector 캐스팅으로 넘기므로 없어도 동작한다.
            conn.rollback()
        yield conn


@contextmanager
def biz_conn_readonly():
    """업무 DB 커넥션. 항상 READ ONLY 트랜잭션이며 커밋하지 않는다."""
    with psycopg.connect(settings.biz_dsn, autocommit=False) as conn:
        conn.read_only = True
        try:
            with conn.cursor() as cur:
                # PostgreSQL의 SET은 바인드 파라미터를 받지 않는다(syntax error at or near "$1").
                # 값은 int로 강제 변환하므로 인젝션 경로가 없다.
                cur.execute(
                    f"SET LOCAL statement_timeout = '{int(settings.sql_timeout_sec)}s'"
                )
                cur.execute("SET LOCAL transaction_read_only = on")
            yield conn
        finally:
            conn.rollback()


@contextmanager
def biz_conn_collect():
    """수집용 업무 DB 커넥션. 프로파일링은 시간이 걸릴 수 있어 타임아웃을 길게 잡는다."""
    with psycopg.connect(settings.biz_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SET statement_timeout = '{int(settings.collect_timeout_sec)}s'"
            )
        yield conn


def dsn_user(dsn: str) -> str:
    m = re.search(r"://([^:/@]+):", dsn)
    return m.group(1) if m else "?"
```

- [ ] **Step 2: `app/cli.py`에 `doctor` 명령 작성**

```python
import sys

import typer
from rich.console import Console

from app.config import settings
from app.db import biz_conn_readonly, dsn_user, mask_dsn, meta_conn

app_cli = typer.Typer(help="AI 메타데이터 검색 CLI")
console = Console()


@app_cli.callback()
def _root() -> None:
    """명령이 하나뿐일 때 Typer가 서브커맨드 이름을 생략시키는 것을 막는다."""


@app_cli.command()
def doctor() -> None:
    """DB / Ollama 연결과 확장 설치 상태를 점검한다."""
    console.print(f"[bold]META_DSN[/] {mask_dsn(settings.meta_dsn)}")
    console.print(f"[bold]BIZ_DSN [/] {mask_dsn(settings.biz_dsn)}")

    if dsn_user(settings.meta_dsn) == dsn_user(settings.biz_dsn):
        console.print(
            "[yellow]경고[/] meta와 biz가 동일 계정입니다. "
            "읽기전용 롤 분리는 보류 상태이며 애플리케이션 방어선에만 의존합니다."
        )

    ok = True

    try:
        with meta_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')")
            exts = {r[0] for r in cur.fetchall()}
        console.print(f"[green]OK[/] meta DB 연결. 확장: {sorted(exts) or '없음'}")
        for need in ("vector", "pg_trgm"):
            if need not in exts:
                console.print(f"[red]FAIL[/] 확장 '{need}' 미설치 - sql/01_extensions.sql 실행 필요")
                ok = False
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] meta DB 연결 실패: {e}")
        ok = False

    try:
        with biz_conn_readonly() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            user, db = cur.fetchone()
        console.print(f"[green]OK[/] biz DB 연결 (READ ONLY). user={user} db={db}")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] biz DB 연결 실패: {e}")
        ok = False

    import httpx

    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=10)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        console.print(f"[green]OK[/] Ollama 연결. 모델 {len(names)}개")
        if settings.llm_model not in names:
            console.print(f"[yellow]경고[/] LLM_MODEL '{settings.llm_model}' 미존재")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] Ollama 연결 실패: {e}")
        ok = False

    raise typer.Exit(0 if ok else 1)


def main() -> None:
    # Windows 기본 코드페이지(CP949)에서 한글 출력이 깨지는 것을 막는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    app_cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 실행 확인**

Run: `python -m app.cli doctor`
Expected: meta/biz DB 연결 OK, Ollama 연결 OK. **확장 `vector`/`pg_trgm`은 아직 미설치이므로 FAIL이 정상이다** (Task 3에서 해결).

- [ ] **Step 4: 커밋**

```bash
git add app/db.py app/cli.py
git commit -m "feat: DB 커넥션 헬퍼와 cli doctor 진단 명령"
```

---

## Task 3: meta 스키마 DDL

**Files:**
- Create: `sql/01_extensions.sql`, `sql/02_meta_schema.sql`, `sql/04_readonly_role.sql`
- Modify: `app/cli.py` (`init-db` 명령 추가)

- [ ] **Step 1: `sql/01_extensions.sql` 작성**

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS biz;
```

- [ ] **Step 2: `sql/02_meta_schema.sql` 작성**

스펙 4.2 / 4.3의 DDL을 그대로 옮기고 `DROP ... CASCADE`를 앞에 붙여 재실행 가능하게 만든다.

```sql
DROP TABLE IF EXISTS meta.metadata_column_term   CASCADE;
DROP TABLE IF EXISTS meta.metadata_business_term CASCADE;
DROP TABLE IF EXISTS meta.metadata_relation      CASCADE;
DROP TABLE IF EXISTS meta.metadata_column_value  CASCADE;
DROP TABLE IF EXISTS meta.metadata_column        CASCADE;
DROP TABLE IF EXISTS meta.metadata_table         CASCADE;
DROP TABLE IF EXISTS meta.datasource             CASCADE;

CREATE TABLE meta.datasource (
    datasource_id   BIGSERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,
    db_kind         VARCHAR(30)  NOT NULL DEFAULT 'postgresql',
    host            VARCHAR(255),
    port            INTEGER,
    database_name   VARCHAR(128),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE meta.metadata_table (
    table_id        BIGSERIAL PRIMARY KEY,
    datasource_id   BIGINT NOT NULL REFERENCES meta.datasource(datasource_id) ON DELETE CASCADE,
    schema_name     VARCHAR(128) NOT NULL,
    table_name      VARCHAR(128) NOT NULL,
    table_type      VARCHAR(30)  NOT NULL DEFAULT 'TABLE',
    table_comment   TEXT,
    business_name   VARCHAR(255),
    business_desc   TEXT,
    row_count_est   BIGINT,
    search_text     TEXT,
    embedding       vector(1024),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_table UNIQUE (datasource_id, schema_name, table_name)
);

CREATE TABLE meta.metadata_column (
    column_id        BIGSERIAL PRIMARY KEY,
    table_id         BIGINT NOT NULL REFERENCES meta.metadata_table(table_id) ON DELETE CASCADE,
    column_name      VARCHAR(128) NOT NULL,
    ordinal_position INTEGER,
    data_type        VARCHAR(100),
    is_nullable      BOOLEAN,
    is_primary_key   BOOLEAN NOT NULL DEFAULT FALSE,
    is_foreign_key   BOOLEAN NOT NULL DEFAULT FALSE,
    column_comment   TEXT,
    business_name    VARCHAR(255),
    business_desc    TEXT,
    distinct_count   BIGINT,
    null_ratio       NUMERIC(5,4),
    min_value        TEXT,
    max_value        TEXT,
    sample_values    TEXT[],
    search_text      TEXT,
    embedding        vector(1024),
    is_sensitive     BOOLEAN NOT NULL DEFAULT FALSE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_column UNIQUE (table_id, column_name)
);

CREATE TABLE meta.metadata_column_value (
    value_id      BIGSERIAL PRIMARY KEY,
    column_id     BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    value_text    TEXT   NOT NULL,
    value_freq    BIGINT,
    embedding     vector(1024),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_column_value UNIQUE (column_id, value_text)
);

CREATE TABLE meta.metadata_relation (
    relation_id     BIGSERIAL PRIMARY KEY,
    from_table_id   BIGINT NOT NULL REFERENCES meta.metadata_table(table_id)   ON DELETE CASCADE,
    from_column_id  BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    to_table_id     BIGINT NOT NULL REFERENCES meta.metadata_table(table_id)   ON DELETE CASCADE,
    to_column_id    BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    relation_type   VARCHAR(30) NOT NULL DEFAULT 'FOREIGN_KEY',
    relation_name   VARCHAR(255),
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_relation UNIQUE (from_table_id, from_column_id, to_table_id, to_column_id)
);

CREATE TABLE meta.metadata_business_term (
    term_id      BIGSERIAL PRIMARY KEY,
    term         VARCHAR(255) NOT NULL UNIQUE,
    term_type    VARCHAR(50),
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE meta.metadata_column_term (
    column_id  BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    term_id    BIGINT NOT NULL REFERENCES meta.metadata_business_term(term_id) ON DELETE CASCADE,
    weight     NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    source     VARCHAR(20)  NOT NULL DEFAULT 'llm',
    PRIMARY KEY (column_id, term_id)
);

CREATE INDEX ix_col_search_trgm ON meta.metadata_column       USING GIN (search_text gin_trgm_ops);
CREATE INDEX ix_tbl_search_trgm ON meta.metadata_table        USING GIN (search_text gin_trgm_ops);
CREATE INDEX ix_val_text_trgm   ON meta.metadata_column_value USING GIN (value_text  gin_trgm_ops);

CREATE INDEX ix_col_emb ON meta.metadata_column       USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_tbl_emb ON meta.metadata_table        USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_val_emb ON meta.metadata_column_value USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_col_table ON meta.metadata_column(table_id);
CREATE INDEX ix_rel_from  ON meta.metadata_relation(from_table_id);
CREATE INDEX ix_rel_to    ON meta.metadata_relation(to_table_id);
```

- [ ] **Step 3: `sql/04_readonly_role.sql` 작성 (적용하지 않음)**

```sql
-- PoC에서는 적용 보류. 운영 전환 시 실행한다.
-- psql -v ro_password='<비밀번호>' -f sql/04_readonly_role.sql
CREATE ROLE itos_ro LOGIN PASSWORD :'ro_password';
GRANT CONNECT ON DATABASE ggydb TO itos_ro;
GRANT USAGE ON SCHEMA biz TO itos_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA biz TO itos_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA biz GRANT SELECT ON TABLES TO itos_ro;
REVOKE ALL ON SCHEMA meta FROM itos_ro;
```

- [ ] **Step 4: `app/cli.py`에 `init-db` 명령 추가**

`doctor` 함수 아래에 추가한다. 파일 상단 import에 `from pathlib import Path`를 더한다.

```python
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_sql_file(name: str) -> None:
    path = SQL_DIR / name
    sql = path.read_text(encoding="utf-8")
    with meta_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    console.print(f"[green]OK[/] {name} 실행 완료")


@app_cli.command("init-db")
def init_db() -> None:
    """확장 설치 + meta 스키마 생성. meta 테이블을 전부 재생성한다."""
    _run_sql_file("01_extensions.sql")
    _run_sql_file("02_meta_schema.sql")
```

- [ ] **Step 5: 실행 및 확인**

Run: `python -m app.cli init-db`
Expected: `OK 01_extensions.sql 실행 완료` / `OK 02_meta_schema.sql 실행 완료`

Run: `python -m app.cli doctor`
Expected: 확장 `['pg_trgm', 'vector']` 표시, 모든 항목 OK

- [ ] **Step 6: 커밋**

```bash
git add sql app/cli.py
git commit -m "feat: meta 스키마 DDL과 init-db 명령"
```

---

## Task 4: biz 테스트 픽스처

**Files:**
- Create: `sql/03_biz_fixture.sql`
- Modify: `app/cli.py` (`fixture` 명령 추가)

- [ ] **Step 1: `sql/03_biz_fixture.sql` 작성**

함정을 의도적으로 심는다: `order_detail.amount`가 `orders.total_amount`와 혼동되도록, `product.category`에 `'서울식품'`이 들어가 값 검색 오탐을 유발하도록.

```sql
DROP TABLE IF EXISTS biz.order_detail CASCADE;
DROP TABLE IF EXISTS biz.orders       CASCADE;
DROP TABLE IF EXISTS biz.product      CASCADE;
DROP TABLE IF EXISTS biz.customer     CASCADE;

CREATE TABLE biz.customer (
    customer_id   BIGSERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    region        VARCHAR(50)  NOT NULL,
    grade         VARCHAR(20)  NOT NULL,
    joined_at     DATE         NOT NULL
);
COMMENT ON TABLE  biz.customer               IS '고객 기본정보';
COMMENT ON COLUMN biz.customer.customer_id   IS '고객 식별번호';
COMMENT ON COLUMN biz.customer.customer_name IS '고객명';
COMMENT ON COLUMN biz.customer.region        IS '고객 지역';
COMMENT ON COLUMN biz.customer.grade         IS '고객 등급';

CREATE TABLE biz.product (
    product_id   BIGSERIAL PRIMARY KEY,
    product_name VARCHAR(100)   NOT NULL,
    category     VARCHAR(50)    NOT NULL,
    unit_price   NUMERIC(15,2)  NOT NULL
);
COMMENT ON TABLE  biz.product              IS '상품 기본정보';
COMMENT ON COLUMN biz.product.product_name IS '상품명';
COMMENT ON COLUMN biz.product.category     IS '상품 분류';
COMMENT ON COLUMN biz.product.unit_price   IS '단가';

CREATE TABLE biz.orders (
    order_id     BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT        NOT NULL REFERENCES biz.customer(customer_id),
    order_date   DATE          NOT NULL,
    total_amount NUMERIC(15,2) NOT NULL,
    status       VARCHAR(20)   NOT NULL
);
COMMENT ON TABLE  biz.orders              IS '고객의 주문 정보';
COMMENT ON COLUMN biz.orders.order_date   IS '주문일자';
COMMENT ON COLUMN biz.orders.total_amount IS '주문 총 금액';
COMMENT ON COLUMN biz.orders.status       IS '주문 상태';

CREATE TABLE biz.order_detail (
    order_detail_id BIGSERIAL PRIMARY KEY,
    order_id        BIGINT        NOT NULL REFERENCES biz.orders(order_id),
    product_id      BIGINT        NOT NULL REFERENCES biz.product(product_id),
    quantity        INTEGER       NOT NULL,
    amount          NUMERIC(15,2) NOT NULL
);
COMMENT ON TABLE  biz.order_detail          IS '주문 상세 항목';
COMMENT ON COLUMN biz.order_detail.quantity IS '판매 수량';
COMMENT ON COLUMN biz.order_detail.amount   IS '항목별 금액';

-- customer 200건. region 8종
INSERT INTO biz.customer (customer_name, region, grade, joined_at)
SELECT
    '고객' || LPAD(g::text, 3, '0'),
    (ARRAY['서울','경기','부산','대구','인천','광주','대전','울산'])[1 + (g % 8)],
    -- g % 4 로 하면 4가 8(region 주기)을 나누므로 등급이 지역의 결정함수가 된다.
    -- (g / 8) % 4 는 region 주기가 한 바퀴 돌 때마다 등급을 바꿔 상관을 끊는다.
    (ARRAY['VIP','GOLD','SILVER','BRONZE'])[1 + ((g / 8) % 4)],
    DATE '2022-01-01' + (g % 900)
FROM generate_series(1, 200) AS g;

-- product 50건. category 5종 (그중 '서울식품'이 값 검색 오탐 함정)
INSERT INTO biz.product (product_name, category, unit_price)
SELECT
    '상품' || LPAD(g::text, 3, '0'),
    (ARRAY['서울식품','가전','의류','도서','생활용품'])[1 + (g % 5)],
    (1000 + (g % 50) * 500)::numeric
FROM generate_series(1, 50) AS g;

-- orders 2000건. 2023~2025 분산
INSERT INTO biz.orders (customer_id, order_date, total_amount, status)
SELECT
    1 + (g % 200),
    DATE '2023-01-01' + (g % 1000),
    (10000 + (g % 890) * 1000)::numeric,
    (ARRAY['COMPLETED','SHIPPED','CANCELLED'])[1 + (g % 3)]
FROM generate_series(1, 2000) AS g;

-- order_detail 6000건
INSERT INTO biz.order_detail (order_id, product_id, quantity, amount)
SELECT
    1 + (g % 2000),
    1 + (g % 50),
    1 + (g % 9),
    (1000 + (g % 200) * 700)::numeric
FROM generate_series(1, 6000) AS g;

ANALYZE biz.customer;
ANALYZE biz.product;
ANALYZE biz.orders;
ANALYZE biz.order_detail;
```

- [ ] **Step 2: `app/cli.py`에 `fixture` 명령 추가**

```python
@app_cli.command()
def fixture() -> None:
    """biz 테스트 테이블과 더미 데이터를 생성한다. 기존 biz 테이블을 삭제한다."""
    _run_sql_file("03_biz_fixture.sql")
```

- [ ] **Step 3: 실행 및 검증**

Run: `python -m app.cli fixture`
Expected: `OK 03_biz_fixture.sql 실행 완료`

Run:
```bash
python -c "
from app.db import biz_conn_readonly
with biz_conn_readonly() as c, c.cursor() as cur:
    for t in ('customer','product','orders','order_detail'):
        cur.execute(f'SELECT count(*) FROM biz.{t}')
        print(t, cur.fetchone()[0])
    cur.execute(\"SELECT DISTINCT region FROM biz.customer ORDER BY 1\")
    print([r[0] for r in cur.fetchall()])
"
```
Expected:
```
customer 200
product 50
orders 2000
order_detail 6000
['경기', '광주', '대구', '대전', '부산', '서울', '울산', '인천']
```

- [ ] **Step 4: 커밋**

```bash
git add sql/03_biz_fixture.sql app/cli.py
git commit -m "test: biz 테스트 픽스처와 함정 데이터"
```

---

## Task 5: Embedding 클라이언트

**계획 수립 후 환경 확인에서 리스크가 해소되었다.** Ollama 서버(192.168.0.169)에 `bge-m3:latest`가 이미 설치되어 있고 `/api/embed` 응답이 **정확히 1024차원**임을 확인했다. KURE-v1이 BGE-M3의 한국어 파인튜닝 모델이라 차원이 동일하므로, 스키마 변경 없이 `bge-m3:latest`로 시작하고 나중에 `.env` 한 줄로 KURE-v1으로 교체할 수 있다.

**Files:**
- Create: `app/embedding/base.py`, `app/embedding/ollama_client.py`, `app/embedding/sstf_client.py`
- Modify: `app/cli.py` (`embed-test` 명령 추가)

- [ ] **Step 1: 기본 임베딩 모델 확인 (변환 작업 불필요)**

`EMBED_MODEL=bge-m3:latest`를 그대로 사용한다. 아래로 서버 상태만 확인한다.

```bash
curl -s http://192.168.0.169:11434/api/tags | grep -o '"name":"[^"]*"'
```

Expected: `"name":"bge-m3:latest"` 가 포함된다.

**KURE-v1 전환은 선택 사항이며 지금 하지 않는다.** 나중에 한국어 정확도를 더 끌어올릴 필요가 생기면 Ollama 서버에서 아래를 수행한 뒤 `.env`의 `EMBED_MODEL`만 바꾼다.

```bash
huggingface-cli download nlpai-lab/KURE-v1 --local-dir ./KURE-v1
python llama.cpp/convert_hf_to_gguf.py ./KURE-v1 --outfile kure-v1-f16.gguf --outtype f16
printf 'FROM ./kure-v1-f16.gguf\n' > Modelfile
ollama create kure-v1 -f Modelfile
```

- [ ] **Step 2: `app/embedding/base.py` 작성**

```python
from functools import lru_cache
from typing import Protocol

from app.config import settings


class EmbeddingClient(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 리스트를 같은 순서의 벡터 리스트로 변환한다."""
        ...


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """프로세스당 하나만 만든다.

    FastAPI/Streamlit은 요청마다 이 함수를 부르는데, 매번 새 httpx.Client를
    만들면 커넥션 풀이 닫히지 않고 쌓여 파일 디스크립터가 샌다.
    """
    provider = settings.embed_provider.lower()
    if provider == "ollama":
        from app.embedding.ollama_client import OllamaEmbedding

        return OllamaEmbedding()
    if provider == "sentence_transformers":
        from app.embedding.sstf_client import SentenceTransformerEmbedding

        return SentenceTransformerEmbedding()
    raise ValueError(f"알 수 없는 EMBED_PROVIDER: {settings.embed_provider}")
```

- [ ] **Step 3: `app/embedding/ollama_client.py` 작성**

```python
import httpx

from app.config import settings


class OllamaEmbedding:
    def __init__(self) -> None:
        self.dim = settings.embed_dim
        self._client = httpx.Client(
            base_url=settings.ollama_base_url, timeout=settings.llm_timeout_sec
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), settings.embed_batch):
            chunk = texts[i : i + settings.embed_batch]
            r = self._client.post(
                "/api/embed", json={"model": settings.embed_model, "input": chunk}
            )
            r.raise_for_status()
            payload = r.json()
            if "embeddings" not in payload:
                raise RuntimeError(
                    f"Ollama 임베딩 응답에 'embeddings' 키가 없습니다. "
                    f"model={settings.embed_model} 응답키={sorted(payload)} "
                    f"본문={str(payload)[:200]}"
                )
            vectors = payload["embeddings"]
            if len(vectors) != len(chunk):
                raise RuntimeError(
                    f"임베딩 개수 불일치: 요청 {len(chunk)} 응답 {len(vectors)}"
                )
            for v in vectors:
                if len(v) != self.dim:
                    raise RuntimeError(
                        f"임베딩 차원 불일치: EMBED_DIM={self.dim} 실제={len(v)}"
                    )
            out.extend(vectors)
        return out
```

- [ ] **Step 4: `app/embedding/sstf_client.py` 작성 (폴백)**

```python
from app.config import settings


class SentenceTransformerEmbedding:
    """Ollama를 쓸 수 없을 때의 폴백. pip install '.[sstf]' 필요.

    주의: 기본 경로(Ollama bge-m3)와 **다른 모델**(KURE-v1)을 쓴다. 두 모델은
    차원이 1024로 같아 차원 가드에 걸리지 않으므로, 이미 bge-m3로 임베딩을
    채운 DB에서 provider만 바꾸면 오류 없이 검색 품질만 조용히 망가진다.
    provider를 바꾼 뒤에는 반드시 `python -m app.cli embed`로 전체를 다시
    임베딩해야 한다.
    """

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("nlpai-lab/KURE-v1")
        self.dim = self._model.get_sentence_embedding_dimension()
        if self.dim != settings.embed_dim:
            raise RuntimeError(
                f"EMBED_DIM={settings.embed_dim} 이지만 모델 차원은 {self.dim} 입니다. "
                ".env의 EMBED_DIM과 DDL의 vector(N)을 맞추세요."
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(
            texts, batch_size=settings.embed_batch, normalize_embeddings=True
        )
        return [v.tolist() for v in vecs]
```

- [ ] **Step 5: `app/cli.py`에 `embed-test` 추가**

```python
@app_cli.command("embed-test")
def embed_test() -> None:
    """임베딩 클라이언트가 살아있는지 확인한다."""
    from app.embedding.base import get_embedding_client

    client = get_embedding_client()
    vecs = client.embed(["매출액", "고객 지역", "서울"])
    console.print(f"[green]OK[/] provider={settings.embed_provider} "
                  f"count={len(vecs)} dim={len(vecs[0])}")
```

- [ ] **Step 6: 실행**

Run: `python -m app.cli embed-test`
Expected: `OK provider=ollama count=3 dim=1024`

실패하면:
```bash
pip install -e ".[sstf]"
# .env 에서 EMBED_PROVIDER=sentence_transformers 로 변경
python -m app.cli embed-test
```
Expected: `OK provider=sentence_transformers count=3 dim=1024`

**둘 다 실패하면 여기서 멈추고 사람에게 보고한다.** 이후 태스크가 전부 이것에 의존한다.

- [ ] **Step 7: 커밋**

```bash
git add app/embedding app/cli.py
git commit -m "feat: 임베딩 클라이언트 (Ollama + sentence-transformers 폴백)"
```

---

## Task 6: LLM 클라이언트

**Files:**
- Create: `app/llm/base.py`, `app/llm/ollama_client.py`

- [ ] **Step 1: `app/llm/base.py` 작성**

```python
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str, system: str | None = None) -> str:
        """프롬프트를 보내고 응답 텍스트를 받는다."""
        ...


def get_llm_client() -> LLMClient:
    from app.llm.ollama_client import OllamaLLM

    return OllamaLLM()
```

- [ ] **Step 2: `app/llm/ollama_client.py` 작성**

```python
import httpx

from app.config import settings


class OllamaLLM:
    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=settings.ollama_base_url, timeout=settings.llm_timeout_sec
        )

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        r = self._client.post(
            "/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        r.raise_for_status()
        return r.json()["message"]["content"]
```

- [ ] **Step 3: 확인**

Run:
```bash
python -c "
from app.llm.base import get_llm_client
print(get_llm_client().complete('1+1은? 숫자만 답하시오.'))
"
```
Expected: `2`를 포함한 짧은 응답

- [ ] **Step 4: 커밋**

```bash
git add app/llm
git commit -m "feat: Ollama LLM 클라이언트"
```

---

## Task 7: 스키마 수집 (introspect)

**Files:**
- Create: `app/collect/introspect.py`
- Modify: `app/cli.py` (`collect` 명령 추가)

- [ ] **Step 1: `app/collect/introspect.py` 작성**

```python
from app.config import settings
from app.db import biz_conn_collect, meta_conn

TABLE_SQL = """
SELECT c.relname,
       obj_description(c.oid, 'pg_class') AS table_comment,
       c.reltuples::bigint                AS row_est
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind = 'r'
ORDER BY c.relname
"""

COLUMN_SQL = """
SELECT a.attname,
       a.attnum,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull                     AS is_nullable,
       col_description(a.attrelid, a.attnum) AS column_comment
FROM pg_attribute a
JOIN pg_class c     ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

PK_SQL = """
SELECT a.attname
FROM pg_index i
JOIN pg_class c     ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
"""

FK_SQL = """
SELECT con.conname,
       src.relname   AS from_table,
       sa.attname    AS from_column,
       tgt.relname   AS to_table,
       ta.attname    AS to_column
FROM pg_constraint con
JOIN pg_class src     ON src.oid = con.conrelid
JOIN pg_namespace n   ON n.oid = src.relnamespace
JOIN pg_class tgt     ON tgt.oid = con.confrelid
JOIN unnest(con.conkey)  WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN unnest(con.confkey) WITH ORDINALITY AS f(attnum, ord) ON f.ord = k.ord
JOIN pg_attribute sa ON sa.attrelid = src.oid AND sa.attnum = k.attnum
JOIN pg_attribute ta ON ta.attrelid = tgt.oid AND ta.attnum = f.attnum
WHERE con.contype = 'f' AND n.nspname = %s
"""


def ensure_datasource() -> int:
    """기본 데이터소스 1건을 보장하고 datasource_id를 돌려준다."""
    with meta_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meta.datasource (name, db_kind, host, port, database_name)
                VALUES ('default', 'postgresql', %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET host = EXCLUDED.host
                RETURNING datasource_id
                """,
                ("192.168.0.140", 5432, "ggydb"),
            )
            ds_id = cur.fetchone()[0]
        conn.commit()
    return ds_id


def collect_schema() -> dict[str, int]:
    """biz 스키마를 읽어 meta의 table/column/relation을 다시 채운다."""
    ds_id = ensure_datasource()
    schema = settings.biz_schema
    stats = {"tables": 0, "columns": 0, "relations": 0}

    with biz_conn_collect() as biz, meta_conn() as meta:
        bcur = biz.cursor()
        mcur = meta.cursor()

        mcur.execute("DELETE FROM meta.metadata_table WHERE datasource_id = %s", (ds_id,))

        bcur.execute(TABLE_SQL, (schema,))
        tables = bcur.fetchall()
        table_ids: dict[str, int] = {}
        column_ids: dict[tuple[str, str], int] = {}

        for tname, tcomment, row_est in tables:
            mcur.execute(
                """
                INSERT INTO meta.metadata_table
                    (datasource_id, schema_name, table_name, table_comment, row_count_est)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING table_id
                """,
                (ds_id, schema, tname, tcomment, row_est),
            )
            tid = mcur.fetchone()[0]
            table_ids[tname] = tid
            stats["tables"] += 1

            bcur.execute(PK_SQL, (schema, tname))
            pks = {r[0] for r in bcur.fetchall()}

            bcur.execute(COLUMN_SQL, (schema, tname))
            for cname, pos, dtype, nullable, ccomment in bcur.fetchall():
                mcur.execute(
                    """
                    INSERT INTO meta.metadata_column
                        (table_id, column_name, ordinal_position, data_type,
                         is_nullable, is_primary_key, column_comment)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING column_id
                    """,
                    (tid, cname, pos, dtype, nullable, cname in pks, ccomment),
                )
                column_ids[(tname, cname)] = mcur.fetchone()[0]
                stats["columns"] += 1

        bcur.execute(FK_SQL, (schema,))
        for conname, ftab, fcol, ttab, tcol in bcur.fetchall():
            if ftab not in table_ids or ttab not in table_ids:
                continue
            fcid = column_ids[(ftab, fcol)]
            tcid = column_ids[(ttab, tcol)]
            mcur.execute(
                "UPDATE meta.metadata_column SET is_foreign_key = TRUE WHERE column_id = %s",
                (fcid,),
            )
            mcur.execute(
                """
                INSERT INTO meta.metadata_relation
                    (from_table_id, from_column_id, to_table_id, to_column_id, relation_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (table_ids[ftab], fcid, table_ids[ttab], tcid, conname),
            )
            stats["relations"] += 1

        meta.commit()

    return stats
```

- [ ] **Step 2: `app/cli.py`에 `collect` 명령 추가**

```python
@app_cli.command()
def collect() -> None:
    """biz 스키마를 읽어 meta에 테이블/컬럼/관계를 적재한다."""
    from app.collect.introspect import collect_schema

    stats = collect_schema()
    console.print(f"[green]OK[/] 수집 완료: {stats}")
```

- [ ] **Step 3: 실행 및 검증**

Run: `python -m app.cli collect`
Expected: `OK 수집 완료: {'tables': 4, 'columns': 19, 'relations': 3}`

Run:
```bash
python -c "
from app.db import meta_conn
with meta_conn() as c, c.cursor() as cur:
    cur.execute('''SELECT t.table_name, c.column_name, c.data_type, c.is_primary_key, c.is_foreign_key
                   FROM meta.metadata_column c JOIN meta.metadata_table t USING (table_id)
                   ORDER BY t.table_name, c.ordinal_position''')
    for r in cur.fetchall(): print(r)
"
```
Expected: 19개 컬럼이 출력되고 `customer.customer_id`는 PK=True, `orders.customer_id`는 FK=True

- [ ] **Step 4: 커밋**

```bash
git add app/collect/introspect.py app/cli.py
git commit -m "feat: biz 스키마 introspect 수집"
```

---

## Task 8: 값 프로파일링

**Files:**
- Create: `app/collect/profile.py`
- Modify: `app/cli.py` (`collect` 명령에 프로파일링 연결)

- [ ] **Step 1: `app/collect/profile.py` 작성**

```python
from psycopg import sql

from app.config import settings
from app.db import biz_conn_collect, meta_conn

TEXT_TYPES = ("character varying", "text", "character", "varchar", "char")


def _is_text(data_type: str) -> bool:
    return any(data_type.startswith(t) for t in TEXT_TYPES)


def profile_columns() -> dict[str, int]:
    """각 컬럼의 통계를 채우고, 저카디널리티 텍스트 컬럼의 값을 적재한다."""
    schema = settings.biz_schema
    stats = {"profiled": 0, "values": 0}

    with meta_conn() as meta, biz_conn_collect() as biz:
        mcur = meta.cursor()
        bcur = biz.cursor()

        mcur.execute(
            """
            SELECT c.column_id, t.table_name, c.column_name, c.data_type
            FROM meta.metadata_column c
            JOIN meta.metadata_table t USING (table_id)
            WHERE c.is_active
            ORDER BY t.table_name, c.ordinal_position
            """
        )
        columns = mcur.fetchall()
        mcur.execute("DELETE FROM meta.metadata_column_value")

        for column_id, tname, cname, dtype in columns:
            ident = sql.Identifier(schema, tname)
            col = sql.Identifier(cname)

            bcur.execute(
                sql.SQL(
                    "SELECT count(*), count({col}), count(DISTINCT {col}), "
                    "min({col})::text, max({col})::text FROM {tbl}"
                ).format(col=col, tbl=ident)
            )
            total, non_null, distinct, vmin, vmax = bcur.fetchone()
            null_ratio = 0.0 if total == 0 else round((total - non_null) / total, 4)

            bcur.execute(
                sql.SQL(
                    "SELECT DISTINCT {col}::text FROM {tbl} "
                    "WHERE {col} IS NOT NULL ORDER BY 1 LIMIT %s"
                ).format(col=col, tbl=ident),
                (settings.sample_value_count,),
            )
            samples = [r[0] for r in bcur.fetchall()]

            mcur.execute(
                """
                UPDATE meta.metadata_column
                SET distinct_count = %s, null_ratio = %s, min_value = %s,
                    max_value = %s, sample_values = %s, updated_at = NOW()
                WHERE column_id = %s
                """,
                (distinct, null_ratio, vmin, vmax, samples, column_id),
            )
            stats["profiled"] += 1

            if not _is_text(dtype) or distinct > settings.value_distinct_max:
                continue

            bcur.execute(
                sql.SQL(
                    "SELECT {col}::text, count(*) FROM {tbl} "
                    "WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
                ).format(col=col, tbl=ident)
            )
            for value_text, freq in bcur.fetchall():
                mcur.execute(
                    """
                    INSERT INTO meta.metadata_column_value (column_id, value_text, value_freq)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (column_id, value_text) DO NOTHING
                    """,
                    (column_id, value_text, freq),
                )
                stats["values"] += 1

        meta.commit()

    return stats
```

- [ ] **Step 2: `app/cli.py`의 `collect`를 확장**

기존 `collect` 함수를 아래로 교체한다.

```python
@app_cli.command()
def collect() -> None:
    """biz 스키마를 읽어 meta에 테이블/컬럼/관계와 값 프로파일을 적재한다."""
    from app.collect.introspect import collect_schema
    from app.collect.profile import profile_columns

    console.print(f"[green]OK[/] 스키마 수집: {collect_schema()}")
    console.print(f"[green]OK[/] 값 프로파일: {profile_columns()}")
```

- [ ] **Step 3: 실행 및 검증**

Run: `python -m app.cli collect`
Expected: `스키마 수집: {'tables': 4, 'columns': 19, 'relations': 3}` / `값 프로파일: {'profiled': 19, 'values': ...}`

Run:
```bash
python -c "
from app.db import meta_conn
with meta_conn() as c, c.cursor() as cur:
    cur.execute('''SELECT t.table_name, col.column_name, v.value_text, v.value_freq
                   FROM meta.metadata_column_value v
                   JOIN meta.metadata_column col USING (column_id)
                   JOIN meta.metadata_table t USING (table_id)
                   ORDER BY 1,2,4 DESC''')
    for r in cur.fetchall(): print(r)
"
```
Expected: `customer.region`의 8개 지역, `customer.grade` 4종, `orders.status` 3종, `product.category` 5종(`서울식품` 포함)이 나타난다. `customer_name`(200종)과 `product_name`(50종)은 **나오지 않아야** 한다 — `product_name`은 50종으로 임계값과 같으므로 포함된다는 점에 주의하고, 임계값을 넘는 `customer_name`만 제외되는 것이 정상이다.

- [ ] **Step 4: 커밋**

```bash
git add app/collect/profile.py app/cli.py
git commit -m "feat: 컬럼 통계와 저카디널리티 값 프로파일링"
```

---

## Task 9: LLM 업무용어 생성 (enrich)

**Files:**
- Create: `app/collect/enrich.py`
- Modify: `app/cli.py` (`enrich` 명령 추가)

- [ ] **Step 1: `app/collect/enrich.py` 작성**

```python
import json
import re

from app.db import meta_conn
from app.llm.base import get_llm_client

SYSTEM = "당신은 데이터 웨어하우스 메타데이터를 정리하는 한국어 데이터 분석가입니다."

TABLE_PROMPT = """다음 PostgreSQL 테이블에 대한 한국어 업무 정보를 만드시오.

테이블: {schema}.{table}
DB 주석: {comment}
컬럼: {columns}

아래 JSON 형식으로만 답하시오. 설명이나 코드펜스 없이 JSON만 출력하시오.
{{"business_name": "짧은 한국어 업무명", "business_desc": "한 문장 설명"}}"""

COLUMN_PROMPT = """다음 PostgreSQL 컬럼에 대한 한국어 업무 정보를 만드시오.

테이블: {schema}.{table} ({table_desc})
컬럼: {column}
타입: {data_type}
DB 주석: {comment}
샘플 값: {samples}
서로 다른 값의 수: {distinct}

business_terms 에는 현업이 이 컬럼을 부를 만한 한국어 동의어를 3~6개 넣으시오.
예: total_amount 라면 ["매출", "매출액", "판매금액", "주문금액", "판매실적"]

아래 JSON 형식으로만 답하시오. 설명이나 코드펜스 없이 JSON만 출력하시오.
{{"business_name": "짧은 한국어 업무명", "business_desc": "한 문장 설명", "business_terms": ["동의어1", "동의어2"]}}"""


def _parse_json(text: str) -> dict:
    """코드펜스나 앞뒤 설명이 섞여도 첫 JSON 오브젝트를 뽑아낸다."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        raise ValueError(f"JSON을 찾을 수 없음: {text[:200]}")
    return json.loads(m.group(0))


def enrich_all() -> dict[str, int]:
    llm = get_llm_client()
    stats = {"tables": 0, "columns": 0, "terms": 0, "failed": 0}

    with meta_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT t.table_id, t.schema_name, t.table_name, t.table_comment,
                   string_agg(c.column_name || ' ' || c.data_type, ', '
                              ORDER BY c.ordinal_position)
            FROM meta.metadata_table t
            JOIN meta.metadata_column c USING (table_id)
            GROUP BY t.table_id, t.schema_name, t.table_name, t.table_comment
            ORDER BY t.table_name
            """
        )
        for table_id, schema, tname, tcomment, cols in cur.fetchall():
            prompt = TABLE_PROMPT.format(
                schema=schema, table=tname, comment=tcomment or "(없음)", columns=cols
            )
            try:
                data = _parse_json(llm.complete(prompt, system=SYSTEM))
            except Exception:  # noqa: BLE001
                stats["failed"] += 1
                continue
            cur.execute(
                """
                UPDATE meta.metadata_table
                SET business_name = %s, business_desc = %s, updated_at = NOW()
                WHERE table_id = %s
                """,
                (data.get("business_name"), data.get("business_desc"), table_id),
            )
            stats["tables"] += 1
        conn.commit()

        cur.execute(
            """
            SELECT c.column_id, t.schema_name, t.table_name,
                   COALESCE(t.business_desc, t.table_comment, ''),
                   c.column_name, c.data_type, c.column_comment,
                   c.sample_values, c.distinct_count
            FROM meta.metadata_column c
            JOIN meta.metadata_table t USING (table_id)
            WHERE c.is_active
            ORDER BY t.table_name, c.ordinal_position
            """
        )
        for (column_id, schema, tname, tdesc, cname, dtype,
             ccomment, samples, distinct) in cur.fetchall():
            prompt = COLUMN_PROMPT.format(
                schema=schema, table=tname, table_desc=tdesc, column=cname,
                data_type=dtype, comment=ccomment or "(없음)",
                samples=", ".join(samples or []) or "(없음)", distinct=distinct,
            )
            try:
                data = _parse_json(llm.complete(prompt, system=SYSTEM))
            except Exception:  # noqa: BLE001
                stats["failed"] += 1
                continue

            cur.execute(
                """
                UPDATE meta.metadata_column
                SET business_name = %s, business_desc = %s, updated_at = NOW()
                WHERE column_id = %s
                """,
                (data.get("business_name"), data.get("business_desc"), column_id),
            )
            cur.execute(
                "DELETE FROM meta.metadata_column_term WHERE column_id = %s", (column_id,)
            )
            for term in data.get("business_terms") or []:
                term = str(term).strip()
                if not term:
                    continue
                cur.execute(
                    """
                    INSERT INTO meta.metadata_business_term (term, term_type)
                    VALUES (%s, 'synonym')
                    ON CONFLICT (term) DO UPDATE SET term = EXCLUDED.term
                    RETURNING term_id
                    """,
                    (term,),
                )
                term_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO meta.metadata_column_term (column_id, term_id, source)
                    VALUES (%s, %s, 'llm')
                    ON CONFLICT DO NOTHING
                    """,
                    (column_id, term_id),
                )
                stats["terms"] += 1
            stats["columns"] += 1
        conn.commit()

    return stats
```

- [ ] **Step 2: `app/cli.py`에 `enrich` 명령 추가**

```python
@app_cli.command()
def enrich() -> None:
    """LLM으로 업무명/설명/동의어를 생성해 meta에 채운다."""
    from app.collect.enrich import enrich_all

    console.print(f"[green]OK[/] enrich 완료: {enrich_all()}")
```

- [ ] **Step 3: 실행 및 육안 검토**

Run: `python -m app.cli enrich`
Expected: `enrich 완료: {'tables': 4, 'columns': 19, 'terms': ..., 'failed': 0}` (23회 LLM 호출이므로 수 분 소요)

Run:
```bash
python -c "
from app.db import meta_conn
with meta_conn() as c, c.cursor() as cur:
    cur.execute('''SELECT t.table_name, col.column_name, col.business_name,
                          string_agg(bt.term, ', ')
                   FROM meta.metadata_column col
                   JOIN meta.metadata_table t USING (table_id)
                   LEFT JOIN meta.metadata_column_term ct USING (column_id)
                   LEFT JOIN meta.metadata_business_term bt USING (term_id)
                   GROUP BY 1,2,3 ORDER BY 1,2''')
    for r in cur.fetchall(): print(r)
"
```
Expected: `orders.total_amount`의 동의어에 "매출" 또는 "매출액"이 포함되어야 한다. 포함되지 않으면 `COLUMN_PROMPT`의 예시를 보강하고 다시 실행한다. **이 결과의 품질이 이후 검색 정확도를 좌우하므로 반드시 눈으로 확인한다.**

- [ ] **Step 4: 커밋**

```bash
git add app/collect/enrich.py app/cli.py
git commit -m "feat: LLM 기반 업무명/설명/동의어 자동 생성"
```

---

## Task 10: search_text 조립과 임베딩 배치

**Files:**
- Create: `app/collect/search_text.py`, `app/collect/embed.py`
- Modify: `app/cli.py` (`embed` 명령 추가)

- [ ] **Step 1: `app/collect/search_text.py` 작성**

```python
from app.db import meta_conn

TABLE_SQL = """
UPDATE meta.metadata_table t
SET search_text = trim(regexp_replace(
        concat_ws(' ', t.table_name, t.business_name, t.business_desc, t.table_comment),
        '\\s+', ' ', 'g')),
    updated_at = NOW()
"""

COLUMN_SQL = """
UPDATE meta.metadata_column c
SET search_text = trim(regexp_replace(
        concat_ws(' ', c.column_name, c.business_name, c.business_desc, c.column_comment,
                  (SELECT string_agg(bt.term, ' ')
                   FROM meta.metadata_column_term ct
                   JOIN meta.metadata_business_term bt USING (term_id)
                   WHERE ct.column_id = c.column_id)),
        '\\s+', ' ', 'g')),
    updated_at = NOW()
"""


def rebuild_search_text() -> dict[str, int]:
    with meta_conn() as conn:
        cur = conn.cursor()
        cur.execute(TABLE_SQL)
        tables = cur.rowcount
        cur.execute(COLUMN_SQL)
        columns = cur.rowcount
        conn.commit()
    return {"tables": tables, "columns": columns}
```

- [ ] **Step 2: `app/collect/embed.py` 작성**

```python
from app.db import meta_conn
from app.embedding.base import get_embedding_client


def _embed_rows(cur, select_sql: str, update_sql: str, client) -> int:
    cur.execute(select_sql)
    rows = cur.fetchall()
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    texts = [r[1] or "" for r in rows]
    vectors = client.embed(texts)
    for row_id, vec in zip(ids, vectors, strict=True):
        # 벡터는 항상 문자열로 넘기고 SQL에서 ::vector로 캐스팅한다.
        # pgvector 어댑터 등록 여부와 무관하게 동작한다.
        cur.execute(update_sql, (str(vec), row_id))
    return len(ids)


def embed_all() -> dict[str, int]:
    client = get_embedding_client()
    with meta_conn() as conn:
        cur = conn.cursor()
        tables = _embed_rows(
            cur,
            "SELECT table_id, search_text FROM meta.metadata_table WHERE is_active",
            "UPDATE meta.metadata_table SET embedding = %s::vector WHERE table_id = %s",
            client,
        )
        columns = _embed_rows(
            cur,
            "SELECT column_id, search_text FROM meta.metadata_column WHERE is_active",
            "UPDATE meta.metadata_column SET embedding = %s::vector WHERE column_id = %s",
            client,
        )
        values = _embed_rows(
            cur,
            "SELECT value_id, value_text FROM meta.metadata_column_value",
            "UPDATE meta.metadata_column_value SET embedding = %s::vector WHERE value_id = %s",
            client,
        )
        conn.commit()
    return {"tables": tables, "columns": columns, "values": values}
```

- [ ] **Step 3: `app/cli.py`에 `embed` 명령 추가**

```python
@app_cli.command()
def embed() -> None:
    """search_text를 다시 만들고 테이블/컬럼/값 임베딩을 채운다."""
    from app.collect.embed import embed_all
    from app.collect.search_text import rebuild_search_text

    console.print(f"[green]OK[/] search_text: {rebuild_search_text()}")
    console.print(f"[green]OK[/] embedding: {embed_all()}")
```

- [ ] **Step 4: 실행 및 검증**

Run: `python -m app.cli embed`
Expected: `search_text: {'tables': 4, 'columns': 19}` / `embedding: {'tables': 4, 'columns': 19, 'values': ...}`

Run:
```bash
python -c "
from app.db import meta_conn
with meta_conn() as c, c.cursor() as cur:
    for t, k in (('metadata_table','table_id'),('metadata_column','column_id'),('metadata_column_value','value_id')):
        cur.execute(f'SELECT count(*) FROM meta.{t} WHERE embedding IS NULL')
        print(t, 'null embeddings =', cur.fetchone()[0])
    cur.execute(\"SELECT search_text FROM meta.metadata_column c JOIN meta.metadata_table t USING(table_id) WHERE t.table_name='orders' AND c.column_name='total_amount'\")
    print(cur.fetchone()[0])
"
```
Expected: 세 테이블 모두 `null embeddings = 0`. `total_amount`의 `search_text`에 컬럼명·업무명·동의어가 모두 포함되어 있다.

- [ ] **Step 5: 커밋**

```bash
git add app/collect/search_text.py app/collect/embed.py app/cli.py
git commit -m "feat: search_text 조립과 임베딩 배치 생성"
```

---

## Task 11: 한국어 토크나이저 (TDD, 순수 함수)

**Files:**
- Create: `app/search/tokenize.py`, `tests/test_tokenize.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tokenize.py`:

```python
from app.search.tokenize import tokenize


def test_조사를_제거하고_2글자_이상만_남긴다():
    assert tokenize("서울 고객의 2025년 판매실적") == ["서울", "고객", "2025년", "판매실적"]


def test_다양한_조사_처리():
    assert tokenize("부산에서 상품을 주문한 건수는") == ["부산", "상품", "주문한", "건수"]


def test_한글자_토큰은_버린다():
    assert tokenize("이 값 총 매출") == ["매출"]


def test_구두점을_제거한다():
    assert tokenize("매출액, 총합?") == ["매출액", "총합"]


def test_중복은_순서를_지키며_제거한다():
    assert tokenize("서울 서울 고객") == ["서울", "고객"]


def test_빈_입력():
    assert tokenize("") == []
    assert tokenize("   ") == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_tokenize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.search.tokenize'`

- [ ] **Step 3: `app/search/tokenize.py` 구현**

```python
import re

# 긴 조사를 먼저 매칭해야 한다. 순서가 중요하다.
PARTICLES = (
    "에서는", "으로는", "에서", "으로", "에게", "까지", "부터",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "과", "와", "도",
)
_PUNCT = re.compile(r"[^\w가-힣]+")


def _strip_particle(token: str) -> str:
    for p in PARTICLES:
        if token.endswith(p) and len(token) - len(p) >= 2:
            return token[: -len(p)]
    return token


def tokenize(text: str) -> list[str]:
    """한국어 질문을 검색용 토큰으로 자른다.

    형태소 분석기 없이 공백 분리 + 말미 조사 제거 + 2글자 이상 필터로 시작한다.
    정확도가 부족하면 형태소 분석기로 교체한다.
    """
    if not text or not text.strip():
        return []
    out: list[str] = []
    for raw in text.split():
        token = _PUNCT.sub("", raw)
        if not token:
            continue
        token = _strip_particle(token)
        if len(token) >= 2 and token not in out:
            out.append(token)
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_tokenize.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 커밋**

```bash
git add app/search/tokenize.py tests/test_tokenize.py
git commit -m "feat: 한국어 단순 토크나이저"
```

---

## Task 12: RRF 융합 (TDD, 순수 함수)

**Files:**
- Create: `app/search/fusion.py`, `tests/test_fusion.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_fusion.py`:

```python
from app.models import SearchHit
from app.search.fusion import fuse

W = {"value": 3.0, "v_col": 1.0, "v_tbl": 1.0, "keyword": 0.7}
BASE = dict(k=60, weights=W, max_hits_per_table=3, top_tables=5, cutoff_ratio=0.2)


def hit(source, table_id, rank, column_id=None):
    return SearchHit(source=source, table_id=table_id, column_id=column_id,
                     rank=rank, raw_score=0.0)


def test_값_히트가_벡터_히트보다_높은_점수를_받는다():
    hits = [hit("value", 1, 1), hit("v_col", 2, 1)]
    result = fuse(hits, **BASE)
    assert [t.table_id for t in result] == [1, 2]
    assert result[0].score == 3.0 / 61
    assert result[1].score == 1.0 / 61


def test_한_테이블의_히트는_상위_3개까지만_합산한다():
    hits = [hit("v_col", 1, r, column_id=r) for r in range(1, 6)]
    result = fuse(hits, **BASE)
    expected = sum(1.0 / (60 + r) for r in (1, 2, 3))
    assert result[0].score == expected
    assert len(result[0].hits) == 3


def test_최고점의_20_퍼센트_미만인_테이블은_제외된다():
    # table 1: value rank1 = 3/61 = 0.04918
    # table 2: keyword rank50 = 0.7/110 = 0.00636  -> 0.04918*0.2 = 0.00984 미만이므로 탈락
    hits = [hit("value", 1, 1), hit("keyword", 2, 50)]
    result = fuse(hits, **BASE)
    assert [t.table_id for t in result] == [1]


def test_top_tables_상한을_지킨다():
    hits = [hit("v_col", tid, 1) for tid in range(1, 11)]
    result = fuse(hits, **BASE)
    assert len(result) == 5


def test_동점이면_table_id_오름차순():
    hits = [hit("v_col", 7, 1), hit("v_col", 3, 1)]
    result = fuse(hits, **BASE)
    assert [t.table_id for t in result] == [3, 7]


def test_히트가_없으면_빈_결과():
    assert fuse([], **BASE) == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_fusion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.search.fusion'`

- [ ] **Step 3: `app/search/fusion.py` 구현**

```python
from collections import defaultdict
from collections.abc import Sequence

from app.models import SearchHit, TableScore


def fuse(
    hits: Sequence[SearchHit],
    *,
    k: int,
    weights: dict[str, float],
    max_hits_per_table: int,
    top_tables: int,
    cutoff_ratio: float,
) -> list[TableScore]:
    """여러 검색 경로의 히트를 테이블 단위 점수로 융합한다 (가중 RRF).

    한 테이블이 컬럼 다수로 점수를 독식하지 않도록 테이블당 상위
    max_hits_per_table 개만 합산하고, 최고점의 cutoff_ratio 미만은 버린다.
    """
    by_table: dict[int, list[SearchHit]] = defaultdict(list)
    for h in hits:
        by_table[h.table_id].append(h)

    def contribution(h: SearchHit) -> float:
        return weights.get(h.source, 0.0) / (k + h.rank)

    scored: list[TableScore] = []
    for table_id, table_hits in by_table.items():
        kept = sorted(table_hits, key=contribution, reverse=True)[:max_hits_per_table]
        scored.append(
            TableScore(
                table_id=table_id,
                score=sum(contribution(h) for h in kept),
                hits=tuple(kept),
            )
        )

    scored.sort(key=lambda t: (-t.score, t.table_id))
    if not scored:
        return []

    cutoff = scored[0].score * cutoff_ratio
    return [t for t in scored[:top_tables] if t.score >= cutoff]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fusion.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 커밋**

```bash
git add app/search/fusion.py tests/test_fusion.py
git commit -m "feat: 가중 RRF 융합 (순수 함수)"
```

---

## Task 13: 3경로 검색

**Files:**
- Create: `app/search/vector.py`, `app/search/keyword.py`, `app/search/value.py`
- Modify: `app/cli.py` (`search` 명령 추가)

- [ ] **Step 1: `app/search/vector.py` 작성**

```python
from app.models import SearchHit

COLUMN_SQL = """
SELECT c.column_id, c.table_id, t.table_name, c.column_name,
       1 - (c.embedding <=> %s::vector) AS similarity
FROM meta.metadata_column c
JOIN meta.metadata_table t USING (table_id)
WHERE c.is_active AND c.embedding IS NOT NULL
ORDER BY c.embedding <=> %s::vector
LIMIT %s
"""

TABLE_SQL = """
SELECT t.table_id, t.table_name,
       1 - (t.embedding <=> %s::vector) AS similarity
FROM meta.metadata_table t
WHERE t.is_active AND t.embedding IS NOT NULL
ORDER BY t.embedding <=> %s::vector
LIMIT %s
"""


def search_columns(cur, qvec: list[float], limit: int = 20) -> list[SearchHit]:
    q = str(qvec)  # 문자열 + ::vector 캐스팅으로 어댑터 의존을 없앤다
    cur.execute(COLUMN_SQL, (q, q, limit))
    return [
        SearchHit(
            source="v_col", table_id=table_id, column_id=column_id,
            rank=i, raw_score=float(sim), detail=f"{tname}.{cname} sim={sim:.3f}",
        )
        for i, (column_id, table_id, tname, cname, sim) in enumerate(cur.fetchall(), 1)
    ]


def search_tables(cur, qvec: list[float], limit: int = 10) -> list[SearchHit]:
    q = str(qvec)
    cur.execute(TABLE_SQL, (q, q, limit))
    return [
        SearchHit(
            source="v_tbl", table_id=table_id, column_id=None,
            rank=i, raw_score=float(sim), detail=f"{tname} sim={sim:.3f}",
        )
        for i, (table_id, tname, sim) in enumerate(cur.fetchall(), 1)
    ]
```

- [ ] **Step 2: `app/search/keyword.py` 작성**

```python
from app.models import SearchHit

SQL = """
WITH col AS (
    SELECT c.column_id, c.table_id, t.table_name, c.column_name,
           similarity(c.search_text, %(q)s) AS sim
    FROM meta.metadata_column c
    JOIN meta.metadata_table t USING (table_id)
    WHERE c.is_active AND c.search_text %% %(q)s
),
tbl AS (
    SELECT NULL::bigint AS column_id, t.table_id, t.table_name,
           NULL::varchar AS column_name,
           similarity(t.search_text, %(q)s) AS sim
    FROM meta.metadata_table t
    WHERE t.is_active AND t.search_text %% %(q)s
)
SELECT * FROM (SELECT * FROM col UNION ALL SELECT * FROM tbl) u
WHERE sim >= %(min_sim)s
ORDER BY sim DESC
LIMIT %(limit)s
"""


def search(cur, tokens: list[str], min_sim: float, limit: int = 20) -> list[SearchHit]:
    """토큰을 공백으로 이어 붙인 문자열로 trgm 유사도를 잰다."""
    q = " ".join(tokens)
    if not q:
        return []
    cur.execute("SET LOCAL pg_trgm.similarity_threshold = %s", (min_sim,))
    cur.execute(SQL, {"q": q, "min_sim": min_sim, "limit": limit})
    hits = []
    for i, (column_id, table_id, tname, cname, sim) in enumerate(cur.fetchall(), 1):
        label = f"{tname}.{cname}" if cname else tname
        hits.append(
            SearchHit(
                source="keyword", table_id=table_id, column_id=column_id,
                rank=i, raw_score=float(sim), detail=f"{label} trgm={sim:.3f}",
            )
        )
    return hits
```

- [ ] **Step 3: `app/search/value.py` 작성**

```python
from app.models import SearchHit

SQL = """
SELECT v.column_id, c.table_id, t.table_name, c.column_name, v.value_text,
       CASE WHEN v.value_text = %(tok)s THEN 1.0
            ELSE similarity(v.value_text, %(tok)s) END AS score
FROM meta.metadata_column_value v
JOIN meta.metadata_column c USING (column_id)
JOIN meta.metadata_table  t USING (table_id)
WHERE v.value_text = %(tok)s OR v.value_text %% %(tok)s
ORDER BY score DESC
LIMIT %(limit)s
"""


def search(cur, tokens: list[str], min_sim: float, limit: int = 10) -> list[SearchHit]:
    """질문에서 뽑은 토큰 각각을 값 테이블과 대조한다.

    질문 전체를 임베딩해 값과 비교하면 노이즈가 크므로 토큰 단위로 조회한다.
    """
    if not tokens:
        return []
    cur.execute("SET LOCAL pg_trgm.similarity_threshold = %s", (min_sim,))
    collected: list[tuple[float, int, int, str]] = []
    for tok in tokens:
        cur.execute(SQL, {"tok": tok, "limit": limit})
        for column_id, table_id, tname, cname, value_text, score in cur.fetchall():
            collected.append((float(score), table_id, column_id, f"{tname}.{cname}='{value_text}'"))

    collected.sort(key=lambda x: -x[0])
    seen: set[tuple[int, int]] = set()
    hits: list[SearchHit] = []
    for score, table_id, column_id, detail in collected:
        key = (table_id, column_id)
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            SearchHit(
                source="value", table_id=table_id, column_id=column_id,
                rank=len(hits) + 1, raw_score=score, detail=detail,
            )
        )
        if len(hits) >= limit:
            break
    return hits
```

- [ ] **Step 4: `app/cli.py`에 `search` 명령 추가**

```python
@app_cli.command()
def search(
    question: str,
    path: str = typer.Option("all", help="vector | keyword | value | all"),
) -> None:
    """검색 경로별 히트를 확인한다."""
    from rich.table import Table

    from app.db import meta_conn
    from app.embedding.base import get_embedding_client
    from app.search import keyword, value, vector
    from app.search.tokenize import tokenize

    tokens = tokenize(question)
    console.print(f"토큰: {tokens}")

    hits = []
    with meta_conn() as conn, conn.cursor() as cur:
        if path in ("vector", "all"):
            qvec = get_embedding_client().embed([question])[0]
            hits += vector.search_columns(cur, qvec)
            hits += vector.search_tables(cur, qvec)
        if path in ("keyword", "all"):
            hits += keyword.search(cur, tokens, settings.trgm_min_similarity)
        if path in ("value", "all"):
            hits += value.search(cur, tokens, settings.trgm_min_similarity)

    tbl = Table("source", "rank", "table_id", "score", "detail")
    for h in hits:
        tbl.add_row(h.source, str(h.rank), str(h.table_id), f"{h.raw_score:.3f}", h.detail)
    console.print(tbl)
```

- [ ] **Step 5: 경로별 실행 확인**

Run: `python -m app.cli search "서울 고객의 2025년 판매 실적" --path value`
Expected: `customer.region='서울'` 히트가 나타난다. `product.category='서울식품'`도 함께 나타날 수 있다 — 이것이 의도한 함정이며, 이후 Task 18의 eval에서 융합이 이를 걸러내는지 본다.

Run: `python -m app.cli search "서울 고객의 2025년 판매 실적" --path vector`
Expected: `orders.total_amount`가 상위에 나타난다.

Run: `python -m app.cli search "고객 정보 보여줘" --path all`
Expected: `customer` 테이블(v_tbl 경로)이 히트에 포함된다.

- [ ] **Step 6: 커밋**

```bash
git add app/search/vector.py app/search/keyword.py app/search/value.py app/cli.py
git commit -m "feat: 벡터/키워드/값 3경로 검색"
```

---

## Task 14: BFS 조인 경로 (TDD, 순수 함수)

**Files:**
- Create: `app/search/graph.py`, `tests/test_graph.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_graph.py`:

```python
from app.models import Edge
from app.search.graph import find_join_paths

# customer(1) - orders(2) - order_detail(3) - product(4)
EDGES = [
    Edge(2, "customer_id", 1, "customer_id"),
    Edge(3, "order_id", 2, "order_id"),
    Edge(3, "product_id", 4, "product_id"),
]


def test_직접_연결된_두_테이블():
    paths = find_join_paths(EDGES, [1, 2], max_depth=3)
    assert len(paths) == 1
    assert paths[0].tables == (1, 2)
    assert len(paths[0].edges) == 1


def test_브릿지를_거치는_경로():
    paths = find_join_paths(EDGES, [1, 4], max_depth=3)
    assert len(paths) == 1
    assert paths[0].tables == (1, 2, 3, 4)
    assert len(paths[0].edges) == 3


def test_max_depth를_넘으면_경로가_없다():
    paths = find_join_paths(EDGES, [1, 4], max_depth=2)
    assert paths == []


def test_연결되지_않은_테이블():
    paths = find_join_paths(EDGES, [1, 99], max_depth=3)
    assert paths == []


def test_세_테이블이면_쌍마다_경로를_찾는다():
    paths = find_join_paths(EDGES, [1, 2, 3], max_depth=3)
    assert len(paths) == 3


def test_테이블이_하나면_경로가_없다():
    assert find_join_paths(EDGES, [1], max_depth=3) == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.search.graph'`

- [ ] **Step 3: `app/search/graph.py` 구현**

```python
from collections import defaultdict, deque
from collections.abc import Sequence

from app.models import Edge, JoinPath


def _adjacency(edges: Sequence[Edge]) -> dict[int, list[Edge]]:
    """FK를 무방향으로 확장한다."""
    adj: dict[int, list[Edge]] = defaultdict(list)
    for e in edges:
        adj[e.from_table_id].append(e)
        adj[e.to_table_id].append(
            Edge(e.to_table_id, e.to_column, e.from_table_id, e.from_column)
        )
    return adj


def _bfs(adj: dict[int, list[Edge]], start: int, goal: int, max_depth: int) -> JoinPath | None:
    if start == goal:
        return None
    queue: deque[tuple[int, tuple[int, ...], tuple[Edge, ...]]] = deque(
        [(start, (start,), ())]
    )
    visited = {start}
    while queue:
        node, tables, path_edges = queue.popleft()
        if len(path_edges) >= max_depth:
            continue
        for e in adj.get(node, []):
            nxt = e.to_table_id
            if nxt in visited:
                continue
            new_tables = tables + (nxt,)
            new_edges = path_edges + (e,)
            if nxt == goal:
                return JoinPath(tables=new_tables, edges=new_edges)
            visited.add(nxt)
            queue.append((nxt, new_tables, new_edges))
    return None


def find_join_paths(
    edges: Sequence[Edge], table_ids: Sequence[int], max_depth: int
) -> list[JoinPath]:
    """선정된 테이블 쌍마다 최단 조인 경로를 찾는다.

    경로가 없으면 억지로 잇지 않는다. 호출자가 '관계 없음'으로 표기한다.
    """
    adj = _adjacency(edges)
    targets = list(dict.fromkeys(table_ids))
    paths: list[JoinPath] = []
    for i, a in enumerate(targets):
        for b in targets[i + 1 :]:
            p = _bfs(adj, a, b, max_depth)
            if p is not None:
                paths.append(p)
    return paths


RELATION_SQL = """
SELECT r.from_table_id, fc.column_name, r.to_table_id, tc.column_name
FROM meta.metadata_relation r
JOIN meta.metadata_column fc ON fc.column_id = r.from_column_id
JOIN meta.metadata_column tc ON tc.column_id = r.to_column_id
WHERE r.is_active
"""


def load_edges(cur) -> list[Edge]:
    cur.execute(RELATION_SQL)
    return [Edge(ft, fc, tt, tc) for ft, fc, tt, tc in cur.fetchall()]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_graph.py -v`
Expected: PASS — 6 passed

Run: `pytest -v`
Expected: PASS — tokenize 6 + fusion 6 + graph 6 = 18 passed

- [ ] **Step 5: 커밋**

```bash
git add app/search/graph.py tests/test_graph.py
git commit -m "feat: BFS 조인 경로 탐색 (순수 함수)"
```

---

## Task 15: LLM 컨텍스트 조립

**Files:**
- Create: `app/search/context.py`
- Modify: `app/cli.py` (`context` 명령 추가)

- [ ] **Step 1: `app/search/context.py` 작성**

```python
from collections.abc import Sequence

from app.config import settings
from app.models import Edge, JoinPath

TABLE_SQL = """
SELECT t.table_id, t.schema_name, t.table_name,
       COALESCE(t.business_desc, t.table_comment, '')
FROM meta.metadata_table t
WHERE t.table_id = ANY(%s)
"""

COLUMN_SQL = """
SELECT c.table_id, c.column_name, c.data_type, c.is_primary_key, c.is_foreign_key,
       COALESCE(c.business_name, c.column_comment, ''),
       c.distinct_count,
       (SELECT array_agg(v.value_text ORDER BY v.value_freq DESC)
        FROM meta.metadata_column_value v WHERE v.column_id = c.column_id),
       (SELECT array_agg(bt.term)
        FROM meta.metadata_column_term ct
        JOIN meta.metadata_business_term bt USING (term_id)
        WHERE ct.column_id = c.column_id)
FROM meta.metadata_column c
WHERE c.table_id = ANY(%s) AND c.is_active
ORDER BY c.table_id, c.ordinal_position
"""


def _key_columns(edges: Sequence[Edge], table_id: int) -> set[str]:
    keys = set()
    for e in edges:
        if e.from_table_id == table_id:
            keys.add(e.from_column)
        if e.to_table_id == table_id:
            keys.add(e.to_column)
    return keys


def build(
    cur,
    question: str,
    selected_ids: Sequence[int],
    paths: Sequence[JoinPath],
) -> tuple[str, list[int], list[str]]:
    """LLM에 보낼 컨텍스트 문자열과, 실제로 포함된 테이블 id/이름을 만든다.

    선정 테이블은 전체 컬럼을, 조인 경로상의 브릿지 테이블은 조인 키 컬럼만 넣는다.
    """
    selected = list(dict.fromkeys(selected_ids))
    bridges: list[int] = []
    for p in paths:
        for tid in p.tables:
            if tid not in selected and tid not in bridges:
                bridges.append(tid)

    all_ids = (selected + bridges)[: settings.max_context_tables]
    path_edges = [e for p in paths for e in p.edges]

    cur.execute(TABLE_SQL, (all_ids,))
    tables = {r[0]: r[1:] for r in cur.fetchall()}
    cur.execute(COLUMN_SQL, (all_ids,))
    columns: dict[int, list[tuple]] = {}
    for row in cur.fetchall():
        columns.setdefault(row[0], []).append(row[1:])

    lines = ["[질문]", question, "", "[테이블]"]
    for tid in all_ids:
        if tid not in tables:
            continue
        schema, tname, tdesc = tables[tid]
        is_bridge = tid in bridges
        suffix = "  (조인 경유 테이블)" if is_bridge else ""
        lines.append(f"{schema}.{tname}  — {tdesc or '(설명 없음)'}{suffix}")
        keys = _key_columns(path_edges, tid)
        for (cname, dtype, is_pk, is_fk, cdesc,
             distinct, values, terms) in columns.get(tid, []):
            if is_bridge and cname not in keys and not is_pk:
                continue
            flag = "PK" if is_pk else ("FK" if is_fk else "  ")
            lines.append(f"  {cname:<16} {dtype:<15} {flag}  {cdesc}")
            if values:
                shown = ", ".join(values[:10])
                lines.append(f"{'':>21}▸ 값: {shown} (총 {distinct}종)")
            if terms:
                lines.append(f"{'':>21}▸ 업무용어: {', '.join(terms)}")
        lines.append("")

    lines.append("[관계]")
    if path_edges:
        seen = set()
        for e in path_edges:
            a, b = tables.get(e.from_table_id), tables.get(e.to_table_id)
            if not a or not b:
                continue
            text = f"{a[0]}.{a[1]}.{e.from_column} = {b[0]}.{b[1]}.{e.to_column}"
            if text not in seen:
                seen.add(text)
                lines.append(text)
    else:
        lines.append("-- 관계 없음")

    lines += [
        "",
        "[규칙]",
        "- PostgreSQL 문법. SELECT 단일문만.",
        "- 위에 없는 테이블/컬럼 사용 금지.",
        "- 값 목록이 제시된 컬럼은 그 값을 정확히 사용할 것.",
        "- 설명 없이 SQL만 출력.",
    ]

    names = [f"{tables[t][0]}.{tables[t][1]}" for t in all_ids if t in tables]
    return "\n".join(lines), all_ids, names
```

- [ ] **Step 2: `app/cli.py`에 `context` 명령 추가**

```python
@app_cli.command()
def context(question: str) -> None:
    """질문에 대한 LLM 컨텍스트를 만들어 출력한다."""
    from app.db import meta_conn
    from app.embedding.base import get_embedding_client
    from app.search import context as ctx
    from app.search import keyword, value, vector
    from app.search.fusion import fuse
    from app.search.graph import find_join_paths, load_edges
    from app.search.tokenize import tokenize

    tokens = tokenize(question)
    qvec = get_embedding_client().embed([question])[0]

    with meta_conn() as conn, conn.cursor() as cur:
        hits = (
            vector.search_columns(cur, qvec)
            + vector.search_tables(cur, qvec)
            + keyword.search(cur, tokens, settings.trgm_min_similarity)
            + value.search(cur, tokens, settings.trgm_min_similarity)
        )
        scores = fuse(
            hits,
            k=settings.rrf_k,
            weights=settings.weights,
            max_hits_per_table=settings.max_hits_per_table,
            top_tables=settings.top_tables,
            cutoff_ratio=settings.score_cutoff_ratio,
        )
        for s in scores:
            console.print(f"  table_id={s.table_id} score={s.score:.5f}")
        ids = [s.table_id for s in scores]
        paths = find_join_paths(load_edges(cur), ids, settings.join_max_depth)
        text, _, names = ctx.build(cur, question, ids, paths)

    console.print(f"[bold]선정 테이블[/] {names}")
    console.print(text)
```

- [ ] **Step 3: 실행 확인**

Run: `python -m app.cli context "서울 고객의 2025년 판매 실적"`
Expected: `biz.customer`와 `biz.orders`가 선정되고, `region`의 값 목록과 `total_amount`의 업무용어가 컨텍스트에 나타나며, `[관계]`에 `customer.customer_id = orders.customer_id`가 출력된다.

Run: `python -m app.cli context "상품별 매출 순위"`
Expected: `biz.product`, `biz.order_detail`가 선정되고 필요 시 `biz.orders`가 경유 테이블로 포함된다.

- [ ] **Step 4: 커밋**

```bash
git add app/search/context.py app/cli.py
git commit -m "feat: LLM 컨텍스트 조립"
```

---

## Task 16: SQL 안전 게이트 (TDD, 순수 함수)

**Files:**
- Create: `app/sqlgen/guard.py`, `tests/test_guard.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_guard.py`:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sqlgen.guard'`

- [ ] **Step 3: `app/sqlgen/guard.py` 구현**

```python
import sqlglot
from sqlglot import exp

from app.models import GuardResult

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create,
    exp.TruncateTable, exp.Grant, exp.Copy, exp.Merge, exp.Command,
    exp.Into,   # SELECT ... INTO 는 SELECT 옷을 입은 쓰기다
)

# AST에서 파싱된 함수 노드 이름으로 막는다. 원문 정규식이 아니므로
# 대소문자나 주석으로 우회되지 않는다.
# dblink는 별도 커넥션을 열어 임의 SQL을 실행하므로 방어선 3(READ ONLY
# 트랜잭션)을 통째로 우회한다. 계정 권한(방어선 4)을 보류한 이 PoC에서는
# 데이터를 실제로 파괴할 수 있는 유일한 탈출구였다.
FORBIDDEN_FUNCTIONS = {
    "dblink", "dblink_exec", "dblink_connect", "dblink_connect_u",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",          # DoS
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir",    # 파일 접근
    "pg_terminate_backend", "pg_cancel_backend",           # 타 세션 방해
}
ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_temp", "pg_toast"}


def validate(sql: str) -> GuardResult:
    """LLM이 생성한 SQL을 AST로 검사한다.

    정규식 블랙리스트는 주석/대소문자/유니코드로 우회되므로 쓰지 않는다.
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
        if isinstance(node, FORBIDDEN_NODES):
            return GuardResult(False, None, f"금지된 구문: {type(node).__name__}")

    for fn in root.find_all(exp.Func, exp.Anonymous):
        fname = (fn.sql_name() if hasattr(fn, "sql_name") else fn.name or "").lower()
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_guard.py -v`
Expected: PASS — 23 passed (parametrize 12건 + dblink/pg_sleep 회귀 테스트 2건 포함)

- [ ] **Step 5: 커밋**

```bash
git add app/sqlgen/guard.py tests/test_guard.py
git commit -m "feat: sqlglot AST 기반 SELECT-only 게이트와 LIMIT 주입"
```

---

## Task 17: SQL 생성과 실행

**Files:**
- Create: `app/sqlgen/prompt.py`, `app/sqlgen/generate.py`, `app/sqlgen/execute.py`

- [ ] **Step 1: `app/sqlgen/prompt.py` 작성**

```python
SYSTEM = (
    "당신은 PostgreSQL 전문가입니다. 주어진 스키마 정보만 사용해 "
    "정확한 SELECT 문 하나를 작성합니다. 설명은 하지 않습니다."
)

RETRY_TEMPLATE = """{context}

직전에 아래 SQL을 생성했으나 PostgreSQL 검증에서 실패했습니다.

실패한 SQL:
{sql}

오류:
{error}

오류를 고친 SELECT 문을 다시 출력하시오. 설명 없이 SQL만 출력하시오."""
```

- [ ] **Step 2: `app/sqlgen/generate.py` 작성**

```python
import re

from app.llm.base import LLMClient
from app.sqlgen.prompt import RETRY_TEMPLATE, SYSTEM

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str:
    """코드펜스나 앞뒤 설명이 섞인 응답에서 SQL만 뽑는다."""
    m = _FENCE.search(text)
    body = m.group(1) if m else text
    body = body.strip()
    m = re.search(r"\b(WITH|SELECT)\b", body, re.IGNORECASE)
    if m:
        body = body[m.start() :]
    return body.strip().rstrip(";").strip()


def looks_like_sql(text: str) -> bool:
    """SELECT/WITH 로 시작하는지만 본다. 상세 검증은 guard가 한다."""
    return bool(re.match(r"^\s*(WITH|SELECT)\b", text or "", re.IGNORECASE))


def generate(llm: LLMClient, context: str) -> str:
    return extract_sql(llm.complete(context, system=SYSTEM))


def regenerate(llm: LLMClient, context: str, failed_sql: str, error: str) -> str:
    prompt = RETRY_TEMPLATE.format(context=context, sql=failed_sql, error=error)
    return extract_sql(llm.complete(prompt, system=SYSTEM))
```

- [ ] **Step 3: `app/sqlgen/execute.py` 작성**

```python
from app.config import settings
from app.db import biz_conn_readonly


def explain(sql: str) -> str | None:
    """실행 전 문법·컬럼 존재를 검증한다. 통과하면 None, 실패하면 오류 문자열."""
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

- [ ] **Step 4: 확인**

Run:
```bash
python -c "
from app.sqlgen.generate import extract_sql
print(repr(extract_sql('''설명입니다.\n```sql\nSELECT 1;\n```''')))
print(repr(extract_sql('SELECT count(*) FROM biz.orders')))
"
```
Expected:
```
'SELECT 1'
'SELECT count(*) FROM biz.orders'
```

Run:
```bash
python -c "
from app.sqlgen.execute import explain, run
print(explain('SELECT count(*) FROM biz.orders'))
print(explain('SELECT nope FROM biz.orders')[:60])
print(run('SELECT region, count(*) FROM biz.customer GROUP BY 1 ORDER BY 1 LIMIT 3'))
"
```
Expected: 첫 줄 `None`, 둘째 줄에 `column \"nope\" does not exist` 포함, 셋째 줄에 지역별 집계 3행

- [ ] **Step 5: 커밋**

```bash
git add app/sqlgen/prompt.py app/sqlgen/generate.py app/sqlgen/execute.py
git commit -m "feat: SQL 생성과 READ ONLY 실행"
```

---

## Task 18: 파이프라인과 `cli ask`

**Files:**
- Create: `app/pipeline.py`
- Modify: `app/cli.py` (`ask` 명령 추가)

- [ ] **Step 1: `app/pipeline.py` 작성**

```python
from app.config import settings
from app.db import meta_conn
from app.embedding.base import get_embedding_client
from app.llm.base import get_llm_client
from app.models import AskResult
from app.search import context as ctx
from app.search import keyword, value, vector
from app.search.fusion import fuse
from app.search.graph import find_join_paths, load_edges
from app.search.tokenize import tokenize
from app.sqlgen import execute, generate, guard


def retrieve(question: str) -> tuple[str, list[int], list[str], dict]:
    """검색 → 융합 → 조인경로 → 컨텍스트. LLM SQL 생성 전까지."""
    tokens = tokenize(question)
    qvec = get_embedding_client().embed([question])[0]

    with meta_conn() as conn, conn.cursor() as cur:
        hits = (
            vector.search_columns(cur, qvec)
            + vector.search_tables(cur, qvec)
            + keyword.search(cur, tokens, settings.trgm_min_similarity)
            + value.search(cur, tokens, settings.trgm_min_similarity)
        )
        scores = fuse(
            hits,
            k=settings.rrf_k,
            weights=settings.weights,
            max_hits_per_table=settings.max_hits_per_table,
            top_tables=settings.top_tables,
            cutoff_ratio=settings.score_cutoff_ratio,
        )
        selected = [s.table_id for s in scores]
        if not selected:
            return "", [], [], {"tokens": tokens, "hits": len(hits), "scores": []}

        paths = find_join_paths(load_edges(cur), selected, settings.join_max_depth)
        text, all_ids, names = ctx.build(cur, question, selected, paths)

    trace = {
        "tokens": tokens,
        "hits": len(hits),
        "scores": [(s.table_id, round(s.score, 5)) for s in scores],
        "hit_details": [h.detail for h in hits[:30]],
        "join_paths": [list(p.tables) for p in paths],
    }
    return text, all_ids, names, trace


def ask(question: str) -> AskResult:
    text, table_ids, names, trace = retrieve(question)
    result = AskResult(question=question, table_ids=table_ids,
                       table_names=names, context=text, trace=trace)

    if not table_ids:
        result.error = "관련 테이블을 찾지 못했습니다."
        return result

    llm = get_llm_client()
    sql = generate.generate(llm, text)

    # 응답이 SQL 형태조차 아니면 1회 재생성한다 (안전 게이트 거부와는 다른 경우).
    if not generate.looks_like_sql(sql):
        trace["not_sql_response"] = sql[:200]
        sql = generate.regenerate(
            llm, text, sql, "응답이 SELECT 문이 아닙니다. SQL만 출력하시오."
        )
    result.sql = sql

    verdict = guard.validate(sql)
    if not verdict.ok:
        result.error = f"안전 검증 거부: {verdict.reason}"
        return result

    safe_sql = guard.inject_limit(
        verdict.sql,
        default_limit=settings.sql_row_limit,
        max_limit=settings.sql_max_limit,
    )
    result.sql = safe_sql

    err = execute.explain(safe_sql)
    if err:
        trace["explain_error_1"] = err
        retry = generate.regenerate(llm, text, safe_sql, err)
        verdict = guard.validate(retry)
        if not verdict.ok:
            result.error = f"재생성 후 안전 검증 거부: {verdict.reason}"
            result.sql = retry
            return result
        safe_sql = guard.inject_limit(
            verdict.sql,
            default_limit=settings.sql_row_limit,
            max_limit=settings.sql_max_limit,
        )
        result.sql = safe_sql
        err = execute.explain(safe_sql)
        if err:
            trace["explain_error_2"] = err
            result.error = f"SQL 검증 실패(재시도 포함 2회): {err}"
            return result

    try:
        result.columns, result.rows = execute.run(safe_sql)
    except Exception as e:  # noqa: BLE001
        result.error = f"실행 실패: {e}"
    return result
```

- [ ] **Step 2: `app/cli.py`에 `ask` 명령 추가**

```python
@app_cli.command()
def ask(question: str, show_context: bool = typer.Option(False, "--show-context")) -> None:
    """질문에 대해 SQL을 생성하고 실행한다."""
    from rich.table import Table

    from app.pipeline import ask as run_ask

    r = run_ask(question)
    console.print(f"[bold]선정 테이블[/] {r.table_names}")
    console.print(f"[bold]점수[/] {r.trace.get('scores')}")
    if show_context:
        console.print(r.context)
    if r.sql:
        console.print(f"[bold]SQL[/]\n{r.sql}")
    if r.error:
        console.print(f"[red]{r.error}[/]")
        raise typer.Exit(1)

    tbl = Table(*r.columns)
    for row in r.rows[:20]:
        tbl.add_row(*[str(v) for v in row])
    console.print(tbl)
    console.print(f"{len(r.rows)}행")
```

- [ ] **Step 3: 실행 확인**

Run: `python -m app.cli ask "서울 고객의 2025년 판매 실적"`
Expected: `customer`, `orders` 선정, `region = '서울'`과 `order_date`의 2025년 조건이 들어간 SQL, 결과 행 출력

Run: `python -m app.cli ask "날씨 정보 알려줘"`
Expected: `관련 테이블을 찾지 못했습니다.` 또는 무관 테이블이 선정된다면 이는 Task 19에서 튜닝할 대상이다

- [ ] **Step 4: 커밋**

```bash
git add app/pipeline.py app/cli.py
git commit -m "feat: 질문 → SQL → 실행 전체 파이프라인"
```

---

## Task 19: 평가 세트와 `cli eval`

**Files:**
- Create: `tests/questions.yaml`
- Modify: `app/cli.py` (`eval` 명령 추가)

- [ ] **Step 1: `tests/questions.yaml` 작성**

```yaml
- id: 1
  question: "서울 고객의 2025년 판매 실적"
  expect_tables: [customer, orders]
  intent: "값 경로 + 업무용어"
- id: 2
  question: "고객 정보 보여줘"
  expect_tables: [customer]
  intent: "테이블 지향 질문"
- id: 3
  question: "상품별 매출 순위"
  expect_tables: [product, order_detail, orders]
  intent: "브릿지 조인"
- id: 4
  question: "매출액 총합"
  expect_tables: [orders]
  intent: "total_amount 와 amount 구분"
- id: 5
  question: "부산 지역 주문 건수"
  expect_tables: [customer, orders]
  intent: "값 경로"
- id: 6
  question: "서울식품 상품 판매량"
  expect_tables: [product, order_detail]
  intent: "값 오탐 함정"
- id: 7
  question: "2024년 주문 목록"
  expect_tables: [orders]
  intent: "단순"
- id: 8
  question: "날씨 정보 알려줘"
  expect_tables: []
  intent: "무관 질문. 0건이 정답"
```

- [ ] **Step 2: `app/cli.py`에 `eval` 명령 추가**

```python
@app_cli.command("eval")
def eval_cmd(
    retrieval_only: bool = typer.Option(False, "--retrieval-only", help="LLM 호출 없이 검색만 평가")
) -> None:
    """평가 세트를 일괄 실행하고 지표를 표로 출력한다."""
    import yaml
    from rich.table import Table

    from app.pipeline import ask as run_ask
    from app.pipeline import retrieve

    cases = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "tests" / "questions.yaml").read_text(
            encoding="utf-8"
        )
    )

    tbl = Table("id", "질문", "기대", "실제", "R", "P", "SQL", "비고")
    recalls, precisions, sql_ok = [], [], 0

    for case in cases:
        expected = set(case["expect_tables"])
        if retrieval_only:
            _, _, names, _ = retrieve(case["question"])
            actual = {n.split(".")[-1] for n in names}
            sql_mark, note = "-", ""
        else:
            r = run_ask(case["question"])
            actual = {n.split(".")[-1] for n in r.table_names}
            ok = r.error is None and r.sql is not None
            sql_ok += int(ok)
            sql_mark = "O" if ok else "X"
            note = (r.error or "")[:40]

        if not expected:
            recall = 1.0 if not actual else 0.0
            precision = recall
        else:
            hit = expected & actual
            recall = len(hit) / len(expected)
            precision = len(hit) / len(actual) if actual else 0.0
        recalls.append(recall)
        precisions.append(precision)

        tbl.add_row(
            str(case["id"]), case["question"][:22],
            ",".join(sorted(expected)) or "(없음)",
            ",".join(sorted(actual)) or "(없음)",
            f"{recall:.2f}", f"{precision:.2f}", sql_mark, note,
        )

    console.print(tbl)
    n = len(cases)
    console.print(
        f"[bold]평균 Recall[/] {sum(recalls)/n:.3f}   "
        f"[bold]평균 Precision[/] {sum(precisions)/n:.3f}   "
        f"[bold]SQL 성공[/] {sql_ok}/{n}"
    )
```

- [ ] **Step 3: 검색만 먼저 평가**

Run: `python -m app.cli eval --retrieval-only`
Expected: 8행 표와 평균 Recall/Precision 출력 (LLM 호출이 없어 수 초 안에 끝난다)

- [ ] **Step 4: 가중치 튜닝 — 여기가 실질적인 작업이다**

Recall이 낮은 케이스와 Precision이 낮은 케이스를 나누어 대응한다.

| 증상 | 조치 |
|---|---|
| 6번에서 `customer`가 끼어든다 (`서울식품` → `서울` 오탐) | `TRGM_MIN_SIMILARITY`를 0.3~0.4로 올린다. 값 경로의 부분일치가 원인이다 |
| 2번 `customer`를 못 찾는다 | `W_VECTOR_TBL`을 1.5로 올린다 |
| 4번에서 `order_detail`이 끼어든다 | `SCORE_CUTOFF_RATIO`를 0.3으로 올린다 |
| 8번에서 무관 테이블이 선정된다 | `SCORE_CUTOFF_RATIO`로는 막을 수 없다(상대 기준). 절대 점수 하한이 필요하다면 스펙 10장의 rerank 도입을 검토한다 |
| 3번에서 `orders`가 빠진다 | 정상이다. 브릿지로 자동 포함되는지 `--show-context`로 확인한다 |

`.env`를 수정한 뒤 매번 다시 실행해 회귀를 확인한다.

```bash
python -m app.cli eval --retrieval-only
```

- [ ] **Step 5: 전체 평가 실행**

Run: `python -m app.cli eval`
Expected: 8문항 전체가 LLM SQL 생성까지 수행된다 (수 분 소요). SQL 성공률과 함께 실패 사유가 비고에 표시된다.

- [ ] **Step 6: 커밋**

```bash
git add tests/questions.yaml app/cli.py
git commit -m "test: 8문항 평가 세트와 eval 명령"
```

---

## Task 20: FastAPI와 Streamlit

**Files:**
- Create: `app/api.py`, `app/ui.py`

- [ ] **Step 1: `app/api.py` 작성**

```python
from fastapi import FastAPI
from pydantic import BaseModel

from app.db import meta_conn
from app.pipeline import ask as run_ask

api = FastAPI(title="AI 메타데이터 검색")


class AskRequest(BaseModel):
    question: str


@api.post("/ask")
def ask_endpoint(req: AskRequest) -> dict:
    r = run_ask(req.question)
    return {
        "question": r.question,
        "tables": r.table_names,
        "sql": r.sql,
        "columns": r.columns,
        "rows": [list(map(str, row)) for row in r.rows],
        "error": r.error,
        "context": r.context,
        "trace": r.trace,
    }


@api.get("/metadata/tables")
def list_tables() -> list[dict]:
    with meta_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.schema_name, t.table_name, t.business_name, t.business_desc,
                   count(c.column_id)
            FROM meta.metadata_table t
            LEFT JOIN meta.metadata_column c USING (table_id)
            GROUP BY 1,2,3,4
            ORDER BY 1,2
            """
        )
        return [
            {"schema": s, "table": t, "business_name": bn,
             "business_desc": bd, "column_count": cnt}
            for s, t, bn, bd, cnt in cur.fetchall()
        ]
```

- [ ] **Step 2: `app/ui.py` 작성**

```python
import streamlit as st

from app.pipeline import ask as run_ask

st.set_page_config(page_title="AI 메타데이터 검색", layout="wide")
st.title("AI 메타데이터 검색")

question = st.text_input("질문", placeholder="서울 고객의 2025년 판매 실적")

if st.button("질의", type="primary") and question:
    with st.spinner("검색 및 SQL 생성 중..."):
        r = run_ask(question)

    st.subheader("선정 테이블")
    st.write(r.table_names or "(없음)")

    if r.sql:
        st.subheader("생성된 SQL")
        st.code(r.sql, language="sql")

    if r.error:
        st.error(r.error)
    elif r.rows:
        st.subheader(f"결과 ({len(r.rows)}행)")
        st.dataframe([dict(zip(r.columns, map(str, row))) for row in r.rows])

    with st.expander("검색 점수 / 히트"):
        st.json(r.trace)
    with st.expander("LLM 컨텍스트"):
        st.text(r.context)
```

- [ ] **Step 3: 실행 확인**

Run: `uvicorn app.api:api --port 8000`
그리고 다른 터미널에서:
```bash
curl -s -X POST localhost:8000/ask -H "Content-Type: application/json" -d "{\"question\":\"서울 고객의 2025년 판매 실적\"}"
```
Expected: `tables`, `sql`, `rows`를 포함한 JSON

Run: `streamlit run app/ui.py`
Expected: 브라우저에서 질문 입력 → 테이블·SQL·결과·trace가 표시된다

- [ ] **Step 4: 전체 테스트 확인**

Run: `pytest -v`
Expected: PASS — 41 passed (tokenize 6 + fusion 6 + graph 6 + guard 23)

- [ ] **Step 5: 커밋**

```bash
git add app/api.py app/ui.py
git commit -m "feat: FastAPI 엔드포인트와 Streamlit UI"
```

- [ ] **Step 6: 푸시**

```bash
git push origin main
```

---

## 부록: 전체 실행 순서 (처음부터)

```bash
pip install -e ".[dev]"
cp .env.example .env          # DSN의 USER:PASSWORD를 채운다
python -m app.cli doctor
python -m app.cli init-db
python -m app.cli fixture
python -m app.cli embed-test  # 실패 시 EMBED_PROVIDER=sentence_transformers 로 전환
python -m app.cli collect
python -m app.cli enrich      # 수 분 소요
python -m app.cli embed
python -m app.cli eval --retrieval-only
python -m app.cli ask "서울 고객의 2025년 판매 실적"
```

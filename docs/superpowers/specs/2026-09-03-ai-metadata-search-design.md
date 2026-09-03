# AI 메타데이터 검색 기반 Text-to-SQL 시스템 — 설계 스펙

- **작성일**: 2026-09-03
- **상태**: 승인됨 (구현 대기)
- **범위**: PoC — 단일 PostgreSQL, 테스트 테이블 4개

---

## 1. 목표

자연어 질문을 받아 관련 테이블·컬럼을 메타데이터에서 검색하고, LLM으로 SQL을 생성한 뒤 **실행해서 결과까지 반환**한다.

PoC의 목적은 "동작 확인"이 아니라 **파이프라인의 어느 단계에서 얼마나 틀리는지 측정**하는 것이다. 따라서 오검색을 유발하는 함정 데이터와 평가 세트가 스펙의 일급 구성요소다.

### 최종 산출물

```
질문 → 메타데이터 검색 → 테이블 선정 → 조인경로 → LLM SQL 생성 → 검증 → 실행 → 결과
```

인터페이스는 셋: 코어 모듈 + CLI + FastAPI + Streamlit UI.

---

## 2. 확정된 환경

| 항목 | 값 |
|---|---|
| DB Host | `192.168.0.140:5432` |
| Database | `ggydb` |
| 계정 | `itos_dev` (메타/업무 공용, PoC 한정) |
| 업무 스키마 | `biz` |
| 메타 스키마 | `meta` |
| LLM | Ollama `gemma4:26b-a4b-it-q4_K_M` @ `192.168.0.169:11434` |
| Embedding | `nlpai-lab/KURE-v1`, **1024차원** |
| Embedding 서빙 | GGUF 변환 → Ollama `/api/embed` (기본) / sentence-transformers (폴백) |
| 확장 | `pgvector`, `pg_trgm` |

**자격증명은 `.env`로만 관리하고 `.gitignore`에 등록한다. 저장소에는 `.env.example`(플레이스홀더)만 커밋한다.**

---

## 3. 원안 대비 변경 사항

초기 제안(metadata_table / metadata_column / metadata_relation + business_term)에서 다음을 변경한다.

| # | 변경 | 이유 |
|---|---|---|
| 1 | `meta.datasource` 테이블 추가. 유니크키를 `(datasource_id, schema_name, table_name)`으로 | `UNIQUE(schema, table)`은 대상 DB가 둘 이상이 되는 순간 충돌한다 |
| 2 | `metadata_column.business_terms TEXT[]` **제거** | `metadata_column_term` 조인 테이블과 같은 사실을 두 곳에 저장하면 반드시 불일치가 발생한다. 정규화 쪽만 남긴다 |
| 3 | `metadata_table`에 `search_text`, `embedding` 추가 | 컬럼만 검색하면 "고객 정보 보여줘" 같은 테이블 지향 질문의 리콜이 떨어진다 |
| 4 | `meta.metadata_column_value` 테이블 신규 | "서울 고객의…"의 `"서울"`은 컬럼명도 업무용어도 아닌 **데이터 값**이다. 값을 저장하지 않으면 이 질문에 원리적으로 답할 수 없다 |
| 5 | `metadata_relation`의 `from_table_id`/`to_table_id` 유지 | `column_id`로 유도 가능한 비정규화지만, 그래프 탐색 쿼리가 크게 단순해진다. 의도적 선택 |
| 6 | FTS를 `to_tsvector('simple')` → **`pg_trgm` GIN**으로 교체 | 원안은 GIN(tsvector) 인덱스를 만들고 `ILIKE`로 조회해 인덱스를 타지 않았다. 또한 `simple` config는 한국어 조사("판매실적을")를 분리하지 못한다 |
| 7 | `vector(1536)` → **`vector(1024)`**, HNSW 인덱스 추가 | KURE-v1 차원. 원안에는 벡터 인덱스가 없어 매 검색이 풀스캔이었다 |
| 8 | `search_text`는 생성 컬럼이 아닌 **배치 갱신 컬럼** | 업무용어가 조인 테이블에 있어 `GENERATED`로 만들 수 없다. 수집 파이프라인이 채운다 |

---

## 4. 데이터 모델

### 4.1 구조

```
meta.datasource
      │ 1:N
meta.metadata_table          (+ search_text, embedding)
      │ 1:N
meta.metadata_column         (+ search_text, embedding, 프로파일 통계)
      ├──→ meta.metadata_column_value      대표값 + embedding
      ├──→ meta.metadata_relation          조인 관계
      └──→ meta.metadata_column_term ──→ meta.metadata_business_term
```

### 4.2 DDL

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS biz;

-- 대상 DB 소스
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

-- 테이블
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

-- 컬럼
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
    -- 프로파일 통계
    distinct_count   BIGINT,
    null_ratio       NUMERIC(5,4),
    min_value        TEXT,
    max_value        TEXT,
    sample_values    TEXT[],          -- 최대 5개. LLM 설명 생성 및 컨텍스트용
    -- 검색
    search_text      TEXT,
    embedding        vector(1024),
    is_sensitive     BOOLEAN NOT NULL DEFAULT FALSE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_column UNIQUE (table_id, column_name)
);

-- 컬럼 대표값 (값 검색 경로의 근거)
CREATE TABLE meta.metadata_column_value (
    value_id      BIGSERIAL PRIMARY KEY,
    column_id     BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    value_text    TEXT   NOT NULL,
    value_freq    BIGINT,
    embedding     vector(1024),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_column_value UNIQUE (column_id, value_text)
);

-- 관계
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

-- 업무용어
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
    source     VARCHAR(20)  NOT NULL DEFAULT 'llm',   -- llm | manual
    PRIMARY KEY (column_id, term_id)
);
```

### 4.3 인덱스

```sql
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

### 4.4 값 수집 규칙

- **저장 대상**: 텍스트 계열 컬럼(`varchar` / `text` / `char`) 중 `distinct_count <= 50`인 컬럼의 전체 distinct 값
- **비대상**: 고카디널리티 컬럼, 숫자·날짜 컬럼 → `metadata_column`에 `distinct_count`, `min_value`, `max_value`, `sample_values`(5개)만 기록
- 카디널리티 폭발을 막기 위한 상한이며, 임계값 50은 `.env`(`VALUE_DISTINCT_MAX`)로 조정한다

### 4.5 search_text 조립 규칙

```
metadata_column.search_text =
    column_name + ' ' + business_name + ' ' + business_desc
  + ' ' + column_comment
  + ' ' + (연결된 business_term 을 공백으로 결합)

metadata_table.search_text =
    table_name + ' ' + business_name + ' ' + business_desc + ' ' + table_comment
```

임베딩 입력 텍스트도 동일한 `search_text`를 사용한다. 키워드 경로와 벡터 경로가 같은 근거를 보게 하기 위함이다.

---

## 5. 모듈 구조

```
D:\AI\AI_MataData_Search\
├── .env / .env.example / .gitignore
├── pyproject.toml
├── sql/
│   ├── 01_extensions.sql
│   ├── 02_meta_schema.sql
│   ├── 03_biz_fixture.sql
│   └── 04_readonly_role.sql        (작성만, 적용 보류)
├── app/
│   ├── config.py                   .env 로드
│   ├── db.py                       meta / biz 커넥션 풀 2개
│   ├── embedding/
│   │   ├── base.py                 EmbeddingClient 인터페이스
│   │   ├── ollama_client.py        기본
│   │   └── sstf_client.py          폴백
│   ├── llm/
│   │   ├── base.py                 LLMClient 인터페이스
│   │   └── ollama_client.py
│   ├── collect/
│   │   ├── introspect.py           테이블·컬럼·FK 추출
│   │   ├── profile.py              카디널리티·대표값·통계
│   │   ├── enrich.py               LLM 업무명/설명/동의어 생성
│   │   ├── search_text.py
│   │   └── embed.py
│   ├── search/
│   │   ├── tokenize.py             단순 토크나이저
│   │   ├── vector.py               [경로1]
│   │   ├── keyword.py              [경로2]
│   │   ├── value.py                [경로3]
│   │   ├── fusion.py               가중 RRF (순수함수)
│   │   ├── graph.py                BFS 조인 경로
│   │   └── context.py              LLM 컨텍스트 조립
│   ├── sqlgen/
│   │   ├── prompt.py
│   │   ├── generate.py
│   │   ├── guard.py                SELECT-only 검증 (순수함수)
│   │   └── execute.py              EXPLAIN → 실행 → 1회 재생성
│   ├── pipeline.py                 ask(question) -> AskResult
│   ├── cli.py
│   ├── api.py                      FastAPI
│   └── ui.py                       Streamlit
└── tests/
    ├── questions.yaml              평가 세트
    ├── test_fusion.py
    ├── test_guard.py
    ├── test_graph.py
    └── test_tokenize.py
```

### 5.1 모듈 계약

| 모듈 | 입력 → 출력 | 의존 |
|---|---|---|
| `collect/introspect` | `datasource_id` → 테이블/컬럼/FK 레코드 | biz DB |
| `collect/profile` | 컬럼 목록 → 통계·대표값 | biz DB |
| `collect/enrich` | 컬럼 + 샘플값 → 업무명/설명/동의어 | LLMClient |
| `search/vector,keyword,value` | 질문 → `[(column_id, table_id, rank, score, source)]` | meta DB, EmbeddingClient |
| `search/fusion` | 3경로 결과 → `[(table_id, score)]` | **없음 (순수함수)** |
| `search/graph` | `table_id[]` → 조인 경로 | meta DB |
| `sqlgen/guard` | SQL → 안전 SQL 또는 거부사유 | **없음 (순수함수)** |

`fusion`과 `guard`가 순수 함수인 것이 중요하다. 가장 버그가 나기 쉬운 두 곳을 DB·LLM 없이 단위 테스트할 수 있다.

### 5.2 데이터 흐름

```
question
  → embed(question)                       1회 임베딩, 3경로가 공유
  → vector.search()  ┐
    keyword.search() ├→ fusion.rrf() → 상위 테이블 3~5개
    value.search()   ┘
  → graph.find_join_paths(tables)
  → context.build()
  → sqlgen.generate()                     Ollama gemma4
  → guard.validate_and_limit()
  → execute.explain_then_run()
  → AskResult{ sql, rows, context, scores, trace, error }
```

`AskResult`는 `context`·`scores`·`trace`를 반드시 포함한다. PoC의 목적이 파이프라인 진단이므로 관측 가능성이 기능과 동등한 요구사항이다.

---

## 6. 검색 알고리즘

### 6.1 세 경로

| 경로 | 대상 | 방식 | 상위 |
|---|---|---|---|
| **V-col** | `metadata_column.embedding` | 코사인 유사도 | 20 |
| **V-tbl** | `metadata_table.embedding` | 코사인 유사도 | 10 |
| **K** | `column.search_text`, `table.search_text` | `pg_trgm` similarity ≥ 0.2 | 20 |
| **VAL** | `metadata_column_value` | 정확일치 + trgm + 벡터 | 10 |

**VAL 경로는 질문 전체가 아니라 토큰 단위로 조회한다.** 질문 전체 임베딩을 값과 비교하면 노이즈가 크다.

토크나이저(`search/tokenize.py`)는 PoC에서 형태소 분석기 없이 시작한다: 공백 분리 → 말미 조사(`의/를/을/은/는/이/가/에/에서/으로/로`) 제거 → 2글자 이상 토큰만 채택. `"서울 고객의 2025년 판매실적"` → `[서울, 고객, 2025년, 판매실적]`. 정확도가 부족하면 그때 개선한다.

### 6.2 융합 — 가중 RRF

점수 스케일이 서로 다르므로(코사인, trgm similarity, 빈도) 원점수를 섞지 않고 **순위 기반 RRF**를 쓴다.

```
score(table) = Σ  w_source / (k + rank_i)          k = 60
             i ∈ hits(table)
```

**초기 가중치**

| source | w | 근거 |
|---|---|---|
| VAL | **3.0** | 가장 강한 신호. 그 값이 실제로 그 컬럼에 존재한다는 것은 사실이다 |
| V-col | 1.0 | 기본 |
| V-tbl | 1.0 | 기본 |
| K | 0.7 | trgm은 부분 문자열 우연 일치로 오탐이 많다 |

**집계 규칙**

- 테이블 단위로 합산하되, **한 테이블당 상위 3개 컬럼 히트까지만** 합산한다. 한 테이블이 점수를 독식해 다른 후보를 밀어내는 것을 막는다
- 상위 **5개 테이블** 선정
- 단, **최고점의 20% 미만인 테이블은 제외**한다. 무관한 테이블이 컨텍스트를 오염시키는 것을 막는다
- 선정 결과가 0건이면 LLM을 호출하지 않고 즉시 종료한다

모든 상수(`k`, 가중치, 상한, 컷오프)는 `config.py`에 모으고 `.env`로 덮어쓸 수 있게 한다. 튜닝이 실제 작업이기 때문이다.

### 6.3 조인 경로 보완

`metadata_relation`을 무방향 그래프로 보고, 선정된 테이블 쌍마다 BFS 최단 경로를 찾는다.

- 최대 깊이 **3**
- 경로 위의 중간 테이블("브릿지")은 컨텍스트에 포함하되 **조인 키 컬럼만** 넣는다 (토큰 절약)
- 경로가 없으면 억지로 잇지 않고 컨텍스트에 `-- 관계 없음`으로 표기한다. LLM의 무근거 조인을 막는다
- 브릿지를 포함한 최종 테이블 수 상한은 **8개**

### 6.4 LLM 컨텍스트 포맷

```
[질문]
서울 고객의 2025년 판매 실적

[테이블]
biz.customer  — 고객 기본정보
  customer_id    bigint       PK   고객 식별번호
  customer_name  varchar(100)      고객명
  region         varchar(50)       고객 지역
                                   ▸ 값: 서울, 경기, 부산, 대구, 인천 (총 8종)

biz.orders  — 고객의 주문 정보
  order_id       bigint       PK   주문 식별번호
  customer_id    bigint       FK   고객 식별번호
  order_date     date              주문일자
  total_amount   numeric(15,2)     주문 총 금액
                                   ▸ 업무용어: 매출, 매출액, 판매금액, 판매실적

[관계]
biz.customer.customer_id = biz.orders.customer_id   (FK)

[규칙]
- PostgreSQL 문법. SELECT 단일문만.
- 위에 없는 테이블/컬럼 사용 금지.
- 값 목록이 제시된 컬럼은 그 값을 정확히 사용할 것.
- 설명 없이 SQL만 출력.
```

저카디널리티 컬럼의 값 목록을 컨텍스트에 직접 넣는 것이 핵심이다. LLM이 `region LIKE '%서울%'`로 추측하는 대신 `region = '서울'`을 정확히 쓰게 된다.

---

## 7. 안전장치

### 7.1 방어선

```
LLM이 생성한 SQL
  │
  ├─① 파싱 게이트 (guard.py — sqlglot AST 검사)
  │    · 단일 statement 인가                          아니면 거부
  │    · 최상위가 SELECT 또는 WITH 인가                아니면 거부
  │    · AST 에 DDL/DML 노드가 있는가                  있으면 거부
  │      (Insert/Update/Delete/Drop/Alter/Create/Truncate/Grant/Copy)
  │    · 세미콜론 뒤 잔여 텍스트 / 주석 삽입           거부
  │    · pg_catalog / information_schema 참조          거부
  │
  ├─② LIMIT 주입
  │    최상위 SELECT 에 LIMIT 없으면 LIMIT 100 추가
  │    있으면 min(기존값, 1000) 으로 클램프
  │    서브쿼리의 LIMIT 은 건드리지 않는다
  │
  ├─③ 세션 강제 (execute.py)
  │    BEGIN READ ONLY
  │    SET LOCAL statement_timeout = '10s'
  │    SET LOCAL transaction_read_only = on
  │    ... 항상 ROLLBACK 으로 종료
  │
  └─④ DB 계정 권한 — PoC 에서는 보류
```

정규식 블랙리스트는 반드시 우회된다(`/**/` 주석, 대소문자, 유니코드, 문자열 리터럴 내 키워드). **AST 검사가 유일하게 신뢰할 수 있는 방법이며 `sqlglot`을 사용한다.**

### 7.2 계정 정책 (PoC 결정)

- 메타 DB와 업무 DB 모두 `itos_dev` 단일 계정을 사용한다
- 읽기 전용 롤 스크립트(`sql/04_readonly_role.sql`)는 작성하되 **적용은 추후**로 미룬다
- `.env`는 `META_DSN`과 `BIZ_DSN`을 별도 키로 두어, 나중에 `itos_ro`를 만들면 한 줄 변경으로 전환되게 한다
- 방어선 ④가 없으므로 ①②③이 유일한 방어선이다. ③의 `BEGIN READ ONLY`는 SELECT 테스트에 지장이 없으므로 **항상 적용한다**
- 앱 기동 시 `BIZ_DSN`의 사용자가 `META_DSN`과 동일하면 경고 로그를 남긴다

`sql/04_readonly_role.sql` (보류 상태로 보관):

```sql
CREATE ROLE itos_ro LOGIN PASSWORD :'ro_password';
GRANT CONNECT ON DATABASE ggydb TO itos_ro;
GRANT USAGE ON SCHEMA biz TO itos_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA biz TO itos_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA biz GRANT SELECT ON TABLES TO itos_ro;
REVOKE ALL ON SCHEMA meta FROM itos_ro;
```

### 7.3 실패 처리

| 실패 지점 | 처리 |
|---|---|
| 검색 결과 0건 | LLM 호출 없이 즉시 종료. "관련 테이블을 찾지 못했습니다" + 검색 점수 덤프 |
| LLM이 SQL 아닌 응답 | 코드펜스·설명문 제거 시도 → 실패 시 재생성 **1회** |
| 파싱 게이트 거부 | **재시도 없이 즉시 실패.** 거부 사유 노출. 거부된 SQL을 LLM에 되먹이면 우회 시도를 유도하게 된다 |
| `EXPLAIN` 실패 | 오류 메시지를 프롬프트에 덧붙여 재생성 **1회**. 2회차도 실패면 SQL을 보여주고 종료 |
| 실행 타임아웃 | 실패 반환. 재시도 없음 |
| Ollama 무응답 | 60초 타임아웃 → 명확한 연결 오류 |

재생성은 **EXPLAIN 실패에 한해 최대 1회**. 무한 루프와 토큰 낭비를 막는다.

### 7.4 자격증명

`.env` (gitignore 대상):

```
META_DSN=postgresql://itos_dev:<password>@192.168.0.140:5432/ggydb
BIZ_DSN=postgresql://itos_dev:<password>@192.168.0.140:5432/ggydb
BIZ_SCHEMA=biz
META_SCHEMA=meta

OLLAMA_BASE_URL=http://192.168.0.169:11434
LLM_MODEL=gemma4:26b-a4b-it-q4_K_M
LLM_TIMEOUT_SEC=60

EMBED_PROVIDER=ollama          # ollama | sentence_transformers
EMBED_MODEL=kure-v1
EMBED_DIM=1024

VALUE_DISTINCT_MAX=50
RRF_K=60
W_VALUE=3.0
W_VECTOR_COL=1.0
W_VECTOR_TBL=1.0
W_KEYWORD=0.7
TOP_TABLES=5
SCORE_CUTOFF_RATIO=0.2
MAX_CONTEXT_TABLES=8
SQL_ROW_LIMIT=100
SQL_TIMEOUT_SEC=10
```

로그 출력 시 DSN의 비밀번호는 마스킹한다.

---

## 8. 검증 전략

### 8.1 테스트 픽스처 (`sql/03_biz_fixture.sql`)

`biz` 스키마에 4개 테이블. **오검색을 유발하는 함정을 의도적으로 심는다.**

| 테이블 | 행 수 | 함정 |
|---|---|---|
| `customer` | 200 | `region` 8종(서울/경기/부산/대구/인천/광주/대전/울산), `customer_name` 고카디널리티 |
| `orders` | 2,000 | 2023~2025 분산, `total_amount` |
| `order_detail` | 6,000 | `amount` — `orders.total_amount`와 혼동되는 유사 컬럼. 그 외 `order_id`, `product_id`, `quantity`(판매량) |
| `product` | 50 | `category` 5종, 그중 "서울식품"을 포함해 값 검색 오탐 유발 |

깨끗한 데이터로는 융합 가중치가 옳은지 알 수 없다. PoC의 목적은 "어디서 틀리는지" 파악이다.

### 8.2 평가 세트 (`tests/questions.yaml`)

정답은 **기대 테이블 집합**으로만 정의한다. 정답 SQL이 여러 개일 수 있으므로 SQL 문자열은 비교하지 않는다.

| # | 질문 | 기대 테이블 | 검증 의도 |
|---|---|---|---|
| 1 | 서울 고객의 2025년 판매 실적 | customer, orders | 값 경로 + 업무용어 |
| 2 | 고객 정보 보여줘 | customer | 테이블 지향 질문 |
| 3 | 상품별 매출 순위 | product, order_detail, orders | 브릿지 조인 |
| 4 | 매출액 총합 | orders | `total_amount` vs `amount` 구분 |
| 5 | 부산 지역 주문 건수 | customer, orders | 값 경로 |
| 6 | 서울식품 상품 판매량 | product, order_detail | 값 오탐 함정 |
| 7 | 2024년 주문 목록 | orders | 단순 |
| 8 | 날씨 정보 알려줘 | (없음) | 무관 질문 → 0건이 정답 |

**측정 지표**: 테이블 Recall / Precision, SQL 생성 성공률, EXPLAIN 통과율, 재생성 발생률.

`python -m app.cli eval` 로 8개를 일괄 실행하고 표로 출력한다. 가중치를 조정했을 때 회귀를 즉시 볼 수 있게 하는 것이 목적이다.

### 8.3 단위 테스트 (DB·LLM 불필요)

| 대상 | 검증 항목 |
|---|---|
| `fusion.rrf` | 가중치 반영, 테이블당 3컬럼 상한, 20% 컷오프, 동점 처리 |
| `guard.validate` | `DROP TABLE`, `; DELETE`, `/**/` 주석, `UNION` + DDL, `pg_catalog` 접근 → 전부 거부. 정상 `SELECT` / `WITH` → 통과 |
| `guard.inject_limit` | LIMIT 없음 → 100 추가 / LIMIT 5000 → 1000 클램프 / 서브쿼리 LIMIT 미변경 |
| `graph.bfs` | customer ↔ product 경로가 orders → order_detail 를 거치는지 |
| `tokenize` | "서울 고객의 2025년 판매실적" → `[서울, 고객, 2025년, 판매실적]` |

---

## 9. 구축 순서

각 단계가 독립적으로 검증 가능해야 한다.

| # | 단계 | 완료 판정 |
|---|---|---|
| 1 | 스캐폴딩 + `.env` + DB 연결 | `cli doctor` 통과 |
| 2 | `sql/01`, `sql/02` 실행 | `meta` 7개 테이블·인덱스 생성 확인 |
| 3 | `sql/03` 픽스처 + 더미 데이터 | `biz` 4개 테이블 조회 |
| 4 | **Embedding 클라이언트** | 임의 문장 → 1024차원 벡터 반환 |
| 5 | `collect/introspect` + `profile` | `meta`에 테이블·컬럼·값 적재 |
| 6 | `collect/enrich` (LLM) | 생성된 업무용어 육안 검토 |
| 7 | `collect/embed` + `search_text` | 임베딩 NULL 0건 |
| 8 | 3경로 개별 검색 | `cli search --path=vector\|keyword\|value` |
| 9 | `fusion` + `graph` + `context` | `cli context "질문"` 출력 확인 |
| 10 | `sqlgen` + `guard` + `execute` | `cli ask "질문"` 결과 반환 |
| 11 | **eval 세트 실행 → 가중치 튜닝** | 8문항 지표 표 산출 |
| 12 | FastAPI + Streamlit | 데모 동작 |

**4단계가 최대 리스크다.** KURE-v1은 XLM-RoBERTa(BGE-M3) 계열이라 `llama.cpp` GGUF 변환이 한 번에 되지 않을 수 있다. 실패하면 **즉시** `sstf_client.py`(sentence-transformers)로 전환한다. `EMBED_PROVIDER` 한 줄 변경으로 끝나도록 인터페이스를 먼저 만든다. 4단계를 3단계 직후에 배치한 이유가 이것이다. 파이프라인을 다 만든 뒤에 임베딩에서 막히는 상황을 피한다.

**11단계가 실질적인 작업이다.** 1~10은 골격을 세우는 과정이고, 정확도는 11단계의 반복에서 나온다.

---

## 10. 명시적 비범위 (YAGNI)

다음은 이번 PoC에 포함하지 않는다. 필요성이 데이터로 확인되면 그때 추가한다.

- **LLM 재순위(rerank)** — 융합 결과를 LLM으로 한 번 더 거르는 단계. 인터페이스만 열어두고 구현하지 않는다. 11단계에서 Recall은 높은데 Precision이 낮으면 그때 넣는다
- **형태소 분석기** — 단순 토크나이저로 시작
- **다중 데이터소스** — 스키마는 대비하되 PoC는 1개
- **질의 로그·피드백 학습 테이블**
- **인증·권한, 다중 사용자**
- **메타데이터 편집 UI** — Streamlit은 질의응답 전용
- **증분 수집** — 매번 전체 재수집
- **읽기 전용 DB 롤 적용** — 스크립트만 준비

---

## 11. 미해결 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| KURE-v1 GGUF 변환 실패 | 4단계 중단 | sentence-transformers 폴백. 인터페이스 사전 분리 |
| `gemma4:26b`의 한국어 SQL 생성 품질 | SQL 생성률 저하 | 프롬프트 개선 → 부족하면 모델 교체 검토 |
| 단순 토크나이저의 한국어 처리 한계 | VAL 경로 리콜 저하 | 11단계에서 측정 후 판단 |
| RRF 가중치 초기값의 근거 부족 | 잘못된 테이블 선정 | 평가 세트로 측정하며 조정. 이것이 11단계의 내용 |
| 원격 Ollama(26B) 응답 지연 | UX 저하 | PoC 허용. 타임아웃 60초 |

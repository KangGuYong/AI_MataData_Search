# 프로젝트 동작 원리

자연어 질문 → DB 메타데이터 검색 → SQL 생성/실행까지의 전체 흐름을 정리한 문서다.

## 전체 구조 (`app/`)

| 모듈 | 역할 |
|---|---|
| `config.py` | 설정(DB DSN, LLM/임베딩, 검색 튜닝 파라미터) |
| `db.py` | 메타/업무 DB 커넥션 3종 |
| `collect/` | 업무 DB 스키마 수집 → 메타 DB 적재 |
| `search/` | 질문 → 관련 테이블 검색 |
| `sqlgen/` | LLM SQL 생성·검증·실행 |
| `pipeline.py` | `retrieve()`+`ask()` 전체 오케스트레이션 |
| `api.py` / `ui.py` | FastAPI / Streamlit |
| `cli.py` | Typer CLI |

## 1. DB 접근

- `meta_conn()` (`app/db.py:16`) — 메타 DB, 쓰기 가능. pgvector 확장 등록.
- `biz_conn_readonly()` (`app/db.py:29`) — 업무 DB, 항상 READ ONLY 트랜잭션 + `statement_timeout`(`SQL_TIMEOUT_SEC`). 커밋 없이 항상 롤백. SQL 실행 시 사용하는 안전 경로.
- `biz_conn_collect()` (`app/db.py:44`) — 업무 DB, 프로파일링용으로 타임아웃을 길게(`COLLECT_TIMEOUT_SEC`) 잡은 별도 커넥션.

접속 정보는 `.env`의 `META_DSN`/`BIZ_DSN`(PostgreSQL DSN)으로 설정한다 (`app/config.py:15-18`).

## 2. 수집(collect) 파이프라인 — 업무 DB를 메타 DB로 미러링

1. **`collect/introspect.py`** `collect_schema()` — `pg_class`/`pg_attribute`/`pg_index`/`pg_constraint` 카탈로그 쿼리로 biz 스키마의 테이블·컬럼·PK·FK를 읽어 `meta.metadata_table/column/relation`에 적재.
2. **`collect/profile.py`** `profile_columns()` — 각 컬럼의 count/distinct/min/max, 샘플값 5개(`sample_value_count`)를 `metadata_column`에 UPDATE. 텍스트형이고 `distinct_count <= value_distinct_max`(50)이면 값 전체를 `metadata_column_value`에 저장.
3. **`collect/enrich.py`** `enrich_all()` — LLM으로 테이블/컬럼별 `business_name/business_desc`, 컬럼은 `business_terms`(동의어)까지 생성해 저장.
4. **`collect/search_text.py`** `rebuild_search_text()` — table_name+business_name+business_desc+comment(테이블), column_name+business_name+desc+comment+동의어(컬럼)를 합쳐 `search_text` 컬럼 생성.
5. **`collect/embed.py`** `embed_all()` — 테이블/컬럼/값의 `search_text`를 임베딩해 `embedding` 컬럼(`::vector` 캐스팅)에 저장.

CLI 실행 순서: `collect` → `enrich` → `embed`

## 3. 검색(retrieve) 파이프라인 — 질문에서 관련 테이블 찾기 (`pipeline.py:24-83`)

1. **토큰화** (`search/tokenize.py`) — 공백 분리, 말미 조사 제거, 2글자 이상 필터.
2. **IDF 게이트** (`pipeline.py:31-45`) — `selectivity.token_table_counts`로 토큰이 매칭되는 테이블 수를 세고, `max_idf = log(전체테이블 / 매칭테이블)`가 `min_token_idf`(0.5) 미만이면 임베딩 호출 없이 즉시 "무관 질문"으로 중단.
3. **벡터 검색** (`search/vector.py`) — pgvector 코사인 거리(`<=>`)로 컬럼(v_col)/테이블(v_tbl) 매칭.
4. **키워드** (`search/keyword.py`) — trigram word_similarity(`trgm_min_similarity`=0.7).
5. **값 매칭** (`search/value.py`) — 정확 일치(1.0) 또는 pg_trgm similarity.
6. **RRF 융합** (`search/fusion.py`) — 소스별 가중치(`w_value=3.0, w_vector_col/tbl=1.0, w_keyword=0.7`)로 `1/(k+rank)` 합산. 테이블당 `max_hits_per_table`(3)개만 반영, 상위 `top_tables`(2)개 중 최고점의 `score_cutoff_ratio`(0.2) 이상만 채택. `min_table_score`는 기본 비활성(0.0).
7. **조인 경로 탐색** (`search/graph.py`) — FK 그래프 BFS(`join_max_depth`=3)로 선정 테이블 간 경로 탐색.
8. **컨텍스트 조립** (`search/context.py`) — 선정 테이블은 전체 컬럼+값+동의어, 브릿지 테이블은 조인 키 컬럼만 포함해 LLM용 텍스트 조립(`max_context_tables`=8).

## 4. SQL 생성 및 실행 (`sqlgen/`, `pipeline.py:86-157`)

1. LLM이 컨텍스트 기반으로 SQL 생성 → `sqlglot`으로 SQL만 추출(`extract_sql`).
2. `looks_like_sql` 검증 실패 시 1회만 재생성.
3. **`sqlgen/guard.py`** `validate()` — sqlglot AST 기반 화이트리스트 검증. 단일 SELECT/WITH만 허용, INSERT/UPDATE/DDL/dblink/pg_sleep 등 금지 함수·시스템 카탈로그(`pg_*`, `information_schema` 등) 차단. 정규식 블랙리스트가 아니라 AST만 신뢰.
4. **`sqlgen/guard.py`** `inject_limit()` — 최상위 SELECT에 LIMIT이 없으면 `sql_row_limit`(100) 주입, 있으면 `sql_max_limit`(1000) 초과 시 캡.
5. **`sqlgen/execute.py`** — `explain()`으로 `biz_conn_readonly`에서 EXPLAIN 선검증(실패 시 오류 메시지를 붙여 1회 재생성) 후 통과하면 `run()`으로 READ ONLY 트랜잭션에서 실행하고 항상 롤백.

## 5. API / UI

- **`api.py`** — `POST /ask` → `pipeline.ask()` 호출해 question/tables/sql/columns/rows/error/context/trace를 JSON으로 반환. `GET /metadata/tables`는 메타 DB에서 테이블 목록+컬럼 수 조회.
- **`ui.py`** — Streamlit. API를 거치지 않고 `pipeline`을 직접 import해서 `run_ask()` 호출 → 선정 테이블/SQL/결과표/trace(JSON)/컨텍스트를 expander로 표시.

## 6. CLI (`app/cli.py`)

`doctor`(DB/Ollama/확장 점검), `init-db`, `fixture`(더미 biz 데이터), `embed-test`, `collect`, `enrich`, `embed`, `search`(경로별 히트 확인), `context`(LLM 컨텍스트 미리보기), `ask`(질문→SQL 실행), `eval`(`tests/questions.yaml` 평가셋 일괄 실행, `--retrieval-only`로 LLM 없이 검색만 평가, Recall/Precision/SQL 성공률을 표로 출력).

## 핵심 설계 포인트

- 검색 단계는 LLM 없이 IDF/벡터/키워드/값 매칭 4가지 신호를 RRF로 융합해 테이블을 좁힌다.
- SQL 실행은 항상 READ ONLY + AST 검증 + LIMIT 강제로 안전장치를 이중으로 건다.

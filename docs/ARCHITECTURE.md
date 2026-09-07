# 프로젝트 동작 원리

자연어 질문 → DB 메타데이터 검색 → SQL 생성/실행까지의 전체 흐름을 정리한 문서다.

## 전체 구조 (`app/`)

| 모듈 | 역할 |
|---|---|
| `config.py` | 설정(DB DSN, LLM/임베딩, 검색 튜닝 파라미터) |
| `db.py` | 메타 DB 커넥션 + 수집용 업무 DB 커넥션 |
| `collect/` | 업무 DB 스키마 수집 → 메타 DB 적재 |
| `search/` | 질문 → 관련 테이블 검색 |
| `sqlgen/` | LLM SQL 생성 (`generate.py`). 검증·LIMIT·실행은 `sqlmcp/`로 이전 |
| `sqlgen/mcp_client.py` | MCP 서버 호출 (sync 경계) |
| `pipeline.py` | `retrieve()`+`ask()` 전체 오케스트레이션 |
| `api.py` / `ui.py` | FastAPI / Streamlit |
| `cli.py` | Typer CLI |

`sqlmcp/` 는 별도 프로세스로 도는 MCP 서버다. 업무 DB 자격증명(`BIZ_DSN`)과 안전 검증
(AST 검증 · LIMIT 주입 · READ ONLY 실행)을 독점하며 `run_query` 도구 하나를 노출한다.
이 서버를 호출하는 것은 uvicorn(FastAPI) 프로세스뿐이고, Streamlit·CLI 는 백엔드 API 를
거친다.

## 1. DB 접근

- `meta_conn()` (`app/db.py:16`) — 메타 DB, 쓰기 가능. pgvector 확장 등록.
- `sqlmcp/db.py` `biz_conn_readonly()` — 업무 DB, 항상 READ ONLY 트랜잭션 +
  `statement_timeout`(`.env.mcp` 의 `SQL_TIMEOUT_SEC`). 커밋 없이 항상 롤백.
  **MCP 서버 프로세스에만 존재한다.**
- `biz_conn_collect()` (`app/db.py:44`) — 업무 DB, 프로파일링용으로 타임아웃을 길게(`COLLECT_TIMEOUT_SEC`) 잡은 별도 커넥션. 앱의 `.env`(`BIZ_DSN`)를 사용한다.

메타 DB 접속 정보는 `.env`의 `META_DSN`으로, 수집용 업무 DB 접속 정보는 `.env`의 `BIZ_DSN`으로
설정한다 (`app/config.py`). SQL 실행용 업무 DB 접속 정보(`BIZ_DSN`)는 `.env.mcp`에 별도로 설정한다.

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
3. `pipeline.ask()` 가 생성된 SQL 을 `sqlgen/mcp_client.run_query()` 로 MCP 서버에 넘긴다.
4. 서버(`sqlmcp/query.py`)가 `guard.validate` → `guard.inject_limit` → `EXPLAIN` → 실행
   순으로 처리하고 `{ok, sql, columns, rows, row_count, error, error_stage}` 를 돌려준다.
5. `error_stage == "explain"` 일 때만 오류 메시지를 붙여 1회 재생성한다.
   `guard` / `execute` / `transport` 는 재생성 없이 종료한다.

## 5. API / UI

- **`api.py`** — `POST /ask` → `pipeline.ask()` 호출해 question/tables/sql/columns/rows/error/context/trace를 JSON으로 반환. `GET /metadata/tables`는 메타 DB에서 테이블 목록+컬럼 수 조회.
- **`ui.py`** — Streamlit. 백엔드 `/ask` 를 호출해서 선정 테이블/SQL/결과표/trace(JSON)/컨텍스트를 expander로 표시.

## 6. CLI (`app/cli.py`)

`doctor`(DB/Ollama 연결과 확장 설치 상태 점검. MCP 서버에도 인증 없이 요청을 보내 401 이 오는지
확인한다), `init-db`, `fixture`(더미 biz 데이터), `embed-test`, `collect`, `enrich`, `embed`,
`search`(경로별 히트 확인), `context`(LLM 컨텍스트 미리보기), `ask`(백엔드 `/ask` 를 호출해 질문 →
SQL 생성 → 실행), `eval`(`tests/questions.yaml` 평가셋 일괄 실행, `--retrieval-only`면 `pipeline.retrieve()`
를 직접 호출해 백엔드 없이 검색만 평가, 그 외에는 백엔드 `/ask` 를 호출해 SQL 실행까지 포함,
Recall/Precision/SQL 성공률을 표로 출력).

## 핵심 설계 포인트

- 검색 단계는 LLM 없이 IDF/벡터/키워드/값 매칭 4가지 신호를 RRF로 융합해 테이블을 좁힌다.
- SQL 실행은 항상 READ ONLY + AST 검증 + LIMIT 강제로 안전장치를 이중으로 건다.

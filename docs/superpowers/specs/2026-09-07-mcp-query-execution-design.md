# AI 생성 조회 SQL의 MCP 서버 분리 — 설계

작성일: 2026-09-07

## 1. 목적

LLM이 생성한 조회 SQL의 **실행 경로만** 별도 프로세스(MCP 서버)로 떼어내, 업무 DB
자격증명(`BIZ_DSN`)과 안전 검증(AST 검증 · LIMIT 주입 · READ ONLY 실행)을 그 프로세스
안에 가둔다.

목표가 아닌 것:
- 메타 DB(`META_DSN`) 접근 분리. 이 앱의 내부 저장소이므로 그대로 둔다.
- 외부 AI 클라이언트에 대한 노출. 이 MCP 서버의 호출자는 이 프로젝트의 백엔드뿐이다.
- 수집 경로의 `BIZ_DSN` 제거. 아래 5절 참조.

## 2. 전체 구조

```
Streamlit ─HTTP─┐
                ├─→ FastAPI ─MCP(HTTP)─→ sqlmcp ─psycopg─→ 업무 DB
CLI      ─HTTP─┘        │
                        └─psycopg─→ 메타 DB
```

MCP를 호출하는 프로세스는 **uvicorn(FastAPI) 하나뿐**이다. Streamlit과 CLI는
`API_URL`만 알고 `MCP_URL`/`MCP_AUTH_TOKEN`을 갖지 않는다.

| | uvicorn | streamlit / cli(ask, eval) | sqlmcp |
|---|---|---|---|
| 아는 DSN | `META_DSN`, `BIZ_DSN`(수집용) | 없음 | `BIZ_DSN` |
| MCP 토큰 | 보유 | 없음 | 보유 |
| 하는 일 | 검색·컨텍스트·LLM·재생성 판단 | 화면/출력 | 검증→EXPLAIN→실행 |

## 3. 패키지 구조

새 최상위 패키지 `sqlmcp/`는 `app/`과 코드를 공유하지 않는 독립 배포 단위다.

```
sqlmcp/
  config.py      # .env.mcp 전용 Settings
  guard.py       # app/sqlgen/guard.py 이동 (로직 변경 없음)
  db.py          # app/db.py 의 biz_conn_readonly() 이동
  server.py      # FastMCP(streamable-http), Bearer 검사, run_query 도구

app/
  sqlgen/guard.py     # 삭제 (sqlmcp 로 이동)
  sqlgen/execute.py   # 삭제 (sqlmcp 로 이동)
  sqlgen/mcp_client.py # 신규
  db.py               # biz_conn_readonly() 제거. meta_conn / biz_conn_collect 만 남음
  ui.py               # pipeline 직접 import 제거 → API 호출
  cli.py              # ask / eval 을 API 경유로 변경
```

## 4. `run_query` 도구 계약

서버가 노출하는 도구는 하나다.

**입력:** `sql: str` — LLM 생성 SQL 원문. 백엔드는 손대지 않고 그대로 넘긴다.

**서버 내부 순서:**
1. `guard.validate(sql)` — 실패 시 반환 (`error_stage="guard"`, `sql=null`)
2. `guard.inject_limit(정규화 SQL, default=SQL_ROW_LIMIT, max=SQL_MAX_LIMIT)`
3. `biz_conn_readonly()` 안에서 `EXPLAIN <sql>` — 실패 시 반환 (`error_stage="explain"`)
4. 같은 방식으로 실제 실행 → `columns`, `rows`. 예외 시 반환 (`error_stage="execute"`)

**출력:** JSON 텍스트 1건. `json.dumps(default=str)`로 직렬화한다. 숫자·불리언·null은
JSON 네이티브 타입으로 남고 `date`/`Decimal`/`UUID` 등만 문자열이 된다.

```json
{
  "ok": true,
  "sql": "SELECT ... LIMIT 100",
  "columns": ["emp_no", "name", "hired_at"],
  "rows": [[101, "홍길동", "2021-03-02"]],
  "row_count": 1,
  "error": null,
  "error_stage": null
}
```

실패 시 `ok: false`, `error`에 사유 문자열, `error_stage`에 `guard`/`explain`/`execute`.
`sql` 필드는 guard 실패면 `null`, 그 이후 실패면 **LIMIT 주입까지 끝난 최종 SQL**이 담긴다.
백엔드가 재생성 프롬프트에 넣어야 하는 값이 이것이다.

**전송:** MCP Streamable HTTP, `127.0.0.1:<MCP_PORT>/mcp`.
`Authorization: Bearer <MCP_AUTH_TOKEN>` 불일치 시 401.

**명시적 결정:**
- EXPLAIN과 실행은 별도 트랜잭션이다. 현재 `explain()`/`run()`도 각각 커넥션을 열므로
  동작은 동일하다. 한 트랜잭션으로 묶는 것은 이번 범위 밖.
- `error_stage`가 백엔드의 재생성 판단 기준이다. 현재 `pipeline.ask()`는 EXPLAIN 실패에만
  재생성하고 실행 실패는 종료하는데, 이 분기를 `error_stage == "explain"`으로 그대로 옮긴다.

## 5. 수집 경로는 그대로 둔다

`biz_conn_collect()`를 쓰는 `collect/introspect.py`, `collect/profile.py`는 카탈로그를 읽어
메타 DB에 적재하는 별개 경로이며 "AI가 생성한 조회 쿼리"가 아니다. 앱 프로세스에 남긴다.

따라서 격리는 **AI 생성 SQL 실행 경로에 한정**되며, `BIZ_DSN`의 완전한 제거가 아니다.
이는 의도된 범위 결정이다.

## 6. 백엔드 변경

### `app/sqlgen/mcp_client.py` (신규)

```python
def run_query(sql: str) -> QueryResult:   # sync. 내부에서 asyncio.run()
```

MCP Python SDK 클라이언트는 async이므로 여기서 sync 경계를 만든다. `api.py`의
`ask_endpoint`가 `def`(sync)라 FastAPI가 스레드풀에서 실행하며, 그 스레드에는 실행 중인
이벤트 루프가 없으므로 `asyncio.run()`이 안전하다.

호출마다 세션을 열고 닫는다. 커넥션 풀은 두지 않는다 — 루프백 HTTP라 왕복 비용이
무의미하다.

연결 실패·타임아웃·401은 예외로 올리지 않고 `QueryResult(ok=False, error_stage="transport")`
로 변환한다. `pipeline.ask()`가 LLM 실패를 `AskResult.error`로 바꿔 eval 루프를 살려두는
것과 같은 이유다.

`QueryResult`는 `app/models.py`에 dataclass로 추가한다:
`ok, sql, columns, rows, row_count, error, error_stage`.

### `app/pipeline.py`

`ask()`의 SQL 실행부를 교체한다.

- `guard.validate` / `inject_limit` / `execute.explain` / `execute.run` 호출 전부 제거
- LLM이 SQL을 만들면 곧장 `mcp_client.run_query(sql)`
- `res.ok`면 `result.sql/columns/rows`를 채우고 종료
- `error_stage == "explain"`이면 `res.sql`과 `res.error`로 1회 재생성 후 다시 `run_query`
- `guard`/`execute`/`transport`는 재생성 없이 `result.error`로 종료
- `trace`에 `error_stage`를 남긴다

MCP 호출 횟수는 최대 2회(초회 + 재생성 1회)로 현재 EXPLAIN 재시도 구조와 같다.

`AskResult.rows` 타입이 `list[tuple]` → `list[list]`로 바뀐다. `ui.py`와 `api.py`는
값에 `str()`을 씌워 쓰므로 수정이 필요 없다.

### `app/ui.py`

`from app.pipeline import ask` 를 제거하고 `httpx.post(f"{API_URL}/ask", json={"question": ...})`
로 바꾼다. `/ask` 응답에 `tables/sql/columns/rows/error/context/trace`가 모두 있으므로
화면 코드는 응답 dict에서 키를 꺼내는 형태로만 바뀐다.

### `app/cli.py`

`ask`, `eval`을 API 경유로 바꾼다. `search`/`context`/`collect`/`enrich`/`embed`/`doctor`는
메타 DB만 쓰므로 변경하지 않는다.

## 7. 설정

### `.env` (앱: uvicorn / streamlit / cli)

추가:
```
API_URL=http://127.0.0.1:8000
MCP_URL=http://127.0.0.1:8100/mcp
MCP_AUTH_TOKEN=<공유 토큰>
MCP_TIMEOUT_SEC=30
```
제거: `SQL_ROW_LIMIT`, `SQL_MAX_LIMIT`, `SQL_TIMEOUT_SEC` (서버로 이동)
유지: `BIZ_DSN` (수집 경로가 사용)

### `.env.mcp` (서버, 신규)

```
BIZ_DSN=postgresql://...
BIZ_SCHEMA=biz
SQL_ROW_LIMIT=100
SQL_MAX_LIMIT=1000
SQL_TIMEOUT_SEC=10
MCP_HOST=127.0.0.1
MCP_PORT=8100
MCP_AUTH_TOKEN=<앱과 동일한 값>
```

`.env.mcp.example`을 커밋하고 `.env.mcp`는 `.gitignore`에 넣는다.
`sqlmcp/config.py`의 Settings는 `app/config.py`와 아무것도 공유하지 않는다.

**`MCP_TIMEOUT_SEC` > `SQL_TIMEOUT_SEC` 이어야 한다.** 서버의 `statement_timeout`(10초)이
먼저 터져야 제대로 된 오류 메시지가 돌아온다. 클라이언트가 먼저 끊으면 `transport` 오류만
남고 원인을 못 본다. 기본값 30초 / 10초.

## 8. 기동

`scripts/run.ps1` (신규):

1. `python -m sqlmcp.server` 백그라운드 기동
2. `/mcp` 응답 확인까지 대기 (최대 10초, 실패 시 중단)
3. `uvicorn app.api:api --port 8000` 백그라운드 기동, 헬스 확인까지 대기
4. 인자에 따라 `streamlit run app/ui.py` 실행 (인자 `ui`) 또는 대기 (인자 `api`)
5. 종료 시 2·3에서 띄운 프로세스를 함께 종료

README의 실행 절차를 이것으로 교체한다.

의존성: `pyproject.toml`에 `mcp>=1.2` 추가. 앱과 서버는 한 저장소·한 가상환경을 공유하되
프로세스와 설정 파일만 분리한다. 도커 도입은 범위 밖.

## 9. 테스트

- `tests/test_guard.py` → `sqlmcp.guard` import로 수정. 순수 함수 테스트라 로직 변경 없음.
- `tests/test_mcp_contract.py` (신규) — 서버의 `run_query` 핸들러를 HTTP 없이 직접 호출해
  `guard` / `explain` / `execute` 세 `error_stage`와 정상 응답이 각각 나오는지 검증.
  `explain`/`execute`는 fixture DB가 필요하다.
- `tests/test_pipeline_branch.py` (신규) — `mcp_client.run_query`를 스텁으로 교체해
  재생성 분기가 `error_stage == "explain"`에서만 도는지, `guard`/`execute`/`transport`에서는
  즉시 종료하는지 검증. LLM·DB 불필요.

## 10. 검증 기준

- `tests/questions.yaml` 8문항 `eval` 결과가 분리 전과 동일하다.
- 앱 프로세스(uvicorn)에서 업무 DB로 나가는 psycopg 커넥션은 수집 명령을 실행할 때만
  발생한다.
- `MCP_AUTH_TOKEN` 없이 `/mcp`를 호출하면 401.
- `sqlmcp` 서버를 내린 상태에서 질문하면 `AskResult.error`가 transport 오류를 담고
  프로세스가 죽지 않는다.

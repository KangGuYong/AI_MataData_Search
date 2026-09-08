"""질문 -> 검색 -> LLM SQL 생성 -> 안전 실행 전체 파이프라인.

재시도 정책 (Task 18에서 확정, 함부로 바꾸지 않는다):
- 응답이 SQL 형태조차 아니면 (looks_like_sql False) 1회만 재생성한다.
- 안전 게이트(guard) 거부는 재시도하지 않는다. 거부된 SQL을 다시 넣으면
  모델이 게이트를 통과하는 변형을 찾도록 유도할 뿐이다.
- EXPLAIN 실패(MCP error_stage="explain")는 오류 메시지를 붙여 1회만 재생성한다.
- guard 거부 / 실행 실패 / MCP 통신 실패는 재생성하지 않는다.
- 검색 결과가 없으면 LLM을 아예 호출하지 않고 즉시 반환한다.
"""

from app.config import settings
from app.db import meta_conn
from app.embedding.base import get_embedding_client
from app.llm.base import get_llm_client
from app.models import AskResult, QueryResult
from app.search import context as ctx
from app.search import keyword, selectivity, value, vector
from app.search.fusion import fuse
from app.search.graph import find_join_paths, load_edges
from app.search.tokenize import tokenize
from app.sqlgen import generate, mcp_client

# MCP 서버가 어느 단계에서 멈췄는지를 사용자 문구로 옮긴다.
_STAGE_LABELS = {
    "guard": "안전 검증 거부",
    "explain": "SQL 검증 실패",
    "execute": "실행 실패",
    "transport": "SQL 실행 서버 연결 실패",
}


def _echo_prompt(trace: dict, key: str, prompt: dict[str, str]) -> None:
    """PROMPT_ECHO=true 일 때만 실제로 보낸 프롬프트를 trace에 남긴다.

    컨텍스트는 테이블 수에 비례해 길어져서 /ask 응답을 크게 부풀린다.
    원인을 파고들 때만 켠다.
    """
    if settings.prompt_echo:
        trace[key] = prompt


def _record(res: QueryResult, result: AskResult, trace: dict) -> None:
    """MCP 응답에서 trace와 result.sql을 갱신한다. 두 번의 시도가 같은 처리를 한다."""
    trace["error_stage"] = res.error_stage
    if res.sql:
        result.sql = res.sql


def retrieve(question: str) -> tuple[str, list[int], list[str], dict]:
    """검색 -> 융합 -> 조인경로 -> 컨텍스트. LLM SQL 생성 전까지."""
    tokens = tokenize(question)

    with meta_conn() as conn, conn.cursor() as cur:
        # 변별력 게이트를 검색보다 먼저 건다. 어떤 토큰도 테이블을 특정하지
        # 못하면 융합 점수는 어차피 의미가 없다. 원격 임베딩 호출도 아낀다.
        cur.execute("SELECT count(*) FROM meta.metadata_table WHERE is_active")
        total_tables = cur.fetchone()[0]
        counts = selectivity.token_table_counts(
            cur, tokens, settings.trgm_min_similarity
        )
        idf = selectivity.max_idf(counts, total_tables)
        if idf < settings.min_token_idf:
            return "", [], [], {
                "tokens": tokens,
                "hits": 0,
                "scores": [],
                "token_table_counts": counts,
                "max_idf": round(idf, 4),
                "rejected_by": f"변별력 부족 (max_idf {idf:.2f} < {settings.min_token_idf})",
            }

        qvec = get_embedding_client().embed([question])[0]
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
            min_score=settings.min_table_score,
        )
        selected = [s.table_id for s in scores]
        if not selected:
            return "", [], [], {
                "tokens": tokens,
                "hits": len(hits),
                "scores": [],
                "max_idf": round(idf, 4),
                "rejected_by": f"점수 하한 미달 (MIN_TABLE_SCORE={settings.min_table_score})",
            }

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

    # LLM 호출은 원격 26B 모델이라 타임아웃/연결 실패가 실제로 일어난다.
    # 예외를 그대로 올리면 eval 루프가 한 문항에서 통째로 중단되므로
    # AskResult.error로 바꿔 다음 문항이 계속되게 한다.
    try:
        _echo_prompt(trace, "prompt_1", generate.prompt_for(text))
        sql = generate.generate(llm, text)

        # 응답이 SQL 형태조차 아니면 1회 재생성한다 (안전 게이트 거부와는 다른 경우).
        if not generate.looks_like_sql(sql):
            trace["not_sql_response"] = sql[:200]
            reason = "응답이 SELECT 문이 아닙니다. SQL만 출력하시오."
            _echo_prompt(
                trace, "prompt_not_sql", generate.retry_prompt_for(text, sql, reason)
            )
            sql = generate.regenerate(llm, text, sql, reason)
    except Exception as e:  # noqa: BLE001
        trace["llm_error"] = f"{type(e).__name__}: {e}"
        result.error = f"LLM 호출 실패: {type(e).__name__}"
        return result
    result.sql = sql

    res = mcp_client.run_query(sql)
    _record(res, result, trace)

    # EXPLAIN 실패만 재생성한다. guard 거부를 다시 넣으면 모델이 게이트를
    # 통과하는 변형을 찾도록 유도할 뿐이고, 실행 실패와 통신 실패는
    # 재생성으로 나아지지 않는다.
    if not res.ok and res.error_stage == "explain":
        trace["explain_error_1"] = res.error
        try:
            _echo_prompt(
                trace,
                "prompt_explain",
                generate.retry_prompt_for(text, result.sql, res.error),
            )
            retry = generate.regenerate(llm, text, result.sql, res.error)
        except Exception as e:  # noqa: BLE001
            trace["llm_error"] = f"{type(e).__name__}: {e}"
            result.error = f"재생성 중 LLM 호출 실패: {type(e).__name__}"
            return result

        result.sql = retry
        res = mcp_client.run_query(retry)
        _record(res, result, trace)
        if not res.ok:
            trace["explain_error_2"] = res.error
            label = _STAGE_LABELS.get(res.error_stage, "실패")
            result.error = f"{label}(재시도 포함 2회): {res.error}"
            return result

    if not res.ok:
        # error_stage가 None인 경우는 res.ok가 True일 때뿐이고, 그 외 모든
        # 단계는 _STAGE_LABELS에 키가 있으므로 이 fallback은 도달 불가능하다.
        result.error = f"{_STAGE_LABELS.get(res.error_stage, '실패')}: {res.error}"
        return result

    result.columns = res.columns
    result.rows = res.rows
    return result

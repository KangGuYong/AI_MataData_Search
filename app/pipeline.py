"""질문 -> 검색 -> LLM SQL 생성 -> 안전 실행 전체 파이프라인.

재시도 정책 (Task 18에서 확정, 함부로 바꾸지 않는다):
- 응답이 SQL 형태조차 아니면 (looks_like_sql False) 1회만 재생성한다.
- 안전 게이트(guard) 거부는 재시도하지 않는다. 거부된 SQL을 다시 넣으면
  모델이 게이트를 통과하는 변형을 찾도록 유도할 뿐이다.
- EXPLAIN 실패는 오류 메시지를 붙여 1회만 재생성한다.
- 검색 결과가 없으면 LLM을 아예 호출하지 않고 즉시 반환한다.
"""

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
    """검색 -> 융합 -> 조인경로 -> 컨텍스트. LLM SQL 생성 전까지."""
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

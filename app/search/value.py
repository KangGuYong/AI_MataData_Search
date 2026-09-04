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
    # SET LOCAL은 바인드 파라미터를 지원하지 않는다 (keyword.py와 동일한 이슈).
    # set_config()로 대체한다. TEXT 값을 받으므로 str()로 넘긴다.
    cur.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(min_sim),))
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

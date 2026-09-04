from app.models import SearchHit

# 토큰 하나를 search_text 전체와 대조한다.
#
# 질문 전체를 similarity()로 재면 안 된다. similarity()는 대칭 집합 유사도라
# 짧은 질의와 긴 search_text 사이에서는 값이 구조적으로 낮게 나온다
# (실측: "서울 고객 2025년 판매 실적" 대 70~83자 search_text = 0.04~0.06).
# word_similarity(token, target)는 target 안에서 token과 가장 잘 맞는 부분만
# 보므로 토큰 단위로 쓰면 변별력이 살아난다
# (실측: '매출액' -> 금액 컬럼 2개는 1.000, 나머지는 0.000).
SQL = """
WITH col AS (
    SELECT c.column_id, c.table_id, t.table_name, c.column_name,
           word_similarity(%(tok)s, c.search_text) AS sim
    FROM meta.metadata_column c
    JOIN meta.metadata_table t USING (table_id)
    WHERE c.is_active AND %(tok)s <%% c.search_text
),
tbl AS (
    SELECT NULL::bigint AS column_id, t.table_id, t.table_name,
           NULL::varchar AS column_name,
           word_similarity(%(tok)s, t.search_text) AS sim
    FROM meta.metadata_table t
    WHERE t.is_active AND %(tok)s <%% t.search_text
)
SELECT * FROM (SELECT * FROM col UNION ALL SELECT * FROM tbl) u
WHERE sim >= %(min_sim)s
ORDER BY sim DESC
LIMIT %(limit)s
"""


def search(cur, tokens: list[str], min_sim: float, limit: int = 20) -> list[SearchHit]:
    """질문 토큰 각각을 search_text와 대조하고 (테이블, 컬럼)당 최고점만 남긴다."""
    if not tokens:
        return []
    # SET은 바인드 파라미터를 받지 않으므로 set_config를 쓴다. 세 번째 인자 true는
    # SET LOCAL과 같은 의미(트랜잭션 한정)다. <%% 연산자는 similarity_threshold가
    # 아니라 word_similarity_threshold를 본다.
    cur.execute(
        "SELECT set_config('pg_trgm.word_similarity_threshold', %s, true)",
        (str(min_sim),),
    )

    collected: list[tuple[float, int, int | None, str]] = []
    for tok in tokens:
        cur.execute(SQL, {"tok": tok, "min_sim": min_sim, "limit": limit})
        for column_id, table_id, tname, cname, sim in cur.fetchall():
            label = f"{tname}.{cname}" if cname else tname
            collected.append(
                (float(sim), table_id, column_id, f"{label} trgm={sim:.3f} <- '{tok}'")
            )

    collected.sort(key=lambda x: -x[0])
    seen: set[tuple[int, int | None]] = set()
    hits: list[SearchHit] = []
    for sim, table_id, column_id, detail in collected:
        key = (table_id, column_id)
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            SearchHit(
                source="keyword", table_id=table_id, column_id=column_id,
                rank=len(hits) + 1, raw_score=sim, detail=detail,
            )
        )
        if len(hits) >= limit:
            break
    return hits

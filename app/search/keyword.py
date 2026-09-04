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
    # SET LOCAL은 바인드 파라미터를 지원하지 않아 "syntax error at or near \"$1\""가
    # 난다. set_config()는 파라미터 바인딩이 가능하지만 TEXT 값을 받으므로 str()로 넘긴다.
    cur.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(min_sim),))
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

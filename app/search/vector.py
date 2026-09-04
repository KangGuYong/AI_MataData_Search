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

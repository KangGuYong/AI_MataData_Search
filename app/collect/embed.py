from app.db import meta_conn
from app.embedding.base import get_embedding_client


def _embed_rows(cur, select_sql: str, update_sql: str, client) -> int:
    cur.execute(select_sql)
    rows = cur.fetchall()
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    texts = [r[1] or "" for r in rows]
    vectors = client.embed(texts)
    for row_id, vec in zip(ids, vectors, strict=True):
        # 벡터는 항상 문자열로 넘기고 SQL에서 ::vector로 캐스팅한다.
        # pgvector 어댑터 등록 여부와 무관하게 동작한다.
        cur.execute(update_sql, (str(vec), row_id))
    return len(ids)


def embed_all() -> dict[str, int]:
    client = get_embedding_client()
    with meta_conn() as conn:
        cur = conn.cursor()
        tables = _embed_rows(
            cur,
            "SELECT table_id, search_text FROM meta.metadata_table WHERE is_active",
            "UPDATE meta.metadata_table SET embedding = %s::vector WHERE table_id = %s",
            client,
        )
        columns = _embed_rows(
            cur,
            "SELECT column_id, search_text FROM meta.metadata_column WHERE is_active",
            "UPDATE meta.metadata_column SET embedding = %s::vector WHERE column_id = %s",
            client,
        )
        values = _embed_rows(
            cur,
            "SELECT value_id, value_text FROM meta.metadata_column_value",
            "UPDATE meta.metadata_column_value SET embedding = %s::vector WHERE value_id = %s",
            client,
        )
        conn.commit()
    return {"tables": tables, "columns": columns, "values": values}

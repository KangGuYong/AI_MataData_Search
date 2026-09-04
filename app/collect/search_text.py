from app.db import meta_conn

TABLE_SQL = """
UPDATE meta.metadata_table t
SET search_text = trim(regexp_replace(
        concat_ws(' ', t.table_name, t.business_name, t.business_desc, t.table_comment),
        '\\s+', ' ', 'g')),
    updated_at = NOW()
"""

COLUMN_SQL = """
UPDATE meta.metadata_column c
SET search_text = trim(regexp_replace(
        concat_ws(' ', c.column_name, c.business_name, c.business_desc, c.column_comment,
                  (SELECT string_agg(bt.term, ' ')
                   FROM meta.metadata_column_term ct
                   JOIN meta.metadata_business_term bt USING (term_id)
                   WHERE ct.column_id = c.column_id)),
        '\\s+', ' ', 'g')),
    updated_at = NOW()
"""


def rebuild_search_text() -> dict[str, int]:
    with meta_conn() as conn:
        cur = conn.cursor()
        cur.execute(TABLE_SQL)
        tables = cur.rowcount
        cur.execute(COLUMN_SQL)
        columns = cur.rowcount
        conn.commit()
    return {"tables": tables, "columns": columns}

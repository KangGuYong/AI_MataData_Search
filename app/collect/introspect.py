from app.config import settings
from app.db import biz_conn_collect, meta_conn

TABLE_SQL = """
SELECT c.relname,
       obj_description(c.oid, 'pg_class') AS table_comment,
       c.reltuples::bigint                AS row_est
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind = 'r'
ORDER BY c.relname
"""

COLUMN_SQL = """
SELECT a.attname,
       a.attnum,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull                     AS is_nullable,
       col_description(a.attrelid, a.attnum) AS column_comment
FROM pg_attribute a
JOIN pg_class c     ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

PK_SQL = """
SELECT a.attname
FROM pg_index i
JOIN pg_class c     ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
"""

FK_SQL = """
SELECT con.conname,
       src.relname   AS from_table,
       sa.attname    AS from_column,
       tgt.relname   AS to_table,
       ta.attname    AS to_column
FROM pg_constraint con
JOIN pg_class src     ON src.oid = con.conrelid
JOIN pg_namespace n   ON n.oid = src.relnamespace
JOIN pg_class tgt     ON tgt.oid = con.confrelid
JOIN unnest(con.conkey)  WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN unnest(con.confkey) WITH ORDINALITY AS f(attnum, ord) ON f.ord = k.ord
JOIN pg_attribute sa ON sa.attrelid = src.oid AND sa.attnum = k.attnum
JOIN pg_attribute ta ON ta.attrelid = tgt.oid AND ta.attnum = f.attnum
WHERE con.contype = 'f' AND n.nspname = %s
"""


def ensure_datasource() -> int:
    """기본 데이터소스 1건을 보장하고 datasource_id를 돌려준다."""
    with meta_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meta.datasource (name, db_kind, host, port, database_name)
                VALUES ('default', 'postgresql', %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET host = EXCLUDED.host
                RETURNING datasource_id
                """,
                ("192.168.0.140", 5432, "ggydb"),
            )
            ds_id = cur.fetchone()[0]
        conn.commit()
    return ds_id


def collect_schema() -> dict[str, int]:
    """biz 스키마를 읽어 meta의 table/column/relation을 다시 채운다."""
    ds_id = ensure_datasource()
    schema = settings.biz_schema
    stats = {"tables": 0, "columns": 0, "relations": 0}

    with biz_conn_collect() as biz, meta_conn() as meta:
        bcur = biz.cursor()
        mcur = meta.cursor()

        mcur.execute("DELETE FROM meta.metadata_table WHERE datasource_id = %s", (ds_id,))

        bcur.execute(TABLE_SQL, (schema,))
        tables = bcur.fetchall()
        table_ids: dict[str, int] = {}
        column_ids: dict[tuple[str, str], int] = {}

        for tname, tcomment, row_est in tables:
            mcur.execute(
                """
                INSERT INTO meta.metadata_table
                    (datasource_id, schema_name, table_name, table_comment, row_count_est)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING table_id
                """,
                (ds_id, schema, tname, tcomment, row_est),
            )
            tid = mcur.fetchone()[0]
            table_ids[tname] = tid
            stats["tables"] += 1

            bcur.execute(PK_SQL, (schema, tname))
            pks = {r[0] for r in bcur.fetchall()}

            bcur.execute(COLUMN_SQL, (schema, tname))
            for cname, pos, dtype, nullable, ccomment in bcur.fetchall():
                mcur.execute(
                    """
                    INSERT INTO meta.metadata_column
                        (table_id, column_name, ordinal_position, data_type,
                         is_nullable, is_primary_key, column_comment)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING column_id
                    """,
                    (tid, cname, pos, dtype, nullable, cname in pks, ccomment),
                )
                column_ids[(tname, cname)] = mcur.fetchone()[0]
                stats["columns"] += 1

        bcur.execute(FK_SQL, (schema,))
        for conname, ftab, fcol, ttab, tcol in bcur.fetchall():
            if ftab not in table_ids or ttab not in table_ids:
                continue
            fcid = column_ids[(ftab, fcol)]
            tcid = column_ids[(ttab, tcol)]
            mcur.execute(
                "UPDATE meta.metadata_column SET is_foreign_key = TRUE WHERE column_id = %s",
                (fcid,),
            )
            mcur.execute(
                """
                INSERT INTO meta.metadata_relation
                    (from_table_id, from_column_id, to_table_id, to_column_id, relation_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (table_ids[ftab], fcid, table_ids[ttab], tcid, conname),
            )
            stats["relations"] += 1

        meta.commit()

    return stats

from psycopg import sql

from app.config import settings
from app.db import biz_conn_collect, meta_conn

TEXT_TYPES = ("character varying", "text", "character", "varchar", "char")


def _is_text(data_type: str) -> bool:
    return any(data_type.startswith(t) for t in TEXT_TYPES)


def profile_columns() -> dict[str, int]:
    """각 컬럼의 통계를 채우고, 저카디널리티 텍스트 컬럼의 값을 적재한다."""
    schema = settings.biz_schema
    stats = {"profiled": 0, "values": 0}

    with meta_conn() as meta, biz_conn_collect() as biz:
        mcur = meta.cursor()
        bcur = biz.cursor()

        mcur.execute(
            """
            SELECT c.column_id, t.table_name, c.column_name, c.data_type
            FROM meta.metadata_column c
            JOIN meta.metadata_table t USING (table_id)
            WHERE c.is_active
            ORDER BY t.table_name, c.ordinal_position
            """
        )
        columns = mcur.fetchall()
        mcur.execute("DELETE FROM meta.metadata_column_value")

        for column_id, tname, cname, dtype in columns:
            ident = sql.Identifier(schema, tname)
            col = sql.Identifier(cname)

            bcur.execute(
                sql.SQL(
                    "SELECT count(*), count({col}), count(DISTINCT {col}), "
                    "min({col})::text, max({col})::text FROM {tbl}"
                ).format(col=col, tbl=ident)
            )
            total, non_null, distinct, vmin, vmax = bcur.fetchone()
            null_ratio = 0.0 if total == 0 else round((total - non_null) / total, 4)

            bcur.execute(
                sql.SQL(
                    "SELECT DISTINCT {col}::text FROM {tbl} "
                    "WHERE {col} IS NOT NULL ORDER BY 1 LIMIT %s"
                ).format(col=col, tbl=ident),
                (settings.sample_value_count,),
            )
            samples = [r[0] for r in bcur.fetchall()]

            mcur.execute(
                """
                UPDATE meta.metadata_column
                SET distinct_count = %s, null_ratio = %s, min_value = %s,
                    max_value = %s, sample_values = %s, updated_at = NOW()
                WHERE column_id = %s
                """,
                (distinct, null_ratio, vmin, vmax, samples, column_id),
            )
            stats["profiled"] += 1

            if not _is_text(dtype) or distinct > settings.value_distinct_max:
                continue

            bcur.execute(
                sql.SQL(
                    "SELECT {col}::text, count(*) FROM {tbl} "
                    "WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
                ).format(col=col, tbl=ident)
            )
            for value_text, freq in bcur.fetchall():
                mcur.execute(
                    """
                    INSERT INTO meta.metadata_column_value (column_id, value_text, value_freq)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (column_id, value_text) DO NOTHING
                    """,
                    (column_id, value_text, freq),
                )
                stats["values"] += 1

        meta.commit()

    return stats

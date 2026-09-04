from fastapi import FastAPI
from pydantic import BaseModel

from app.db import meta_conn
from app.pipeline import ask as run_ask

api = FastAPI(title="AI 메타데이터 검색")


class AskRequest(BaseModel):
    question: str


@api.post("/ask")
def ask_endpoint(req: AskRequest) -> dict:
    r = run_ask(req.question)
    return {
        "question": r.question,
        "tables": r.table_names,
        "sql": r.sql,
        "columns": r.columns,
        "rows": [list(map(str, row)) for row in r.rows],
        "error": r.error,
        "context": r.context,
        "trace": r.trace,
    }


@api.get("/metadata/tables")
def list_tables() -> list[dict]:
    with meta_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.schema_name, t.table_name, t.business_name, t.business_desc,
                   count(c.column_id)
            FROM meta.metadata_table t
            LEFT JOIN meta.metadata_column c USING (table_id)
            GROUP BY 1,2,3,4
            ORDER BY 1,2
            """
        )
        return [
            {"schema": s, "table": t, "business_name": bn,
             "business_desc": bd, "column_count": cnt}
            for s, t, bn, bd, cnt in cur.fetchall()
        ]

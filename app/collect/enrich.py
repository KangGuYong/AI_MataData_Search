import json
import re

from app.db import meta_conn
from app.llm.base import get_llm_client

SYSTEM = "당신은 데이터 웨어하우스 메타데이터를 정리하는 한국어 데이터 분석가입니다."

TABLE_PROMPT = """다음 PostgreSQL 테이블에 대한 한국어 업무 정보를 만드시오.

테이블: {schema}.{table}
DB 주석: {comment}
컬럼: {columns}

아래 JSON 형식으로만 답하시오. 설명이나 코드펜스 없이 JSON만 출력하시오.
{{"business_name": "짧은 한국어 업무명", "business_desc": "한 문장 설명"}}"""

COLUMN_PROMPT = """다음 PostgreSQL 컬럼에 대한 한국어 업무 정보를 만드시오.

테이블: {schema}.{table} ({table_desc})
컬럼: {column}
타입: {data_type}
DB 주석: {comment}
샘플 값: {samples}
서로 다른 값의 수: {distinct}

business_terms 에는 현업이 이 컬럼을 부를 만한 한국어 동의어를 3~6개 넣으시오.
컬럼이 주문/판매의 총 결제 금액처럼 매출 실적을 나타낸다면, 경영진과 현업이 실제로 쓰는
일반 경영 용어인 "매출", "매출액"을 동의어 목록에 반드시 포함하시오.
예: pay_amt 라면 ["매출", "매출액", "판매금액", "결제금액", "판매실적"]

아래 JSON 형식으로만 답하시오. 설명이나 코드펜스 없이 JSON만 출력하시오.
{{"business_name": "짧은 한국어 업무명", "business_desc": "한 문장 설명", "business_terms": ["동의어1", "동의어2"]}}"""


def _parse_json(text: str) -> dict:
    """코드펜스나 앞뒤 설명이 섞여도 첫 JSON 오브젝트를 뽑아낸다."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        raise ValueError(f"JSON을 찾을 수 없음: {text[:200]}")
    return json.loads(m.group(0))


def enrich_all() -> dict[str, int]:
    llm = get_llm_client()
    stats = {"tables": 0, "columns": 0, "terms": 0, "failed": 0}

    with meta_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT t.table_id, t.schema_name, t.table_name, t.table_comment,
                   string_agg(c.column_name || ' ' || c.data_type, ', '
                              ORDER BY c.ordinal_position)
            FROM meta.metadata_table t
            JOIN meta.metadata_column c USING (table_id)
            GROUP BY t.table_id, t.schema_name, t.table_name, t.table_comment
            ORDER BY t.table_name
            """
        )
        rows = cur.fetchall()
        for i, (table_id, schema, tname, tcomment, cols) in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] 테이블 처리 중: {schema}.{tname}")
            prompt = TABLE_PROMPT.format(
                schema=schema, table=tname, comment=tcomment or "(없음)", columns=cols
            )
            try:
                data = _parse_json(llm.complete(prompt, system=SYSTEM))
            except Exception:  # noqa: BLE001
                print(f"  실패: {schema}.{tname}")
                stats["failed"] += 1
                continue
            cur.execute(
                """
                UPDATE meta.metadata_table
                SET business_name = %s, business_desc = %s, updated_at = NOW()
                WHERE table_id = %s
                """,
                (data.get("business_name"), data.get("business_desc"), table_id),
            )
            stats["tables"] += 1
        conn.commit()

        cur.execute(
            """
            SELECT c.column_id, t.schema_name, t.table_name,
                   COALESCE(t.business_desc, t.table_comment, ''),
                   c.column_name, c.data_type, c.column_comment,
                   c.sample_values, c.distinct_count
            FROM meta.metadata_column c
            JOIN meta.metadata_table t USING (table_id)
            WHERE c.is_active
            ORDER BY t.table_name, c.ordinal_position
            """
        )
        crows = cur.fetchall()
        for i, (column_id, schema, tname, tdesc, cname, dtype,
                ccomment, samples, distinct) in enumerate(crows, 1):
            print(f"[{i}/{len(crows)}] 컬럼 처리 중: {schema}.{tname}.{cname}")
            prompt = COLUMN_PROMPT.format(
                schema=schema, table=tname, table_desc=tdesc, column=cname,
                data_type=dtype, comment=ccomment or "(없음)",
                samples=", ".join(samples or []) or "(없음)", distinct=distinct,
            )
            try:
                data = _parse_json(llm.complete(prompt, system=SYSTEM))
            except Exception:  # noqa: BLE001
                print(f"  실패: {schema}.{tname}.{cname}")
                stats["failed"] += 1
                continue

            cur.execute(
                """
                UPDATE meta.metadata_column
                SET business_name = %s, business_desc = %s, updated_at = NOW()
                WHERE column_id = %s
                """,
                (data.get("business_name"), data.get("business_desc"), column_id),
            )
            cur.execute(
                "DELETE FROM meta.metadata_column_term WHERE column_id = %s", (column_id,)
            )
            for term in data.get("business_terms") or []:
                term = str(term).strip()
                if not term:
                    continue
                cur.execute(
                    """
                    INSERT INTO meta.metadata_business_term (term, term_type)
                    VALUES (%s, 'synonym')
                    ON CONFLICT (term) DO UPDATE SET term = EXCLUDED.term
                    RETURNING term_id
                    """,
                    (term,),
                )
                term_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO meta.metadata_column_term (column_id, term_id, source)
                    VALUES (%s, %s, 'llm')
                    ON CONFLICT DO NOTHING
                    """,
                    (column_id, term_id),
                )
                stats["terms"] += 1
            stats["columns"] += 1
        conn.commit()

    return stats

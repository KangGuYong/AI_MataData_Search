from collections.abc import Sequence

from app.config import settings
from app.models import Edge, JoinPath

TABLE_SQL = """
SELECT t.table_id, t.schema_name, t.table_name,
       COALESCE(t.business_desc, t.table_comment, '')
FROM meta.metadata_table t
WHERE t.table_id = ANY(%s)
"""

COLUMN_SQL = """
SELECT c.table_id, c.column_name, c.data_type, c.is_primary_key, c.is_foreign_key,
       COALESCE(c.business_name, c.column_comment, ''),
       c.distinct_count,
       (SELECT array_agg(v.value_text ORDER BY v.value_freq DESC)
        FROM meta.metadata_column_value v WHERE v.column_id = c.column_id),
       (SELECT array_agg(bt.term)
        FROM meta.metadata_column_term ct
        JOIN meta.metadata_business_term bt USING (term_id)
        WHERE ct.column_id = c.column_id)
FROM meta.metadata_column c
WHERE c.table_id = ANY(%s) AND c.is_active
ORDER BY c.table_id, c.ordinal_position
"""


def _key_columns(edges: Sequence[Edge], table_id: int) -> set[str]:
    keys = set()
    for e in edges:
        if e.from_table_id == table_id:
            keys.add(e.from_column)
        if e.to_table_id == table_id:
            keys.add(e.to_column)
    return keys


def build(
    cur,
    question: str,
    selected_ids: Sequence[int],
    paths: Sequence[JoinPath],
) -> tuple[str, list[int], list[str]]:
    """LLM에 보낼 컨텍스트 문자열과, 실제로 포함된 테이블 id/이름을 만든다.

    선정 테이블은 전체 컬럼을, 조인 경로상의 브릿지 테이블은 조인 키 컬럼만 넣는다.
    """
    selected = list(dict.fromkeys(selected_ids))
    bridges: list[int] = []
    for p in paths:
        for tid in p.tables:
            if tid not in selected and tid not in bridges:
                bridges.append(tid)

    all_ids = (selected + bridges)[: settings.max_context_tables]
    path_edges = [e for p in paths for e in p.edges]

    cur.execute(TABLE_SQL, (all_ids,))
    tables = {r[0]: r[1:] for r in cur.fetchall()}
    cur.execute(COLUMN_SQL, (all_ids,))
    columns: dict[int, list[tuple]] = {}
    for row in cur.fetchall():
        columns.setdefault(row[0], []).append(row[1:])

    lines = ["[질문]", question, "", "[테이블]"]
    for tid in all_ids:
        if tid not in tables:
            continue
        schema, tname, tdesc = tables[tid]
        is_bridge = tid in bridges
        suffix = "  (조인 경유 테이블)" if is_bridge else ""
        lines.append(f"{schema}.{tname}  — {tdesc or '(설명 없음)'}{suffix}")
        keys = _key_columns(path_edges, tid)
        for (cname, dtype, is_pk, is_fk, cdesc,
             distinct, values, terms) in columns.get(tid, []):
            if is_bridge and cname not in keys and not is_pk:
                continue
            flag = "PK" if is_pk else ("FK" if is_fk else "  ")
            lines.append(f"  {cname:<16} {dtype:<15} {flag}  {cdesc}")
            if values:
                shown = ", ".join(values[:10])
                lines.append(f"{'':>21}▸ 값: {shown} (총 {distinct}종)")
            if terms:
                lines.append(f"{'':>21}▸ 업무용어: {', '.join(terms)}")
        lines.append("")

    lines.append("[관계]")
    if path_edges:
        seen = set()
        for e in path_edges:
            a, b = tables.get(e.from_table_id), tables.get(e.to_table_id)
            if not a or not b:
                continue
            text = f"{a[0]}.{a[1]}.{e.from_column} = {b[0]}.{b[1]}.{e.to_column}"
            if text not in seen:
                seen.add(text)
                lines.append(text)
    else:
        lines.append("-- 관계 없음")

    lines += [
        "",
        "[규칙]",
        "- PostgreSQL 문법. SELECT 단일문만.",
        "- 위에 없는 테이블/컬럼 사용 금지.",
        "- 값 목록이 제시된 컬럼은 그 값을 정확히 사용할 것.",
        "- 설명 없이 SQL만 출력.",
    ]

    names = [f"{tables[t][0]}.{tables[t][1]}" for t in all_ids if t in tables]
    return "\n".join(lines), all_ids, names

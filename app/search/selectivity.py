"""토큰 변별력(IDF) 계산.

무관한 질문을 거르기 위한 것이다. 융합 점수에 절대 하한을 두는 방식은
이 데이터에서 작동하지 않는다 - 실측 결과 "날씨 정보 알려줘"의 최고
점수(0.04892)가 정상 질문인 "2024년 주문 목록"(0.04892)과 같고
"고객 정보 보여줘"(0.04865)보다 오히려 높았다. 벡터 경로에 유사도 하한을
두는 것도 마찬가지다("날씨" 0.4274 > "서울식품" 0.3960).

실제로 갈리는 것은 토큰의 변별력이다. '정보'는 4개 테이블 중 3개에
매칭되어 어느 테이블도 특정하지 못하는 반면, '주문'이나 '부산'은 특정
테이블을 가리킨다. 무관한 질문에는 변별력 있는 토큰이 아예 없다.
"""

import math

COUNT_SQL = """
SELECT count(DISTINCT table_id) FROM (
    SELECT c.table_id
    FROM meta.metadata_column c
    WHERE c.is_active AND %(tok)s <%% c.search_text
    UNION
    SELECT t.table_id
    FROM meta.metadata_table t
    WHERE t.is_active AND %(tok)s <%% t.search_text
    UNION
    SELECT c.table_id
    FROM meta.metadata_column_value v
    JOIN meta.metadata_column c USING (column_id)
    WHERE v.value_text = %(tok)s OR v.value_text %% %(tok)s
) u
"""


def token_table_counts(cur, tokens: list[str], min_sim: float) -> dict[str, int]:
    """토큰별로 몇 개의 테이블에 매칭되는지 센다."""
    if not tokens:
        return {}
    cur.execute(
        "SELECT set_config('pg_trgm.word_similarity_threshold', %s, true)",
        (str(min_sim),),
    )
    cur.execute(
        "SELECT set_config('pg_trgm.similarity_threshold', %s, true)",
        (str(min_sim),),
    )
    counts: dict[str, int] = {}
    for tok in tokens:
        cur.execute(COUNT_SQL, {"tok": tok})
        counts[tok] = cur.fetchone()[0]
    return counts


def max_idf(counts: dict[str, int], total_tables: int) -> float:
    """가장 변별력 있는 토큰의 IDF를 돌려준다.

    아무 테이블에도 매칭되지 않는 토큰(count=0)은 변별력이 아니라 무지식이므로
    0으로 친다. 모든 테이블에 매칭되는 토큰도 log(M/M)=0 으로 자연히 0이 된다.
    """
    if not counts or total_tables <= 0:
        return 0.0
    best = 0.0
    for n in counts.values():
        if n <= 0:
            continue
        best = max(best, math.log(total_tables / n))
    return best

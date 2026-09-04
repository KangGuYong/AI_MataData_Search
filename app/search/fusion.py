from collections import defaultdict
from collections.abc import Sequence

from app.models import SearchHit, TableScore


def fuse(
    hits: Sequence[SearchHit],
    *,
    k: int,
    weights: dict[str, float],
    max_hits_per_table: int,
    top_tables: int,
    cutoff_ratio: float,
) -> list[TableScore]:
    """여러 검색 경로의 히트를 테이블 단위 점수로 융합한다 (가중 RRF).

    경로마다 점수 스케일이 달라 원점수를 섞지 않고 순위만 쓴다.
    한 테이블이 컬럼 다수로 점수를 독식하지 않도록 테이블당 상위
    max_hits_per_table 개만 합산하고, 최고점의 cutoff_ratio 미만은 버린다.
    """
    by_table: dict[int, list[SearchHit]] = defaultdict(list)
    for h in hits:
        by_table[h.table_id].append(h)

    def contribution(h: SearchHit) -> float:
        return weights.get(h.source, 0.0) / (k + h.rank)

    scored: list[TableScore] = []
    for table_id, table_hits in by_table.items():
        kept = sorted(table_hits, key=contribution, reverse=True)[:max_hits_per_table]
        scored.append(
            TableScore(
                table_id=table_id,
                score=sum(contribution(h) for h in kept),
                hits=tuple(kept),
            )
        )

    scored.sort(key=lambda t: (-t.score, t.table_id))
    if not scored:
        return []

    cutoff = scored[0].score * cutoff_ratio
    return [t for t in scored[:top_tables] if t.score >= cutoff]

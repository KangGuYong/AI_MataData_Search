from collections import defaultdict, deque
from collections.abc import Sequence

from app.models import Edge, JoinPath


def _adjacency(edges: Sequence[Edge]) -> dict[int, list[Edge]]:
    """FK를 무방향으로 확장한다. 조인은 어느 방향으로도 가능하다."""
    adj: dict[int, list[Edge]] = defaultdict(list)
    for e in edges:
        adj[e.from_table_id].append(e)
        adj[e.to_table_id].append(
            Edge(e.to_table_id, e.to_column, e.from_table_id, e.from_column)
        )
    return adj


def _bfs(adj: dict[int, list[Edge]], start: int, goal: int, max_depth: int) -> JoinPath | None:
    if start == goal:
        return None
    queue: deque[tuple[int, tuple[int, ...], tuple[Edge, ...]]] = deque(
        [(start, (start,), ())]
    )
    visited = {start}
    while queue:
        node, tables, path_edges = queue.popleft()
        if len(path_edges) >= max_depth:
            continue
        for e in adj.get(node, []):
            nxt = e.to_table_id
            if nxt in visited:
                continue
            new_tables = tables + (nxt,)
            new_edges = path_edges + (e,)
            if nxt == goal:
                return JoinPath(tables=new_tables, edges=new_edges)
            visited.add(nxt)
            queue.append((nxt, new_tables, new_edges))
    return None


def find_join_paths(
    edges: Sequence[Edge], table_ids: Sequence[int], max_depth: int
) -> list[JoinPath]:
    """선정된 테이블 쌍마다 최단 조인 경로를 찾는다.

    경로가 없으면 억지로 잇지 않는다. 호출자가 '관계 없음'으로 표기한다.
    """
    adj = _adjacency(edges)
    targets = list(dict.fromkeys(table_ids))
    paths: list[JoinPath] = []
    for i, a in enumerate(targets):
        for b in targets[i + 1 :]:
            p = _bfs(adj, a, b, max_depth)
            if p is not None:
                paths.append(p)
    return paths


RELATION_SQL = """
SELECT r.from_table_id, fc.column_name, r.to_table_id, tc.column_name
FROM meta.metadata_relation r
JOIN meta.metadata_column fc ON fc.column_id = r.from_column_id
JOIN meta.metadata_column tc ON tc.column_id = r.to_column_id
WHERE r.is_active
"""


def load_edges(cur) -> list[Edge]:
    cur.execute(RELATION_SQL)
    return [Edge(ft, fc, tt, tc) for ft, fc, tt, tc in cur.fetchall()]

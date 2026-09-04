from app.models import Edge
from app.search.graph import find_join_paths

# customer(1) - orders(2) - order_detail(3) - product(4)
EDGES = [
    Edge(2, "customer_id", 1, "customer_id"),
    Edge(3, "order_id", 2, "order_id"),
    Edge(3, "product_id", 4, "product_id"),
]


def test_직접_연결된_두_테이블():
    paths = find_join_paths(EDGES, [1, 2], max_depth=3)
    assert len(paths) == 1
    assert paths[0].tables == (1, 2)
    assert len(paths[0].edges) == 1


def test_브릿지를_거치는_경로():
    paths = find_join_paths(EDGES, [1, 4], max_depth=3)
    assert len(paths) == 1
    assert paths[0].tables == (1, 2, 3, 4)
    assert len(paths[0].edges) == 3


def test_max_depth를_넘으면_경로가_없다():
    paths = find_join_paths(EDGES, [1, 4], max_depth=2)
    assert paths == []


def test_연결되지_않은_테이블():
    paths = find_join_paths(EDGES, [1, 99], max_depth=3)
    assert paths == []


def test_세_테이블이면_쌍마다_경로를_찾는다():
    paths = find_join_paths(EDGES, [1, 2, 3], max_depth=3)
    assert len(paths) == 3


def test_테이블이_하나면_경로가_없다():
    assert find_join_paths(EDGES, [1], max_depth=3) == []

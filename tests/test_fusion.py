from app.models import SearchHit
from app.search.fusion import fuse

W = {"value": 3.0, "v_col": 1.0, "v_tbl": 1.0, "keyword": 0.7}
BASE = dict(k=60, weights=W, max_hits_per_table=3, top_tables=5, cutoff_ratio=0.2)


def hit(source, table_id, rank, column_id=None):
    return SearchHit(source=source, table_id=table_id, column_id=column_id,
                     rank=rank, raw_score=0.0)


def test_값_히트가_벡터_히트보다_높은_점수를_받는다():
    hits = [hit("value", 1, 1), hit("v_col", 2, 1)]
    result = fuse(hits, **BASE)
    assert [t.table_id for t in result] == [1, 2]
    assert result[0].score == 3.0 / 61
    assert result[1].score == 1.0 / 61


def test_한_테이블의_히트는_상위_3개까지만_합산한다():
    hits = [hit("v_col", 1, r, column_id=r) for r in range(1, 6)]
    result = fuse(hits, **BASE)
    expected = sum(1.0 / (60 + r) for r in (1, 2, 3))
    assert result[0].score == expected
    assert len(result[0].hits) == 3


def test_최고점의_20_퍼센트_미만인_테이블은_제외된다():
    # table 1: value rank1  = 3.0/61  = 0.04918
    # table 2: keyword rank50 = 0.7/110 = 0.00636  < 0.04918*0.2 = 0.00984 이므로 탈락
    hits = [hit("value", 1, 1), hit("keyword", 2, 50)]
    result = fuse(hits, **BASE)
    assert [t.table_id for t in result] == [1]


def test_top_tables_상한을_지킨다():
    hits = [hit("v_col", tid, 1) for tid in range(1, 11)]
    result = fuse(hits, **BASE)
    assert len(result) == 5


def test_동점이면_table_id_오름차순():
    hits = [hit("v_col", 7, 1), hit("v_col", 3, 1)]
    result = fuse(hits, **BASE)
    assert [t.table_id for t in result] == [3, 7]


def test_히트가_없으면_빈_결과():
    assert fuse([], **BASE) == []


def test_min_score는_절대_하한으로_동작한다():
    # 상대 컷오프(20%)로는 살아남지만 절대 하한에는 못 미치는 경우
    hits = [hit("v_col", 1, 1), hit("v_col", 2, 2)]
    kept = fuse(hits, **{**BASE, "min_score": 0.0})
    assert [t.table_id for t in kept] == [1, 2]
    cut = fuse(hits, **{**BASE, "min_score": 1.0 / 61})
    assert [t.table_id for t in cut] == [1]
    assert fuse(hits, **{**BASE, "min_score": 1.0}) == []


def test_min_score_기본값은_비활성이다():
    hits = [hit("v_col", 1, 1)]
    assert fuse(hits, **BASE) == fuse(hits, **{**BASE, "min_score": 0.0})

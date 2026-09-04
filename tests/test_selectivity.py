import math

from app.search.selectivity import max_idf


def test_모든_테이블에_매칭되는_토큰은_변별력이_0이다():
    # '정보'처럼 어디에나 있는 단어는 어느 테이블도 특정하지 못한다
    assert max_idf({"정보": 4}, total_tables=4) == 0.0


def test_한_테이블만_가리키는_토큰이_변별력이_가장_높다():
    assert max_idf({"부산": 1}, total_tables=4) == math.log(4)


def test_가장_변별력_높은_토큰을_고른다():
    counts = {"정보": 4, "주문": 2, "부산": 1}
    assert max_idf(counts, total_tables=4) == math.log(4)


def test_아무데도_매칭되지_않는_토큰은_변별력이_아니라_무지식이다():
    # count=0은 log(4/0) 예외를 내지 않고 무시되어야 한다
    assert max_idf({"날씨": 0, "알려줘": 0}, total_tables=4) == 0.0


def test_실측된_무관_질문과_정상_질문이_갈린다():
    # "날씨 정보 알려줘" -> '정보'만 3개 테이블에 매칭
    irrelevant = max_idf({"날씨": 0, "정보": 3, "알려줘": 0}, total_tables=4)
    # "2024년 주문 목록" -> '주문'이 2개 테이블에 매칭
    relevant = max_idf({"2024년": 0, "주문": 2, "목록": 0}, total_tables=4)
    assert irrelevant < 0.5 <= relevant


def test_빈_입력():
    assert max_idf({}, total_tables=4) == 0.0
    assert max_idf({"주문": 2}, total_tables=0) == 0.0

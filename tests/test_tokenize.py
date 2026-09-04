from app.search.tokenize import tokenize


def test_조사를_제거하고_2글자_이상만_남긴다():
    assert tokenize("서울 고객의 2025년 판매실적") == ["서울", "고객", "2025년", "판매실적"]


def test_다양한_조사_처리():
    assert tokenize("부산에서 상품을 주문한 건수는") == ["부산", "상품", "주문한", "건수"]


def test_한글자_토큰은_버린다():
    assert tokenize("이 값 총 매출") == ["매출"]


def test_구두점을_제거한다():
    assert tokenize("매출액, 총합?") == ["매출액", "총합"]


def test_중복은_순서를_지키며_제거한다():
    assert tokenize("서울 서울 고객") == ["서울", "고객"]


def test_빈_입력():
    assert tokenize("") == []
    assert tokenize("   ") == []

import re

# 긴 조사를 먼저 매칭해야 한다. 순서가 중요하다.
PARTICLES = (
    "에서는", "으로는", "에서", "으로", "에게", "까지", "부터",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "과", "와", "도",
)
_PUNCT = re.compile(r"[^\w가-힣]+")


def _strip_particle(token: str) -> str:
    for p in PARTICLES:
        if token.endswith(p) and len(token) - len(p) >= 2:
            return token[: -len(p)]
    return token


def tokenize(text: str) -> list[str]:
    """한국어 질문을 검색용 토큰으로 자른다.

    형태소 분석기 없이 공백 분리 + 말미 조사 제거 + 2글자 이상 필터로 시작한다.
    정확도가 부족하면 형태소 분석기로 교체한다.
    """
    if not text or not text.strip():
        return []
    out: list[str] = []
    for raw in text.split():
        token = _PUNCT.sub("", raw)
        if not token:
            continue
        token = _strip_particle(token)
        if len(token) >= 2 and token not in out:
            out.append(token)
    return out

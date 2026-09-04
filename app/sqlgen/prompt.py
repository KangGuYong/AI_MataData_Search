"""LLM에게 보내는 SQL 생성 프롬프트 템플릿."""

SYSTEM = (
    "당신은 PostgreSQL 전문가입니다. 주어진 스키마 정보만 사용해 "
    "정확한 SELECT 문 하나를 작성합니다. 설명은 하지 않습니다."
)

RETRY_TEMPLATE = """{context}

직전에 아래 SQL을 생성했으나 PostgreSQL 검증에서 실패했습니다.

실패한 SQL:
{sql}

오류:
{error}

오류를 고친 SELECT 문을 다시 출력하시오. 설명 없이 SQL만 출력하시오."""

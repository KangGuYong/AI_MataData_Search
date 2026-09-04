"""LLM 응답에서 SQL을 뽑아내는 순수 함수 모듈."""

import re

from app.llm.base import LLMClient
from app.sqlgen.prompt import RETRY_TEMPLATE, SYSTEM

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str:
    """코드펜스나 앞뒤 설명이 섞인 응답에서 SQL만 뽑는다."""
    m = _FENCE.search(text)
    body = m.group(1) if m else text
    body = body.strip()
    m = re.search(r"\b(WITH|SELECT)\b", body, re.IGNORECASE)
    if m:
        body = body[m.start() :]
    return body.strip().rstrip(";").strip()


def looks_like_sql(text: str) -> bool:
    """SELECT/WITH 로 시작하는지만 본다. 상세 검증은 guard가 한다."""
    return bool(re.match(r"^\s*(WITH|SELECT)\b", text or "", re.IGNORECASE))


def generate(llm: LLMClient, context: str) -> str:
    return extract_sql(llm.complete(context, system=SYSTEM))


def regenerate(llm: LLMClient, context: str, failed_sql: str, error: str) -> str:
    prompt = RETRY_TEMPLATE.format(context=context, sql=failed_sql, error=error)
    return extract_sql(llm.complete(prompt, system=SYSTEM))
